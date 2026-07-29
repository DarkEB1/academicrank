"""Integration-test fixtures.

These tests hit a **live stack**: a real Postgres carrying the pgmer2 connector, a real
MeritRank service and the real corpus. Nothing is mocked -- a mocked MeritRank would
test our mock, and every interesting property of this system (walk-based scores, tie
groups, path reconstruction) only exists when the engine is actually there.

Run against docker compose:

    docker compose up -d db mr-service
    cd api && python -m pytest

`DATABASE_URL` overrides the target. The default is the host-side port from
provenance.config (55432 -- a host-installed Postgres squats on 5432).
"""
from __future__ import annotations

import os
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from provenance import config
from provenance.db import SessionLocal, engine
from provenance.main import app
from provenance.models import Base

# The corpus loader may still be running when the suite starts.
CORPUS_WAIT_SECONDS = float(os.environ.get("PROVENANCE_TEST_CORPUS_WAIT", "180"))
MIN_WORKS = 100
# A cold ranking is ~7 full engine passes over the whole graph.
SLOW_TIMEOUT = 600.0


def _wait_for_corpus() -> int:
    deadline = time.time() + CORPUS_WAIT_SECONDS
    n = 0
    while time.time() < deadline:
        with engine.connect() as conn:
            n = int(conn.execute(text("SELECT count(*) FROM works")).scalar_one())
            edges = int(conn.execute(
                text("SELECT count(*) FROM graph_edges")).scalar_one())
        if n >= MIN_WORKS and edges > 0:
            return n
        time.sleep(5)
    return n


@pytest.fixture(scope="session", autouse=True)
def live_stack() -> None:
    """Fail loudly rather than silently testing against an empty database."""
    Base.metadata.create_all(engine)
    with engine.connect() as conn:
        version = conn.execute(text(
            "SELECT extversion FROM pg_extension WHERE extname = 'pgmer2'"
        )).scalar()
        assert version, (
            "The pgmer2 extension is missing -- this is not the MeritRank Postgres. "
            f"DATABASE_URL={config.DATABASE_URL}"
        )
    n = _wait_for_corpus()
    assert n >= MIN_WORKS, (
        f"Only {n} works in the corpus after waiting {CORPUS_WAIT_SECONDS:.0f}s. "
        "These are integration tests; they need the loaded corpus."
    )


@pytest.fixture(scope="session")
def client() -> TestClient:
    # Long timeout: the first ranking for a fresh ego makes the engine build walks.
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture(scope="session")
def corpus_ids() -> list[str]:
    """Well-connected, non-stub works -- a trust set of isolated stubs would reach
    nothing and every ranking assertion would be vacuous."""
    with SessionLocal() as db:
        rows = db.execute(text(
            "SELECT id FROM works WHERE is_stub = false AND in_corpus_cited_by > 0 "
            "ORDER BY in_corpus_cited_by DESC LIMIT 8"
        )).all()
        if len(rows) < 6:
            rows = db.execute(text(
                "SELECT id FROM works ORDER BY cited_by_count DESC LIMIT 8")).all()
    return [r[0] for r in rows]


def _new_profile(client: TestClient, label: str) -> dict:
    r = client.post("/api/profiles", json={"label": label})
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture(scope="session")
def anon(client: TestClient) -> dict:
    """A profile with no trust set: exercises the cold-start paths."""
    return _new_profile(client, "pytest-anon")


@pytest.fixture(scope="session")
def seeded(client: TestClient, corpus_ids: list[str]) -> dict:
    """A profile with a real trust set, warmed once for the whole session."""
    prof = _new_profile(client, "pytest-seeded")
    auth = {"Authorization": f"Bearer {prof['token']}"}
    for wid in corpus_ids[:6]:
        r = client.post(f"/api/profiles/{prof['id']}/trust",
                        json={"work_id": wid, "strength": 4}, headers=auth)
        assert r.status_code == 200, r.text
    prof["auth"] = auth
    prof["work_ids"] = corpus_ids[:6]

    # Pay the cold-ranking cost once, here, so individual tests are not each waiting
    # on the engine to build walks.
    r = client.get(f"/api/profiles/{prof['id']}/rankings",
                   params={"limit": 1}, headers=auth)
    assert r.status_code == 200, r.text
    return prof


@pytest.fixture
def auth(seeded: dict) -> dict:
    return seeded["auth"]


@pytest.fixture(autouse=True)
def _no_leaked_cookie(client: TestClient):
    """`POST /api/profiles` sets `pv_token`, and the cookie is a valid credential. On a
    shared client that would silently authenticate tests that meant to be anonymous."""
    client.cookies.clear()
    yield
    client.cookies.clear()
