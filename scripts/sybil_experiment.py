"""Phase 3: the sybil experiment, and a NetworkX personalised-PageRank baseline.

Two claims are tested, with real measured numbers that go into the README:

  1. MeritRank correlates strongly with personalised PageRank but does NOT match it.
     Where they diverge is the decay machinery doing its job.
  2. A citation ring -- 20 mutually-citing synthetic papers attached to the corpus by a
     single edge -- gains substantially less score under MeritRank than under plain PPR.

Caveat that belongs with the result: MeritRank's sybil tolerance was derived for
tokenomic feedback systems. A citation ring is an *analogy* to that threat model, not
an instance of it. See the README limitations section.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api"))

import networkx as nx  # noqa: E402
from scipy import stats as scipy_stats  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from provenance import config  # noqa: E402
from provenance.meritrank import Edge, MeritRank  # noqa: E402
from provenance.models import node_to_work_id, work_node  # noqa: E402
from provenance.ranking import compose  # noqa: E402

RING_SIZE = 20
RING_PREFIX = "Usybil_"
EGO = "Usybil_ego"


def load_edges(conn) -> list[tuple[str, str, float, str]]:
    return [(r[0], r[1], float(r[2]), r[3]) for r in conn.execute(text(
        "SELECT src, dst, weight, context FROM graph_edges")).all()]


def ppr(edges, seeds: dict[str, float], alpha: float = 0.85) -> dict[str, float]:
    g = nx.DiGraph()
    for s, d, w, _ in edges:
        if g.has_edge(s, d):
            g[s][d]["weight"] += w
        else:
            g.add_edge(s, d, weight=w)
    tot = sum(seeds.values()) or 1.0
    pers = {k: v / tot for k, v in seeds.items()}
    for n in pers:
        if n not in g:
            g.add_node(n)
    return nx.pagerank(g, alpha=alpha, personalization=pers, weight="weight")


def mr_scores_for(mr: MeritRank, ego: str, seeds: list[str]) -> dict[str, float]:
    for s in seeds:
        mr.put_edge(ego, work_node(s), 1.0, config.AGGREGATE)
    per_ctx = {c: {x.node: x.value for x in mr.scores(ego, context=c, limit=20000, kind="User")}
               for c in config.CONTEXTS}
    return compose(per_ctx)


def main() -> int:
    engine = create_engine(os.environ.get("DATABASE_URL", config.DATABASE_URL), future=True)
    out: dict = {}
    with engine.connect() as conn:
        mr = MeritRank(conn)
        base_edges = load_edges(conn)

        seeds = [r[0] for r in conn.execute(text(
            "SELECT id FROM works WHERE is_stub=false "
            "ORDER BY in_corpus_cited_by DESC LIMIT 8")).all()]
        print(f"Seed set: {seeds}\n")

        # ---------- 1. MeritRank vs personalised PageRank -------------------
        print("Computing MeritRank scores...", flush=True)
        mr_s = mr_scores_for(mr, EGO, seeds)
        conn.commit()

        print("Computing NetworkX personalised PageRank baseline...", flush=True)
        ppr_s = ppr(base_edges, {work_node(s): 1.0 for s in seeds})

        common = [n for n in mr_s if n in ppr_s and node_to_work_id(n)]
        common.sort()
        a = [mr_s[n] for n in common]
        b = [ppr_s[n] for n in common]
        rho, _ = scipy_stats.spearmanr(a, b)
        tau, _ = scipy_stats.kendalltau(a, b)
        print(f"  n={len(common)}  spearman={rho:.4f}  kendall={tau:.4f}")
        out["ppr_comparison"] = {"n": len(common), "spearman": rho, "kendall": tau}

        top_mr = {n for n, _ in sorted(mr_s.items(), key=lambda kv: -kv[1])[:50]}
        top_ppr = {n for n, _ in sorted(ppr_s.items(), key=lambda kv: -kv[1])[:50]}
        overlap = len(top_mr & top_ppr) / 50
        print(f"  top-50 overlap: {overlap:.2f}")
        out["ppr_comparison"]["top50_overlap"] = overlap

        # ---------- 2. sybil / citation ring --------------------------------
        print("\nInjecting a 20-paper citation ring...", flush=True)
        anchor = seeds[0]
        ring = [f"{RING_PREFIX}{i}" for i in range(RING_SIZE)]
        ring_edges: list[Edge] = []
        for i, n in enumerate(ring):                       # dense mutual citation
            for j, m in enumerate(ring):
                if i != j:
                    ring_edges.append(Edge(n, m, 1.0, config.BASELINE_CONTEXT, "cites"))
        # a single edge attaching the ring to the real corpus
        ring_edges.append(Edge(ring[0], work_node(anchor), 1.0,
                               config.BASELINE_CONTEXT, "cites"))
        ring_edges.append(Edge(work_node(anchor), ring[0], 1.0,
                               config.BASELINE_CONTEXT, "cites"))

        all_edges = [Edge(s, d, w, c) for s, d, w, c in base_edges] + ring_edges
        mr.bulk_load(all_edges)
        conn.commit()
        mr_ring = mr_scores_for(mr, EGO, seeds)
        conn.commit()

        ppr_ring = ppr(
            base_edges + [(e.src, e.dst, e.weight, e.context) for e in ring_edges],
            {work_node(s): 1.0 for s in seeds},
        )

        def share(scores: dict[str, float]) -> float:
            tot = sum(v for v in scores.values() if v > 0) or 1.0
            return sum(max(scores.get(n, 0.0), 0.0) for n in ring) / tot

        mr_share, ppr_share = share(mr_ring), share(ppr_ring)
        ratio = (mr_share / ppr_share) if ppr_share else float("nan")
        print(f"  ring share of total score -- MeritRank: {mr_share*100:.4f}%")
        print(f"  ring share of total score -- plain PPR: {ppr_share*100:.4f}%")
        print(f"  MeritRank/PPR ratio: {ratio:.3f}  "
              f"(<1 means MeritRank suppresses the ring)")
        out["sybil"] = {
            "ring_size": RING_SIZE,
            "meritrank_share_pct": mr_share * 100,
            "ppr_share_pct": ppr_share * 100,
            "ratio": ratio,
            "suppression_factor": (ppr_share / mr_share) if mr_share else float("inf"),
        }

        # restore the clean graph
        print("\nRestoring clean graph...", flush=True)
        mr.bulk_load([Edge(s, d, w, c) for s, d, w, c in base_edges])
        conn.commit()

    Path("data").mkdir(exist_ok=True)
    Path("data/sybil_results.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print("\nWrote data/sybil_results.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
