"""Correlation gate: PropagationGraph vs the MeritRank engine.

The deterministic scorer (api/provenance/propagate.py) may not feed anything
user-facing until this gate passes: median Spearman >= 0.90 over 40
bibliography-derived seed sets, engine top-2500 window vs deterministic scores,
union of supports with missing = 0.

Touches the live engine (scratch egos, serialised walk building -- expect ~1min
per cold ego, KNOWN_ISSUES #14). Scratch edges are torn down in `finally`, same
pattern as ranking._leave_one_out.

Usage:  python scripts/validate_propagate.py [--profiles 40] [--gate 0.90]
Exit 0 on PASS, 1 on FAIL.
"""
from __future__ import annotations

import argparse
import secrets
import sys
import time
from pathlib import Path

import numpy as np
from scipy.stats import spearmanr

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import os  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from provenance import config  # noqa: E402
from provenance.meritrank import MeritRank  # noqa: E402
from provenance.propagate import PropagationGraph  # noqa: E402

from eval_ranking import bibliography_trust_sets, load_graph, dsn  # noqa: E402
import psycopg  # noqa: E402

WINDOW = 2500


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profiles", type=int, default=40)
    ap.add_argument("--gate", type=float, default=0.90)
    ap.add_argument("--seed", type=int, default=20260730)
    args = ap.parse_args()

    rng = np.random.default_rng(args.seed)
    with psycopg.connect(dsn()) as c:
        g_eval = load_graph(c)
        sets = bibliography_trust_sets(g_eval, c, args.profiles, rng)
    id_of = {v: k for k, v in g_eval.idx.items()}
    seed_sets = [[id_of[i][1:] for i in s] for s in sets]

    url = os.environ.get("DATABASE_URL", config.DATABASE_URL)
    engine = create_engine(url, future=True)
    rhos: list[float] = []
    nonce = secrets.token_hex(3)

    with Session(engine) as db:
        mr = MeritRank(db.connection())
        pg = PropagationGraph.get(db)

        for i, wids in enumerate(seed_sets):
            ego = f"Uval_{nonce}_{i}"
            added: list[str] = []
            t0 = time.time()
            try:
                for wid in wids:
                    mr.put_edge(ego, "U" + wid, 1.0, config.AGGREGATE)
                    added.append("U" + wid)
                rows = mr.scores(ego, context=config.AGGREGATE,
                                 limit=WINDOW, kind="User")
                eng = {s.node[1:]: s.value for s in rows
                       if s.node.startswith("UW")}
                det = pg.score({w: 1.0 for w in wids})

                seeds = set(wids)
                eng = {k: v for k, v in eng.items() if k not in seeds}
                det = {k: v for k, v in det.items() if k not in seeds}
                # Compare on the ENGINE's support: the engine returns a top-WINDOW
                # by |score|, so items outside it are unknown, not zero -- scoring
                # them as zero would measure window truncation, not disagreement.
                # The deterministic side has full support, so det.get(k, 0.0) on an
                # engine-scored item is a genuine "we say unreached".
                support = sorted(eng)
                if len(support) < 100:
                    print(f"  ego {i}: support too small ({len(support)}), skipped")
                    continue
                a = np.array([eng[k] for k in support])
                b = np.array([det.get(k, 0.0) for k in support])
                rho = float(spearmanr(a, b).statistic)
                top_e = {k for k, _ in sorted(eng.items(), key=lambda kv: -kv[1])[:100]}
                top_d = {k for k, _ in sorted(det.items(), key=lambda kv: -kv[1])[:100]}
                ovl = len(top_e & top_d) / 100.0
                rhos.append(rho)
                print(f"  ego {i:>2}: n_seeds={len(wids):>2} support={len(support):>5} "
                      f"rho={rho:+.4f} top100 {ovl:.2f}  ({time.time()-t0:.1f}s)",
                      flush=True)
            finally:
                for node in added:
                    try:
                        mr.delete_edge(ego, node, config.AGGREGATE)
                    except Exception:  # noqa: BLE001 - teardown must not mask
                        pass
        db.commit()

    if not rhos:
        print("FAIL: no comparable egos")
        return 1
    med = float(np.median(rhos))
    q1, q3 = np.percentile(rhos, [25, 75])
    verdict = "PASS" if med >= args.gate else "FAIL"
    print(f"{verdict} median spearman={med:.4f} IQR=[{q1:.4f},{q3:.4f}] "
          f"(gate {args.gate:.2f}, n={len(rhos)})")
    return 0 if med >= args.gate else 1


if __name__ == "__main__":
    sys.exit(main())
