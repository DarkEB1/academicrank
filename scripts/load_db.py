#!/usr/bin/env python
"""Phase 1 database loader: data/raw/*.jsonl.gz -> Postgres.

Usage
-----
    python -m scripts.load_db            # idempotent load / re-load
    python scripts/load_db.py --reset    # drop + recreate every table first
    DATABASE_URL=... python -m scripts.load_db

Idempotency strategy (stated per the Phase 1 brief)
---------------------------------------------------
COPY-into-staging + INSERT ... SELECT ... ON CONFLICT DO UPDATE.

Every row first goes into an UNLOGGED temp table via ``COPY ... FROM STDIN``
(the fast path -- ~59k works and ~250k citation rows in seconds), and is then
merged into the real table with a single upsert statement. Running the loader
twice is a no-op apart from rewriting identical values: primary keys come from
OpenAlex, so ON CONFLICT DO UPDATE converges. We deliberately do *not*
truncate-then-load, because ``works`` is the FK target of the user-owned
``trust`` / ``read_marks`` tables and a TRUNCATE ... CASCADE would silently
destroy a profile's trust set.

Non-obvious decisions
---------------------
* Dangling references are dropped, not inserted: the citation upsert joins both
  endpoints against ``works``, so a ref to a paper that was never scraped simply
  produces no row (there is a real FK, so this is not optional).
* ``DISTINCT ON`` guards every upsert. Postgres raises "cannot affect row a
  second time" if one INSERT touches the same PK twice, and the source data does
  contain intra-record duplicates (25 works repeat an author across two
  authorship blocks, one repeats a topic).
* A work carrying no ``primary_location.source`` (796 of them) gets a NULL
  ``venue_id``; the upsert also LEFT JOINs ``venues`` so an unknown venue can
  never raise an FK error.
* 630 authorship blocks have no ``author.id`` (unmatched raw names). They are
  skipped -- we cannot key them.
* Stub works carry only id/title/year/cited_by_count/type and ``raw`` is left
  NULL for them; storing ~52k raw payloads we never re-derive anything from
  would triple the table size for nothing.
* ``corpus_degree`` counts DISTINCT **non-stub** works only. Stubs have no
  authorships/topics/venue of their own, but counting distinct is what makes the
  number a true "papers this entity appears on" and therefore safe to use as a
  hub-damping denominator.
* ``topics.idf = ln(N_nonstub / (1 + corpus_degree))`` floored at 0.01, so a
  subfield on a handful of papers outweighs a blanket "Mathematics" tag.
* Derived columns (corpus_degree, idf, ref_count, in_corpus_cited_by, tsv) are
  recomputed from scratch in SQL after every load, so they are always consistent
  with what is actually in the tables rather than with what we think we inserted.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import sqlalchemy as sa  # noqa: E402

from provenance.models import Base  # noqa: E402
from scripts.openalex import reconstruct_abstract, short_id  # noqa: E402

# A host-installed PostgreSQL 18 service squats port 5432 on this machine and
# shadows Docker's published port, so localhost:5432 silently reaches the WRONG
# server. The compose stack publishes the container on 55432 instead; keep this
# in lockstep with api/provenance/config.py (imported below when available).
FALLBACK_URL = "postgresql+psycopg://postgres:postgres@localhost:55432/provenance"
try:
    from provenance.config import DATABASE_URL as DEFAULT_URL  # noqa: E402
except Exception:  # pragma: no cover - config is optional for a standalone run
    DEFAULT_URL = FALLBACK_URL
DATA = ROOT / "data" / "raw"
FULL_FILE = DATA / "works_full.jsonl.gz"
STUB_FILE = DATA / "works_stub.jsonl.gz"
CHUNK = 5000


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _t(value, n):
    """Defensive truncation for the fixed-width String() columns."""
    if isinstance(value, str) and len(value) > n:
        return value[:n]
    return value


def _log(msg: str) -> None:
    print(msg, flush=True)


class Timer:
    def __init__(self) -> None:
        self.t0 = time.perf_counter()

    def lap(self) -> str:
        return f"{time.perf_counter() - self.t0:6.1f}s"


# --------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------

STAGE_DDL = """
DROP TABLE IF EXISTS stage_works, stage_venues, stage_authors, stage_institutions,
                     stage_topics, stage_work_authors, stage_work_institutions,
                     stage_work_topics, stage_citations;

CREATE TEMP TABLE stage_works (
    id text, title text, abstract text, year int, publication_date date,
    cited_by_count int, doi text, type text, language text, is_oa boolean,
    is_stub boolean, venue_id text, raw text
) ON COMMIT PRESERVE ROWS;

CREATE TEMP TABLE stage_venues (
    id text, display_name text, type text, issn_l text, publisher text);
CREATE TEMP TABLE stage_authors (
    id text, display_name text, orcid text);
CREATE TEMP TABLE stage_institutions (
    id text, display_name text, ror text, country_code text);
CREATE TEMP TABLE stage_topics (
    id text, display_name text, subfield text, field text, domain text);
CREATE TEMP TABLE stage_work_authors (work_id text, author_id text, position int);
CREATE TEMP TABLE stage_work_institutions (work_id text, institution_id text);
CREATE TEMP TABLE stage_work_topics (work_id text, topic_id text, score float);
CREATE TEMP TABLE stage_citations (src_id text, dst_id text);
"""

WORK_COLS = ("id", "title", "abstract", "year", "publication_date", "cited_by_count",
             "doi", "type", "language", "is_oa", "is_stub", "venue_id", "raw")


def _copy(cur, table, cols, rows, label, total=None):
    """COPY an iterable of tuples into a staging table, printing progress."""
    n = 0
    sql = f"COPY {table} ({', '.join(cols)}) FROM STDIN"
    with cur.copy(sql) as cp:
        for row in rows:
            cp.write_row(row)
            n += 1
            if n % CHUNK == 0:
                suffix = f" / {total:,}" if total else ""
                _log(f"    {label}: {n:,}{suffix}")
    _log(f"    {label}: {n:,} rows staged")
    return n


def stage_full_works(cur, venues, authors, institutions, topics,
                     work_authors, work_institutions, work_topics, citations):
    """Stream works_full.jsonl.gz straight into stage_works.

    Entities and link rows are accumulated in the caller's dicts (they are small);
    only the work rows -- which carry the fat ``raw`` payload -- are streamed, so
    the ~60 MB of raw JSON never all sits in memory at once.
    """
    def rows():
        with gzip.open(FULL_FILE, "rt", encoding="utf-8") as fh:
            for line in fh:
                rec = json.loads(line)
                wid = short_id(rec.get("id"))
                if not wid:
                    continue

                # --- venue -------------------------------------------------
                venue_id = None
                src = (rec.get("primary_location") or {}).get("source") or {}
                if src.get("id"):
                    venue_id = short_id(src["id"])
                    if venue_id not in venues:
                        venues[venue_id] = (
                            _t(venue_id, 24),
                            src.get("display_name"),
                            _t(src.get("type"), 32),
                            _t(src.get("issn_l"), 16),
                            src.get("host_organization_name"),
                        )

                # --- authors / institutions --------------------------------
                for pos, ship in enumerate(rec.get("authorships") or []):
                    au = ship.get("author") or {}
                    aid = short_id(au.get("id"))
                    if aid:
                        if aid not in authors:
                            authors[aid] = (_t(aid, 24), au.get("display_name"),
                                            au.get("orcid"))
                        work_authors.setdefault((wid, aid), pos)
                    for inst in ship.get("institutions") or []:
                        iid = short_id(inst.get("id"))
                        if not iid:
                            continue
                        if iid not in institutions:
                            institutions[iid] = (
                                _t(iid, 24), inst.get("display_name"),
                                inst.get("ror"), _t(inst.get("country_code"), 8),
                            )
                        work_institutions.add((wid, iid))

                # --- topics (primary_topic is folded into the same set) -----
                for topic in list(rec.get("topics") or []) + \
                        ([rec["primary_topic"]] if rec.get("primary_topic") else []):
                    tid = short_id(topic.get("id"))
                    if not tid:
                        continue
                    if tid not in topics:
                        topics[tid] = (
                            _t(tid, 24), topic.get("display_name"),
                            (topic.get("subfield") or {}).get("display_name"),
                            (topic.get("field") or {}).get("display_name"),
                            (topic.get("domain") or {}).get("display_name"),
                        )
                    score = float(topic.get("score") or 0.0)
                    key = (wid, tid)
                    if score > work_topics.get(key, -1.0):
                        work_topics[key] = score

                # --- citations ---------------------------------------------
                for ref in rec.get("referenced_works") or []:
                    dst = short_id(ref)
                    if dst and dst != wid:
                        citations.add((wid, dst))

                pub_date = rec.get("publication_date") or None
                yield (
                    _t(wid, 24),
                    rec.get("display_name"),
                    reconstruct_abstract(rec.get("abstract_inverted_index")),
                    rec.get("publication_year"),
                    pub_date,
                    rec.get("cited_by_count") or 0,
                    rec.get("doi"),
                    _t(rec.get("type"), 48),
                    _t(rec.get("language"), 8),
                    bool((rec.get("open_access") or {}).get("is_oa")),
                    False,
                    _t(venue_id, 24),
                    json.dumps(rec, separators=(",", ":")),
                )

    return _copy(cur, "stage_works", WORK_COLS, rows(), "full works", 7211)


def stage_stub_works(cur):
    def rows():
        with gzip.open(STUB_FILE, "rt", encoding="utf-8") as fh:
            for line in fh:
                rec = json.loads(line)
                wid = short_id(rec.get("id"))
                if not wid:
                    continue
                yield (
                    _t(wid, 24), rec.get("display_name"), None,
                    rec.get("publication_year"), None,
                    rec.get("cited_by_count") or 0, None,
                    _t(rec.get("type"), 48), None, False, True, None, None,
                )

    return _copy(cur, "stage_works", WORK_COLS, rows(), "stub works", 51917)


# --------------------------------------------------------------------------
# upserts (FK-safe order)
# --------------------------------------------------------------------------

UPSERTS: list[tuple[str, str]] = [
    # NB: the model's defaults are Python-side (``default=0``), not server-side, so
    # every NOT NULL counter has to be given an explicit value here. The derived
    # pass below recomputes them all anyway, so seeding 0 is safe on a re-run.
    ("venues", """
        INSERT INTO venues (id, display_name, type, issn_l, publisher, corpus_degree)
        SELECT DISTINCT ON (id) id, display_name, type, issn_l, publisher, 0
        FROM stage_venues ORDER BY id
        ON CONFLICT (id) DO UPDATE SET
            display_name = EXCLUDED.display_name, type = EXCLUDED.type,
            issn_l = EXCLUDED.issn_l, publisher = EXCLUDED.publisher
    """),
    ("authors", """
        INSERT INTO authors (id, display_name, orcid, works_count, cited_by_count,
                             corpus_degree)
        SELECT DISTINCT ON (id) id, display_name, orcid, 0, 0, 0
        FROM stage_authors ORDER BY id
        ON CONFLICT (id) DO UPDATE SET
            display_name = EXCLUDED.display_name, orcid = EXCLUDED.orcid
    """),
    ("institutions", """
        INSERT INTO institutions (id, display_name, ror, country_code, corpus_degree)
        SELECT DISTINCT ON (id) id, display_name, ror, country_code, 0
        FROM stage_institutions ORDER BY id
        ON CONFLICT (id) DO UPDATE SET
            display_name = EXCLUDED.display_name, ror = EXCLUDED.ror,
            country_code = EXCLUDED.country_code
    """),
    ("topics", """
        INSERT INTO topics (id, display_name, subfield, field, domain,
                            corpus_degree, idf)
        SELECT DISTINCT ON (id) id, display_name, subfield, field, domain, 0, 1.0
        FROM stage_topics ORDER BY id
        ON CONFLICT (id) DO UPDATE SET
            display_name = EXCLUDED.display_name, subfield = EXCLUDED.subfield,
            field = EXCLUDED.field, domain = EXCLUDED.domain
    """),
    # works after venues (FK), full beats stub if an id ever appeared in both.
    ("works", """
        INSERT INTO works (id, title, abstract, year, publication_date, cited_by_count,
                           doi, type, language, is_oa, is_stub, venue_id, raw,
                           ref_count, in_corpus_cited_by)
        SELECT DISTINCT ON (s.id)
               s.id, s.title, s.abstract, s.year, s.publication_date,
               coalesce(s.cited_by_count, 0),
               s.doi, s.type, s.language, coalesce(s.is_oa, false),
               coalesce(s.is_stub, false), v.id, s.raw::jsonb, 0, 0
        FROM stage_works s
        LEFT JOIN venues v ON v.id = s.venue_id
        ORDER BY s.id, s.is_stub
        ON CONFLICT (id) DO UPDATE SET
            title = EXCLUDED.title, abstract = EXCLUDED.abstract, year = EXCLUDED.year,
            publication_date = EXCLUDED.publication_date,
            cited_by_count = EXCLUDED.cited_by_count, doi = EXCLUDED.doi,
            type = EXCLUDED.type, language = EXCLUDED.language, is_oa = EXCLUDED.is_oa,
            is_stub = EXCLUDED.is_stub, venue_id = EXCLUDED.venue_id, raw = EXCLUDED.raw
    """),
    ("work_authors", """
        INSERT INTO work_authors (work_id, author_id, position)
        SELECT DISTINCT ON (s.work_id, s.author_id) s.work_id, s.author_id, s.position
        FROM stage_work_authors s
        JOIN works w ON w.id = s.work_id
        JOIN authors a ON a.id = s.author_id
        ORDER BY s.work_id, s.author_id, s.position
        ON CONFLICT (work_id, author_id) DO UPDATE SET position = EXCLUDED.position
    """),
    ("work_institutions", """
        INSERT INTO work_institutions (work_id, institution_id)
        SELECT DISTINCT s.work_id, s.institution_id
        FROM stage_work_institutions s
        JOIN works w ON w.id = s.work_id
        JOIN institutions i ON i.id = s.institution_id
        ON CONFLICT (work_id, institution_id) DO NOTHING
    """),
    ("work_topics", """
        INSERT INTO work_topics (work_id, topic_id, score)
        SELECT DISTINCT ON (s.work_id, s.topic_id) s.work_id, s.topic_id, s.score
        FROM stage_work_topics s
        JOIN works w ON w.id = s.work_id
        JOIN topics t ON t.id = s.topic_id
        ORDER BY s.work_id, s.topic_id, s.score DESC
        ON CONFLICT (work_id, topic_id) DO UPDATE SET score = EXCLUDED.score
    """),
    # Both endpoints must exist -- the inner joins are what drops dangling refs.
    ("citations", """
        INSERT INTO citations (src_id, dst_id)
        SELECT DISTINCT s.src_id, s.dst_id
        FROM stage_citations s
        JOIN works a ON a.id = s.src_id
        JOIN works b ON b.id = s.dst_id
        ON CONFLICT (src_id, dst_id) DO NOTHING
    """),
]


DERIVED: list[tuple[str, str]] = [
    ("authors.corpus_degree", """
        UPDATE authors SET corpus_degree = 0 WHERE corpus_degree <> 0;
        UPDATE authors a SET corpus_degree = d.c FROM (
            SELECT wa.author_id AS id, count(DISTINCT wa.work_id) AS c
            FROM work_authors wa JOIN works w ON w.id = wa.work_id
            WHERE NOT w.is_stub GROUP BY 1
        ) d WHERE a.id = d.id AND a.corpus_degree <> d.c
    """),
    ("institutions.corpus_degree", """
        UPDATE institutions SET corpus_degree = 0 WHERE corpus_degree <> 0;
        UPDATE institutions i SET corpus_degree = d.c FROM (
            SELECT wi.institution_id AS id, count(DISTINCT wi.work_id) AS c
            FROM work_institutions wi JOIN works w ON w.id = wi.work_id
            WHERE NOT w.is_stub GROUP BY 1
        ) d WHERE i.id = d.id AND i.corpus_degree <> d.c
    """),
    ("topics.corpus_degree", """
        UPDATE topics SET corpus_degree = 0 WHERE corpus_degree <> 0;
        UPDATE topics t SET corpus_degree = d.c FROM (
            SELECT wt.topic_id AS id, count(DISTINCT wt.work_id) AS c
            FROM work_topics wt JOIN works w ON w.id = wt.work_id
            WHERE NOT w.is_stub GROUP BY 1
        ) d WHERE t.id = d.id AND t.corpus_degree <> d.c
    """),
    ("venues.corpus_degree", """
        UPDATE venues SET corpus_degree = 0 WHERE corpus_degree <> 0;
        UPDATE venues v SET corpus_degree = d.c FROM (
            SELECT venue_id AS id, count(DISTINCT id) AS c
            FROM works WHERE NOT is_stub AND venue_id IS NOT NULL GROUP BY 1
        ) d WHERE v.id = d.id AND v.corpus_degree <> d.c
    """),
    # ln(N/(1+df)) floored at 0.01: niche subfield >> "Mathematics".
    ("topics.idf", """
        UPDATE topics t SET idf = greatest(0.01, ln(
            (SELECT count(*)::float FROM works WHERE NOT is_stub)
            / (1 + t.corpus_degree)))
    """),
    # One rewrite of works: in/out citation counts and the search vector together.
    ("works.ref_count / in_corpus_cited_by / tsv", """
        UPDATE works w SET
            ref_count = coalesce(o.c, 0),
            in_corpus_cited_by = coalesce(i.c, 0),
            tsv = setweight(to_tsvector('english', coalesce(w2.title, '')), 'A')
               || setweight(to_tsvector('english', coalesce(w2.abstract, '')), 'B')
        FROM works w2
        LEFT JOIN (SELECT src_id, count(*) AS c FROM citations GROUP BY 1) o
               ON o.src_id = w2.id
        LEFT JOIN (SELECT dst_id, count(*) AS c FROM citations GROUP BY 1) i
               ON i.dst_id = w2.id
        WHERE w.id = w2.id
    """),
]


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Load the Provenance corpus into Postgres.")
    ap.add_argument("--reset", action="store_true",
                    help="drop and recreate every table before loading")
    ap.add_argument("--database-url", default=os.environ.get("DATABASE_URL", DEFAULT_URL))
    args = ap.parse_args()

    for path in (FULL_FILE, STUB_FILE):
        if not path.exists():
            raise SystemExit(f"missing input: {path}")

    timer = Timer()
    engine = sa.create_engine(args.database_url, future=True)
    _log(f"[{timer.lap()}] connecting: "
         f"{sa.engine.make_url(args.database_url).render_as_string(hide_password=True)}")

    # Extensions first: ix_works_title_trgm needs gin_trgm_ops to exist.
    # pgmer2 is the MeritRank connector and only exists on the container's
    # Postgres -- it is our proof we are not talking to the host's PG18.
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS btree_gin"))
        mer = conn.execute(sa.text(
            "SELECT extversion FROM pg_extension WHERE extname='pgmer2'")).scalar()
    _log(f"[{timer.lap()}] server check: pgmer2={mer or 'ABSENT (not the container DB?)'}")

    if args.reset:
        _log(f"[{timer.lap()}] --reset: dropping all tables")
        Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    _log(f"[{timer.lap()}] schema ready ({len(Base.metadata.tables)} tables)")

    raw_conn = engine.raw_connection()
    try:
        pg = raw_conn.driver_connection
        cur = pg.cursor()
        cur.execute(STAGE_DDL)

        venues: dict[str, tuple] = {}
        authors: dict[str, tuple] = {}
        institutions: dict[str, tuple] = {}
        topics: dict[str, tuple] = {}
        work_authors: dict[tuple[str, str], int] = {}
        work_institutions: set[tuple[str, str]] = set()
        work_topics: dict[tuple[str, str], float] = {}
        citations: set[tuple[str, str]] = set()

        _log(f"[{timer.lap()}] staging {FULL_FILE.name}")
        n_full = stage_full_works(cur, venues, authors, institutions, topics,
                                  work_authors, work_institutions, work_topics, citations)
        _log(f"[{timer.lap()}] staging {STUB_FILE.name}")
        n_stub = stage_stub_works(cur)

        _log(f"[{timer.lap()}] staging entities and edges")
        _copy(cur, "stage_venues", ("id", "display_name", "type", "issn_l", "publisher"),
              venues.values(), "venues")
        _copy(cur, "stage_authors", ("id", "display_name", "orcid"),
              authors.values(), "authors")
        _copy(cur, "stage_institutions", ("id", "display_name", "ror", "country_code"),
              institutions.values(), "institutions")
        _copy(cur, "stage_topics", ("id", "display_name", "subfield", "field", "domain"),
              topics.values(), "topics")
        _copy(cur, "stage_work_authors", ("work_id", "author_id", "position"),
              ((w, a, p) for (w, a), p in work_authors.items()), "work_authors")
        _copy(cur, "stage_work_institutions", ("work_id", "institution_id"),
              work_institutions, "work_institutions")
        _copy(cur, "stage_work_topics", ("work_id", "topic_id", "score"),
              ((w, t, s) for (w, t), s in work_topics.items()), "work_topics")
        _copy(cur, "stage_citations", ("src_id", "dst_id"), citations, "citations (raw refs)")

        cur.execute("ANALYZE stage_works")
        cur.execute("ANALYZE stage_citations")
        raw_conn.commit()

        _log(f"[{timer.lap()}] upserting in FK-safe order")
        for label, sql in UPSERTS:
            t0 = time.perf_counter()
            cur.execute(sql)
            _log(f"    {label:<20} {cur.rowcount:>9,} rows  "
                 f"({time.perf_counter() - t0:5.1f}s)")
            raw_conn.commit()

        dropped = len(citations) - _scalar(cur, "SELECT count(*) FROM citations")
        _log(f"    dangling refs dropped: {dropped:,} of {len(citations):,}")

        _log(f"[{timer.lap()}] computing derived columns")
        for label, sql in DERIVED:
            t0 = time.perf_counter()
            cur.execute(sql)
            _log(f"    {label:<40} ({time.perf_counter() - t0:5.1f}s)")
            raw_conn.commit()

        cur.execute("DROP TABLE IF EXISTS stage_works, stage_venues, stage_authors,"
                    " stage_institutions, stage_topics, stage_work_authors,"
                    " stage_work_institutions, stage_work_topics, stage_citations")
        raw_conn.commit()

        pg.autocommit = True
        _log(f"[{timer.lap()}] ANALYZE")
        for table in ("works", "authors", "institutions", "topics", "venues",
                      "work_authors", "work_institutions", "work_topics", "citations"):
            cur.execute(f"ANALYZE {table}")
        pg.autocommit = False
    finally:
        raw_conn.close()

    with engine.connect() as conn:
        idx = conn.execute(sa.text(
            "SELECT indexname FROM pg_indexes WHERE tablename='works'"
            " AND indexname IN ('ix_works_tsv','ix_works_title_trgm')"
            " ORDER BY indexname")).scalars().all()
    _log(f"[{timer.lap()}] GIN indexes present: {', '.join(idx) or 'NONE'}")
    _log(f"[{timer.lap()}] done -- {n_full:,} full + {n_stub:,} stub works loaded")
    return 0


def _scalar(cur, sql):
    cur.execute(sql)
    return cur.fetchone()[0]


if __name__ == "__main__":
    raise SystemExit(main())
