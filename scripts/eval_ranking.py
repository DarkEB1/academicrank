"""Offline evaluation harness for ranking changes.

This is the gate every scoring change in the v2 plan must pass
(docs/superpowers/specs/2026-07-30-ranking-repair-v2-design.md). It reports THREE
numbers, never one:

  * recall@25 with a paired-bootstrap 95% CI (held-out-seed recovery),
  * recall on held-out seeds >= 2 hops from the retained set (the discovery number;
    known to be ~0.007 for every scorer -- reported so regressions are visible, not
    because any current scorer moves it),
  * median in-corpus-citation percentile of the top 25 (the "famous, not relevant"
    complaint, made measurable).

Method (fixed by the experiments in 2026-07-29-ranking-experiments-results.md):

  * Trust sets are real paper bibliographies (18-60 in-corpus references) -- a
    human-curated set, exogenous to any single edge family. 20% held out per fold.
  * `couples`/`co_cited` edges between held-out and retained seeds are ABLATED per
    fold: their weights are computed from the held-out paper's own reference list,
    so leaving them in leaks the answer into the features.
  * Reference scorers (random / popularity / 1-hop adjacency) are always computed.
    Propagation beating adjacency is what makes the metric non-degenerate; if that
    ever inverts, stop trusting the harness before you stop trusting the scorer.
  * Stubs and entity nodes are excluded from results, exactly as the product does.

Everything runs deterministically over the persisted `graph_edges` table -- no
mr-service involvement, so no Monte Carlo confound and no engine contention.

Usage:
    python scripts/eval_ranking.py --selftest
    python scripts/eval_ranking.py --sets 200 --json baseline.json
    python scripts/eval_ranking.py --sets 200 --gamma 0.5
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np
import psycopg
import scipy.sparse as sp

DEFAULT_DSN = "postgresql://postgres:postgres@localhost:55432/provenance"
BASELINE_RELS = ("cites", "cited_by", "couples", "co_cited")
LEAK_RELS = ("couples", "co_cited")
EPS = 1e-9
DEFAULT_SEED = 20260729
BOOT_R = 4000


def dsn() -> str:
    url = os.environ.get("DATABASE_URL", DEFAULT_DSN)
    # accept SQLAlchemy-style URLs from .env
    return url.replace("postgresql+psycopg://", "postgresql://")


class Graph:
    """The persisted graph_edges table as scipy CSR, plus paper metadata."""

    def __init__(self, conn):
        rows = conn.execute(
            "SELECT src, dst, weight, relation FROM graph_edges").fetchall()
        works = conn.execute(
            "SELECT id, is_stub, in_corpus_cited_by FROM works").fetchall()

        nodes = sorted({r[0] for r in rows} | {r[1] for r in rows})
        self.idx = {n: i for i, n in enumerate(nodes)}
        self.N = len(nodes)
        self.src = np.fromiter((self.idx[r[0]] for r in rows), np.int32, len(rows))
        self.dst = np.fromiter((self.idx[r[1]] for r in rows), np.int32, len(rows))
        self.w = np.fromiter((float(r[2]) for r in rows), np.float64, len(rows))
        self.rel = np.array([r[3] for r in rows], dtype=object)
        self.A = sp.csr_matrix((self.w, (self.src, self.dst)), shape=(self.N, self.N))

        self.is_paper = np.array([n.startswith("UW") for n in nodes])
        stub = {wid: s for wid, s, _ in works}
        icc = {wid: c for wid, _, c in works}
        self.stub = np.array([
            stub.get(n[1:], True) if p else False
            for n, p in zip(nodes, self.is_paper)])
        self.iccv = np.array([
            float(icc.get(n[1:], 0)) if p else 0.0
            for n, p in zip(nodes, self.is_paper)])

        self.nonstub = np.where(self.is_paper & ~self.stub)[0]
        self._icc_sorted = np.sort(self.iccv[self.nonstub])
        self.BASE = self.submatrix(BASELINE_RELS)

    def submatrix(self, rels) -> sp.csr_matrix:
        m = np.isin(self.rel, list(rels))
        return sp.csr_matrix(
            (self.w[m], (self.src[m], self.dst[m])), shape=(self.N, self.N))

    @staticmethod
    def row_normalise(A: sp.csr_matrix) -> sp.csr_matrix:
        s = np.asarray(A.sum(axis=1)).ravel()
        s[s == 0] = 1.0
        return sp.diags(1.0 / s) @ A

    def node(self, work_id: str) -> int:
        return self.idx["U" + work_id]

    def icc_pctile(self, node_idx: np.ndarray) -> np.ndarray:
        return np.searchsorted(self._icc_sorted, self.iccv[node_idx]) / max(
            len(self._icc_sorted), 1)


def load_graph(conn=None) -> Graph:
    if conn is not None:
        return Graph(conn)
    with psycopg.connect(dsn()) as c:
        return Graph(c)


def geometric_theta(alpha: float, K: int) -> np.ndarray:
    return alpha ** np.arange(K + 1)


def propagate(PT: sp.csr_matrix, seedv: np.ndarray, theta: np.ndarray) -> np.ndarray:
    x = seedv.copy()
    out = theta[0] * x
    for k in range(1, len(theta)):
        x = PT @ x
        out = out + theta[k] * x
    return out


def bibliography_trust_sets(g: Graph, conn, n: int, rng: np.random.Generator):
    """Real bibliographies: papers with 18-60 non-stub in-corpus references."""
    rows = conn.execute("""
        SELECT c.src_id, array_agg(c.dst_id)
        FROM citations c
        JOIN works w ON w.id = c.dst_id AND NOT w.is_stub
        JOIN works s ON s.id = c.src_id AND NOT s.is_stub
        GROUP BY c.src_id HAVING count(*) BETWEEN 18 AND 60
    """).fetchall()
    sets = []
    for _, refs in rows:
        idx = np.array([g.node(r) for r in refs if ("U" + r) in g.idx])
        if len(idx) >= 18:
            sets.append(idx)
    order = rng.permutation(len(sets))
    return [sets[i] for i in order[:n]]


def _hop_distance(g: Graph, seeds: np.ndarray, maxd: int = 3) -> np.ndarray:
    d = np.full(g.N, 99, dtype=np.int8)
    d[seeds] = 0
    frontier = seeds
    for step in range(1, maxd + 1):
        nxt = np.unique(g.BASE[frontier].indices)
        nxt = nxt[d[nxt] == 99]
        if len(nxt) == 0:
            break
        d[nxt] = step
        frontier = nxt
    return d


def _boot(v: list[float], rng: np.random.Generator, R: int = BOOT_R):
    a = np.array(v)
    i = rng.integers(0, len(a), (R, len(a)))
    m = a[i].mean(axis=1)
    return float(a.mean()), float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))


def make_scorers(g: Graph, rng: np.random.Generator, alpha: float = 0.85, K: int = 5):
    theta = geometric_theta(alpha, K)
    # Own stream: drawing from the shared rng would shift every later fold split,
    # breaking the pairing that the bootstrap comparison across scorers assumes.
    rand_rng = np.random.default_rng(rng.integers(2**63))

    def s_random(PT, seedv, seeds):
        return rand_rng.random(g.N)

    def s_popularity(PT, seedv, seeds):
        return g.iccv.astype(float)

    def s_adjacency(PT, seedv, seeds, A_abl=None):
        A = A_abl if A_abl is not None else g.A
        return np.asarray(A[seeds].max(axis=0).todense()).ravel()

    def s_geom(PT, seedv, seeds):
        return propagate(PT, seedv, theta)

    return {"random": s_random, "popularity": s_popularity,
            "adjacency": s_adjacency, "geom": s_geom}


def evaluate(g: Graph, sets, scorer_name: str, gamma: float = 0.0,
             seed: int = DEFAULT_SEED, extra_scorer=None) -> dict:
    """Run one scorer over all trust sets. Returns the three-number report.

    `extra_scorer(PT, seedv, seeds) -> np.ndarray` evaluates a caller-supplied
    scorer under the same protocol (used by validate/gate scripts).
    """
    rng = np.random.default_rng(seed)
    scorers = make_scorers(g, rng)
    fn = extra_scorer if extra_scorer is not None else scorers[scorer_name]

    bg = None
    if gamma > 0.0:
        sv = np.zeros(g.N)
        sv[g.nonstub] = 1.0 / len(g.nonstub)
        bg = propagate(Graph.row_normalise(g.A).T.tocsr(), sv,
                       geometric_theta(0.85, 5))

    recalls, pops = [], []
    far_hit = far_n = 0
    for allrefs in sets:
        k = max(2, len(allrefs) // 5)
        perm = rng.permutation(len(allrefs))
        held, kept = allrefs[perm[:k]], allrefs[perm[k:]]
        hs = set(held.tolist())

        kill = ((np.isin(g.src, held) & np.isin(g.dst, kept)) |
                (np.isin(g.src, kept) & np.isin(g.dst, held))) & \
               np.isin(g.rel, list(LEAK_RELS))
        w = g.w.copy()
        w[kill] = 0.0
        A_abl = sp.csr_matrix((w, (g.src, g.dst)), shape=(g.N, g.N))
        PT = Graph.row_normalise(A_abl).T.tocsr()

        seedv = np.zeros(g.N)
        seedv[kept] = 1.0 / len(kept)

        if scorer_name == "adjacency" and extra_scorer is None:
            sc = scorers["adjacency"](PT, seedv, kept, A_abl=A_abl)
        else:
            sc = fn(PT, seedv, kept)
        sc = np.asarray(sc, dtype=float).copy()

        if bg is not None:
            reached = sc > 0
            sc = np.where(reached, np.log(sc + EPS) - gamma * np.log(bg + EPS),
                          -np.inf)

        sc[kept] = -np.inf
        sc[g.stub | ~g.is_paper] = -np.inf
        sc[~np.isfinite(sc)] = -np.inf
        top = np.argpartition(-sc, 25)[:25]
        top = top[np.argsort(-sc[top])]
        topset = set(top.tolist())

        recalls.append(len(topset & hs) / len(held))
        pops.append(float(np.median(g.icc_pctile(top))))
        d = _hop_distance(g, kept)
        far = set(held[d[held] >= 2].tolist())
        far_hit += len(topset & far)
        far_n += len(far)

    boot_rng = np.random.default_rng(seed + 1)
    return {
        "scorer": scorer_name if extra_scorer is None else "custom",
        "gamma": gamma,
        "n_sets": len(sets),
        "recall25": _boot(recalls, boot_rng),
        "recall_d2": far_hit / max(far_n, 1),
        "n_d2_targets": far_n,
        "pop_pctile": _boot(pops, boot_rng),
    }


def report_line(r: dict) -> str:
    m, lo, hi = r["recall25"]
    pm, plo, phi = r["pop_pctile"]
    return (f"{r['scorer']:>12} g={r['gamma']:.2f}  "
            f"recall@25 {m:.4f} [{lo:.4f},{hi:.4f}]  "
            f"d>=2 {r['recall_d2']:.4f} (n={r['n_d2_targets']})  "
            f"pop {pm:.3f} [{plo:.3f},{phi:.3f}]")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sets", type=int, default=200)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--scorer", default=None,
                    help="one of geom|adjacency|popularity|random; default: all")
    ap.add_argument("--gamma", type=float, default=0.0)
    ap.add_argument("--json", default=None)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    t0 = time.time()
    with psycopg.connect(dsn()) as conn:
        g = load_graph(conn)
        rng = np.random.default_rng(args.seed)
        n = 30 if args.selftest else args.sets
        sets = bibliography_trust_sets(g, conn, n, rng)
    print(f"graph: {g.N} nodes; {len(sets)} trust sets; "
          f"load {time.time()-t0:.1f}s", flush=True)

    names = [args.scorer] if args.scorer else ["random", "popularity",
                                               "adjacency", "geom"]
    results = []
    for name in names:
        r = evaluate(g, sets, name, gamma=args.gamma if name == "geom" else 0.0,
                     seed=args.seed)
        results.append(r)
        print(report_line(r), flush=True)

    if args.selftest:
        by = {r["scorer"]: r["recall25"][0] for r in results}
        assert by["geom"] > by["adjacency"] > by["popularity"] > by["random"], (
            f"reference ordering violated: {by} -- the metric is degenerate, "
            "do not trust this harness until diagnosed")
        print("selftest OK: geom > adjacency > popularity > random")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"seed": args.seed, "results": results}, f, indent=2)
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
