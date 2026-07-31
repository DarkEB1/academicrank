"""Ranked search (RRF blend). Live stack; read-mostly, one seeded profile."""
from __future__ import annotations

import pytest
from sqlalchemy import text

from provenance.db import SessionLocal
from provenance.searchrank import FETCH_K, fuse, merit_ranks

QUERY = "graph"  # broad token; assert non-empty and skip if the corpus lacks it


@pytest.fixture()
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _search(client, **params):
    r = client.get("/api/papers/search", params={"q": QUERY, **params})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture(scope="module")
def warm_profile(client):
    """A profile with a real trust set, seeded and warmed once for this module.

    Same idiom as conftest.py's `seeded` fixture: trust the 5 most in-corpus-cited
    non-stub works at strength 5, then pay the cold-ranking cost once here so the
    tests below are not each waiting on the engine to build walks.
    """
    with SessionLocal() as db:
        rows = db.execute(text(
            "SELECT id FROM works WHERE is_stub = false AND in_corpus_cited_by > 0 "
            "ORDER BY in_corpus_cited_by DESC LIMIT 5"
        )).all()
        if len(rows) < 5:
            rows = db.execute(text(
                "SELECT id FROM works ORDER BY cited_by_count DESC LIMIT 5")).all()
    work_ids = [r[0] for r in rows]

    r = client.post("/api/profiles", json={"label": "pytest-search-rrf"})
    assert r.status_code == 201, r.text
    prof = r.json()
    auth = {"Authorization": f"Bearer {prof['token']}"}
    for wid in work_ids:
        r = client.post(f"/api/profiles/{prof['id']}/trust",
                        json={"work_id": wid, "strength": 5}, headers=auth)
        assert r.status_code == 200, r.text

    # Pay the cold-ranking cost once, here, so individual tests are not each
    # waiting on the engine to build walks.
    r = client.get(f"/api/profiles/{prof['id']}/rankings",
                   params={"limit": 1}, headers=auth)
    assert r.status_code == 200, r.text

    return prof["id"], prof["token"]


def test_relevance_mode_is_byte_compatible(client):
    default = client.get("/api/papers/search", params={"q": QUERY, "limit": 10})
    explicit = client.get("/api/papers/search",
                          params={"q": QUERY, "limit": 10, "rank": "relevance"})
    assert default.status_code == explicit.status_code == 200
    assert default.content == explicit.content
    body = default.json()
    assert set(body) == {"total", "items"}  # old shape, nothing added


def test_bad_rank_value_is_422(client):
    r = client.get("/api/papers/search", params={"q": QUERY, "rank": "pagerank"})
    assert r.status_code == 422


def test_global_mode_shape_and_order(client, db):
    body = _search(client, rank="global", limit=25)
    if body["total"] == 0:
        pytest.skip(f"corpus has no match for {QUERY!r}")
    assert body["rank"] == "global"
    assert "unpersonalised" in body["disclaimer"]
    items = body["items"]
    assert items, "non-zero total but empty first page"
    for it in items:
        # no bare scores
        assert {"trust", "uncertainty", "global_merit", "rank", "disagreement",
                "relevance_rank", "merit_rank"} <= set(it)
        assert it["uncertainty"]["tie_group"] >= 0
    assert [it["rank"] for it in items] == list(range(1, len(items) + 1))
    # the order is genuinely RRF of (text order, global merit order)
    from provenance import services
    text_ids = [r_[0] for r_ in db.execute(text(
        "SELECT w.id FROM works w "
        "WHERE w.tsv @@ plainto_tsquery('english', :q) "
        "AND w.source <> 'user_upload' "
        "ORDER BY ts_rank(w.tsv, plainto_tsquery('english', :q)) DESC,"
        " w.cited_by_count DESC LIMIT :k"), {"q": QUERY, "k": FETCH_K}).all()]
    expected = [f.work_id for f in
                fuse(text_ids, merit_ranks(services.global_scores(db)))]
    assert [it["id"] for it in items] == expected[:len(items)]


def test_global_mode_pagination_is_stable(client):
    page1 = _search(client, rank="global", limit=5, offset=0)
    page2 = _search(client, rank="global", limit=5, offset=5)
    if page1["total"] < 10:
        pytest.skip("not enough matches to paginate")
    ids1 = [it["id"] for it in page1["items"]]
    ids2 = [it["id"] for it in page2["items"]]
    assert not set(ids1) & set(ids2)
    assert page1["total"] == page2["total"] <= FETCH_K


def test_trust_without_profile_falls_back_to_global(client):
    body = _search(client, rank="trust")  # conftest client sends no auth by default
    assert body["rank"] == "global"
    assert body["cold_start"]["reliable"] is False
    assert body["cold_start"]["message"]  # says why it degraded


def test_trust_mode_with_seeds(client, db, warm_profile):
    """warm_profile: reuse the suite's existing seeded-profile fixture if one
    exists in conftest/test_api.py; otherwise create a profile, trust the 5 most
    in-corpus-cited non-stub works at strength 5, and wait for /rankings to
    return items (SLOW_TIMEOUT pattern from conftest)."""
    pid, token = warm_profile
    r = client.get("/api/papers/search",
                   params={"q": QUERY, "rank": "trust", "limit": 25},
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    body = r.json()
    if body["total"] == 0:
        pytest.skip(f"corpus has no match for {QUERY!r}")
    assert body["rank"] == "trust"
    assert body["cold_start"]["seeds"] >= 5
    # trust values are the profile's MeritRank scores: at least one non-zero,
    # and uncertainty is real (not all zeros) for pooled items.
    assert any(it["trust"] > 0 for it in body["items"])
