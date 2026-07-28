"""Phase 2: build the heterogeneous graph and load it into MeritRank.

Design constraints imposed by the engine (verified; DECISIONS.md D1):

  * Papers are `U` nodes and entities are `B` nodes, because Paper->Paper citation is
    the strongest signal and only (User,User) edges are accepted.
  * We never materialise pairwise "same institution" / "same topic" edges between
    papers. Entity nodes are first-class, so walks discover meta-paths
    (Paper->Author->Paper = co-authorship trust) for free and the graph stays sparse.
  * Entity out-edges are hub-damped by 1/sqrt(corpus_degree). Without this an author
    with 400 papers or a topic with 2,000 swallows every walk and all users get
    identical results.

Run:  python scripts/build_graph.py [--no-load]
"""
from __future__ import annotations

import argparse
import collections
import math
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api"))

from sqlalchemy import create_engine, text  # noqa: E402

from provenance import config  # noqa: E402
from provenance.meritrank import Edge, MeritRank  # noqa: E402
from provenance.models import (  # noqa: E402
    author_node, institution_node, topic_node, venue_node, work_node,
)

W = config.DEFAULT_WEIGHTS

# Bibliographic coupling / co-citation are dense by nature. Cap per paper so a single
# review article with 400 references cannot dominate the edge budget.
COUPLING_MIN_SHARED = 3
COUPLING_TOP_K = 15
COCITATION_MIN_SHARED = 3
COCITATION_TOP_K = 15


def damp(degree: int) -> float:
    """Hub damping for entity out-edges."""
    return 1.0 / math.sqrt(max(degree, 1))


def epoch_factor(year: int | None, half_life: float) -> float:
    """Gentle recency decay. Mathematics has a very long half-life: a 1960s paper is
    not stale, so this is floored and only mildly reduces old work.

    This is OURS, applied at graph-construction time. It is NOT the epoch decay from
    the MeritRank paper -- the engine does not expose that. See KNOWN_ISSUES.md.
    """
    if not year or half_life <= 0:
        return 1.0
    age = max(0, config.EPOCH_REFERENCE_YEAR - year)
    return max(0.4, 0.5 ** (age / half_life))


def build(conn, half_life: float) -> list[Edge]:
    edges: list[Edge] = []
    t0 = time.time()

    print("Loading works...", flush=True)
    works = {
        r[0]: (r[1], r[2])  # id -> (year, is_stub)
        for r in conn.execute(text("SELECT id, year, is_stub FROM works")).all()
    }
    print(f"  {len(works)} works", flush=True)

    # -- citations -----------------------------------------------------------
    print("Citations...", flush=True)
    cites = conn.execute(text("SELECT src_id, dst_id FROM citations")).all()
    for src, dst in cites:
        dy = works.get(dst, (None, True))[0]
        f = epoch_factor(dy, half_life)
        edges.append(Edge(work_node(src), work_node(dst), W["cites"] * f,
                          config.BASELINE_CONTEXT, "cites"))
        # The reverse edge is weak evidence but it is what lets trust flow *backwards*
        # from a trusted paper to the work that cites it.
        edges.append(Edge(work_node(dst), work_node(src), W["cited_by"] * f,
                          config.BASELINE_CONTEXT, "cited_by"))
    print(f"  {len(cites)} citations -> {len(edges)} edges", flush=True)

    # -- authors -------------------------------------------------------------
    print("Authorship...", flush=True)
    rows = conn.execute(text(
        "SELECT wa.work_id, wa.author_id, a.corpus_degree FROM work_authors wa "
        "JOIN authors a ON a.id = wa.author_id")).all()
    for wid, aid, deg in rows:
        an = author_node(aid)
        edges.append(Edge(work_node(wid), an, W["authored_by"], "author", "authored_by"))
        edges.append(Edge(an, work_node(wid), W["wrote"] * damp(deg), "author", "wrote"))
    print(f"  {len(rows)} authorships", flush=True)

    # -- topics (IDF-scaled: a niche subfield means far more than "Mathematics") ---
    print("Topics...", flush=True)
    rows = conn.execute(text(
        "SELECT wt.work_id, wt.topic_id, t.corpus_degree, t.idf FROM work_topics wt "
        "JOIN topics t ON t.id = wt.topic_id")).all()
    max_idf = max([r[3] for r in rows], default=1.0) or 1.0
    for wid, tid, deg, idf in rows:
        tn = topic_node(tid)
        scale = (idf or 0.0) / max_idf
        edges.append(Edge(work_node(wid), tn, W["tagged"] * scale, "topic", "tagged"))
        edges.append(Edge(tn, work_node(wid), W["tags"] * scale * damp(deg), "topic", "tags"))
    print(f"  {len(rows)} topic tags", flush=True)

    # -- venues --------------------------------------------------------------
    print("Venues...", flush=True)
    rows = conn.execute(text(
        "SELECT w.id, w.venue_id, v.corpus_degree FROM works w "
        "JOIN venues v ON v.id = w.venue_id WHERE w.venue_id IS NOT NULL")).all()
    for wid, vid, deg in rows:
        vn = venue_node(vid)
        edges.append(Edge(work_node(wid), vn, W["published_in"], "venue", "published_in"))
        edges.append(Edge(vn, work_node(wid), W["publishes"] * damp(deg), "venue", "publishes"))
    print(f"  {len(rows)} venue links", flush=True)

    # -- institutions (weakest: a shared employer is barely evidence) ---------
    print("Institutions...", flush=True)
    rows = conn.execute(text(
        "SELECT wi.work_id, wi.institution_id, i.corpus_degree FROM work_institutions wi "
        "JOIN institutions i ON i.id = wi.institution_id")).all()
    for wid, iid, deg in rows:
        inn = institution_node(iid)
        edges.append(Edge(work_node(wid), inn, W["affiliated"], "institution", "affiliated"))
        edges.append(Edge(inn, work_node(wid), W["hosts"] * damp(deg), "institution", "hosts"))
    print(f"  {len(rows)} affiliations", flush=True)

    # -- bibliographic coupling ---------------------------------------------
    print("Bibliographic coupling...", flush=True)
    refs: dict[str, set[str]] = collections.defaultdict(set)
    for src, dst in cites:
        refs[src].add(dst)
    inverted: dict[str, list[str]] = collections.defaultdict(list)
    for src, rs in refs.items():
        for r in rs:
            inverted[r].append(src)
    shared: dict[tuple[str, str], int] = collections.Counter()
    for r, srcs in inverted.items():
        if len(srcs) < 2 or len(srcs) > 200:  # skip mega-referenced hubs
            continue
        srcs = sorted(srcs)
        for i in range(len(srcs)):
            for j in range(i + 1, len(srcs)):
                shared[(srcs[i], srcs[j])] += 1
    per_paper: dict[str, list] = collections.defaultdict(list)
    for (a, b), n in shared.items():
        if n < COUPLING_MIN_SHARED:
            continue
        jac = n / len(refs[a] | refs[b])
        per_paper[a].append((jac, b))
        per_paper[b].append((jac, a))
    n_coup = 0
    for a, lst in per_paper.items():
        for jac, b in sorted(lst, reverse=True)[:COUPLING_TOP_K]:
            edges.append(Edge(work_node(a), work_node(b), W["couples"] * jac,
                              config.BASELINE_CONTEXT, "couples"))
            n_coup += 1
    print(f"  {n_coup} coupling edges", flush=True)

    # -- co-citation ---------------------------------------------------------
    print("Co-citation...", flush=True)
    co: dict[tuple[str, str], int] = collections.Counter()
    for src, rs in refs.items():
        rl = sorted(rs)
        if len(rl) > 200:
            continue
        for i in range(len(rl)):
            for j in range(i + 1, len(rl)):
                co[(rl[i], rl[j])] += 1
    per_paper.clear()
    max_co = max(co.values(), default=1)
    for (a, b), n in co.items():
        if n < COCITATION_MIN_SHARED:
            continue
        s = math.log1p(n) / math.log1p(max_co)
        per_paper[a].append((s, b))
        per_paper[b].append((s, a))
    n_cocite = 0
    for a, lst in per_paper.items():
        for s, b in sorted(lst, reverse=True)[:COCITATION_TOP_K]:
            edges.append(Edge(work_node(a), work_node(b), W["co_cited"] * s,
                              config.BASELINE_CONTEXT, "co_cited"))
            n_cocite += 1
    print(f"  {n_cocite} co-citation edges", flush=True)

    print(f"TOTAL {len(edges)} edges in {time.time()-t0:.1f}s", flush=True)
    return edges


def persist(conn, edges: list[Edge]) -> None:
    """Store the edge list so /explain can reconstruct paths over exactly the same
    data the scores were computed from."""
    print("Persisting graph_edges...", flush=True)
    conn.execute(text("TRUNCATE graph_edges"))
    CH = 10000
    for i in range(0, len(edges), CH):
        chunk = edges[i:i + CH]
        conn.execute(
            text("INSERT INTO graph_edges (src,dst,weight,context,relation) "
                 "VALUES (:src,:dst,:w,:ctx,:rel) ON CONFLICT DO NOTHING"),
            [{"src": e.src, "dst": e.dst, "w": e.weight, "ctx": e.context,
              "rel": e.relation} for e in chunk],
        )
    conn.commit()
    print(f"  {len(edges)} rows", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-load", action="store_true", help="build and persist only")
    ap.add_argument("--half-life", type=float, default=config.DEFAULT_EPOCH_HALF_LIFE_YEARS)
    args = ap.parse_args()

    url = os.environ.get("DATABASE_URL", config.DATABASE_URL)
    engine = create_engine(url, future=True)
    with engine.connect() as conn:
        edges = build(conn, args.half_life)
        persist(conn, edges)

        if args.no_load:
            return 0

        mr = MeritRank(conn)
        ok, info = mr.health()
        if not ok:
            print(f"MeritRank unreachable: {info}", file=sys.stderr)
            return 1
        for c in config.CONTEXTS:
            mr.create_context(c)
        conn.commit()

        print("Bulk loading into MeritRank...", flush=True)
        t0 = time.time()
        mr.bulk_load(edges)
        conn.commit()
        dt = time.time() - t0
        print(f"  bulk_load returned in {dt:.1f}s", flush=True)
        if dt > 120:
            print("  WARNING: exceeded the 2 minute Phase 2 gate", file=sys.stderr)

        for c in [config.AGGREGATE] + config.CONTEXTS:
            n = len(mr.nodelist(c))
            print(f"  context {c or '(aggregate)':<12} nodes={n}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
