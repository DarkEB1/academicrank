"""Phase 3a gate: mr_put_edges + the Postgres-first confirm path.

Runs LAST of all (zzz): the final test performs a FULL graph rebuild
(scripts/build_graph.py), which truncates graph_edges, reloads the engine and
invalidates every cache -- the survival gate that would have caught the worst
pre-review design bug (B4/N6: uploads destroyed on rebuild).
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest
from sqlalchemy import text

from provenance import config, confirm as confirm_mod
from provenance.db import SessionLocal
from provenance.graphmeta import graph_version
from provenance.meritrank import Edge, MeritRank
from provenance.models import Upload, profile_node, work_node

from test_uploads_draft import bibliography_pdf

REPO = Path(__file__).resolve().parents[2]


UPLOAD_SUITE_LOCK = 919_191_001


@pytest.fixture(scope="module", autouse=True)
def _exclusive_upload_suite():
    """Two pytest processes running the upload modules against the shared stack
    interleave DESTRUCTIVELY: each module starts with a purge, so one run's
    purge deletes the other run's confirmed upload mid-flight (observed: the
    rebuild-survival test found {} because a concurrent session purged its
    upload between confirm and the rebuild assertions). A Postgres advisory
    lock serialises whole modules across processes."""
    from provenance.db import engine as _engine
    conn = _engine.connect()
    conn.exec_driver_sql(f"SELECT pg_advisory_lock({UPLOAD_SUITE_LOCK})")
    try:
        yield
    finally:
        conn.exec_driver_sql(f"SELECT pg_advisory_unlock({UPLOAD_SUITE_LOCK})")
        conn.close()


def purge_upload_state() -> None:
    """Remove every upload artefact from the database (uploads, UL works and
    their citations/edges, pytest trust rows) so an aborted earlier run cannot
    poison this one's assertions. Engine litter from old UL nodes persists
    until the next rebuild; the fresh never-reused L-ids keep it inert."""
    with SessionLocal() as db:
        db.execute(text(
            "UPDATE works w SET in_corpus_cited_by = "
            " greatest(in_corpus_cited_by - c.n, 0) "
            "FROM (SELECT dst_id, count(*) AS n FROM citations "
            "      WHERE src_id IN (SELECT id FROM works WHERE source = 'user_upload') "
            "      GROUP BY dst_id) c WHERE w.id = c.dst_id"))
        db.execute(text(
            "DELETE FROM citations WHERE src_id IN "
            "(SELECT id FROM works WHERE source = 'user_upload')"))
        db.execute(text(
            "DELETE FROM graph_edges WHERE src IN "
            "(SELECT 'U' || id FROM works WHERE source = 'user_upload') "
            "OR dst IN (SELECT 'U' || id FROM works WHERE source = 'user_upload')"))
        db.execute(text(
            "DELETE FROM trust WHERE profile_id IN "
            "(SELECT id FROM profiles WHERE label LIKE 'pytest-%')"))
        db.execute(text("DELETE FROM uploads"))
        db.execute(text("DELETE FROM works WHERE source = 'user_upload'"))
        db.execute(text(
            "INSERT INTO graph_meta (id, version) VALUES (1, 2) "
            "ON CONFLICT (id) DO UPDATE SET version = graph_meta.version + 1"))
        db.commit()


@pytest.fixture(scope="module", autouse=True)
def _clean_slate(_exclusive_upload_suite):
    purge_upload_state()


@pytest.fixture(scope="module")
def confirmer(client) -> dict:
    r = client.post("/api/profiles", json={"label": "pytest-confirmer"})
    assert r.status_code == 201
    prof = r.json()
    prof["auth"] = {"Authorization": f"Bearer {prof['token']}"}
    return prof


@pytest.fixture(scope="module")
def corpus_works_16() -> list[dict]:
    with SessionLocal() as db:
        rows = db.execute(text(
            "SELECT id, title, year, "
            " lower(regexp_replace(doi, '^https?://(dx\\.)?doi\\.org/', '')) "
            "FROM works WHERE doi IS NOT NULL AND is_stub = false "
            " AND title IS NOT NULL AND year IS NOT NULL "
            "ORDER BY in_corpus_cited_by DESC LIMIT 16")).all()
    assert len(rows) == 16
    return [{"id": r[0], "title": r[1], "year": r[2], "doi": r[3]} for r in rows]


def _upload_and_get(client, auth, works, title) -> dict:
    entries = [
        f"A. Author and B. Author, {w['title'][:120]}, "
        f"Journal of Examples {i + 1} ({w['year']}) 101-110. doi:{w['doi']}"
        for i, w in enumerate(works)
    ]
    pdf = bibliography_pdf(entries, title)
    r = client.post("/api/uploads", headers=auth,
                    files={"file": (f"{title[:20]}.pdf", pdf, "application/pdf")})
    assert r.status_code == 201, r.text
    return r.json()


def test_mr_put_edges_exists_and_is_non_clearing(seeded, capsys):
    """The engine patch: the function exists, a batch lands in one call, and
    NOTHING pre-existing is cleared -- walks, contexts, or other edges."""
    with SessionLocal() as db:
        n_fn = db.execute(text(
            "SELECT count(*) FROM pg_proc WHERE proname = 'mr_put_edges'"
        )).scalar_one()
        assert n_fn == 1, "mr_put_edges is not installed -- rebuild the db image"

        mr = MeritRank(db.connection())
        # Pre-clean: an earlier aborted run may have left these fixed-name test
        # edges in the engine, and put_edges is idempotent (re-putting them
        # would produce a zero count delta).
        for i in range(20):
            try:
                mr.delete_edge(f"Uputedges_test_{i}", f"Uputedges_test_{i + 1}", "")
            except Exception:  # noqa: BLE001 - they usually do not exist
                db.rollback()
        db.commit()
        mr = MeritRank(db.connection())
        before_total = mr.edge_count("")
        before_ctx = mr.edge_count(config.BASELINE_CONTEXT)
        assert before_total > 100_000, "engine should hold the full graph"

        # A seeded profile's walks exist (session fixture paid for them);
        # scores must still be there AFTER the batch if nothing was cleared.
        ego = profile_node(seeded["id"])

        batch = [
            Edge(f"Uputedges_test_{i}", f"Uputedges_test_{i + 1}", 0.5, "", "cites")
            for i in range(20)
        ]
        t0 = time.time()
        mr.put_edges(batch)
        elapsed = time.time() - t0
        db.commit()

        # Session.commit() releases the underlying Connection: rebuild the
        # adapter rather than use a closed one (the services.py NB).
        # Assertions count the SPECIFIC batch edges, not totals: background
        # warm threads legitimately churn trust/LOO-scratch edges in every
        # context while this test runs, so total deltas are not stable.
        mr = MeritRank(db.connection())

        def batch_count(ctx: str) -> int:
            return db.execute(text(
                "SELECT count(*) FROM mr_edgelist(:c) "
                "WHERE src LIKE 'Uputedges\\_test\\_%'"), {"c": ctx}).scalar_one()

        in_aggregate = batch_count("")
        in_citation = batch_count(config.BASELINE_CONTEXT)
        after_total = mr.edge_count("")
        scores_after = mr.scores(ego, context="", limit=5, kind="User")

        with capsys.disabled():
            print(f"\nmr_put_edges: 20 edges in {elapsed:.2f}s "
                  f"({elapsed / 20 * 1000:.0f}ms/edge vs 87ms serial); "
                  f"batch present: aggregate {in_aggregate}/20, "
                  f"citation ctx {in_citation}/20; "
                  f"total {before_total}->{after_total}")

        assert in_aggregate == 20
        # U->U edges replicate into every context (D1.5).
        assert in_citation == 20
        # Non-clearing: the graph is still there and the ego kept its walks.
        assert after_total >= before_total, "batch write REDUCED the graph"
        assert scores_after, "existing ego lost its scores: the batch CLEARED state"

        # Cleanup: delete the synthetic edges (propagates to all contexts).
        for e in batch:
            mr.delete_edge(e.src, e.dst, "")
        db.commit()
        mr = MeritRank(db.connection())
        assert batch_count("") == 0


def test_confirm_lands_in_postgres_and_engine(client, confirmer, corpus_works_16, capsys):
    draft = _upload_and_get(client, confirmer["auth"], corpus_works_16[:8],
                            "Confirm Path Synthetic Review Alpha")
    upload_id = draft["id"]
    assert all(r["decision"] == "accept" for r in draft["references"])

    with SessionLocal() as db:
        v_before = graph_version(db)

    t0 = time.time()
    r = client.post(f"/api/uploads/{upload_id}/confirm", headers=confirmer["auth"])
    elapsed = time.time() - t0
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "confirmed", body
    own = body["work_id"]
    assert own.startswith("L"), "synthetic own paper must be a UL-labelled local"
    assert body["n_cited"] == 8 and body["n_trust"] == 8

    with SessionLocal() as db:
        src_rows = db.execute(text(
            "SELECT count(*) FROM citations WHERE src_id = :s"), {"s": own}).scalar_one()
        assert src_rows == 8
        w = db.execute(text(
            "SELECT source, is_stub FROM works WHERE id = :i"), {"i": own}).first()
        assert w[0] == "user_upload" and w[1] is False

        ge = db.execute(text(
            "SELECT relation, count(*) FROM graph_edges "
            "WHERE src = :n OR dst = :n GROUP BY relation ORDER BY relation"),
            {"n": work_node(own)}).all()
        by_rel = dict(ge)
        assert by_rel.get("cites") == 8 and by_rel.get("cited_by") == 8, by_rel

        # Weight parity with build_graph.py: cites = 1.00 * epoch_factor(ref year).
        row = db.execute(text(
            "SELECT ge.dst, ge.weight FROM graph_edges ge "
            "WHERE ge.src = :n AND ge.relation = 'cites' LIMIT 1"),
            {"n": work_node(own)}).first()
        ref_id = row[0][1:]
        ref_year = db.execute(text("SELECT year FROM works WHERE id = :i"),
                              {"i": ref_id}).scalar_one()
        expected = config.DEFAULT_WEIGHTS["cites"] * confirm_mod.epoch_factor(ref_year)
        assert abs(row[1] - expected) < 1e-9, (row[1], expected)

        n_engine = db.execute(text(
            "SELECT count(*) FROM mr_edgelist('') WHERE src = :n OR dst = :n"),
            {"n": work_node(own)}).scalar_one()
        assert n_engine == 16, f"engine holds {n_engine} of 16 citation edges"

        n_trust = db.execute(text(
            "SELECT count(*) FROM trust WHERE profile_id = :p"),
            {"p": confirmer["id"]}).scalar_one()
        n_sources = db.execute(text(
            "SELECT count(*) FROM trust_sources WHERE upload_id = :u"),
            {"u": upload_id}).scalar_one()
        assert n_trust == 8 and n_sources == 8

        assert graph_version(db) == v_before + 1, "confirm must bump graph_meta"

    with capsys.disabled():
        print(f"\nconfirm: 8 refs -> 16 citation edges + 8 trust edges in "
              f"{elapsed:.1f}s end-to-end; version {v_before}->{v_before + 1}; "
              f"own work {own} (source=user_upload)")

    confirmer["upload_alpha"] = upload_id
    confirmer["own_alpha"] = own


def test_forced_engine_failure_leaves_no_orphan_and_reconciles(
        client, confirmer, corpus_works_16, monkeypatch, capsys):
    """The negative gate: fail between the Postgres commit and the engine push.
    The committed rows must stand, the engine must hold NOTHING of the new
    work (no scoreable orphan), and the reconcile sweep must repair it."""
    draft = _upload_and_get(client, confirmer["auth"], corpus_works_16[8:16],
                            "Confirm Path Synthetic Review Beta")
    upload_id = draft["id"]

    def boom(self, edges, timeout_msec=600_000):
        raise RuntimeError("forced engine failure (test)")

    monkeypatch.setattr(MeritRank, "put_edges", boom)
    r = client.post(f"/api/uploads/{upload_id}/confirm", headers=confirmer["auth"])
    monkeypatch.undo()

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "engine_pending"
    own = body["work_id"]

    with SessionLocal() as db:
        n_cit = db.execute(text(
            "SELECT count(*) FROM citations WHERE src_id = :s"), {"s": own}).scalar_one()
        n_ge = db.execute(text(
            "SELECT count(*) FROM graph_edges WHERE src = :n OR dst = :n"),
            {"n": work_node(own)}).scalar_one()
        assert n_cit == 8 and n_ge == 16, "Postgres truth must be committed"

        n_engine = db.execute(text(
            "SELECT count(*) FROM mr_edgelist('') WHERE src = :n OR dst = :n"),
            {"n": work_node(own)}).scalar_one()
        assert n_engine == 0, (
            f"engine holds {n_engine} edges for a work whose push failed -- "
            "that is a scoreable orphan")

        repaired = confirm_mod.reconcile_pending(db)
        assert repaired == 1

        n_engine = db.execute(text(
            "SELECT count(*) FROM mr_edgelist('') WHERE src = :n OR dst = :n"),
            {"n": work_node(own)}).scalar_one()
        assert n_engine == 16, "reconcile must push the committed edges"
        status = db.execute(text(
            "SELECT status FROM uploads WHERE id = :u"), {"u": upload_id}).scalar_one()
        assert status == "confirmed"

    with capsys.disabled():
        print(f"\nforced failure: engine_pending with 0 engine edges "
              f"(no orphan); reconcile_pending repaired 1 -> 16 edges, confirmed")


def test_upload_survives_full_graph_rebuild(confirmer, capsys):
    """THE gate test (spec B4/N6): `python scripts/build_graph.py` truncates
    graph_edges and reloads the engine. The upload must be REGENERATED from
    the durable citations/works rows, not destroyed."""
    own = confirmer["own_alpha"]

    t0 = time.time()
    proc = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "build_graph.py")],
        capture_output=True, text=True, cwd=str(REPO), timeout=1200,
    )
    elapsed = time.time() - t0
    assert proc.returncode == 0, proc.stderr[-2000:]

    with SessionLocal() as db:
        by_rel = dict(db.execute(text(
            "SELECT relation, count(*) FROM graph_edges "
            "WHERE src = :n OR dst = :n GROUP BY relation"),
            {"n": work_node(own)}).all())
        assert by_rel.get("cites") == 8 and by_rel.get("cited_by") == 8, (
            f"rebuild lost the upload's edges: {by_rel}")

        n_engine = db.execute(text(
            "SELECT count(*) FROM mr_edgelist('') WHERE src = :n OR dst = :n"),
            {"n": work_node(own)}).scalar_one()
        assert n_engine >= 16, (
            f"engine holds {n_engine} upload edges after the rebuild reload")

        n_cit = db.execute(text(
            "SELECT count(*) FROM citations WHERE src_id = :s"),
            {"s": own}).scalar_one()
        assert n_cit == 8

    with capsys.disabled():
        print(f"\nfull rebuild in {elapsed:.0f}s: upload {own} regenerated -- "
              f"16 citation edges in graph_edges AND the engine survive")
