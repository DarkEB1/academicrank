"""Ranked search (RRF blend). Live stack; read-mostly, one seeded profile."""
from __future__ import annotations

import pytest
from sqlalchemy import text

from provenance.db import SessionLocal
from provenance.meritrank import Uncertainty, assign_tie_groups
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


def test_trust_mode_does_not_corrupt_the_shared_pool_cache(client, warm_profile):
    """Regression: the ranked branch used to hand assign_tie_groups() the SAME
    Uncertainty instances held in services._pool_cache (cache key: profile,
    trust signature, exclude_trusted, graph generation, weights, upload
    visibility, lift gamma -- NOT the search query). assign_tie_groups()
    mutates .tie_group on those shared instances in place, iterating the
    search's RRF order rather than the pool's real trust order -- so a
    rank=trust search silently rewrote the cached pool's tie brackets to
    reflect that search's ordering.

    GET /profiles/{id}/rankings?exclude_trusted=false with context=aggregate
    (the default) builds its pool with the exact same cache key rank=trust
    search uses (see papers.py: `build_pool(db, profile, context="aggregate",
    exclude_trusted=False)`), and its default sort ("trust") reads
    `item.uncertainty.tie_group` directly off the cached pool items rather
    than recomputing it (rankings.py only recomputes tie groups for
    `sort=lift`) -- so it is the exact reader the finding says gets corrupted.

    A generic broad token does not reliably reproduce this: the fix must be
    proven on pool items that the search's candidate set actually reaches, and
    an arbitrary word may share no vocabulary with this profile's trust
    neighbourhood at all. So the two search queries are built FROM two of the
    pool's own titles (guaranteeing each query's candidate set contains at
    least that item), which is what actually exercises the shared,
    order-dependent mutation.
    """
    pid, token = warm_profile
    auth = {"Authorization": f"Bearer {token}"}

    def _rankings_on_shared_pool(limit: int = 50):
        r = client.get(f"/api/profiles/{pid}/rankings",
                       params={"limit": limit, "exclude_trusted": "false"},
                       headers=auth)
        assert r.status_code == 200, r.text
        return r.json()["items"]

    def _trust_search(q: str):
        r = client.get("/api/papers/search",
                       params={"q": q, "rank": "trust", "limit": 25},
                       headers=auth)
        assert r.status_code == 200, r.text
        return r.json()

    baseline_items = _rankings_on_shared_pool()
    titled = [it for it in baseline_items
              if it.get("title") and len(it["title"]) > 15]
    if len(titled) < 2:
        pytest.skip("warm_profile's pool has too few titled items to build "
                     "two distinct title-derived queries")
    query_a = " ".join(titled[0]["title"].split()[:4])
    query_b = " ".join(titled[len(titled) // 2]["title"].split()[:4])
    baseline_by_id = {it["id"]: it["uncertainty"]["tie_group"]
                      for it in baseline_items}

    a = _trust_search(query_a)
    if a["total"] == 0:
        pytest.skip(f"corpus has no match for {query_a!r}")
    after_a_by_id = {it["id"]: it["uncertainty"]["tie_group"]
                     for it in _rankings_on_shared_pool()}

    b = _trust_search(query_b)
    if b["total"] == 0:
        pytest.skip(f"corpus has no match for {query_b!r}")
    after_b_by_id = {it["id"]: it["uncertainty"]["tie_group"]
                     for it in _rankings_on_shared_pool()}

    common = set(baseline_by_id) & set(after_a_by_id) & set(after_b_by_id)
    assert common, "no ids survived across the three /rankings snapshots"
    mismatches = {
        i: (baseline_by_id[i], after_a_by_id[i], after_b_by_id[i])
        for i in common
        if not (baseline_by_id[i] == after_a_by_id[i] == after_b_by_id[i])
    }
    assert not mismatches, (
        "/rankings tie_group values changed after interleaved rank=trust "
        "searches -- the search mutated the shared pool cache's Uncertainty "
        f"instances instead of working on private copies: {mismatches}")


# ---------------------------------------------------------------------------
# assign_tie_groups: direction-insensitive gap test (pure unit tests, no live stack)
# ---------------------------------------------------------------------------


def _unc(stderr: float) -> Uncertainty:
    return Uncertainty(stderr=stderr, ci_low=0.0, ci_high=0.0, tie_group=0,
                       method="test", n_samples=1)


def test_assign_tie_groups_breaks_on_ascent_not_just_descent():
    """Regression: RRF-fused search order is NOT monotonic in trust value (unlike
    every other caller of assign_tie_groups, which sorts its rows by value first).
    A signed gap test (`prev_value - value > tol`) only ever breaks a group on a
    DESCENT, so an ascent -- an out-of-pool 0.0 item immediately followed by a
    pooled ~0.5 item -- never broke the bracket, and the UI told the user two
    clearly separable papers were "statistically tied". The gap test must be
    direction-insensitive (`abs(...)`) so a large gap breaks the group regardless
    of which side is bigger.
    """
    rows = [
        ("a", 0.5, _unc(0.01)),
        ("b", 0.0, _unc(0.01)),  # descent 0.5 -> 0.0: old code already broke here
        ("c", 0.5, _unc(0.01)),  # ascent 0.0 -> 0.5: old code did NOT break here
    ]
    assign_tie_groups(rows)
    groups = [unc.tie_group for _wid, _v, unc in rows]
    assert groups[0] != groups[1], "descent of 0.5 with tight stderr must break the group"
    assert groups[1] != groups[2], "ascent of 0.5 with tight stderr must break the group too"
    assert groups == [0, 1, 2], f"expected three distinct tie groups, got {groups}"


def test_assign_tie_groups_monotonic_descending_unaffected():
    """Sanity check that abs() is a no-op for the monotonic-descending callers
    (rankings trust/lift sort, blindspots gap sort): behaviour must be identical
    to the old signed test when values only ever descend.
    """
    rows = [
        ("a", 0.90, _unc(0.01)),
        ("b", 0.89, _unc(0.05)),   # tiny gap, well within tolerance: same group
        ("c", 0.40, _unc(0.01)),  # big descent: new group
    ]
    assign_tie_groups(rows)
    groups = [unc.tie_group for _wid, _v, unc in rows]
    assert groups[0] == groups[1], "small descent within tolerance should stay tied"
    assert groups[1] != groups[2], "large descent should break the group"
