# Ranking repair and query-conditioned search

**Status:** design, awaiting review
**Date:** 2026-07-29

## Problem

Rankings are poor at the seed counts users will actually have. A realistic trust set is
10–50 papers, and at inception closer to 5–10, because nobody invests effort in a tool
before they believe it works. The product has to be convincing at 10 seeds.

Observed by the user, in their words: results are *"famous, not relevant"*, *"unrelated
to my seeds"*, and *"tiny, and do not seem related to my paper, just seem to be well
connected nodes in the network."*

That is hub collapse. It has three distinct causes, and one previously-recorded fourth
symptom that appears to be seed-set dependent.

### Cause 1 — `compose()` pays a bonus per relation family

`ranking.py:92` computes `score = w_base·b + Σ_c w_c·(score_c − b)`. With four entity
contexts at default weights this expands to `Σ_c score_c − 3b`.

Each context is "citation baseline + one entity family" (KNOWN_ISSUES #2), so
`score_c ≥ b` for essentially every reachable node. A paper reachable via authors *and*
topics *and* venue *and* institution therefore collects four non-negative bonuses; a
paper reachable only along a citation path collects none and scores at baseline. The
four bonuses also share the same underlying baseline paths, so common connectivity is
counted up to four times.

The formula rewards being well-connected across many relation types. That is the
definition of a hub. This is assessed as the single largest contributor.

### Cause 2 — `ALPHA=0.85` mixes past the seed neighbourhood

Expected walk length is `1/(1−α) ≈ 6.7` hops. With topic nodes holding >1,000 papers,
six hops exceeds the mixing time of the hub core, so the walk distribution converges
toward the graph's stationary distribution — which is global PageRank. Personalisation
is washed out by construction. A 10-seed trust set needs mass to stay within 2–3 hops.

### Cause 3 — hub damping is a no-op, so entity hops are uniform teleports

`core/src/graph.rs:75` builds a `WeightedIndex` over each node's out-edges, normalising
by their sum. In `build_graph.py`, every out-edge of a given entity node carries the
same weight — `damp(deg)` is constant per entity, and the topic `idf` scale is constant
across one topic's outgoing edges. The constant cancels under normalisation.

Consequences:

- The hub damping described at `build_graph.py:11` does not happen.
- `paper → topic → paper` is a uniform jump to a random paper in that topic. For a
  1,191-paper topic that step carries almost no information.
- Topic IDF *does* work, but only on the `tagged` entry edge, where weights genuinely
  differ per paper. The `tags` back-edge scaling is inert.
- `DEFAULT_WEIGHTS["authored_by"] = 0.60` does not mean "co-authorship is 60% as strong
  as citation". Effective strength is set by entity degree via dilution, invisibly. The
  configured per-relation weights are substantially decorative on the entity out-side.

### Symptom 4 — 1-hop dominance (recorded, seed-dependent)

KNOWN_ISSUES #12 measured 19 of the top 20 as direct citation neighbours on a 5-seed
profile. That is the opposite of hub collapse and both have been observed. Treated here
as evidence that the head and the tail of the ranking fail for different reasons: the
head is trivially adjacent, the tail below roughly rank 100 is sampling noise
(`NUM_WALKS=10000` over ~96k nodes gives ~67k node-visits and 1e-4 score resolution,
with ~10,500 papers holding non-zero score, so most sit on 1–5 visits). There is no
informative middle.

## Non-goals

- Re-scraping the corpus. KNOWN_ISSUES #5 (the corpus is statistics, not mathematics)
  is real and larger than everything here, but it is a separate piece of work.
- Multi-user serving. KNOWN_ISSUES #14 stands; this remains a single-user demo.
- Reworking `/recommendations` or `/blindspots`. Noted below that the diversity dial is
  structurally the same blend as the search blend and could later collapse into it, but
  that is out of scope.

## Approach

Two milestones. **B depends on A6** and cannot be built without it.

### Milestone A — ranking repair

**A0. Evaluation harness.** `scripts/eval_ranking.py`. Nothing else in this spec may be
judged by eye.

Metric: **held-out-seed recovery.** Take a trust set of size N, hide k seeds, rank with
the remaining N−k, and check whether the hidden seeds return in the top M. Report
recall@50 and MRR, swept across N ∈ {5, 10, 25, 50}. No human labels required, and it
measures exactly the sparse-seed regime the product cares about.

Trust sets are synthesised from the corpus by sampling coherent neighbourhoods (a
topic, an author's body of work, a citation cluster) so that "the seeds belong together"
is true by construction. Record the sampling seed so runs are reproducible.

Baseline numbers are captured **before any change**. Every subsequent phase reports
against that baseline, and anything that does not improve it is reverted rather than
kept on faith.

**A1. Corpus sanity check.** For the user's actual seed paper(s), report in-corpus
citation degree, number of full (non-stub) neighbours at 1 and 2 hops, and topic
neighbourhood size. If the neighbourhood is thin, "unrelated results" is a corpus
problem and Milestone A cannot fix it. Cheap, and it gates interpretation of everything
downstream.

**A2. Hub damping moved to entry edges.** Apply the degree penalty on
`authored_by` / `tagged` / `published_in` / `affiliated` — the paper→entity direction,
where a given paper's edges to different entities genuinely differ, so the weight
survives normalisation. Remove `damp()` from the entity→paper direction, where it is
inert, and correct the comment at `build_graph.py:11`.

This is what makes the per-relation weights — and the user-facing institution knob —
actually control anything.

**A3. `compose()` — stop summing marginals.** Replace the additive marginal sum with a
normalised combination (weighted mean, or max) so that multi-family connectivity stops
compounding. Both are implemented and chosen by A0's numbers, not by argument.

**A4. Lift over a uniform background.** Rank by `log((personal+ε)/(background+ε))`
rather than by raw proximity, with a minimum-visit floor so the noise tail cannot reach
the top. The existing `GLOBAL_EGO` is seeded from the 200 most-cited papers and is
itself hub-biased; it must be replaced by a uniform-over-all-non-stub-papers ego to be
a valid denominator. Retain the current top-200 ego for the comparison strip, which is
a different question and where its citation bias is disclosed already.

**A5. Seed-coverage term.** Count how many *distinct* seeds reach a paper, not only
total mass. At 10 seeds, "reached by 6 of them" is far stronger evidence than "reached
hard by 1", and it is robust to a single idiosyncratic seed. The leave-one-out
machinery in `ranking.py:245` already computes most of this.

**A6. Deterministic truncated-PPR scorer.** `api/provenance/propagate.py`, computed
over the `graph_edges` table already persisted for `/explain`:

```
score = Σ_{k=0..K} α^k · Pᵏ · s        # s = seed vector, P = weighted transition matrix
```

Per-edge-type decay constants live inside `P`. This is the user's per-edge-type decay
idea in its correct form — **multiplicative, and summed over paths**. Subtractive decay
is rejected: a 3-hop path through three strong edges would score identically to one
through three weak edges, and scores go negative at depth so path length dominates edge
quality.

What it buys:

- **Exact scores.** Removes sampling noise entirely, which fixes the tiny/noisy tail
  better than raising `NUM_WALKS` does, and without the 40–90s cold start.
- **Bounded depth by construction.** K=3 is a hard cap, so Cause 2 cannot occur.
- **Exact path decomposition.** `explain_paths()` currently reconstructs paths by hand
  in Python because the engine will not expose them; under this scorer the explanation
  *is* the computation.
- **Millisecond queries**, which is the precondition for Milestone B.

What it costs, stated plainly: MeritRank's transitivity and connectivity decay are the
sybil-resistance story, and truncated PPR has none. KNOWN_ISSUES #8 already concedes
that claim is weak for citation graphs, but the cost is real.

**A7. Engine parameters.** `ALPHA` → ~0.5, `NUM_WALKS` up for score resolution. Both
are process-global env vars requiring an `mr-service` restart (KNOWN_ISSUES #1).
Expected to matter less once A6 exists; measured anyway, because "expected" is not
evidence.

### Milestone B — query-conditioned search

**B8. Retrieval fix.** KNOWN_ISSUES #13 moves from "not applied" to prerequisite:
weighted tsvector (`setweight(title,'A') || setweight(abstract,'B')`) plus a trigram
boost on title, using the existing `ix_works_title_trgm` index. Both the `ir` term and
anchor selection depend on retrieval quality.

**B9. Query-conditioned ranking.** Structurally this is Haveliwala's topic-sensitive
PageRank, with the trust set as one bias vector and the query as the other:

```
score(p) = a·log(trust(p)/bg(p)) + b·log(query(p)/bg(p)) + c·log(ir(p))
           └─ cached per profile ─┘  └─ cached per query ─┘   └ text match ┘
```

- `trust` — truncated PPR from the user's seeds. Cached per trust set (exists).
- `query` — truncated PPR from **query anchors**. Profile-independent, so it caches
  across all users issuing the same query.
- `ir` — direct text relevance, so an exact title match cannot be buried by graph
  structure.

Log space is multiplicative in the underlying scores, giving the required semantics: a
result must be near **both** the trust set and the query. A plain sum would let a
heavily-trusted but irrelevant paper win the query.

`a=1, b=0` recovers today's `/rankings`; `a=0, b=1` is pure topic search. One scorer,
one code path, ratio exposed as a slider in the existing parameter-playground idiom.

**Query anchors include entity nodes.** Match the query against topic, venue and author
names as well as paper text, and seed those Beacon nodes directly. Searching "algebraic
geometry" then anchors on the topic node rather than on whichever five papers matched
the string.

**Onboarding consequence, and the reason this phase matters most:** at zero seeds the
scorer degrades to plain topic search, and every seed added visibly sharpens it. The
user sees the mechanism work before investing anything — which is the objection this
whole spec exists to answer.

### B10 — variant bake-off

Five scorers behind one protocol, `score(profile, query, weights) -> dict[work_id,
float]`. The interface and registry land **first**, as a single task; the variants then
each add one file and never touch shared code, so they fan out in parallel with no
worktree isolation needed.

| | Variant | Hypothesis under test |
|---|---|---|
| V1 | Retrieve-then-rerank (classic) | Does the query vector earn its cost at all? If V1 wins, B9 collapses to a filter |
| V2 | Log-space additive blend | The B9 design |
| V3 | Merged-seed propagation | Query anchors in the *same* ego as trust seeds; permits paths running through query territory into trust territory. Less explainable, possibly better |
| V4 | Raw multiplicative, no background term | Ablation: is lift normalisation load-bearing? |
| V5 | Entity-anchored only | Does anchoring on topic/venue/author nodes beat anchoring on matched papers? |

V4 and V5 are ablations by design — they identify which *components* carry the win, not
merely which bundle scores highest.

## Evaluation design

Two instruments, deliberately separated.

**Tier 1 — scripts.** Held-out-seed recovery (A0), extended for search by using a
held-out seed's own title as the query and checking whether that paper returns. Metrics
are deterministic; computing them with an LLM would inject variance into the only
source of truth. Runs on every variant, every time, at no cost.

**Tier 2 — subagent judge panel.** Graded relevance on `(query, paper)` pairs → nDCG.
Tier 1 cannot see whether a result is *relevant to the query*, or whether a top result
is a genuine answer versus a well-connected hub. There is no ground truth for that, and
that is where an LLM judge is the correct instrument.

Protocol: blind — variant labels stripped, result order shuffled. Three judges per pair.
Disagreement is **recorded, not averaged away**; high-variance pairs are reported
separately, because they mark where the metric itself is unreliable.

**Judgment caching is what makes Tier 2 affordable.** A relevance rating is a property
of `(query, paper)` and does not depend on which scorer surfaced it. Judge the *union*
of every variant's top-10, cache on `(query, work_id)`, and score all variants offline
against the same cache.

≈20 queries × ~30 unique papers ≈ **600 judgments total**, not the ~3,000 that
`(query, variant, paper)` triples would require. A sixth variant later costs only the
papers nothing else surfaced. Without this the bake-off runs once and never again; with
it, it is a regression suite.

## Components

| Path | Status | Purpose |
|---|---|---|
| `scripts/eval_ranking.py` | new | Tier 1 metrics, synthetic trust sets, baseline capture |
| `scripts/eval_judge.py` | new | Tier 2 panel driver, judgment cache, nDCG |
| `api/provenance/propagate.py` | new | Deterministic truncated-PPR scorer (A6) |
| `api/provenance/scorers/` | new | `Scorer` protocol, registry, V1–V5 |
| `api/provenance/query.py` | new | Query anchors, query vector, `ir` term (B9) |
| `api/provenance/ranking.py` | edit | `compose()` (A3), lift (A4), coverage (A5) |
| `api/provenance/routers/papers.py` | edit | Retrieval fix (B8) |
| `scripts/build_graph.py` | edit | Entry-side damping (A2) |
| `api/provenance/config.py` | edit | New parameters |
| `docker-compose.yml` | edit | `ALPHA`, `NUM_WALKS` (A7) |

New endpoint `GET /api/search?q=&profile=&trust_weight=` returning `ScoredPaper[]` with
a per-term breakdown `{trust_lift, query_lift, ir}`. Follows the two API rules in
`API_CONTRACT.md`: no bare scores, and the disclaimer string is carried verbatim.

## Assumptions recorded for review

1. **Default scorer.** The deterministic scorer (A6) becomes the default *if it wins
   A0's evaluation*, and the README's MeritRank framing changes accordingly. If the
   user prefers MeritRank stays in front regardless, A6 becomes an alternate scorer and
   Milestone B runs on it for latency reasons only.
2. **Test paper.** The user's specific seed paper was not supplied; A1 stands in for it.
   Supplying it later is a one-minute check, not a re-plan.
3. **Corpus.** KNOWN_ISSUES #5 is not addressed. If A1 shows the user's seeds sit in a
   thin region, the ceiling on all of Milestone A is low and the corpus work should be
   resequenced ahead of it.

## Risks

- **A2 changes the graph**, so it requires a full rebuild and invalidates every cached
  ranking. `mr_bulk_load_edges` clears all engine state (KNOWN_ISSUES #9), so trust
  edges must be re-seeded; `ensure_seeded()` already covers this.
- **A6 duplicates scoring logic.** Two scorers over one graph will drift unless the
  `Scorer` protocol is the only entry point. Enforced by routing all endpoints through
  the registry, with no direct `MeritRank` calls left in routers.
- **Tier 2 judges are LLMs rating academic relevance in a corpus that is mostly
  biostatistics.** Judge competence is itself uncertain. Mitigated by recording
  disagreement, and by treating Tier 1 as the primary metric where the two conflict.
- **Ten phases is a large spec.** Milestones A and B should become separate
  implementation plans, with B not started until A6 lands and A0 shows a real
  improvement over baseline.
