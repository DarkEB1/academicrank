"""Reference matching: shared DOI/trigram helpers plus the upload precedence.

Precedence per entry (spec): DOI -> corpus | DOI -> OpenAlex | arXiv id ->
OpenAlex | title+year -> corpus trigram (TITLE_THRESHOLD reused, year +/-1
REQUIRED) | title -> OpenAlex search | review queue.

Pre-ticking policy is encoded here as the returned `decision`: only DOI and
arXiv matches (identity claims, confidence 1.0) come back 'accept'; every
trigram or OpenAlex-search match is 'pending' -- the 0.55 threshold was tuned
on curated BibTeX and PDF text is materially noisier (spec B6).

The DOI-first philosophy and TITLE_THRESHOLD are shared with the BibTeX
importer (routers/imports.py), which imports them from here: one threshold, one
normaliser, no drift.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from .openalex import OpenAlex, OpenAlexUnavailable, short_id
from .pdfbib.types import BibEntry

log = logging.getLogger("provenance.matching")

# Above this trigram similarity a title match is safe enough to OFFER (never to
# auto-accept). Tuned high on purpose: a wrong match silently poisons somebody's
# trust set, which is worse than reporting the entry as unmatched.
TITLE_THRESHOLD = 0.55
# Matching a title against the corpus requires year agreement within this bound.
YEAR_TOLERANCE = 1

_DOI_RE = re.compile(r"10\.\d{4,9}/\S+", re.I)


def normalise_doi(value: str | None) -> str | None:
    if not value:
        return None
    m = _DOI_RE.search(value.replace("\\", ""))
    if not m:
        return None
    return m.group(0).rstrip(".,;)]").lower()


_DOI_LOOKUP_SQL = text(
    "SELECT id FROM works WHERE doi IS NOT NULL AND "
    "lower(regexp_replace(doi, '^https?://(dx\\.)?doi\\.org/', '')) = :d "
    "LIMIT 1")


def corpus_work_by_doi(db: Session, doi: str) -> str | None:
    row = db.execute(_DOI_LOOKUP_SQL, {"d": doi}).first()
    return row[0] if row else None


def corpus_work_by_title(
    db: Session, title: str, year: int | None,
    threshold: float = TITLE_THRESHOLD,
) -> tuple[str, float] | None:
    """Best trigram title match above `threshold`, constrained to year +/-1 when
    a year is supplied. Returns (work_id, similarity)."""
    clauses = ["title % :t"]
    params: dict = {"t": title, "th": str(threshold)}
    if year is not None:
        clauses.append("year BETWEEN :y0 AND :y1")
        params["y0"], params["y1"] = year - YEAR_TOLERANCE, year + YEAR_TOLERANCE
    db.execute(text(
        "SELECT set_config('pg_trgm.similarity_threshold', :th, true)"),
        {"th": str(threshold)})
    row = db.execute(text(
        f"SELECT id, similarity(title, :t) FROM works WHERE {' AND '.join(clauses)} "
        "ORDER BY similarity(title, :t) DESC LIMIT 1"), params).first()
    return (row[0], float(row[1])) if row else None


def title_similarity(db: Session, a: str, b: str) -> float:
    return float(db.execute(
        text("SELECT similarity(:a, :b)"), {"a": a, "b": b}).scalar_one())


@dataclass
class MatchResult:
    work_id: str | None = None            # existing corpus work
    resolved_openalex_id: str | None = None  # OpenAlex work NOT yet in the corpus
    method: str = "none"                  # doi | arxiv | trigram | openalex | none
    confidence: float = 0.0
    decision: str = "pending"             # 'accept' only for doi/arxiv
    couldnt_check: bool = False           # OpenAlex was needed and unreachable
    # Author ids from the OpenAlex response, kept so self-citation labelling
    # does not have to re-fetch the work.
    oa_author_ids: tuple[str, ...] = ()


def _oa_result(db: Session, oa_work: dict, method: str, entry: BibEntry,
               decision: str) -> MatchResult:
    """An OpenAlex work: use the corpus row when we already hold it, otherwise
    record the resolution without creating anything (spec B6)."""
    oa_id = short_id(oa_work.get("id"))
    if not oa_id:
        return MatchResult()
    row = db.execute(text("SELECT id FROM works WHERE id = :i"), {"i": oa_id}).first()
    conf = 1.0 if method in ("doi", "arxiv") else _search_confidence(db, oa_work, entry)
    authors = tuple(
        a for a in (short_id((au.get("author") or {}).get("id"))
                    for au in oa_work.get("authorships") or []) if a)
    return MatchResult(
        work_id=row[0] if row else None,
        resolved_openalex_id=None if row else oa_id,
        method=method, confidence=conf, decision=decision,
        oa_author_ids=authors,
    )


def _search_confidence(db: Session, oa_work: dict, entry: BibEntry) -> float:
    got = oa_work.get("title") or oa_work.get("display_name") or ""
    want = entry.title_guess or ""
    if not (got and want):
        return 0.0
    return title_similarity(db, got.lower(), want.lower())


def match_entry(db: Session, entry: BibEntry, oa: OpenAlex | None) -> MatchResult:
    couldnt_check = False

    # 1. DOI -> corpus, then OpenAlex. A DOI is an identity claim: confidence 1.0.
    doi = normalise_doi(entry.doi)
    if doi:
        wid = corpus_work_by_doi(db, doi)
        if wid:
            return MatchResult(work_id=wid, method="doi", confidence=1.0,
                               decision="accept")
        if oa is not None:
            try:
                oa_work = oa.work_by_doi(doi)
                if oa_work:
                    return _oa_result(db, oa_work, "doi", entry, decision="accept")
            except OpenAlexUnavailable:
                couldnt_check = True

    # 2. arXiv id -> OpenAlex (via the DataCite 10.48550 DOI).
    if entry.arxiv_id and oa is not None:
        try:
            oa_work = oa.work_by_arxiv(entry.arxiv_id)
            if oa_work:
                return _oa_result(db, oa_work, "arxiv", entry, decision="accept")
        except OpenAlexUnavailable:
            couldnt_check = True

    # 3. title+year -> corpus trigram. Year agreement REQUIRED: the threshold was
    #    tuned on curated BibTeX and cannot carry the match alone on PDF text.
    if entry.title_guess and entry.year is not None:
        hit = corpus_work_by_title(db, entry.title_guess, entry.year)
        if hit:
            return MatchResult(work_id=hit[0], method="trigram",
                               confidence=hit[1], decision="pending")

    # 4. title -> OpenAlex search; kept only when the returned title actually
    #    resembles what we parsed (same threshold), year +/-1 when both known.
    if entry.title_guess and oa is not None:
        try:
            oa_work = oa.search_title(entry.title_guess)
        except OpenAlexUnavailable:
            oa_work = None
            couldnt_check = True
        if oa_work:
            sim = _search_confidence(db, oa_work, entry)
            oa_year = oa_work.get("publication_year")
            year_ok = (entry.year is None or oa_year is None
                       or abs(oa_year - entry.year) <= YEAR_TOLERANCE)
            if sim >= TITLE_THRESHOLD and year_ok:
                res = _oa_result(db, oa_work, "openalex", entry, decision="pending")
                res.confidence = sim
                return res

    # 5. Review queue.
    return MatchResult(couldnt_check=couldnt_check)
