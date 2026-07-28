#!/usr/bin/env python
"""Phase 1 gate report for the Provenance corpus.

Usage
-----
    python -m scripts.stats          # from the repo root
    python scripts/stats.py          # equivalent
    DATABASE_URL=... python -m scripts.stats

Prints node counts, edge counts, degree distributions, orphan flags and the two
Phase 1 gate criteria. Output is plain ASCII and fixed-width so it can be pasted
into BUILD_LOG.md verbatim.

Exit code is 0 only if BOTH gates pass:
    * >= 2,500 full papers
    * >= 90% of full papers have at least one resolved in-corpus reference

Notes
-----
* Read-only: this script never writes to the database.
* Degree distributions read the denormalised ``works.ref_count`` /
  ``works.in_corpus_cited_by`` columns rather than recounting ``citations``. The
  INTEGRITY block then cross-checks those columns against the citation table, so
  a stale denormalisation shows up instead of being papered over.
* Stub works have out-degree 0 by construction (we never scraped their
  references), so the distributions are reported for full papers and for the
  whole node set separately -- mixing them would understate out-degree ~8x.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import sqlalchemy as sa  # noqa: E402

# Port 55432, not 5432: a host-installed PostgreSQL 18 service squats 5432 on
# this machine and shadows the container's published port.
FALLBACK_URL = "postgresql+psycopg://postgres:postgres@localhost:55432/provenance"
try:
    from provenance.config import DATABASE_URL as DEFAULT_URL  # noqa: E402
except Exception:  # pragma: no cover
    DEFAULT_URL = FALLBACK_URL

WIDTH = 78
MIN_FULL_PAPERS = 2500
MIN_RESOLVED_REF_PCT = 90.0


# --------------------------------------------------------------------------
# formatting helpers
# --------------------------------------------------------------------------

def rule(char: str = "=") -> str:
    return char * WIDTH


def heading(title: str) -> None:
    print()
    print(title)
    print(rule("-"))


def row(label: str, value, note: str = "") -> None:
    val = f"{value:,}" if isinstance(value, int) else str(value)
    print(f"  {label:<44}{val:>12}  {note}".rstrip())


def dist_header() -> None:
    print(f"  {'metric':<28}{'n':>8}{'min':>7}{'median':>9}{'mean':>9}"
          f"{'p90':>8}{'p99':>8}{'max':>9}")
    print("  " + "-" * (WIDTH - 4))


def dist_row(label: str, d: dict) -> None:
    if not d["n"]:
        print(f"  {label:<28}{0:>8}{'-':>7}{'-':>9}{'-':>9}{'-':>8}{'-':>8}{'-':>9}")
        return
    print(f"  {label:<28}{d['n']:>8,}{d['min']:>7,}{d['median']:>9.1f}"
          f"{d['mean']:>9.2f}{d['p90']:>8,.0f}{d['p99']:>8,.0f}{d['max']:>9,}")


# --------------------------------------------------------------------------
# queries
# --------------------------------------------------------------------------

def scalar(conn, sql: str):
    return conn.execute(sa.text(sql)).scalar()


def distribution(conn, source_sql: str) -> dict:
    """min / median / mean / p90 / p99 / max over a single-column subquery."""
    r = conn.execute(sa.text(f"""
        SELECT count(*)                                                AS n,
               coalesce(min(v), 0)                                     AS mn,
               coalesce(percentile_cont(0.5)  WITHIN GROUP (ORDER BY v), 0) AS p50,
               coalesce(avg(v), 0)                                     AS mean,
               coalesce(percentile_cont(0.90) WITHIN GROUP (ORDER BY v), 0) AS p90,
               coalesce(percentile_cont(0.99) WITHIN GROUP (ORDER BY v), 0) AS p99,
               coalesce(max(v), 0)                                     AS mx
        FROM ({source_sql}) t(v)
    """)).one()
    return {"n": r.n, "min": int(r.mn), "median": float(r.p50), "mean": float(r.mean),
            "p90": float(r.p90), "p99": float(r.p99), "max": int(r.mx)}


def main() -> int:
    url = os.environ.get("DATABASE_URL", DEFAULT_URL)
    engine = sa.create_engine(url, future=True)
    safe_url = sa.engine.make_url(url).render_as_string(hide_password=True)

    with engine.connect() as conn:
        # ---------------------------------------------------------------- nodes
        works_total = scalar(conn, "SELECT count(*) FROM works")
        works_full = scalar(conn, "SELECT count(*) FROM works WHERE NOT is_stub")
        works_stub = scalar(conn, "SELECT count(*) FROM works WHERE is_stub")
        n_authors = scalar(conn, "SELECT count(*) FROM authors")
        n_inst = scalar(conn, "SELECT count(*) FROM institutions")
        n_topics = scalar(conn, "SELECT count(*) FROM topics")
        n_venues = scalar(conn, "SELECT count(*) FROM venues")

        # ---------------------------------------------------------------- edges
        n_cit = scalar(conn, "SELECT count(*) FROM citations")
        n_wa = scalar(conn, "SELECT count(*) FROM work_authors")
        n_wi = scalar(conn, "SELECT count(*) FROM work_institutions")
        n_wt = scalar(conn, "SELECT count(*) FROM work_topics")
        n_wv = scalar(conn, "SELECT count(*) FROM works WHERE venue_id IS NOT NULL")

        # ------------------------------------------------------------ integrity
        sum_out = scalar(conn, "SELECT coalesce(sum(ref_count),0) FROM works")
        sum_in = scalar(conn, "SELECT coalesce(sum(in_corpus_cited_by),0) FROM works")
        tsv_null = scalar(conn, "SELECT count(*) FROM works WHERE tsv IS NULL")
        gin_idx = conn.execute(sa.text(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'works'"
            "   AND indexdef ILIKE '%USING gin%' ORDER BY indexname")).scalars().all()
        abstracts = scalar(conn,
                           "SELECT count(*) FROM works WHERE NOT is_stub"
                           "   AND abstract IS NOT NULL AND abstract <> ''")
        raw_full = scalar(conn, "SELECT count(*) FROM works"
                                " WHERE NOT is_stub AND raw IS NOT NULL")
        raw_stub = scalar(conn, "SELECT count(*) FROM works"
                                " WHERE is_stub AND raw IS NOT NULL")

        # -------------------------------------------------------- distributions
        dists = [
            ("citation in-degree (all)", "SELECT in_corpus_cited_by FROM works"),
            ("citation out-degree (all)", "SELECT ref_count FROM works"),
            ("citation in-degree (full)",
             "SELECT in_corpus_cited_by FROM works WHERE NOT is_stub"),
            ("citation out-degree (full)",
             "SELECT ref_count FROM works WHERE NOT is_stub"),
            ("author corpus_degree", "SELECT corpus_degree FROM authors"),
            ("topic corpus_degree", "SELECT corpus_degree FROM topics"),
            ("venue corpus_degree", "SELECT corpus_degree FROM venues"),
            ("institution corpus_degree", "SELECT corpus_degree FROM institutions"),
        ]
        dist_results = [(label, distribution(conn, sql)) for label, sql in dists]

        # -------------------------------------------------------------- orphans
        orph_all = scalar(conn, "SELECT count(*) FROM works"
                                " WHERE ref_count = 0 AND in_corpus_cited_by = 0")
        orph_full = scalar(conn, "SELECT count(*) FROM works WHERE NOT is_stub"
                                 "   AND ref_count = 0 AND in_corpus_cited_by = 0")
        orph_stub = scalar(conn, "SELECT count(*) FROM works WHERE is_stub"
                                 "   AND ref_count = 0 AND in_corpus_cited_by = 0")
        zero_deg = {
            t: scalar(conn, f"SELECT count(*) FROM {t} WHERE corpus_degree = 0")
            for t in ("authors", "institutions", "topics", "venues")
        }

        # --------------------------------------------------------- idf sanity
        idf_lo = conn.execute(sa.text(
            "SELECT display_name, corpus_degree, idf FROM topics"
            " ORDER BY idf ASC, corpus_degree DESC LIMIT 3")).all()
        idf_hi = conn.execute(sa.text(
            "SELECT display_name, corpus_degree, idf FROM topics"
            " WHERE corpus_degree > 0 ORDER BY idf DESC, display_name LIMIT 3")).all()

        # ----------------------------------------------------------------- gates
        with_refs = scalar(conn, "SELECT count(*) FROM works"
                                 " WHERE NOT is_stub AND ref_count > 0")
        # Papers OpenAlex ships with no reference list at all: they can never
        # resolve a reference, so they cap gate 2 no matter how good the loader is.
        no_ref_list = scalar(conn, """
            SELECT count(*) FROM works WHERE NOT is_stub
              AND coalesce(jsonb_array_length(raw -> 'referenced_works'), 0) = 0
        """)

    pct_refs = (with_refs / works_full * 100.0) if works_full else 0.0
    have_list = works_full - no_ref_list
    ceiling = (have_list / works_full * 100.0) if works_full else 0.0
    pct_of_resolvable = (with_refs / have_list * 100.0) if have_list else 0.0
    gate1 = works_full >= MIN_FULL_PAPERS
    gate2 = pct_refs >= MIN_RESOLVED_REF_PCT

    # ------------------------------------------------------------------ output
    print(rule())
    print(" PROVENANCE -- PHASE 1 CORPUS STATS")
    print(f" {safe_url}")
    print(rule())

    heading("NODES")
    row("works (total)", works_total)
    row("  full", works_full)
    row("  stub", works_stub)
    row("authors", n_authors)
    row("institutions", n_inst)
    row("topics", n_topics)
    row("venues", n_venues)

    heading("EDGES")
    row("citations (work -> work, in-corpus)", n_cit)
    row("authorships (work_authors)", n_wa)
    row("affiliations (work_institutions)", n_wi)
    row("topic tags (work_topics)", n_wt)
    row("venue links (works.venue_id)", n_wv)

    heading("DEGREE DISTRIBUTIONS")
    dist_header()
    for label, d in dist_results:
        dist_row(label, d)

    heading("ORPHANS")
    row("works with no in-corpus citation either way", orph_all,
        f"({orph_all / works_total * 100:.1f}% of works)" if works_total else "")
    row("  of which full", orph_full,
        f"({orph_full / works_full * 100:.1f}% of full)" if works_full else "")
    row("  of which stub", orph_stub,
        f"({orph_stub / works_stub * 100:.1f}% of stubs)" if works_stub else "")
    for table, count in zero_deg.items():
        row(f"{table} with corpus_degree = 0", count)

    heading("INTEGRITY")
    ok_out = sum_out == n_cit
    ok_in = sum_in == n_cit
    row("sum(works.ref_count) == count(citations)", sum_out,
        "OK" if ok_out else f"MISMATCH (citations={n_cit:,})")
    row("sum(works.in_corpus_cited_by) == citations", sum_in,
        "OK" if ok_in else f"MISMATCH (citations={n_cit:,})")
    row("works with NULL tsv", tsv_null, "OK" if tsv_null == 0 else "UNPOPULATED")
    row("full works with an abstract", abstracts,
        f"({abstracts / works_full * 100:.1f}%)" if works_full else "")
    row("full works with raw JSONB", raw_full,
        "OK" if raw_full == works_full else "INCOMPLETE")
    row("stub works with raw JSONB", raw_stub,
        "OK (skipped by design)" if raw_stub == 0 else "UNEXPECTED")
    row("GIN indexes on works", len(gin_idx), ", ".join(gin_idx) or "NONE")

    heading("TOPIC IDF SANITY  (ln(N_full / (1 + corpus_degree)), floor 0.01)")
    print(f"  {'lowest idf (broad tags)':<44}")
    for name, deg, idf in idf_lo:
        print(f"    {(name or '?')[:38]:<38} deg={deg:>5,}  idf={idf:6.3f}")
    print(f"  {'highest idf (niche tags)':<44}")
    for name, deg, idf in idf_hi:
        print(f"    {(name or '?')[:38]:<38} deg={deg:>5,}  idf={idf:6.3f}")

    heading("PHASE 1 GATE")
    print(f"  [{'PASS' if gate1 else 'FAIL'}]  full papers >= {MIN_FULL_PAPERS:,}"
          f"{'':<12}{works_full:>10,}")
    print(f"  [{'PASS' if gate2 else 'FAIL'}]  full papers with >= 1 resolved "
          f"in-corpus reference >= {MIN_RESOLVED_REF_PCT:.0f}%")
    print(f"          {with_refs:,} / {works_full:,} = {pct_refs:.2f}%")
    if not gate2:
        print()
        print(f"          diagnostic: {no_ref_list:,} full papers "
              f"({100 - ceiling:.2f}%) ship from OpenAlex with an EMPTY")
        print("          referenced_works list (mostly books and pre-2000 articles),")
        print("          so no loader can resolve a reference for them.")
        print(f"          Ceiling for this gate with the current scrape: {ceiling:.2f}%")
        print(f"          Of the {have_list:,} papers that DO carry a reference list, "
              f"{with_refs:,}")
        print(f"          resolve at least one in-corpus target = {pct_of_resolvable:.2f}%")
    print()
    print(rule())
    overall = gate1 and gate2
    print(f" RESULT: {'PASS' if overall else 'FAIL'}")
    print(rule())

    return 0 if overall else 1


if __name__ == "__main__":
    raise SystemExit(main())
