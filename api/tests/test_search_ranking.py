"""Retrieval quality gate for KNOWN_ISSUES #13. Live-stack, read-only.

The property under test: a user who types a paper's title gets that paper first,
not third. 20 sampled titles must land top-1 at >=90%; the two concrete failures
recorded in KNOWN_ISSUES #13 are pinned as regressions.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from provenance.db import SessionLocal


@pytest.fixture()
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


def test_exact_title_lands_first(client, db):
    rows = db.execute(text(
        "SELECT id, title FROM works WHERE NOT is_stub AND title IS NOT NULL "
        "AND length(title) BETWEEN 30 AND 120 ORDER BY id LIMIT 20")).all()
    assert len(rows) == 20
    misses = []
    for wid, title in rows:
        r = client.get("/api/papers/search", params={"q": title, "limit": 3})
        assert r.status_code == 200
        items = r.json()["items"]
        if not items or items[0]["id"] != wid:
            misses.append((wid, title[:60]))
    assert len(misses) <= 2, f"exact-title top-1 gate (>=18/20) failed: {misses}"


def test_known_issue_13_propensity_query(client, db):
    """Non-exact query: KNOWN_ISSUES #13 recorded Rosenbaum & Rubin absent from the
    top 3 for this query. The corpus holds duplicate records of the paper, so the
    gate is title-match in the top 3, not a specific work id."""
    r = client.get("/api/papers/search", params={
        "q": "central role propensity score observational", "limit": 3})
    assert r.status_code == 200
    titles = [(i["title"] or "").lower() for i in r.json()["items"]]
    assert any("central role of the propensity score" in t for t in titles), titles


def test_known_issue_13_em_paper(client, db):
    row = db.execute(text(
        "SELECT id, title FROM works WHERE title ILIKE "
        "'Maximum Likelihood from Incomplete Data%' AND NOT is_stub LIMIT 1")).first()
    if row is None:
        pytest.skip("EM paper not in corpus")
    r = client.get("/api/papers/search", params={"q": row.title, "limit": 3})
    assert r.status_code == 200
    items = r.json()["items"]
    assert items and items[0]["id"] == row.id, (
        f"EM paper ranked below top for its own title; got {[i['id'] for i in items]}")
