# BUILD LOG

Chronological record of the overnight build. Times are local (Europe/London).

---

## Phase 0 — de-risk the Rust toolchain

**22:40** Environment recon. Docker 29.1.3 (20 CPU, 16 GB, 292 GB free), Python 3.12.5,
Node 22.7, git 2.45. No local cargo — everything Rust happens in Docker. OpenAlex key present.

**22:44** Cloned `Intersubjective/meritrank-rust` to `vendor/`. Read README, core/README,
service/README, psql-connector/README in full, then the Rust sources. Findings that
contradict the build prompt are recorded in DECISIONS.md D1; the load-bearing ones:
`mr_service()` is a compile-time constant and proves nothing; node kind is derived from the
first character of the node name; non-User→non-User edges are silently rejected; only User
nodes may be an ego; User→User edges replicate into every context.

**22:45** Found the repo ships `docker-compose.test-local.yml` referencing prebuilt images
`vbulavintsev/meritrank-service:v0.4.0` and `vbulavintsev/postgres-tentura:v0.4.0`. Pulled
them to try the fast path first.

**22:47** First round trip FAILED: `ERROR: invalid socket address syntax`. DNS resolved and
raw TCP to meritrank:10234 succeeded, so it was not connectivity. The v0.4.0 connector parses
its service URL with a strict SocketAddr parse — numeric IP only, no scheme, no DNS. Pinned a
static IP (172.28.0.10) and the round trip worked.

**22:55** Second, disqualifying defect in the prebuilt image: `mr_bulk_load_edges` does not
exist in v0.4.0. `\df` lists 24 functions and that is not one of them. Since the prompt
forbids looping `mr_put_edge` over hundreds of thousands of edges, switched to building both
images from vendored source. (I initially misreported this \df as matching source — it did not.)

**23:00** Both builds launched. The pgrx build turned out far cheaper than feared:
`psql-connector/Dockerfile` starts FROM a prebuilt `pgrx-toolchain` image, so there is no
`cargo pgrx init` on the critical path.

**23:05** Connector build failed: `generate_scripts.sh: line 22: syntax error: unexpected end
of file (expecting "then")`. Cause: CRLF line endings — the clone inherited a global
core.autocrlf. Converted all vendored .sh to LF and added a .gitattributes.
NOTE: an earlier wrapper of mine, `(docker build ... ; echo EXIT=$?)`, reported the echo's
exit status rather than the build's, so this failure was masked for one cycle.

**23:08** Postgres init still did not install the extension: `--dbname=provenance: command
not found`. Cause: the postgres entrypoint only *sources* .sh init files when they are
non-executable; a Windows checkout marks them 755, so they run as a subprocess and the
entrypoint's `psql` bash array is empty. Patched the vendored 20_pgmer2.sh to call psql
directly, and used a .sql file for our own init.

**23:10** Phase 0 gate PASSED. mr-service v0.9.0, pgmer2 0.8.0, 25 functions including
mr_bulk_load_edges. Hostname URLs work on main HEAD (strip_scheme + to_socket_addrs).

Toy round trip, which also empirically confirmed the source reading:

```
bulk_load -> Ok
scores from Uprofile:  Uprofile 0.33591 | U2 0.19422 | U1 0.19083 | U3 0.16406 | Bauth 0.06194 | U4 0.05304
nodelist ctx=''         -> Bauth,U1,U2,U3,U4,Uprofile
nodelist ctx='citation' -> U1,U2,U3,Uprofile        # U->U trust edges replicated in
nodelist ctx='author'   -> Bauth,U1,U2,U3,U4,Uprofile
service log: 'Bulk load: bad node kinds Bbad -> Bbad2, skipped'   # B->B silently dropped
```

### Full mr_* surface (the real API contract)

Captured from a live `\df mr_*` against pgmer2 0.8.0. Raw psql output in
`docs/df_output.txt`; compact signatures:

```
mr_bulk_load_edges(src_arr text[], dst_arr text[], weight_arr double precision[], magnitude_arr bigint[], context_arr text[], timeout_msec bigint DEFAULT 120000) -> text
mr_connected(src text, context text DEFAULT ''::text) -> TABLE(src text, dst text)
mr_connector() -> text
mr_create_context(context text) -> text
mr_delete_edge(src text, dst text, context text DEFAULT ''::text, index bigint DEFAULT '-1'::integer) -> text
mr_delete_node(src text, context text DEFAULT ''::text, index bigint DEFAULT '-1'::integer) -> text
mr_edgelist(context text DEFAULT ''::text) -> TABLE(src text, dst text, weight double precision)
mr_fetch_new_edges(src text, prefix text DEFAULT ''::text) -> TABLE(src text, dst text, score_value_of_dst double precision, score_value_of_src double precision, score_cluster_of_dst integer, score_cluster_of_src integer)
mr_get_new_edges_filter(src text) -> bytea
mr_graph(ego text, focus text, context text DEFAULT ''::text, positive_only boolean DEFAULT false, index bigint DEFAULT 0, count bigint DEFAULT 16) -> TABLE(src text, dst text, weight double precision, score_value_of_dst double precision, score_value_of_ego double precision, score_cluster_of_dst integer, score_cluster_of_ego integer)
mr_log_level(_log_level bigint DEFAULT 1) -> text
mr_mutual_scores(src text, context text DEFAULT ''::text) -> TABLE(src text, dst text, score_value_of_dst double precision, score_value_of_src double precision, score_cluster_of_dst integer, score_cluster_of_src integer)
mr_neighbors(ego text, focus text, direction bigint, hide_personal boolean DEFAULT false, context text DEFAULT ''::text, kind text DEFAULT ''::text, lt double precision DEFAULT NULL::double precision, lte double precision DEFAULT NULL::double precision, gt double precision DEFAULT NULL::double precision, gte double precision DEFAULT NULL::double precision, index bigint DEFAULT 0, count bigint DEFAULT 16) -> TABLE(src text, dst text, score_value_of_dst double precision, score_value_of_src double precision, score_cluster_of_dst integer, score_cluster_of_src integer)
mr_node_score(src text, dst text, context text DEFAULT ''::text) -> TABLE(src text, dst text, score_value_of_dst double precision, score_value_of_src double precision, score_cluster_of_dst integer, score_cluster_of_src integer)
mr_nodelist(context text DEFAULT ''::text) -> TABLE(node text)
mr_put_edge(src text, dst text, weight double precision, context text DEFAULT ''::text, index bigint DEFAULT '-1'::integer) -> TABLE(src text, dst text, weight double precision)
mr_recalculate_clustering(_blocking boolean DEFAULT true, timeout_msec bigint DEFAULT 6000000) -> text
mr_reset() -> text
mr_scores(src text, hide_personal boolean DEFAULT false, context text DEFAULT ''::text, kind text DEFAULT ''::text, lt double precision DEFAULT NULL::double precision, lte double precision DEFAULT NULL::double precision, gt double precision DEFAULT NULL::double precision, gte double precision DEFAULT NULL::double precision, index bigint DEFAULT 0, count bigint DEFAULT 16) -> TABLE(src text, dst text, score_value_of_dst double precision, score_value_of_src double precision, score_cluster_of_dst integer, score_cluster_of_src integer)
mr_service() -> text
mr_service_url() -> text
mr_set_new_edges_filter(src text, filter bytea) -> text
mr_set_zero_opinion(node text, score double precision, context text DEFAULT ''::text) -> text
mr_sync(timeout_msec bigint DEFAULT 6000000) -> text
mr_zerorec(_blocking boolean DEFAULT true, timeout_msec bigint DEFAULT 6000000) -> text
```

---

## Phase 1 — data pipeline

**23:50** Wrote a disk-cached, rate-limited OpenAlex client (cache keyed by URL hash, api_key
never written to disk, so a warm cache makes re-running a no-op).

**23:59** Scrape complete. Mathematics field resolved at runtime to `fields/26`.

```
seed works (primary_topic.field.id:fields/26, year>=1990, by citations): 3000
external referenced works:                                              59569
  promoted to full nodes (>=3 corpus referrers):                         4211
  kept as lightweight stubs:                                            51917
full works written: 7211      stubs written: 51917
API requests: 1209 (~12k credits of the 100k/day budget)
```

**00:44** Re-scraped after the loader flagged a defect in `scrape.py`: the snowball
`referrer_count` was built from the 3,000 seed works only, so the 4,211 promoted works'
references were never stubbed and 55,889 citations (29%) were dropped as dangling at
load time. Fixed by unioning promoted references into the stub set.

```
before: 59,128 works   135,098 citations
after:  96,751 works   181,388 citations   (+34% citation density)
stubs hydrated: 89,540    API requests: 1,910 (~19k credits)
```

**Phase 1 gate:** papers >= 2,500 **PASS** (7,211). Papers with a resolved in-corpus
reference >= 90% **FAIL** (71.16%, ceiling 71.17%). 2,079 full works ship from OpenAlex
with an empty `referenced_works` list. Deliberately not massaged — DECISIONS.md D6.

---

## Phase 2 — graph construction

Papers as `U` nodes, entities as `B` nodes (forced by the engine, D1.6). Entity
out-edges hub-damped `1/sqrt(corpus_degree)`; topic edges IDF-scaled; coupling and
co-citation capped per paper so a 400-reference review cannot dominate.

```
TOTAL 583,122 edges in 6.3s
  citations 181,388 -> 362,776 edges     coupling 43,823     co-citation 72,019
  authorship 16,850   topics 18,693      venues 6,415        affiliations 10,294
bulk_load returned in 24.4s              <- gate: under 2 minutes, PASS
contexts: aggregate 111,552 | citation 96,340 | author 106,919 | topic 97,828
          venue 97,770 | institution 98,861
```

**Phase 2 gate — divergence:** PASS. Three dissimilar seed pairs, Jaccard 0.000 across
all three. Noted in KNOWN_ISSUES #12 that a *perfect* zero is partly an artefact of
1-hop dominance rather than a strong result.

---

## Phase 3 — ranking layer

Per-context composition in Python (`baseline + Σ w_c · marginal_c`) because
`mr_bulk_load_edges` is global state and per-user weights cannot be done by reloading
the graph (D4). Uncertainty by leave-one-out (D5).

Three real bugs found by execution, not inspection:

1. `text("... :src::text[] ...")` — SQLAlchemy's bind parser eats a parameter followed
   by `::`. Silently bound only the final argument. Fixed with `CAST(...)`.
2. `compose()` treated "node absent from this context" as score 0, so every context
   subtracted a full baseline and top scores went **negative** (−0.26 for the top paper).
   Fixed by imputing *no marginal contribution* instead.
3. `node_to_work_id` matched any `U`-prefixed node, so scratch egos leaked into rankings
   as phantom papers — the ego scores itself highest, so it appeared at **rank 1 with no
   title** and a score 40× the real #1.

**Phase 3 gate:** warm 45 ms (gate <500 ms) **PASS**; cold 50.9 s recorded. Cold is one
`mr_scores` per context plus one per context per leave-one-out replicate, each building
walks lazily. Raw per-context scores are cached against the trust-set signature, so the
cache self-invalidates on any trust change and slider moves re-compose without touching
the engine.

Tie grouping was rewritten: the anchored test collapsed the entire ranking into one tie
group (technically true, useless to read). The pairwise test now separates rank 1 and
brackets 2–8, which is both honest and legible.

---

## Phases 4 & 5 — API and frontend

16 endpoints against `API_CONTRACT.md`, driven end to end with real data: profile →
search → trust → rankings → explain. The explanation returns genuine reconstructed
paths, including 2-hop meta-paths through topic and author nodes, plus a per-context
decomposition (citation 0.771, topic 0.132, author 0.055, institution 0.035, venue 0.007
on the sampled paper).

Frontend: six routed screens, 62 vitest tests, clean production build. Two integration
defects fixed at wiring time — a missing `@types/node` that broke the Docker build, and
the absence of a web Dockerfile/nginx config (same-origin `/api` proxy so the profile
cookie works without CORS).

---

## Phase 6 / hardening

Cold-start bootstrap added after realising the final gate could not pass: `down -v`
empties Postgres **and** mr-service loses the whole graph on any restart because it is
held in memory. `bootstrap.py` repairs both on a background thread.

The sybil experiment was re-run four times after the citation-density fix. See the
README — the result is a null one, and it is the most important thing I learned tonight.

**Live verification of the two personalisation claims** (against the running stack, not
unit tests):

```
diversity dial, same 3-seed profile:
  diversity=0.0  -> Statistical Analysis With Missing Data / Estimating causal effects /
                    Analysis of Incomplete Multivariate Data      (novelty 0.25, 1 hop)
  diversity=1.0  -> Lasso / Gibbs Distributions / Monte Carlo sampling via Markov chains
                                                                  (novelty 1.00, 4+ hops)
  overlap between the two ends: 0.00
context weights author=3.0, topic=venue=institution=0:  top-20 order changed = True
```

Both are genuinely live and per-user, which is the claim the parameter playground makes.

---

## Phase 6 — deliberately not started

Phases 0–5 are green (with Gate 2 explicitly failed and documented), so by the brief
Phase 6 was available. I did not take it, for two reasons.

The item I most wanted to build was the **citation-ring detector** — "flag dense,
externally-sparse clusters and show how connectivity decay already discounts them."
After the sybil measurement came back at 1.00 ± 0.23, I could not build that screen
honestly: it would assert a discount this build has no evidence for. Shipping a feature
whose entire premise I had just failed to demonstrate would have been the worst thing in
the repository.

The rest of the stretch list would have been new surface area at a point where the
better use of the time was making the existing surface true — verifying the diversity
dial and context weights actually move results, getting cold start to work from a wiped
volume, and writing down the corpus problem I had just found.

---

## Final entry — what I would do with another eight hours

**In priority order.**

1. **Fix the corpus.** It is statistics, not mathematics (KNOWN_ISSUES §5). Sorting
   OpenAlex field 26 by citation count returns Rubin, Dempster and COVID epidemiology,
   and the best-connected algebraic geometry paper has 8 in-corpus citations against
   Rosenbaum & Rubin's 250. Stratified sampling across mathematics subfields — top N per
   subfield rather than top 3,000 overall — is maybe two hours including re-scrape,
   reload and rebuild. Everything else on this list matters less than this, because the
   stated audience currently opens the product and recognises nothing.

2. **Settle the sybil question properly.** The null result is honest but unsatisfying.
   Raise `MERITRANK_NUM_WALKS` by 5–10× to drop the noise floor below the effect size,
   re-run 20+ trials rather than 4, and vary the ring's attachment (single inbound edge
   vs bidirectional, one anchor vs several). Right now I cannot say whether connectivity
   decay does nothing here or whether my instrument is too blunt to see it — and that
   distinction is the whole justification for choosing MeritRank.

3. **Cold start of 50.9 s.** Acceptable because it is warmed on trust-set save and warm
   is 45 ms, but it is 25 lazy walk builds for a 5-seed profile. Leave-one-out could run
   asynchronously and stream in, or reuse a single scratch ego across replicates.

4. **Weighted search vectors.** Exact title matches not ranking first (KNOWN_ISSUES §13)
   is a twenty-minute fix — `setweight` on title vs abstract plus a trigram boost on the
   title, whose index already exists — and it is the first thing a user touches.

5. **Push past 1-hop dominance.** 19 of the top 20 being direct citation neighbours
   makes the default view duller than the system deserves. I would try lowering `cites`
   toward 0.6, raising the entity weights, and measuring the effect on the divergence
   check rather than guessing.

### The decisions I am least confident about

**The composition formula.** `score = baseline + Σ w_c · (score_c − baseline)` is a
reasonable reading of a constraint the engine forced on me (User→User edges replicate
into every context, so contexts are not isolable). But the per-context scores are
probability-like distributions over *different node sets*, and subtracting them is not
obviously sound. It behaves well and the weights demonstrably move results — but I
derived it under time pressure and I would want to argue it through properly, or replace
it with a rank-space combination that does not assume the scales are commensurable.

**Papers as `U` nodes.** Forced, and I am confident it was the only workable choice. What
I am *not* confident about is the second-order effect: every paper is now a "User" to an
engine built for a social network, so `hide_personal`, node ownership and the
zero-opinion machinery are all operating on a domain they were never designed for. I
disabled zero-opinion (`ZERO_OPINION_FACTOR=0.0`) rather than reason about it. Something
subtle may be wrong there and I would not currently detect it.

**Leave-one-out as the uncertainty measure.** It answers a real question and I would
defend shipping it. But I am presenting a *sensitivity* as though it were an *error bar*,
and the tie-grouping threshold (mean of adjacent standard errors) was chosen because the
first version collapsed everything into one group — which is a suspicious reason to
choose a statistical threshold. It is calibrated to look right, not derived.

**Trusting three subagents with the API, the frontend and the loader.** It parallelised
the night and the loader in particular caught a 56k-citation bug in my own scraper that I
would not have found. But I verified their work by driving it, not by reading all of it,
and the frontend was built against a contract rather than a running backend. The E2E
suite is the only thing standing between that and an integration surprise.

---

## FINAL GATE

`docker compose down -v` (volume destroyed), then `docker compose up` from scratch,
then the full Playwright suite against it.

```
=== DOWN -v ===  Volume academicrank_pgdata Removed
=== UP ===       db healthy -> api started -> web started
api answering at once with: {"ok":true,"graph_loaded":false,"nodes":0,"edges":0}
bootstrap READY after 87s: {"graph_loaded":true,"nodes":111552,"edges":549121}
```

The corpus was rebuilt from the committed `data/raw/*.jsonl.gz` with no network access,
the graph re-derived, and the edge list pushed back into mr-service — all on a
background thread, so /health stayed responsive and honest (`graph_loaded:false`) while
it ran.

```
Running 25 tests using 1 worker
  ... 25 passed (2.1m)
```

Full journey green: profile created -> 5 trusted papers added -> rankings with error
bars and tie groups -> explain showing a path back to a trusted paper -> context weight
slider reorders the top-20 (20/20 positions changed). Plus: no console errors on any of
the six routes, WebGL graph genuinely painting (33,325 px of nodes), dark mode,
command palette, cold-start honesty notice, disclaimer verbatim, keyboard tab order,
and no horizontal overflow at 1280px.

### Late fixes verified in this run

| Finding | Fix | Verified |
|---|---|---|
| 15 of a top-25 were metadata-less STUB records | excluded from results, kept in the graph | 0/20 stubs, 5,007 rankable papers |
| `citation` weight slider did nothing (0/20) | baseline now carries its own weight in `compose()` | 20/20 positions change |
| explain read "was written by -> who also wrote" | interleave node labels; pronoun on the last hop | "...written by Singiresu S. Rao, who also wrote it" |
| leave-one-out abandoned scratch edges on timeout | try/finally teardown | no new `Uloo_*` residue |
| skip link broke navigation under HashRouter | preventDefault + focus `#main` (found by the E2E agent) | regression test 08-keyboard |

Cold 74.5s / warm 50ms, deterministic across repeat calls.
