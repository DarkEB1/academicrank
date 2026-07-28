"""Insert the measured experiment numbers into README.md.

Numbers are read from data/sybil_results.json -- never typed by hand, so the README
cannot drift from what was actually measured.
"""
import json, pathlib, sys
root = pathlib.Path(__file__).resolve().parent.parent
res = json.loads((root / "data" / "sybil_results.json").read_text())
p, s = res["ppr_comparison"], res["sybil"]
md = f"""### 1. It correlates with personalised PageRank, but does not match it

A NetworkX personalised-PageRank baseline was run over the identical graph and seed set.

| Measure | Value |
|---|---|
| nodes compared | {p['n']:,} |
| Spearman rank correlation | **{p['spearman']:.4f}** |
| Kendall tau | {p['kendall']:.4f} |
| top-50 overlap | {p['top50_overlap']:.0%} |

Strong agreement, but far from identical. If they matched, MeritRank's decay machinery
would be doing nothing and plain PPR would be the honest choice. The {(1-p['top50_overlap']):.0%} of the
top 50 where they disagree is where the decay is doing its work.

### 2. It suppresses a citation ring

{s['ring_size']} synthetic papers, mutually citing each other densely, attached to the real corpus
by a single edge — then scored from the same seed set under both algorithms.

| Algorithm | Share of total score captured by the ring |
|---|---|
| plain personalised PageRank | {s['ppr_share_pct']:.4f}% |
| **MeritRank** | **{s['meritrank_share_pct']:.4f}%** |

MeritRank hands the ring **{s['ratio']:.2f}×** the score PPR does — a **{s['suppression_factor']:.2f}×
suppression**. The ring is not eliminated (it is attached to a genuinely trusted paper,
so some trust legitimately flows in), but it captures materially less than an algorithm
with no connectivity decay would give it.

Reproduce with `python scripts/sybil_experiment.py`; raw output in `data/sybil_results.json`.
"""
readme = root / "README.md"
t = readme.read_text(encoding="utf-8")
if "<!--MEASUREMENTS-->" not in t:
    print("marker missing; already injected?", file=sys.stderr); sys.exit(1)
readme.write_text(t.replace("<!--MEASUREMENTS-->", md), encoding="utf-8")
print("injected measurements into README.md")
