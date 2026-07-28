"""Phase 2 gate: do dissimilar trust sets actually produce dissimilar rankings?

If two deliberately unrelated seed sets return near-identical top-20 lists, then the
walk has collapsed into hubs and the product is a lie -- everyone would get the same
answer regardless of what they said they trust. Gate: Jaccard overlap < 0.4.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api"))

from sqlalchemy import create_engine, text  # noqa: E402

from provenance import config  # noqa: E402
from provenance.meritrank import MeritRank  # noqa: E402
from provenance.models import node_to_work_id, work_node  # noqa: E402
from provenance.ranking import compose  # noqa: E402

TOP_N = 20


def pick_seeds(conn, topic_like: str, n: int = 5) -> list[str]:
    """Most-cited corpus papers whose primary topic matches a pattern."""
    rows = conn.execute(text(
        "SELECT DISTINCT w.id, w.in_corpus_cited_by FROM works w "
        "JOIN work_topics wt ON wt.work_id = w.id "
        "JOIN topics t ON t.id = wt.topic_id "
        "WHERE w.is_stub = false AND (t.display_name ILIKE :p OR t.subfield ILIKE :p) "
        "ORDER BY w.in_corpus_cited_by DESC LIMIT :n"), {"p": topic_like, "n": n}).all()
    return [r[0] for r in rows]


def top_for(mr: MeritRank, ego: str, seeds: list[str], n: int = TOP_N) -> list[str]:
    for s in seeds:
        mr.put_edge(ego, work_node(s), 1.0, config.AGGREGATE)
    per_ctx = {c: {x.node: x.value for x in mr.scores(ego, context=c, limit=3000, kind="User")}
               for c in config.CONTEXTS}
    composed = compose(per_ctx)
    seedset = set(seeds)
    ranked = []
    for node, v in sorted(composed.items(), key=lambda kv: -kv[1]):
        wid = node_to_work_id(node)
        if wid and wid not in seedset:
            ranked.append(wid)
        if len(ranked) >= n:
            break
    for s in seeds:
        mr.delete_edge(ego, work_node(s), config.AGGREGATE)
    return ranked


def main() -> int:
    engine = create_engine(os.environ.get("DATABASE_URL", config.DATABASE_URL), future=True)
    with engine.connect() as conn:
        mr = MeritRank(conn)
        pairs = [
            ("number theory / cryptography", "%number theory%", "%statistic%", "statistics"),
            ("geometry / optimization", "%geometr%", "%optimi%", "optimization"),
            ("algebra / numerical analysis", "%algebra%", "%numerical%", "numerical"),
        ]
        results, failures = [], 0
        for label, pa, pb, _ in pairs:
            a, b = pick_seeds(conn, pa), pick_seeds(conn, pb)
            if len(a) < 3 or len(b) < 3:
                print(f"SKIP {label}: not enough seeds ({len(a)}, {len(b)})")
                continue
            ta = top_for(mr, "Udiv_a", a)
            tb = top_for(mr, "Udiv_b", b)
            conn.commit()
            inter = len(set(ta) & set(tb))
            union = len(set(ta) | set(tb)) or 1
            jac = inter / union
            ok = jac < 0.4
            failures += 0 if ok else 1
            results.append((label, jac, ok))
            print(f"{'PASS' if ok else 'FAIL'}  {label:<32} jaccard={jac:.3f} "
                  f"(shared {inter}/{union})")

        print()
        if not results:
            print("GATE FAIL: no comparable seed pairs found")
            return 1
        worst = max(r[1] for r in results)
        print(f"GATE {'PASS' if failures == 0 else 'FAIL'}: "
              f"worst jaccard {worst:.3f} (threshold < 0.4)")
        return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
