# KNOWN ISSUES

Ranked by severity. Under-reporting here is worse than the bugs themselves, so this
list is deliberately blunt.

---

## 1. The MeritRank decay parameters are NOT exposed per request — the paper's three decay mechanisms are only partly reachable

This is the most important caveat in the project, because the decay mechanisms are the
entire reason for choosing MeritRank over personalised PageRank.

What the engine actually exposes, verified against `service/src/settings.rs` and the
service README:

| Parameter | Exposed how | Per-user? |
|---|---|---|
| `MERITRANK_ALPHA` (walk continuation, 0–1) | service **env var** | No — process-global |
| `MERITRANK_NUM_WALKS` | service **env var** | No — process-global |
| `MERITRANK_ZERO_OPINION_FACTOR` | service **env var** | No — process-global |
| `VSIDS_BUMP` | service **env var** | No — process-global |
| transitivity / connectivity decay | **not exposed at all** — internal to the core crate | No |
| epoch decay | **not exposed at all** | No |

Consequences, stated plainly:

- **There is no per-request or per-user decay knob.** Changing alpha requires
  restarting `mr-service`, and it changes the answer for every user at once.
- The **transitivity and connectivity decay** described in the paper are compiled into
  `meritrank_core` with no runtime surface. We cannot tune them, and we cannot show a
  user what they are set to. We rely on them being active; we cannot demonstrate their
  values from outside.
- **Epoch decay in this product is ours, not the paper's.** It is a recency factor
  applied to paper→paper edge weights at graph-construction time
  (`scripts/build_graph.py::epoch_factor`, half-life 60 years, floored at 0.4). It is
  *not* MeritRank's epoch decay. Because it is baked in at build time it is global and
  cannot be moved per user without a full graph rebuild.

**What the parameter playground therefore does:** it moves **per-context weights**,
which are genuinely per-user and genuinely live (they are applied in
`ranking.compose`, composing separate `mr_scores` calls per context). It does **not**
move alpha, num_walks, or any decay parameter. `POST /api/params` returns **422** for
those rather than accepting them and silently doing nothing.

---

## 2. Contexts are not isolated relation families

The engine replicates every `User→User` edge into every context and ignores the
context declared on such an edge (`state_manager.rs`, verified empirically — see
BUILD_LOG Phase 0). Since papers must be `User` nodes (DECISIONS.md D1.6), this means:

- All paper→paper edges (`cites`, `cited_by`, `couples`, `co_cited`) and all trust
  edges live in **every** context.
- A named context is therefore **"the paper-to-paper baseline + one entity family"**,
  not an isolated family.
- The per-context decomposition shown in the UI is a **marginal** contribution
  (`score(ctx) − score(citation)`), which is a real quantity but is *not* "trust
  arriving purely through topic". The UI must not claim otherwise.
- `coupling` and `cocitation` could not be separate contexts at all, for the same
  reason. They are folded into the baseline and are weightable only at build time,
  not per user.

---

## 3. Uncertainty is leave-one-out, not repeated sampling

The service exposes neither a per-call walk count nor a sampling seed, so we cannot
cheaply draw repeated independent estimates for the same ego and report a true Monte
Carlo standard error. We use **leave-one-out over the trust set** instead, jackknife-
scaled. It answers a different (arguably more useful) question — how much the ranking
depends on any single trust decision — but it is **not** a sampling error bar, and it
will understate pure Monte Carlo noise.

Leave-one-out is only computed for trust sets of size 2–12. Outside that range the API
falls back to a crude proportional band, which is honest but coarse. Single-seed
profiles get a deliberately huge band.

---

## 4. Global merit is an approximation

MeritRank has no ego-free score — every walk starts somewhere. "Global merit" in this
product is the score from a synthetic ego attached uniformly to the 200 most-cited
corpus papers. That is a defensible reference point but it is **not** an objective
measure, and it inherits the citation bias of its seed set.

---

## 5. Corpus coverage

~7,200 full papers and ~52,000 stubs, seeded from the 3,000 most-cited mathematics
works since 1990. This is a demo-scale corpus, not the literature:

- Heavily biased toward highly-cited, English-language, digitally-indexed work.
- Pre-1990 work appears only as snowballed nodes or stubs, so foundational older papers
  are systematically under-represented.
- Stubs have no authors, topics, venues or institutions, so they can only ever be
  reached through citation edges.
- A low score frequently means "thinly represented in OpenAlex", not "untrustworthy".

---

## 6. Distrust is our extension, not the paper's

Negative edge weights are accepted by the engine (VSIDS takes `weight.abs()`, and
`mr_graph` has a `positive_only` flag, implying negatives exist). We encode distrust as
a negative-weight trust edge plus exclusion from rankings. The MeritRank paper does not
define semantics for negative seed edges, so **this is our extension** and its
behaviour under the decay mechanisms is not something the paper guarantees.

---

## 7. Sybil resistance is tested by analogy

MeritRank's sybil tolerance was derived for tokenomic feedback systems where the
attacker pays a cost to create edges. A citation ring is a reasonable analogy but not
an instance of that threat model: citations are free, rings are usually small, and real
citation manipulation is much subtler than a clique. The measured suppression number in
the README is real, but it is evidence about *this graph under this attack*, not a
general guarantee.

---

## 8. Bulk load is global, shared state

`mr_bulk_load_edges` **clears all engine state** and every user shares one graph. So:

- Rebuilding the graph wipes every profile's trust edges. `ranking.ensure_seeded()` is
  idempotent and re-adds them before each read, which covers it, at some cost.
- Two concurrent graph rebuilds would corrupt each other. There is no locking.
- The `/simulate` endpoint mutates a scratch ego on the shared engine; it cleans up
  after itself, but a crash mid-call could leave a stray `Uloo_*` or `Usim_*` node
  behind. These are harmless (they are unreachable from any real ego) but they are
  litter.

---

## 9. Operational

- The stack pins `mr-service` to a static IP (172.28.0.10). Harmless, but it means the
  compose network subnet 172.28.0.0/16 must be free on the host.
- The vendored `meritrank-rust` checkout is **patched** (line endings, and
  `20_pgmer2.sh`). It is not a pristine clone; `git diff` inside `vendor/` will show
  our changes. Documented in DECISIONS.md D2.

---

## 10. Phase 1 Gate 2 is FAILED and shipped that way

**≥90% of full papers should have at least one resolved in-corpus reference. Measured
69.7%; the ceiling for this corpus is 71.2%.**

28.8% of full works arrive from OpenAlex with an empty `referenced_works` list — mostly
books and pre-2000 articles. Of the papers that *do* carry a reference list, 97.9%
resolve at least one in-corpus target.

I deliberately did not make this gate pass, because both available routes (filtering
the seed query on `has_references:true`, or lowering the threshold) would have passed it
by damaging the corpus or by moving the goalposts. See DECISIONS.md D6. **Consequence
for the product:** roughly 2,000 papers, disproportionately books and older work, can
only be reached through *incoming* citations and entity edges, never through their own
bibliography. They are systematically harder to surface than their importance warrants.

## 11. Rankings are dominated by direct citation neighbours

Measured on a 5-seed profile: **19 of the top 20 results were papers directly cited by,
or directly citing, a seed.** ~10,500 papers receive a non-zero score, so the walk does
spread, but the head of the ranking is essentially the seeds' bibliography.

This is partly correct behaviour — `cites` is weighted 1.00 against 0.10–0.60 for
everything else, and a deliberate citation *is* the strongest signal. But it means the
default ranking is closer to "your reading list's references" than to discovery. The
diversity dial on `/recommendations` and the `/blindspots` endpoint exist to counter
this, and they are where the interesting results actually live. A user who only looks at
`/rankings` will find it duller than the system deserves.

The divergence gate passes with Jaccard 0.000 across all three dissimilar seed pairs —
but note that a *perfect* zero is partly an artefact of this same 1-hop dominance:
disjoint seed sets have disjoint bibliographies. It is a real pass, not a strong one.
