# KNOWN ISSUES

Ranked by severity. Under-reporting here is worse than the bugs themselves, so this
list is deliberately blunt.

---

## 1. Of the paper's three decay mechanisms, exactly ONE exists in this build — and it is alpha

**Corrected 2026-07-30 against the vendored source.** The earlier version of this
entry said transitivity and connectivity decay were "compiled into `meritrank_core`
with no runtime surface" and that "we rely on them being active". That was wrong in
both directions: one of them is fully exposed, and the other two are absent.

| Paper mechanism | In this build? | Where |
|---|---|---|
| transitivity decay | **YES — it IS `MERITRANK_ALPHA`** | `core/src/graph.rs:359`: the walk continues with probability α before each step, so a node at distance d contributes ∝ α^d. There is no separate mechanism. |
| connectivity decay | **NO — absent** | No connectivity/β/κ machinery anywhere in the crate. Walk counters are keyed `(ego, node)` with no per-intermediary counts, so the paper's `T_ij(k)/T_ij` estimator is not even computable from what the engine stores. |
| epoch decay | **NO — absent** | VSIDS is a different, time-ordered edge-weight mechanism — and it is inert here anyway, because `meritrank.py` pins `magnitude=0` on every edge. |

Note the α inversion when reading the paper: it decays by `(1−α_paper)^d`, the engine
continues with probability `α_engine`, so `α_engine = 1 − α_paper`. The paper's
recommended 0.1–0.2 corresponds to the engine's 0.8–0.9 — our `MERITRANK_ALPHA: 0.85`
is squarely the paper's recommended operating point, not a deviation from it.

Consequences, stated plainly:

- **What this build computes is personalised PageRank with unique-visit-per-walk
  counting** (`core/src/counter.rs`: each walk contributes at most once per node,
  which suppresses 2-cycle and hub re-entry mass — a real, load-bearing anti-hub
  property, and the one genuine behavioural difference from textbook PPR). Claims
  that this product's ranking benefits from the paper's connectivity or epoch decay
  are false and must not be made.
- **There is no per-request or per-user decay knob.** Changing alpha requires
  restarting `mr-service`, and it changes the answer for every user at once.
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

Leave-one-out runs for every trust set of 2 or more seeds. Above 12 seeds it leaves
out a deterministic, evenly-spaced subsample of 12 seeds rather than every seed in
turn, with the jackknife inflation still scaled by the true trust-set size. (Fixed
2026-07-30: previously the API silently fell back to the crude proportional band for
13+ seeds — i.e. across most of the 10–50 seed range this product targets — while
still labelling the result `leave_one_out`.) Single-seed profiles get a deliberately
huge band, now honestly labelled `proportional_fallback`, and the UI copy says it is a
placeholder rather than a measurement.

---

## 4. Global merit is an approximation

MeritRank has no ego-free score — every walk starts somewhere. "Global merit" in this
product is the score from a synthetic ego attached uniformly to the 200 most-cited
corpus papers. That is a defensible reference point but it is **not** an objective
measure, and it inherits the citation bias of its seed set.

---

## 5. The corpus is not really mathematics -- it is statistics and biostatistics

**This is the most serious product-level problem in the build, and I found it late.**

The corpus was built exactly as specified: OpenAlex field Mathematics
(`primary_topic.field.id:fields/26`), `publication_year >= 1990`, sorted by
`cited_by_count:desc`. What comes back is dominated by statistical methodology, because
OpenAlex files statistics and biostatistics under Mathematics and those papers are cited
by the entire applied scientific literature at a volume pure mathematics never reaches.

Measured composition of the 7,211 full papers, by topic:

| Topic | Papers |
|---|---|
| Statistical Methods and Inference | 1,191 |
| Statistical Methods and Bayesian Inference | 1,151 |
| Advanced Statistical Methods and Models | 970 |
| Advanced Causal Inference Techniques | 666 |
| Statistical Methods in Clinical Trials | 502 |
| Bayesian Methods and Mixture Models | 376 |
| **COVID-19 epidemiological studies** | 331 |
| Advanced Optimization Algorithms Research | 308 |

The single most-cited paper in the corpus is Rosenbaum & Rubin (1983) on propensity
scores, with 250 in-corpus citations. By contrast the best-connected algebraic geometry
paper has **8**.

**Consequence:** the stated audience of this product is working mathematicians, and a
pure mathematician would open it and recognise almost nothing. Searching "algebraic
geometry" returns real results, but they sit in a graph with almost no pure-mathematics
neighbourhood, so their personalised rankings are thin and the meta-paths are weak. The
demo is best driven from statistics/causal-inference seeds, which is what DEMO.md does --
that is making the best of the corpus, not a design choice.

**The fix, not applied:** sort-by-citations globally is the culprit. Stratified sampling
would fix it -- resolve the *subfields* under Mathematics and take the top N papers
within each (algebra, topology, number theory, analysis, geometry, logic, ...) rather
than the top 3,000 overall. That yields a corpus a mathematician recognises, at the cost
of lower absolute citation counts and a sparser citation graph. It needs a re-scrape, a
reload and a graph rebuild. I chose not to start that with the end-to-end suite already
running against the current dataset and no one awake to recover a half-migrated state --
but it is the first thing I would change, and it is a bigger deal than anything else on
this list.

---

## 6. Corpus coverage (general)

~7,200 full papers and ~52,000 stubs, seeded from the 3,000 most-cited mathematics
works since 1990. This is a demo-scale corpus, not the literature:

- Heavily biased toward highly-cited, English-language, digitally-indexed work.
- Pre-1990 work appears only as snowballed nodes or stubs, so foundational older papers
  are systematically under-represented.
- Stubs have no authors, topics, venues or institutions, so they can only ever be
  reached through citation edges.
- A low score frequently means "thinly represented in OpenAlex", not "untrustworthy".

---

## 7. Distrust is our extension, not the paper's

Negative edge weights are accepted by the engine (VSIDS takes `weight.abs()`, and
`mr_graph` has a `positive_only` flag, implying negatives exist). We encode distrust as
a negative-weight trust edge plus exclusion from rankings. The MeritRank paper does not
define semantics for negative seed edges, so **this is our extension** and its
behaviour under the decay mechanisms is not something the paper guarantees.

---

## 8. Sybil resistance

MeritRank's sybil tolerance was derived for tokenomic feedback systems where the
attacker pays a cost to create edges. A citation ring is a reasonable analogy but not
an instance of that threat model: citations are free, rings are usually small, and real
citation manipulation is much subtler than a clique. The measured suppression number in
the README is real, but it is evidence about *this graph under this attack*, not a
general guarantee.

---

## 9. Bulk load is global, shared state

`mr_bulk_load_edges` **clears all engine state** and every user shares one graph. So:

- Rebuilding the graph wipes every profile's trust edges. `ranking.ensure_seeded()` is
  idempotent and re-adds them before each read, which covers it, at some cost.
- Two concurrent graph rebuilds would corrupt each other. There is no locking.
- The `/simulate` endpoint mutates a scratch ego on the shared engine; it cleans up
  after itself, but a crash mid-call could leave a stray `Uloo_*` or `Usim_*` node
  behind. These are harmless (they are unreachable from any real ego) but they are
  litter.

---

## 10. Operational

- The stack pins `mr-service` to a static IP (172.28.0.10). Harmless, but it means the
  compose network subnet 172.28.0.0/16 must be free on the host.
- The vendored `meritrank-rust` checkout is **patched** (line endings, and
  `20_pgmer2.sh`). It is not a pristine clone; `git diff` inside `vendor/` will show
  our changes. Documented in DECISIONS.md D2.

---

## 11. Phase 1 Gate 2 is FAILED and shipped that way

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

## 12. Rankings are dominated by direct citation neighbours

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

---

## 13. Search does not rank exact title matches first — FIXED 2026-07-30

Was: `ts_rank` over a concatenated title+abstract vector treated a title hit and an
abstract hit alike, and long abstracts diluted the signal — searching
`Maximum Likelihood from Incomplete Data` returned that paper **third**, and
`central role propensity score observational` did not return Rosenbaum & Rubin in
the top 3 at all.

Fixed by the weighted vector alone (migration `a9d4f0c1b3e5`:
`setweight(title,'A') || setweight(abstract,'B')`; ts_rank scores A=1.0 vs B=0.4).
The trigram-boost half of the originally proposed fix was **not** applied: both
recorded failures pass without it, so it would have been an untested ranking term.
Gates live in `api/tests/test_search_ranking.py` — 20-title exact-match property
test (≥18/20 top-1) plus both regressions pinned.

Residual caveat: the corpus contains near-duplicate records of some classic papers
(three copies of Rosenbaum & Rubin 1983 rank 1-2-3 for its own title). That is a
corpus-quality issue (§5 territory), not a ranking one.

---

## 14. The engine serialises work; this stack serves one user

`mr-service` processes requests one at a time and builds an ego's random walks lazily on
first read. A new profile's first ranking is **40-90 seconds** of walk building
(measured 40.6s, 52.4s, 56.7s, 71.1s, 74.5s across runs), and concurrent cold starts
queue behind each other rather than running in parallel.

This was measured the hard way: probing the engine while the end-to-end suite was
driving it returned 0 rows and a 10-minute timeout, both of which looked like data
corruption and were pure contention. Re-run in isolation the same calls answered in
~0.1s.

**Consequence:** this is a single-user demo. Two people hitting it with fresh trust sets
at the same time will both wait. Serving several would need a request queue with
per-ego workers, or a much lower `MERITRANK_NUM_WALKS`, and neither was attempted.
It is warmed on trust-set save so the cost lands where the user expects to wait.

---

## 15. Not covered by any test

Recorded rather than quietly omitted. Reachable in the UI and exercised by unit tests,
but never driven end to end: **distrust edges**, **BibTeX import**, and the
**simulate / "Preview impact" dialog**. Graph node-click re-centring is also unasserted
-- testing it means computing a node's screen position from sigma's camera, which tests
the renderer rather than the product; the keyboard-accessible node list that performs
the same action *is* asserted.

---

## 17. Uploads write into a graph shared by every profile; exclusion is display-level only

`POST /api/uploads/{id}/confirm` writes real `citations`/`graph_edges` rows and engine
edges into the ONE graph all profiles share. The `include_user_uploads` toggle
(default off) removes uploaded `UL…` works from rankings, search, recommendations,
blindspots and the graph view — but walks still propagate through uploaded edges for
everyone, so an excluding user's scores are still perturbed by uploads *existing*.
This cannot be fixed on this engine (one graph; U→U edges replicate into every
context). It is bounded — hundreds of edges among ~550k, under Monte Carlo score
estimates — but the system must never claim exclusion isolates you, and the UI
carries that caveat verbatim.

## 18. A bibliography is not an endorsement

Upload-seeded trust defaults to 3/5 ("I cited this"), is labelled with its source
upload, and is undoable as a batch. That is mitigation, not a claim the semantics
are exact: people cite work they refute.

## 19. Nothing measurably discounts coordinated or self-citation-heavy uploads

The sybil suppression measured on this graph was 1.00 ± 0.23 — absent. Labelling
(`source='user_upload'`, self-citation badges in review) and default-exclusion are
the actual defence. A citation ring uploaded as PDFs would enter the graph like any
other upload.

## 20. Bibliography recall degrades hardest exactly where the corpus is weakest

Undated book bibliographies — pre-2000 monographs — fail the extraction year gate
and are refused to review rather than parsed (deliberate: the failure mode is
review, never silent mis-trust). Combined with issue 11, older literature is
doubly under-served: harder to parse in, harder to reach in the graph.

## 21. One confirmed upload globally invalidates ranking caches

Confirm bumps `graph_meta.version`, which every score cache keys on: every other
profile's next read is a cold one (40–90 s worst case). Chosen deliberately over
serving scores computed against a graph that no longer exists.

## 22. Engine node names from deleted uploads persist until restart

`mr_delete_edge` removes edges (verified: all contexts in one call), but the engine's
node registry never shrinks: an undone upload's `UL…` node NAME lingers until the
next mr-service restart or full rebuild. Harmless — no edges, unreachable by walks —
but visible in `mr_nodelist`, and it is litter.

---

## 16. ~~Alembic is present but never runs~~ — FIXED in the upload build's Phase 0

Fixed 2026-07-29: `alembic upgrade head` now runs from the api lifespan hook before
`create_all`, stamping legacy databases at the initial revision first (DECISIONS D7).
Verified live: the Phase-2 and Phase-3b column additions reached the running database
on container restart with no manual step. Original finding kept below for the record.

Found during adversarial review of the upload-feature spec. `api/Dockerfile` copies
`alembic.ini` and `alembic/` into the image, but nothing — no compose command, no
entrypoint, no startup hook — ever executes `alembic upgrade head`. The live schema
comes entirely from `Base.metadata.create_all()`, which creates missing *tables* but
never adds *columns* to existing ones. Consequence: any future column addition works on
a fresh `docker compose down -v` and silently fails on every existing database — the
worst possible failure shape. Fix scheduled as stage 0 of the upload feature
(`docs/superpowers/specs/2026-07-29-own-papers-bibliography-trust-design.md`), but it
is a defect of the current app independent of that feature.

---

## 16. Degree-1 entities are excluded from the ranking graph

Measured (experiments doc E1/E2): 67.8% of entity nodes -- authors, venues,
institutions, topics attached to exactly ONE corpus paper -- cannot carry trust
between papers by construction. A walk entering one can only bounce back to its
source, and 36.5% of all entity-hop mass was absorbed this way for zero
transmitted signal. Since 2026-07-30 `build_graph.py` drops them from the graph.

Consequences: they no longer appear in `/explain` paths or the graph explorer
(they carried no trust, so no path through them was ever load-bearing), but they
remain in Postgres and still render on paper detail pages. Takes effect at the
next graph rebuild.
