"""POST /api/import/bibtex -- resolve a .bib file against the corpus.

Matching is DOI-first because a DOI is an identity claim and a title is a guess. Only
entries with no usable DOI fall through to trigram title matching, and the threshold
there is deliberately high: a wrong match silently poisons somebody's trust set, which
is worse than reporting the entry as unmatched.
"""
from __future__ import annotations

import io
import re

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from sqlalchemy import text

from .. import schemas, services
from ..deps import CurrentProfile, DbSession
# One threshold, one DOI normaliser, shared with the upload matcher: the two
# import paths must never drift apart on what counts as a safe match.
from ..matching import TITLE_THRESHOLD, normalise_doi
from ..models import Trust

router = APIRouter(prefix="/api", tags=["import"])

MAX_BYTES = 4 * 1024 * 1024
IMPORT_STRENGTH = 3

_BRACES = re.compile(r"[{}\\]")
_WS = re.compile(r"\s+")


def _clean(value: str | None) -> str:
    if not value:
        return ""
    return _WS.sub(" ", _BRACES.sub("", value)).strip()


def _parse(raw: str) -> list[tuple[str | None, str, str]]:
    """-> [(doi, title, human_label)]. Works with bibtexparser v1 and v2."""
    entries: list[dict] = []
    try:  # v2 API
        import bibtexparser  # type: ignore

        if hasattr(bibtexparser, "parse_string"):
            lib = bibtexparser.parse_string(raw)
            entries = [
                {**{f.key: f.value for f in e.fields}, "ID": e.key}
                for e in lib.entries
            ]
        else:  # v1 API
            from bibtexparser.bparser import BibTexParser  # type: ignore

            parser = BibTexParser(common_strings=True)
            parser.ignore_nonstandard_types = False
            entries = bibtexparser.load(io.StringIO(raw), parser=parser).entries
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not parse the BibTeX file: {e}",
        ) from e

    out: list[tuple[str | None, str, str]] = []
    for e in entries:
        title = _clean(e.get("title"))
        doi = normalise_doi(e.get("doi")) or normalise_doi(e.get("url"))
        label = title or e.get("ID") or "(untitled entry)"
        out.append((doi, title, label))
    return out


@router.post("/import/bibtex", response_model=schemas.BibtexImportResponse)
async def import_bibtex(
    profile: CurrentProfile,
    db: DbSession,
    file: UploadFile = File(...),
) -> schemas.BibtexImportResponse:
    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Empty upload.")
    if len(payload) > MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"BibTeX file exceeds {MAX_BYTES // (1024 * 1024)} MB.")
    try:
        raw = payload.decode("utf-8")
    except UnicodeDecodeError:
        raw = payload.decode("latin-1")

    parsed = _parse(raw)
    if not parsed:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="No BibTeX entries found in the upload.")

    # --- pass 1: DOI ------------------------------------------------------
    dois = [d for d, _t, _l in parsed if d]
    doi_map: dict[str, str] = {}
    if dois:
        rows = db.execute(text(
            "SELECT id, lower(regexp_replace(doi, '^https?://(dx\\.)?doi\\.org/', '')) "
            "FROM works WHERE doi IS NOT NULL AND "
            "lower(regexp_replace(doi, '^https?://(dx\\.)?doi\\.org/', '')) = ANY(:d)"
        ), {"d": dois}).all()
        doi_map = {r[1]: r[0] for r in rows}

    matched_ids: list[str] = []
    unmatched: list[str] = []

    # --- pass 2: trigram title -------------------------------------------
    # SET does not accept bind parameters; set_config(..., is_local => true) does.
    db.execute(text("SELECT set_config('pg_trgm.similarity_threshold', :t, true)"),
               {"t": str(TITLE_THRESHOLD)})
    for doi, title, label in parsed:
        if doi and doi in doi_map:
            matched_ids.append(doi_map[doi])
            continue
        if title:
            row = db.execute(text(
                "SELECT id FROM works WHERE title % :t "
                "ORDER BY similarity(title, :t) DESC LIMIT 1"
            ), {"t": title}).first()
            if row is not None:
                matched_ids.append(row[0])
                continue
        unmatched.append(label)

    matched_ids = list(dict.fromkeys(matched_ids))

    # --- add to the trust set --------------------------------------------
    # Importing a bibliography is a statement of trust: these are the papers you cite.
    # Entries already present keep whatever strength the user set by hand.
    existing = {
        t.work_id for t in db.query(Trust).filter(Trust.profile_id == profile.id)
    }
    added = 0
    for wid in matched_ids:
        if wid in existing:
            continue
        db.add(Trust(profile_id=profile.id, work_id=wid,
                     strength=IMPORT_STRENGTH, is_distrust=False))
        added += 1
    db.commit()
    services.invalidate_scores(profile.id)

    if added:
        # Background, like POST /trust: an import can add dozens of seeds at once and a
        # full warm is minutes of engine time.
        services.schedule_warm(profile.id)

    briefs = services.paper_briefs(db, matched_ids)
    return schemas.BibtexImportResponse(
        matched=[services.brief_or_placeholder(briefs, w) for w in matched_ids],
        unmatched=unmatched,
        added=added,
    )
