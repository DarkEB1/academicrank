"""Phase 3b gate: undo leaves zero residue; visibility hides UL works from
other profiles; the upload is ONE leave-one-out unit.

Runs after the main suite (zz) but before the full-rebuild file (zzz).
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from provenance import ranking
from provenance.db import SessionLocal
from provenance.models import Profile, work_node

from test_uploads_draft import bibliography_pdf

UPLOAD_TITLE = "Undo Gate Xylophone Perambulation Treatise"


@pytest.fixture(scope="module", autouse=True)
def _clean_slate():
    from provenance.db import engine as _engine
    from test_zzz_uploads_confirm import UPLOAD_SUITE_LOCK, purge_upload_state
    conn = _engine.connect()
    conn.exec_driver_sql(f"SELECT pg_advisory_lock({UPLOAD_SUITE_LOCK})")
    try:
        purge_upload_state()
        yield
    finally:
        conn.exec_driver_sql(f"SELECT pg_advisory_unlock({UPLOAD_SUITE_LOCK})")
        conn.close()


@pytest.fixture(scope="module")
def undoer(client) -> dict:
    r = client.post("/api/profiles", json={"label": "pytest-undoer"})
    assert r.status_code == 201
    prof = r.json()
    prof["auth"] = {"Authorization": f"Bearer {prof['token']}"}
    return prof


@pytest.fixture(scope="module")
def watcher(client) -> dict:
    """A second profile that must never see the first one's uploads."""
    r = client.post("/api/profiles", json={"label": "pytest-watcher"})
    assert r.status_code == 201
    prof = r.json()
    prof["auth"] = {"Authorization": f"Bearer {prof['token']}"}
    return prof


@pytest.fixture(scope="module")
def corpus_doi_works_b() -> list[dict]:
    with SessionLocal() as db:
        rows = db.execute(text(
            "SELECT id, title, year, "
            " lower(regexp_replace(doi, '^https?://(dx\\.)?doi\\.org/', '')) "
            "FROM works WHERE doi IS NOT NULL AND is_stub = false "
            " AND title IS NOT NULL AND year IS NOT NULL "
            "ORDER BY in_corpus_cited_by DESC OFFSET 16 LIMIT 8")).all()
    assert len(rows) == 8
    return [{"id": r[0], "title": r[1], "year": r[2], "doi": r[3]} for r in rows]


def _confirmed_upload(client, prof, works) -> tuple[str, str]:
    entries = [
        f"A. Author and B. Author, {w['title'][:120]}, "
        f"Journal of Examples {i + 1} ({w['year']}) 101-110. doi:{w['doi']}"
        for i, w in enumerate(works)
    ]
    pdf = bibliography_pdf(entries, UPLOAD_TITLE)
    r = client.post("/api/uploads", headers=prof["auth"],
                    files={"file": ("undo.pdf", pdf, "application/pdf")})
    assert r.status_code == 201, r.text
    upload_id = r.json()["id"]
    r = client.post(f"/api/uploads/{upload_id}/confirm", headers=prof["auth"])
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "confirmed"
    return upload_id, r.json()["work_id"]


def test_upload_counts_as_one_loo_unit(client, undoer, watcher, corpus_doi_works_b):
    """Spec B1: hand seeds are units of one; the whole upload is ONE unit."""
    # Two hand seeds FIRST (one will also be cited by the upload: survivorship).
    hand = [corpus_doi_works_b[0]["id"], corpus_doi_works_b[7]["id"]]
    for wid in hand:
        r = client.post(f"/api/profiles/{undoer['id']}/trust",
                        json={"work_id": wid, "strength": 4},
                        headers=undoer["auth"])
        assert r.status_code == 200, r.text

    upload_id, own = _confirmed_upload(client, undoer, corpus_doi_works_b)
    undoer["upload_id"], undoer["own"] = upload_id, own

    with SessionLocal() as db:
        prof = db.get(Profile, undoer["id"])
        units = ranking.trust_units(db, prof)
        n_trust = db.execute(text(
            "SELECT count(*) FROM trust WHERE profile_id = :p"),
            {"p": undoer["id"]}).scalar_one()
    # 8 upload seeds + 2 hand seeds, one of which the upload also cites -> 9 rows... no:
    # hand seeds are works 0 and 7 of the SAME list the upload cites, so trust rows = 8
    # (6 upload-only + 2 shared), and units = 1 upload + 1 hand-only... the shared works
    # carry sources, so they count with the upload unit. Hand-only rows: none remain.
    labels = [u[0] for u in units]
    upload_units = [l for l in labels if l.startswith("upload:")]
    assert len(upload_units) == 1, labels
    assert n_trust == 8
    total_members = sum(len(m) for _l, m in units)
    assert total_members == 8


def test_undo_leaves_zero_residue(client, undoer, capsys):
    """The gate: zero residue in Postgres, zero upload edges in mr_edgelist('').
    Hand-added trust rows survive the undo."""
    upload_id, own = undoer["upload_id"], undoer["own"]
    own_node = work_node(own)

    r = client.delete(f"/api/uploads/{upload_id}", headers=undoer["auth"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["removed_local_work"] is True

    with SessionLocal() as db:
        residue = {
            "uploads": db.execute(text(
                "SELECT count(*) FROM uploads WHERE id = :u"),
                {"u": upload_id}).scalar_one(),
            "upload_references": db.execute(text(
                "SELECT count(*) FROM upload_references WHERE upload_id = :u"),
                {"u": upload_id}).scalar_one(),
            "trust_sources": db.execute(text(
                "SELECT count(*) FROM trust_sources WHERE upload_id = :u"),
                {"u": upload_id}).scalar_one(),
            "works": db.execute(text(
                "SELECT count(*) FROM works WHERE id = :w"), {"w": own}).scalar_one(),
            "citations": db.execute(text(
                "SELECT count(*) FROM citations WHERE src_id = :w OR dst_id = :w"),
                {"w": own}).scalar_one(),
            "graph_edges": db.execute(text(
                "SELECT count(*) FROM graph_edges WHERE src = :n OR dst = :n"),
                {"n": own_node}).scalar_one(),
        }
        assert all(v == 0 for v in residue.values()), residue

        engine_edges = db.execute(text(
            "SELECT count(*) FROM mr_edgelist('') WHERE src = :n OR dst = :n"),
            {"n": own_node}).scalar_one()
        assert engine_edges == 0, (
            f"{engine_edges} upload edges survive in the engine")

        # Survivorship: the two hand-added seeds keep their trust rows; the six
        # upload-created ones are gone.
        n_trust = db.execute(text(
            "SELECT count(*) FROM trust WHERE profile_id = :p"),
            {"p": undoer["id"]}).scalar_one()
        assert n_trust == 2, f"expected the 2 hand-added seeds to survive, got {n_trust}"

    with capsys.disabled():
        print(f"\nundo gate: residue counts all zero {residue}; "
              f"0 engine edges for {own_node}; hand-added trust survives (2 rows); "
              f"engine deletes issued: {body['n_engine_deleted']}")


def test_second_profile_never_sees_ul_work(client, undoer, watcher,
                                           corpus_doi_works_b, capsys):
    """Visibility gate: a UL... work is invisible to another profile in search
    (default off), visible to its uploader, and visible to the second profile
    only after it explicitly opts in."""
    upload_id, own = _confirmed_upload(client, undoer, corpus_doi_works_b)
    undoer["upload_id2"] = upload_id

    q = {"q": "Xylophone Perambulation"}

    r = client.get("/api/papers/search", params=q, headers=undoer["auth"])
    assert any(i["id"] == own for i in r.json()["items"]), (
        "uploader must see their own uploaded paper")

    r = client.get("/api/papers/search", params=q, headers=watcher["auth"])
    assert not any(i["id"] == own for i in r.json()["items"]), (
        "second profile saw a UL work with the toggle off")

    r = client.get("/api/papers/search", params=q)  # anonymous
    assert not any(i["id"] == own for i in r.json()["items"])

    # Opt in -> visible; opt back out -> hidden again (the flag is live).
    r = client.post(f"/api/profiles/{watcher['id']}/params",
                    json={"include_user_uploads": True}, headers=watcher["auth"])
    assert r.status_code == 200 and r.json()["include_user_uploads"] is True
    r = client.get("/api/papers/search", params=q, headers=watcher["auth"])
    assert any(i["id"] == own for i in r.json()["items"])

    r = client.post(f"/api/profiles/{watcher['id']}/params",
                    json={"include_user_uploads": False}, headers=watcher["auth"])
    assert r.json()["include_user_uploads"] is False
    r = client.get("/api/papers/search", params=q, headers=watcher["auth"])
    assert not any(i["id"] == own for i in r.json()["items"])

    # hidden_upload_ids drives rankings/recommendations/blindspots/subgraph via
    # the shared pool path; assert the helper itself distinguishes the two.
    with SessionLocal() as db:
        up = db.get(Profile, undoer["id"])
        wp = db.get(Profile, watcher["id"])
        assert own not in ranking.hidden_upload_ids(db, up)
        assert own in ranking.hidden_upload_ids(db, wp)

    with capsys.disabled():
        print(f"\nvisibility gate: {own} hidden from watcher+anonymous search, "
              "visible to uploader, toggle round-trips live")

    # Clean up: undo this upload too, leaving the corpus as we found it.
    r = client.delete(f"/api/uploads/{upload_id}", headers=undoer["auth"])
    assert r.status_code == 200
