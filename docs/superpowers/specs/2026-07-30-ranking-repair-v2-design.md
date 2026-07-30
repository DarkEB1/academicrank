# Ranking repair v2 — post-measurement design

**Status:** design, awaiting review
**Date:** 2026-07-30
**Supersedes:** `2026-07-29-ranking-and-query-conditioned-search-design.md`
**Evidence:** `2026-07-29-ranking-experiments-results.md` (six experiments, 200–300
bibliography-derived trust sets, leakage-ablated, bootstrap CIs), five adversarial
reviews, and the MeritRank paper read directly.

The v1 spec is retained unedited as the record of what we believed before measuring.
Its diagnosis was wrong in three places and its sequencing was inverted; this document
states what replaced it and on what evidence. Per this repo's convention, the failed
hypotheses are listed, not tidied away.

---

## What the measurements established

1. **The "famous, not relevant" complaint lives in the citation backbone, not the
   entity graph.** A citation hop lands on papers 1.42× the mean corpus citation
   count; an entity hop lands on 0.99×. Every v1 fix aimed at entity edges was aimed
   at the wrong subsystem.
2. **Every graph-proximity scorer is equally hub-biased** (top-25 at the 86th–87th
   popularity percentile) regardless of how per-context scores are combined.
   `compose()` is not the cause. Changing the combiner moves nothing the user sees.
3. **Background normalisation is the only lever that moves hubness**, and the
   operating point matters: `score = log(personal) − γ·log(background)` at **γ = 0.5**
   holds recall while dropping the top-25 popularity percentile by 6 points in the
   thin-corpus regime the user is in. γ = 1.0 (v1's proposal) and a degree-matched
   null both over-correct, buying extra de-hubbing with recall the user did pay for.
4. **The held-out-seed metric is valid once leakage is ablated** — propagation 0.43
   vs trivial adjacency 0.31 — but it is blind to discovery (d≥2 recall ≈ 0.007 for
   every scorer), so it is a guard-rail, not an optimisation target. `couples` /
   `co_cited` edges between held-out and retained seeds are computed from the
   held-out paper's own reference list and must be ablated per fold.
5. **This build is personalised PageRank with unique-visit-per-walk counting.** Of
   the paper's three decay mechanisms, connectivity and epoch decay are absent from
   the vendored code; transitivity decay *is* alpha (`α_engine = 1 − α_paper`, so
   0.85 is the paper's own recommended operating point). Unique-visit counting is the
   one genuine difference from textbook PPR and it is load-bearing: it suppresses
   hub re-entry and 2-cycles, which matters because **49% of all entity-hop mass
   returns to its own source** (67.8% of entity nodes are singletons).
6. **The entity apparatus buys 2.3 points of recall** (0.4295 vs 0.4070
   citation-only) at the cost of half its mass wasted on self-returns. The README's
   framing of meta-path discovery as a headline capability overstates what it
   measures as delivering.

### Hypotheses from v1 that failed measurement, kept on the record

- *A2 entry-side hub damping*: no measurable effect (0.4209 vs 0.4295, CIs overlap;
  hubness unchanged). The damping-cancellation it targeted is real but is the
  paper's own Relative Feedback normalisation working as designed — any effective
  weight must vary *between* one node's out-edges, and A2's did not reach the edges
  where that matters.
- *Cause 2 (α=0.85 washes out personalisation)*: refuted twice — top-100 overlap
  with the global ego is 2/100, and the setting is the paper's recommendation.
- *Cause 1's mechanism* (compose pays per-family bonuses): scores are visit
  fractions, mass is conserved, marginals are net negative. The hub bias attributed
  to `compose()` is real but arrives via papers with zero baseline score whose raw
  entity scores are summed unsubtracted — and fixing it does not move the complaint.
- *Heat-kernel (unimodal) diffusion coefficients*: 0.4295 vs 0.4252 geometric, CIs
  overlap. No gain. (Argued in review as "the single highest ratio of improvement to
  cost"; it wasn't.)
- *Degree-matched null background*: recall 0.216 vs 0.389 for the uniform
  background. Over-corrects worst of all candidates.

---

## Already landed (bug fixes, committed independently of this design)

| Commit | Fix |
|---|---|
| `126c303` | `build_graph.py` deduplicates the 33,994 colliding `(src,dst,context)` keys before either consumer sees them (was: first-wins in Postgres, last-wins in the engine — ~30k weights silently divergent after any documented rebuild) |
| `1a93c1a` | Leave-one-out now runs at every trust-set size ≥2 (12-replicate deterministic subsample above 12 seeds, jackknife scaled by true n); fallback bands report `proportional_fallback` instead of claiming to be leave-one-out; `to_uncertainty` no longer coerces unknown methods to `repeat_sample` |
| `709140a` | KNOWN_ISSUES #1 corrected: connectivity/epoch decay are absent, not hidden; transitivity decay is alpha |

## Remaining plan

Ordered; each step gated on the evaluation harness from the experiments (recall@25
leakage-ablated + top-25 popularity percentile + d≥2 recall reported together, never
a single scalar).

**R1 — Retrieval fix (v1's B8, promoted).** Weighted tsvector
(`setweight(title,'A') || setweight(abstract,'B')`) plus trigram boost on title using
the existing `ix_works_title_trgm`. Search is how trust sets get built; while an
exact title lands third (KNOWN_ISSUES #13), every downstream complaint is confounded
by possibly-wrong seeds.

**R2 — Deterministic scorer** (`api/provenance/propagate.py`), behind the existing
`RankingBackend` seam. Two properties are requirements, not options:

- **Unique-visit semantics, not `Σ α^k Pᵏ s`.** The naive series switches to
  expected-visit counting and adds back the hub re-entry mass the engine currently
  suppresses (37% of visit mass is revisits). Either kill self-return mass at each
  step or compute reach probabilities; validate against the engine on a 40-fold
  correlation sample before it is allowed to rank anything.
- **No per-edge-type constants inside a row-stochastic P** for single-relation-type
  nodes — they cancel exactly as v1's Cause 3 described. Relation weights act on the
  paper side, where a paper's out-edges genuinely differ by type.

Marketing consequence, accepted up front: README's "the ranking algorithm is never
reimplemented in this codebase" and the `RankingBackend` docstring's "must be
reported as such" are honoured by reporting it — the scorer ships as what it is, and
the MeritRank framing changes in the same commit if R2 becomes the default. The
sybil-resistance story does not change, because (KNOWN_ISSUES #1, corrected) there
was no connectivity/epoch decay to lose, and measured suppression was already null.

**R3 — Fame normalisation at γ = 0.5** as a *displayed, sortable field* (`lift`),
not a redefinition of `trust`: numerator and denominator are both derivable, but a
lift has no path derivation for its denominator, and `trust` is described on-screen
as seed proximity. Uniform-over-non-stub background ego. γ exposed in the parameter
playground (0 = today's ranking, 1 = full lift), default 0.5, with the same
live-preview mechanics as the context weights. Uncertainty: LOO replicates pass
through the same transform; the denominator's sampling error is disclosed as
uncounted in the method copy (it is profile-independent and cannot be jackknifed by
seed removal).

**R4 — `compose()` as weighted mean.** Justified by variance (≈13σ² → σ²/5) and by
deleting the `max(0, ·)` clamp's zero-score tie block — explicitly *not* by hubness,
which it does not move. The `max` option is dropped (discontinuous in the weights,
turns sliders into no-ops per paper).

**R5 — Singleton-entity cleanup in `build_graph.py`.** 67.8% of entity nodes
transmit nothing (out-degree 1: pure self-loop under unique-visit counting, pure
waste under R2). Drop entity nodes with corpus degree 1 from the graph; they cannot
carry trust between papers by construction. This is a data fix, not a ranking
change; measured effect expected near zero on recall (the mass was wasted) and
positive on walk budget.

**Deferred, unchanged from v1's cuts:** query-conditioned search (v1 B9/B10) waits
until R1–R3 are measured in place; the five-variant bake-off and LLM judge panel
stay cut; corpus re-scrape (KNOWN_ISSUES #5) remains the largest known ceiling on
everything here and is separate work.

## Evaluation protocol (carried from the experiments)

- Trust sets: real paper bibliographies (18–60 in-corpus refs), 20% held out,
  ≥200 sets, fixed RNG seed.
- Per-fold ablation of `couples`/`co_cited` between held and kept seeds.
- Reference scorers always reported alongside: random, popularity, 1-hop adjacency.
- Three numbers per variant, never one: recall@25 [bootstrap CI], d≥2 recall,
  top-25 popularity percentile.
- A change ships if recall's CI does not degrade and the popularity percentile
  improves, or vice versa; a change that trades one for the other goes to the user
  with both numbers, because that trade is a product decision, not a statistical one.

## Open questions for review

1. **γ default.** 0.5 is measured-safe in the thin region; in the dense region even
   0.5 costs a little recall (0.331 → 0.327, within CI). Acceptable as a global
   default, or should γ scale with the trust set's measured neighbourhood density?
2. **R5 scope.** Dropping degree-1 entities is safe for ranking; it also removes
   them from `/explain` paths and the graph explorer. Is "this paper's sole author"
   worth keeping as display-only data outside the ranking graph?
3. **The user's actual seed paper** is still unsupplied; every thin-region number
   above uses bibliography-derived proxies. One paper id turns A1 from a proxy into
   a direct check.
