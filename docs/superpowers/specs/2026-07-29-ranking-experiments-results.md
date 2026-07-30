# Ranking experiments — measured results

**Date:** 2026-07-29
**Method:** deterministic propagation over the persisted `graph_edges` table
(`scipy.sparse`, no `mr-service` involvement, so no Monte Carlo confound). 200–300
trust sets derived from real paper bibliographies (18–60 in-corpus references), 20%
held out per fold, paired bootstrap CIs at 95%.

**Leakage ablation.** `couples` and `co_cited` edges between held-out and retained
seeds are computed *from the held-out paper's own reference list*, so they leak the
answer. Every number below with "ablated" removes those two relations between held
and kept. It does **not** remove genuine `cites`/`cited_by` edges — an earlier run
that removed all relation types was over-aggressive and its numbers are discarded.

---

## Summary of what survived

| Proposal | Verdict | Evidence |
|---|---|---|
| A2 entry-side hub damping | **No measurable effect. Cut.** | recall 0.4209 vs 0.4295 baseline, CIs fully overlap; hubness unchanged |
| A3 fix `compose()` | **Does not fix hubness.** Keep on variance/simplicity grounds only | compose 0.874 vs weighted-mean 0.867 vs single-graph 0.868 popularity percentile — all tied |
| A4 background lift | **The only intervention that works — but at γ≈0.5, not 1.0** | thin region: hubness 0.813 → 0.753 at zero recall cost |
| A6 deterministic scorer | **Confirmed as substrate** | all of this ran on it; MeritRank could not have supplied the fold count |
| Heat-kernel coefficients | **No significant gain over geometric** | 0.4295 vs 0.4252, CIs overlap |
| Self-return / singleton entities | **Real, large, but not a hub mechanism** | 49% of entity mass wasted; correlates *negatively* with popularity (ρ = −0.28) |
| Held-out-seed metric degeneracy | **Refuted** — propagation beats adjacency | 0.43 vs 0.31 ablated |
| Metric blindness to discovery | **Confirmed** | d≥2 recall ≈ 0.007 for every scorer |

---

## E1 — structural diagnostics

Non-stub papers, probability of leaving a paper via an entity hop: mean **0.281**,
median 0.142, p90 0.827.

Self-return `P(paper → entity → same paper)`: mean **0.138**; conditional on taking an
entity hop, mean **0.421**. **49.0% of all entity-hop mass returns to its source.**
67.8% of entity nodes (10,035 of 14,801) have out-degree 1, and 10.3% of a paper's
out-mass goes to such a singleton, returning with probability 1.0.

**The hypothesis that self-return is a hub mechanism is refuted.** Correlations with
`in_corpus_cited_by`: self-return **ρ = −0.285**, entity-exit ρ = −0.182. Obscure
papers self-loop; famous ones do not. The predicted "many-entity papers accumulate
self-loops" pattern is absent (self-return by entity count is non-monotonic:
0.189 / 0.238 / 0.124 / 0.091 / 0.209).

## E2 — where entity-path mass actually goes

| entity out-degree | n | mass entering | mass transmitted | share transmitted |
|---|---|---|---|---|
| 1 | 10,035 | 740.2 | **0.00** | 0.0% |
| 2 | 1,974 | 263.6 | 131.8 | 12.7% |
| 3–5 | 1,519 | 290.5 | 209.1 | 20.2% |
| 6–20 | 941 | 298.4 | 266.3 | 25.7% |
| 21–100 | 286 | 282.7 | 275.8 | 26.7% |
| 101+ | 46 | 152.0 | 151.2 | 14.6% |

Self-return acts as a **filter**: the top 1% of entities carry 35.5% of transmitted
mass against 18.8% of entering mass. So surviving entity paths are hub-concentrated.

But the destination is not: an entity hop lands on papers **0.99×** the mean corpus
citation count, while a **citation hop lands on 1.42×**. Entity hops are *0.70× as
hub-seeking as citation hops*.

**Consequence: "famous, not relevant" originates in the citation backbone, not the
entity apparatus.** Every fix aimed at entity edges (A2, and the exit-weight variants)
is aimed at the wrong subsystem.

## E3/E4 — recall with baselines, leakage ablated

300 trust sets, heat t=2.5, K=5.

| variant | recall@25 | d≥2 recall | top-25 popularity pctile |
|---|---|---|---|
| adjacency 1-hop | 0.3106 [0.281, 0.341] | 0.0000 | 0.782 |
| geometric α=.85 | 0.4252 [0.393, 0.457] | 0.0068 | 0.856 |
| heat t=2.5 | 0.4295 [0.398, 0.461] | 0.0068 | 0.854 |
| heat + A2 entry damping | 0.4209 [0.390, 0.453] | 0.0068 | 0.855 |
| heat + target-dependent exit | 0.4287 [0.396, 0.461] | 0.0068 | 0.843 |
| heat, **citation edges only** | 0.4070 [0.376, 0.438] | 0.0000 | 0.841 |

**Propagation beats 1-hop adjacency by 12 points** — the metric is not degenerate, and
the reviewers' strongest structural objection does not hold once ablation is correct.

**But d≥2 recall is ~0.007 everywhere.** The metric is genuinely blind to discovery, so
it cannot be the sole gate.

**The entire heterogeneous-graph apparatus buys 2.3 points of recall** (0.4295 vs
0.4070) while wasting 49% of its mass on self-returns.

## E5 — `compose()` and normalisation

200 trust sets.

| scorer | recall@25 | top-25 popularity pctile |
|---|---|---|
| compose (current) | 0.4456 | 0.874 |
| weighted mean | 0.4305 | 0.867 |
| max over contexts | 0.4339 | 0.856 |
| single full graph | 0.4471 | 0.868 |
| citation only | 0.4195 | 0.863 |
| **full + lift (uniform bg)** | 0.3885 | **0.699** |
| **full + lift (degree-matched null)** | 0.2159 | **0.470** |
| full / degree | 0.3045 | 0.614 |

**Every graph-proximity scorer lands in the 86th–87th popularity percentile.** Changing
how contexts are combined does not move it. A3 is therefore *not* a fix for the
reported complaint, though the variance argument for replacing `Σ_c s_c − 3b` stands
independently.

**Only background normalisation moves hubness**, and it trades against recall. The
degree-matched null over-corrects badly.

## E6 — the operating point

`score = log(full) − γ·log(background)`, stratified by the stub share of the trust
set's own citation neighbourhood.

**Dense region** (median stub share 0.05):

| γ | recall@25 | popularity pctile |
|---|---|---|
| 0.00 | 0.3313 | 0.869 |
| 0.25 | 0.3426 | 0.832 |
| 0.50 | 0.3271 | 0.797 |
| 0.75 | 0.2834 | 0.727 |
| 1.00 | 0.2380 | 0.671 |

**Thin region** (median stub share 0.26 — the regime the user is in):

| γ | recall@25 | popularity pctile |
|---|---|---|
| 0.00 | 0.4232 | 0.813 |
| 0.25 | 0.4409 | 0.781 |
| **0.50** | **0.4432** | **0.753** |
| 0.75 | 0.4427 | 0.720 |
| 1.00 | 0.4174 | 0.683 |

**In the thin region, γ = 0.5 is free**: recall is flat-to-slightly-better than raw
proximity while popularity percentile drops 6 points. γ = 0.75 also holds recall.
Full lift (γ = 1.0) over-corrects in both regions.

**Recommended default: γ = 0.5, exposed as a slider.** This is A4, corrected — the spec
proposed γ = 1.0, which measurably costs recall for no additional benefit the user
asked for.

---

## Revised plan

1. **Fix the `build_graph.py` / `bootstrap.py` divergence** (33,994 duplicate keys,
   first-wins vs last-wins). Blocks any rebuild and any before/after comparison.
2. **B8 retrieval fix.** Search builds the trust set; if it returns the wrong paper,
   every downstream complaint is confounded.
3. **A6 deterministic scorer**, with `θ` as an array and γ as a parameter. Note it must
   *not* be `Σ α^k P^k s` naively: the engine's unique-visit counting suppresses hub
   re-entry, and the spec's per-edge-type decay inside a row-stochastic `P` cancels
   exactly as Cause 3 does.
4. **A4 at γ = 0.5**, as a separate displayed field with its own uncertainty — not a
   redefinition of `trust`.
5. **A3 as weighted mean**, justified by variance (13σ² → σ²/5) and simplicity, not by
   hubness. Drop the `max` option.
6. **Fix the LOO cap at 12 seeds**, which silently disables the error bar across the
   entire 13–50 seed target range while still reporting `method="leave_one_out"`.

**Cut:** A2 (no measurable effect), A7 alpha change (Cause 2 refuted — top-100 overlap
with the global ego is 2/100), the five-variant bake-off, the LLM judge panel.

**Unresolved:** the corpus (KNOWN_ISSUES #5) still caps everything. Thin-region trust
sets score *better* on recall than dense-region ones (0.42 vs 0.33) but that is because
their neighbourhoods are smaller, not because the results are better.

---

## Addendum 2026-07-30: engine correlation gate (plan Task 4)

`scripts/validate_propagate.py`, 40 bibliography-derived egos, engine top-2500
window, engine-support comparison: **FAIL — median Spearman 0.8417, IQR
[0.8196, 0.8516], top-100 overlap ~0.85.** Seed absorption plus a first-order
non-backtracking correction (subtracting the 2-step return diagonal) moved the
median by less than 0.01; the residual is the engine's revisit-dedup at all lags,
which is rank-structural and cannot be closed by any per-node monotone transform.

Consequence, applied: `propagate.py` trust scores are not user-facing. The engine
remains the sole trust scorer; the product consumes only `background()` (lift
denominator, plain diffusion exactly as measured in E5/E6).
