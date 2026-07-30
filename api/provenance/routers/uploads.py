"""Upload a PDF of your own paper -> parsed bibliography -> reviewable draft.

Phase 2 surface: draft creation, inspection and per-reference review edits.
NOTHING here writes to works/citations/graph_edges or the engine -- a draft is
rows in `uploads`/`upload_references` only. Confirm/undo land in Phase 3.
"""
from __future__ import annotations

import hashlib
import logging
import uuid

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from sqlalchemy import text

from .. import schemas, services
from ..deps import CurrentProfile, DbSession, OwnedProfile
from ..matching import MatchResult, match_entry
from ..openalex import OpenAlex, OpenAlexUnavailable
from ..pdfbib import extract_with_timeout
from ..pdfbib.fields import parse_fields
from ..pdfbib.types import BibEntry
from ..models import Upload, UploadReference, Work

log = logging.getLogger("provenance.uploads")

router = APIRouter(prefix="/api", tags=["uploads"])

# One client per process: it holds the disk cache and the rate-limit clock.
_oa: OpenAlex | None = None


def _openalex() -> OpenAlex | None:
    global _oa
    if _oa is None:
        try:
            _oa = OpenAlex()
        except Exception:  # noqa: BLE001 - matching degrades to corpus-only
            log.warning("OpenAlex client unavailable; corpus-only matching")
    return _oa


def _corpus_authors(db, work_ids: list[str]) -> dict[str, set[str]]:
    if not work_ids:
        return {}
    out: dict[str, set[str]] = {}
    for wid, aid in db.execute(text(
        "SELECT work_id, author_id FROM work_authors WHERE work_id = ANY(:ids)"),
        {"ids": work_ids},
    ).all():
        out.setdefault(wid, set()).add(aid)
    return out


def _resolve_own_paper(db, oa: OpenAlex | None, title: str | None):
    """Draft-time resolution of the uploaded paper itself. Returns
    (resolved_work_id, resolved_openalex_id, own_author_ids)."""
    if not title:
        return None, None, set()
    entry = parse_fields(BibEntry(raw=title))
    entry.title_guess = title
    entry.year = None
    from ..matching import corpus_work_by_title
    hit = corpus_work_by_title(db, title, year=None)
    if hit:
        wid = hit[0]
        return wid, None, _corpus_authors(db, [wid]).get(wid, set())
    if oa is not None:
        try:
            oa_work = oa.search_title(title)
        except OpenAlexUnavailable:
            oa_work = None
        if oa_work:
            from ..matching import title_similarity, TITLE_THRESHOLD
            got = oa_work.get("title") or oa_work.get("display_name") or ""
            if got and title_similarity(db, got.lower(), title.lower()) >= TITLE_THRESHOLD:
                from ..openalex import short_id
                authors = {
                    short_id((a.get("author") or {}).get("id"))
                    for a in oa_work.get("authorships") or []
                }
                return None, short_id(oa_work.get("id")), {a for a in authors if a}
    return None, None, set()


@router.post("/uploads", response_model=schemas.UploadOut,
             status_code=status.HTTP_201_CREATED)
def create_upload(
    profile: CurrentProfile,
    db: DbSession,
    file: UploadFile = File(...),
) -> schemas.UploadOut:
    payload = file.file.read()
    content_hash = hashlib.sha256(payload).hexdigest()

    dupe = db.query(Upload).filter(
        Upload.profile_id == profile.id,
        Upload.content_hash == content_hash).one_or_none()
    if dupe is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Already uploaded as {dupe.id!r} "
                   f"({dupe.filename or 'unnamed'}, status {dupe.status}).")

    # Extraction runs in a worker subprocess with a wall-clock timeout; a
    # refusal carries the specific reason (encrypted / scan / no bibliography /
    # ambiguous split) and nothing is persisted.
    result = extract_with_timeout(payload)
    if result.refused:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=result.refusal_reason)

    oa = _openalex()
    own_work_id, own_oa_id, own_authors = _resolve_own_paper(
        db, oa, result.paper_title)

    upload = Upload(
        id=uuid.uuid4().hex,
        profile_id=profile.id,
        filename=file.filename,
        content_hash=content_hash,
        title=result.paper_title,
        resolved_work_id=own_work_id,
        resolved_openalex_id=own_oa_id,
        status="draft",
        n_parsed=len(result.entries),
    )
    db.add(upload)

    matches: list[MatchResult] = []
    for entry in result.entries:
        matches.append(match_entry(db, entry, oa))

    corpus_ids = [m.work_id for m in matches if m.work_id]
    ref_authors = _corpus_authors(db, corpus_ids)

    n_matched = 0
    for i, (entry, m) in enumerate(zip(result.entries, matches)):
        matched = bool(m.work_id or m.resolved_openalex_id)
        n_matched += int(matched)
        is_self = False
        if own_authors:
            if m.work_id:
                is_self = bool(own_authors & ref_authors.get(m.work_id, set()))
            elif m.oa_author_ids:
                is_self = bool(own_authors & set(m.oa_author_ids))
        db.add(UploadReference(
            upload_id=upload.id, idx=i, raw=entry.raw,
            parsed_title=entry.title_guess, parsed_doi=entry.doi,
            parsed_year=entry.year,
            resolved_openalex_id=m.resolved_openalex_id,
            work_id=m.work_id, match_method=m.method,
            confidence=m.confidence, decision=m.decision,
            is_self_citation=is_self, couldnt_check=m.couldnt_check,
        ))
    upload.n_matched = n_matched
    upload.n_unresolved = len(result.entries) - n_matched
    db.commit()
    return _upload_out(db, upload)


@router.get("/uploads/{upload_id}", response_model=schemas.UploadOut)
def get_upload(upload_id: str, profile: CurrentProfile, db: DbSession) -> schemas.UploadOut:
    return _upload_out(db, _owned_upload(db, upload_id, profile.id))


@router.patch("/uploads/{upload_id}", response_model=schemas.UploadOut)
def patch_upload(
    upload_id: str, body: schemas.UploadPatch,
    profile: CurrentProfile, db: DbSession,
) -> schemas.UploadOut:
    upload = _owned_upload(db, upload_id, profile.id)
    _require_draft(upload)
    if body.title is not None:
        upload.title = body.title.strip() or None
        # The paper's identity changed: re-resolve it against corpus/OpenAlex.
        own_work_id, own_oa_id, _authors = _resolve_own_paper(
            db, _openalex(), upload.title)
        upload.resolved_work_id = own_work_id
        upload.resolved_openalex_id = own_oa_id
    db.commit()
    return _upload_out(db, upload)


@router.patch("/uploads/{upload_id}/references/{idx}",
              response_model=schemas.UploadReferenceOut)
def patch_reference(
    upload_id: str, idx: int, body: schemas.UploadReferencePatch,
    profile: CurrentProfile, db: DbSession,
) -> schemas.UploadReferenceOut:
    upload = _owned_upload(db, upload_id, profile.id)
    _require_draft(upload)
    ref = db.get(UploadReference, {"upload_id": upload.id, "idx": idx})
    if ref is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"No reference {idx} on this upload.")

    if body.work_id is not None:
        work = db.get(Work, body.work_id)
        if work is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Unknown work id {body.work_id!r}. It is not in the corpus.")
        ref.work_id = work.id
        ref.resolved_openalex_id = None
        ref.match_method = "manual"
        ref.confidence = 1.0
        # Choosing a work by hand IS the tick, unless the body says otherwise.
        ref.decision = body.decision or "accept"

    retitled = False
    if body.parsed_title is not None:
        ref.parsed_title = body.parsed_title.strip() or None
        retitled = True
    if body.parsed_year is not None:
        ref.parsed_year = body.parsed_year
        retitled = True
    if retitled and body.work_id is None:
        entry = BibEntry(raw=ref.raw, doi=ref.parsed_doi,
                         year=ref.parsed_year, title_guess=ref.parsed_title)
        m = match_entry(db, entry, _openalex())
        ref.work_id = m.work_id
        ref.resolved_openalex_id = m.resolved_openalex_id
        ref.match_method = m.method
        ref.confidence = m.confidence
        ref.decision = m.decision
        ref.couldnt_check = m.couldnt_check

    if body.decision is not None:
        ref.decision = body.decision
    if body.strength is not None:
        ref.strength = body.strength

    # Keep the counts honest after edits.
    rows = db.query(UploadReference).filter(
        UploadReference.upload_id == upload.id).all()
    upload.n_matched = sum(
        1 for r in rows if r.work_id or r.resolved_openalex_id)
    upload.n_unresolved = len(rows) - upload.n_matched
    db.commit()

    briefs = services.paper_briefs(db, [ref.work_id]) if ref.work_id else {}
    return _ref_out(ref, briefs)


@router.get("/profiles/{profile_id}/uploads",
            response_model=schemas.UploadListResponse)
def list_uploads(profile: OwnedProfile, db: DbSession) -> schemas.UploadListResponse:
    rows = (db.query(Upload).filter(Upload.profile_id == profile.id)
            .order_by(Upload.created_at.desc()).all())
    return schemas.UploadListResponse(items=[
        schemas.UploadListItem(
            id=u.id, filename=u.filename, title=u.title, status=u.status,
            n_parsed=u.n_parsed, n_matched=u.n_matched, n_added=u.n_added,
            n_unresolved=u.n_unresolved, created_at=u.created_at,
        ) for u in rows
    ])


# ---------------------------------------------------------------------------


def _owned_upload(db, upload_id: str, profile_id: str) -> Upload:
    upload = db.get(Upload, upload_id)
    if upload is None or upload.profile_id != profile_id:
        # 404 for both cases: existence of another user's upload is not leaked.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="No such upload.")
    return upload


def _require_draft(upload: Upload) -> None:
    if upload.status != "draft":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Upload is {upload.status!r}; only drafts can be edited.")


def _ref_out(ref: UploadReference, briefs: dict) -> schemas.UploadReferenceOut:
    return schemas.UploadReferenceOut(
        idx=ref.idx, raw=ref.raw, parsed_title=ref.parsed_title,
        parsed_doi=ref.parsed_doi, parsed_year=ref.parsed_year,
        match_method=ref.match_method, confidence=ref.confidence,
        decision=ref.decision, strength=ref.strength,
        is_self_citation=ref.is_self_citation,
        resolved_openalex_id=ref.resolved_openalex_id,
        work=briefs.get(ref.work_id) if ref.work_id else None,
        couldnt_check=ref.couldnt_check,
    )


def _upload_out(db, upload: Upload) -> schemas.UploadOut:
    refs = (db.query(UploadReference)
            .filter(UploadReference.upload_id == upload.id)
            .order_by(UploadReference.idx).all())
    briefs = services.paper_briefs(db, [r.work_id for r in refs if r.work_id])
    return schemas.UploadOut(
        id=upload.id, filename=upload.filename, title=upload.title,
        status=upload.status, n_parsed=upload.n_parsed,
        n_matched=upload.n_matched, n_added=upload.n_added,
        n_unresolved=upload.n_unresolved, created_at=upload.created_at,
        references=[_ref_out(r, briefs) for r in refs],
    )
