"""Phase 2 gate: PDF upload -> reviewable draft, against the live stack.

Two kinds of upload are exercised: a SYNTHETIC PDF whose bibliography cites
real corpus works by DOI (fully controlled: exact match assertions), and the
committed lme4 fixture (a real 37-entry statistics paper: the round-trip gate).

The load-bearing assertion is `_graph_snapshot`: a draft must write NOTHING to
works/citations/graph_edges or the engine.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text

from provenance.db import SessionLocal

FIXDIR = Path(__file__).parent / "fixtures" / "pdfbib"


# ---------------------------------------------------------------------------
# Synthetic PDF builder: real text lines at chosen positions/font sizes.
# ---------------------------------------------------------------------------

def _esc(s: str) -> str:
    s = s.encode("latin-1", errors="replace").decode("latin-1")
    return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def make_pdf(pages: list[list[tuple[str, float, float]]]) -> bytes:
    """pages = [[(text, y, font_size), ...], ...] -> a valid one-column PDF."""
    objs: list[bytes] = []
    n_pages = len(pages)
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(n_pages))
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode())
    font_obj_no = 3 + 2 * n_pages
    for i, lines in enumerate(pages):
        stream = "\n".join(
            f"BT /F1 {fs:.1f} Tf 50 {y:.1f} Td ({_esc(t)}) Tj ET"
            for t, y, fs in lines).encode("latin-1")
        objs.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font_obj_no} 0 R >> >> "
            f"/Contents {4 + 2 * i} 0 R >>".encode())
        objs.append(f"<< /Length {len(stream)} >>\nstream\n".encode()
                    + stream + b"\nendstream")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objs) + 1}\n".encode() + b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_at}\n%%EOF\n").encode()
    return bytes(out)


def bibliography_pdf(entries: list[str], title: str) -> bytes:
    page1 = [(title, 720.0, 18.0),
             ("A. Uploader and B. Colleague", 690.0, 11.0),
             ("Abstract. This synthetic paper exists to exercise the upload "
              "draft pipeline.", 660.0, 10.0)]
    page2 = [("References", 740.0, 14.0)]
    y = 712.0
    for i, e in enumerate(entries):
        text_line = f"[{i + 1}] {e}"
        # wrap crudely at ~95 chars so lines stay on the page
        while len(text_line) > 95:
            page2.append((text_line[:95], y, 10.0))
            text_line = "    " + text_line[95:]
            y -= 14
        page2.append((text_line, y, 10.0))
        y -= 18
    return make_pdf([page1, page2])


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def corpus_doi_works() -> list[dict]:
    """Eight non-stub corpus works that carry a DOI: the ground truth the
    synthetic bibliography cites."""
    with SessionLocal() as db:
        rows = db.execute(text(
            "SELECT id, title, year, "
            " lower(regexp_replace(doi, '^https?://(dx\\.)?doi\\.org/', '')) "
            "FROM works WHERE doi IS NOT NULL AND is_stub = false "
            " AND title IS NOT NULL AND year IS NOT NULL "
            "ORDER BY in_corpus_cited_by DESC LIMIT 8")).all()
    assert len(rows) == 8
    return [{"id": r[0], "title": r[1], "year": r[2], "doi": r[3]} for r in rows]


@pytest.fixture(scope="module")
def uploader(client) -> dict:
    r = client.post("/api/profiles", json={"label": "pytest-uploader"})
    assert r.status_code == 201
    prof = r.json()
    prof["auth"] = {"Authorization": f"Bearer {prof['token']}"}
    return prof


def _graph_snapshot(db) -> tuple:
    """Everything a draft must NOT touch. The engine count excludes edges whose
    src is an ego node (Uprofile_/Uloo_/Usim_/Uglobal_merit): background warm
    threads from other tests legitimately re-seed trust edges at any moment,
    and counting those made this snapshot flaky under the full suite. A draft
    could only ever add work/entity edges, which this still counts."""
    return (
        db.execute(text("SELECT count(*) FROM works")).scalar_one(),
        db.execute(text("SELECT count(*) FROM citations")).scalar_one(),
        db.execute(text("SELECT count(*) FROM graph_edges")).scalar_one(),
        db.execute(text("SELECT count(*) FROM trust")).scalar_one(),
        db.execute(text(
            "SELECT count(*) FROM mr_edgelist('') "
            "WHERE src NOT LIKE 'Uprofile\\_%' AND src NOT LIKE 'Uloo\\_%' "
            "AND src NOT LIKE 'Usim\\_%' AND src <> 'Uglobal_merit'"
        )).scalar_one(),
    )


# ---------------------------------------------------------------------------
# The gate tests
# ---------------------------------------------------------------------------

def test_synthetic_corpus_doi_upload(client, uploader, corpus_doi_works, capsys):
    """DOI-cited corpus works: every entry must match by DOI at confidence 1.0,
    pre-ticked -- and the draft must write nothing anywhere near the graph."""
    entries = [
        f"A. Author and B. Author, {w['title'][:120]}, "
        f"Journal of Examples {i + 1} ({w['year']}) 101-110. doi:{w['doi']}"
        for i, w in enumerate(corpus_doi_works)
    ]
    pdf = bibliography_pdf(entries, "A Synthetic Review of Trusted Statistical Methods")

    with SessionLocal() as db:
        before = _graph_snapshot(db)

    r = client.post("/api/uploads", headers=uploader["auth"],
                    files={"file": ("synthetic.pdf", pdf, "application/pdf")})
    assert r.status_code == 201, r.text
    draft = r.json()

    assert draft["status"] == "draft"
    assert draft["n_parsed"] == len(entries)
    got_ids = []
    for ref, w in zip(draft["references"], corpus_doi_works):
        assert ref["match_method"] == "doi", ref
        assert ref["confidence"] == 1.0
        assert ref["decision"] == "accept", "DOI matches are pre-ticked"
        assert ref["work"] and ref["work"]["id"] == w["id"]
        assert ref["strength"] == 3, "default seed strength is 3/5"
        got_ids.append(ref["work"]["id"])

    with SessionLocal() as db:
        after = _graph_snapshot(db)
    assert after == before, (
        f"draft creation wrote to the graph: {before} -> {after}")

    with capsys.disabled():
        print(f"\nPhase 2 gate (synthetic): {len(entries)}/8 DOI-matched "
              f"pre-ticked; works/citations/graph_edges/trust/engine "
              f"counts unchanged {before}")

    uploader["synthetic_upload_id"] = draft["id"]
    uploader["synthetic_pdf"] = pdf


def test_duplicate_upload_rejected_by_content_hash(client, uploader):
    r = client.post("/api/uploads", headers=uploader["auth"],
                    files={"file": ("again.pdf", uploader["synthetic_pdf"],
                                    "application/pdf")})
    assert r.status_code == 409
    assert "Already uploaded" in r.json()["detail"]


def test_real_pdf_roundtrips_to_reviewable_draft(client, uploader, capsys):
    """The committed lme4 fixture (37 real references, statistics -- the same
    field as the corpus) becomes a draft; the pre-tick policy holds: only
    doi/arxiv matches may arrive accepted. No graph writes."""
    pdf = (FIXDIR / "apa_unnumbered_stats.pdf").read_bytes()

    with SessionLocal() as db:
        before = _graph_snapshot(db)

    r = client.post("/api/uploads", headers=uploader["auth"],
                    files={"file": ("lme4.pdf", pdf, "application/pdf")})
    assert r.status_code == 201, r.text
    draft = r.json()
    assert draft["n_parsed"] == 37

    for ref in draft["references"]:
        if ref["decision"] == "accept":
            assert ref["match_method"] in ("doi", "arxiv"), (
                "only identity-claim matches may be pre-ticked", ref)
        if ref["match_method"] in ("trigram", "openalex"):
            assert ref["decision"] == "pending", ref

    with SessionLocal() as db:
        after = _graph_snapshot(db)
    assert after == before

    n_by = {}
    for ref in draft["references"]:
        n_by[ref["match_method"]] = n_by.get(ref["match_method"], 0) + 1
    with capsys.disabled():
        print(f"\nPhase 2 gate (real PDF): 37 parsed; match methods {n_by}; "
              f"n_matched={draft['n_matched']} n_unresolved={draft['n_unresolved']}; "
              "graph untouched")

    # Round trip: GET returns the same reviewable draft.
    r = client.get(f"/api/uploads/{draft['id']}", headers=uploader["auth"])
    assert r.status_code == 200
    assert len(r.json()["references"]) == 37


def test_patch_reference_review_actions(client, uploader, corpus_doi_works):
    upload_id = uploader["synthetic_upload_id"]
    auth = uploader["auth"]

    # Reject one entry.
    r = client.patch(f"/api/uploads/{upload_id}/references/0",
                     json={"decision": "reject"}, headers=auth)
    assert r.status_code == 200 and r.json()["decision"] == "reject"

    # Promote another to 5/5.
    r = client.patch(f"/api/uploads/{upload_id}/references/1",
                     json={"strength": 5}, headers=auth)
    assert r.status_code == 200 and r.json()["strength"] == 5

    # Manual match: point entry 2 at a different corpus work.
    other = corpus_doi_works[7]["id"]
    r = client.patch(f"/api/uploads/{upload_id}/references/2",
                     json={"work_id": other}, headers=auth)
    body = r.json()
    assert r.status_code == 200
    assert body["match_method"] == "manual" and body["work"]["id"] == other
    assert body["decision"] == "accept", "hand-picking a work is the tick"

    # Unknown work id -> 404, nothing changed.
    r = client.patch(f"/api/uploads/{upload_id}/references/3",
                     json={"work_id": "W0000000000"}, headers=auth)
    assert r.status_code == 404


def test_upload_ownership_and_listing(client, uploader):
    upload_id = uploader["synthetic_upload_id"]

    # Another profile cannot see the upload (404, not 403: no existence leak).
    r2 = client.post("/api/profiles", json={"label": "pytest-other"})
    other_auth = {"Authorization": f"Bearer {r2.json()['token']}"}
    r = client.get(f"/api/uploads/{upload_id}", headers=other_auth)
    assert r.status_code == 404

    r = client.get("/api/profiles/me/uploads", headers=uploader["auth"])
    assert r.status_code == 200
    items = r.json()["items"]
    assert any(u["id"] == upload_id for u in items)
    assert all(u["status"] == "draft" for u in items)


def test_unparseable_pdf_refused_with_reason(client, uploader):
    """No bibliography -> 422 with the specific reason, and no draft row."""
    pdf = make_pdf([[("Just a page of prose without any references section.",
                      700.0, 10.0)]])
    r = client.post("/api/uploads", headers=uploader["auth"],
                    files={"file": ("prose.pdf", pdf, "application/pdf")})
    assert r.status_code == 422
    assert "bibliography" in r.json()["detail"].lower() or \
        "text" in r.json()["detail"].lower()
