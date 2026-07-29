"""Phase 0 gate: alembic actually runs, and graph_meta.version invalidates caches.

Runs LAST (zz prefix): bumping the graph version deliberately invalidates every
score cache in the process, and doing that mid-suite would force the other tests
to re-pay multi-second engine reads for no extra coverage.
"""
from __future__ import annotations

import time

from sqlalchemy import text

from provenance import ranking, services
from provenance.db import SessionLocal
from provenance.graphmeta import bump_graph_version, graph_version
from provenance.meritrank import MeritRank
from provenance.models import Profile


def test_alembic_version_is_stamped_and_at_head():
    """KNOWN_ISSUES §16: alembic never ran. After the fix, the live database must
    carry alembic_version at the graph_meta revision (the current head)."""
    with SessionLocal() as db:
        rev = db.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert rev == "c9e1a7b4d2f0", (
        f"alembic_version is {rev!r}; expected the graph_meta head. "
        "Did the api container start without running migrations?"
    )


def test_graph_meta_row_exists_and_bump_is_monotonic():
    with SessionLocal() as db:
        v0 = graph_version(db)
        assert v0 >= 1, "graph_meta must be seeded by the migration"
        v1 = bump_graph_version(db)
        db.commit()
        assert v1 == v0 + 1
        assert graph_version(db) == v1


def test_version_bump_invalidates_other_profiles_cached_ranking(client, seeded):
    """The gate demonstration: a manual graph_edges insert plus a version bump must
    invalidate a DIFFERENT profile's cached ranking (both cache layers)."""
    auth = seeded["auth"]

    # The seeded fixture already paid for its ranking; both layers must hold it.
    r = client.get(f"/api/profiles/{seeded['id']}/rankings",
                   params={"limit": 1}, headers=auth)
    assert r.status_code == 200
    assert seeded["id"] in ranking._CACHE
    with services._pool_lock:
        assert any(k[0] == seeded["id"] for k in services._pool_cache)

    with SessionLocal() as db:
        gen_before = services.graph_generation(db)
        # A manual graph mutation, exactly as the gate specifies: one edge row in,
        # version bumped, committed. (Src/dst use engine-legal U-prefixed names.)
        db.execute(text(
            "INSERT INTO graph_edges (src, dst, weight, context, relation) "
            "VALUES ('Ugate_test_src', 'Ugate_test_dst', 0.5, 'citation', 'cites') "
            "ON CONFLICT DO NOTHING"))
        bump_graph_version(db)
        db.commit()

        # Any other profile's next read runs _check_generation and must see the bump.
        gen_after = services._check_generation(db, "some_other_profile")
        assert gen_after == gen_before + 1

    # Layer 1: the composed-pool cache is emptied outright.
    with services._pool_lock:
        assert not services._pool_cache, "pool cache must be cleared on version bump"
    # Layer 2: the raw per-context score cache for the seeded profile is dropped.
    assert seeded["id"] not in ranking._CACHE

    # And a fresh pool build must key on the new generation, not the old one.
    with SessionLocal() as db:
        stale_key_gens = {k[3] for k in services._pool_cache if k[0] == seeded["id"]}
        assert gen_before not in stale_key_gens

        # Cleanup: remove the synthetic row; bump again so nothing keys on a graph
        # containing an edge that no longer exists.
        db.execute(text(
            "DELETE FROM graph_edges WHERE src = 'Ugate_test_src'"))
        bump_graph_version(db)
        db.commit()


def test_ensure_seeded_skips_engine_writes_when_nothing_changed(seeded, monkeypatch):
    """The old behaviour re-put every trust edge on every cold read (~87ms each,
    serialised). Now: zero mr_put_edge calls when (trust set, graph version) are
    unchanged, full re-seed after a version bump."""
    calls: list[tuple] = []
    orig = MeritRank.put_edge

    def counting(self, src, dst, weight, context=""):
        calls.append((src, dst))
        return orig(self, src, dst, weight, context)

    monkeypatch.setattr(MeritRank, "put_edge", counting)

    with SessionLocal() as db:
        prof = db.get(Profile, seeded["id"])

        # First call may or may not write depending on prior state; it settles the marker.
        ranking.ensure_seeded(db, prof)
        db.commit()
        calls.clear()

        t0 = time.time()
        n = ranking.ensure_seeded(db, prof)
        elapsed_ms = (time.time() - t0) * 1000.0
        assert n == len(seeded["work_ids"])
        assert calls == [], "unchanged trust set + graph version must not touch the engine"
        assert elapsed_ms < 250, f"gated ensure_seeded took {elapsed_ms:.0f}ms"

        # A graph-version bump must force a full re-seed (rebuilds clear engine state).
        bump_graph_version(db)
        db.commit()
        ranking.ensure_seeded(db, prof)
        db.commit()
        assert len(calls) == len(seeded["work_ids"]), (
            f"expected {len(seeded['work_ids'])} re-seeded edges, got {len(calls)}")
