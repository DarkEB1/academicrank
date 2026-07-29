# API implementation notes

Decisions taken while building `api/`, and the things that could not be made real.
Everything here was established by running against the live stack, not by reading docs.

---

## N1. Endpoints that reject rather than pretend

The product rule in `config.py` is "we never expose a slider that does nothing". These
are the places where the API enforces it with a 422 instead of accepting a value and
quietly discarding it.

### N1.1 `POST /params`: `alpha`, `num_walks`, `epoch_half_life_years` → 422

* `alpha` and `num_walks` are read by the Rust service from `MERITRANK_ALPHA` /
  `MERITRANK_NUM_WALKS` **at process start**. `mr_scores()` takes neither as an
  argument — there is no per-request or per-ego override anywhere in the 25-function
  `mr_*` surface. A per-profile value could only ever be decoration.
* `epoch_half_life_years` is ours, not the engine's: `scripts/build_graph.py` bakes it
  into edge weights at graph-construction time. Honouring it per profile would mean
  rebuilding and re-loading the entire 583k edge list per user. It is a build-time
  flag, so the API refuses it as a request parameter.

`context_weights` **is** honoured: it flows into `ranking.compose()`, which is what
actually produces the composed score. The endpoint proves this by returning a
`preview` — the top of the ranking recomputed under the weights just stored. That
field is an addition to the contract's "→ the stored params"; it is additive, so a
client that ignores it is unaffected.

### N1.2 `context=coupling` and `context=cocitation` → 422

`API_CONTRACT.md` lists eight values for the `context` query parameter. Only six of
them are real.

`build_graph.py` emits `couples` and `co_cited` as **paper→paper** edges. Papers are
`U` (User) nodes — they have to be, per DECISIONS.md D1.6 — so those are `User→User`
edges, and D1.5 is explicit that the engine replicates every `User→User` edge into
*every* context, ignoring the context declared on the edge. There is consequently no
subgraph in the engine containing coupling edges and not containing citation edges.
`mr_scores(context => 'coupling')` would return either nothing or the whole baseline
mislabelled.

Both are rejected with a message explaining this and pointing at `context=citation`,
which is the baseline they are part of. Returning a plausible-looking ranking here
would have been the single most misleading thing in the API.

The same reasoning rejects `coupling`/`cocitation` keys in `context_weights`.

---

## N2. Percentiles and `disagreement`

`disagreement` is the normalised spread `max − min` across three percentile ranks, so
it is in `[0, 1]` by construction. The reference class differs per component, which is
deliberate:

* **trust** and **global merit** — percentile *within the candidate pool*, i.e. the
  papers the ego reaches with a non-zero score. A corpus-wide percentile for a
  personalised score is meaningless: the overwhelming majority of 96,751 works score
  exactly 0 for any given ego, so every real result would sit at the 99th percentile.
* **citations** — corpus-wide over non-stub works, since it is ego-independent. This
  matches `ranking.percentile_of` so the paper-detail page and the list endpoints
  agree.

---

## N3. Novelty is a real graph distance

`recommendations.novelty` is BFS hop count from the trust set over `graph_edges` —
the same edge list the scores were computed from — normalised by a 4-hop horizon.
Unreachable within 4 hops scores 1.0.

Each BFS level is capped at 30,000 newly discovered nodes, taken in descending edge
weight. Without a cap, level 4 touches most of the graph. With the original 6,000 cap
the expansion starved at depth 2 and almost everything reported as "unreachable",
which made the dial nearly binary; 30,000 leaves levels 1–3 complete in practice.

The blend is `(1 − d)·trust_pct + d·(global_pct × novelty)`. At `d = 0` this is exactly
the trust ranking (verified in `test_recommendations_diversity_dial_moves_results`,
which asserts the first five items equal `/rankings`). At `d = 1` it is high global
merit *times* distance — distant-and-obscure is noise, and near-and-famous is what the
user would have found anyway, so only the product is informative.

---

## N4. Diversity normalisation

`max_entropy` is `log(min(#observations, #categories in corpus))`, not
`log(#categories in corpus)`.

With five trusted papers you cannot touch more than five distinct topics, so
normalising by the ~4,500 topics in the corpus would report every small trust set as a
near-total echo chamber regardless of how varied it actually is. Bounding by the
number of observations asks the answerable question: *given how much you have told us,
how evenly is it spread?*

`echo_chamber_score` is `1 − mean(normalised entropy)` over the dimensions that have
any observations. With an empty trust set it is `0.0` and the `message` says the
figure is not computable rather than implying perfect diversity.

---

## N5. Caching, and why it is not a shortcut

A cold ranking is expensive: `ranking.rank_profile` runs one full pass plus one
leave-one-out replicate per seed, each pass being one `mr_scores` call per context.
For a 6-seed profile that is 35 engine calls and ~90 s wall clock.

Two cache layers, both keyed on inputs rather than on time:

1. `ranking._CACHE` (owned by `ranking.py`) — raw per-context scores, keyed on the
   trust signature.
2. `services._pool_cache` — the derived pool (percentiles, global merit, ordering),
   keyed on `(profile, trust signature, exclude_trusted, graph generation, weights)`.

Neither is a TTL guess about staleness: any change to the trust set or the weights is
a different key.

**Graph generation.** `ranking.py`'s cache keys on the trust set alone, which cannot
see a `build_graph.py` reload — and a reload calls `mr_bulk_load_edges`, which clears
*all* engine state and invalidates every cached score. `services._check_generation`
uses `max(graph_edges.id)` as a generation marker and clears both layers, plus the
global-merit and BFS-distance caches, when it moves. This was not hypothetical: the
corpus was reloaded underneath the API during development.

**Two invalidation verbs, on purpose.** `invalidate_pool` drops only the composed pool;
`invalidate_scores` also drops `ranking._CACHE`. Weight changes use the former, because
raw per-context scores do not depend on the weights and re-composing them is pure
arithmetic — clearing them on a `/params` call forced a full engine recomputation for
work that should have been microseconds. Trust changes use the latter, because the
trust set is what those scores are computed *from*.

**Single-flight.** A background warm and the user's own read routinely ask for the same
pool at the same instant. `build_pool` gates each key so the second caller waits and
then reads the finished result instead of paying the same multi-minute cost again.
Verified directly: 8 concurrent callers produce exactly 1 `rank_profile` invocation and
share one `Pool`.

`POST /trust` schedules a warm that pays the cold cost off the request path, so the
user's next read is served from cache.

**Runtime, measured.** A cold 6-seed ranking is ~35 `mr_scores` calls (one per context,
for the main pass plus one leave-one-out replicate per seed) over a 111k-node / 549k-edge
graph, and the engine builds 10,000 walks per new ego. That is ~60 s idle and several
minutes under concurrent load; cached reads are ~0.1 s. `/simulate` is the heaviest
endpoint, because the counterfactual needs a full scratch-ego ranking with its own
replicates — `before` is taken from the cached pool precisely so that cost is paid once
rather than twice. The integration suite is correspondingly slow (tens of minutes); that
is the price of testing against a real Monte-Carlo engine rather than a mock.

---

## N6. Engine behaviours found by running it

### N6.1 `Service returned Fail` is transient, and it poisons the transaction

While the service builds a fresh ego's walks, a concurrent `mr_*` call can come back as
`Service returned Fail` — the connector's catch-all, with nothing logged service-side.
Two consequences, both handled:

* It is transient, so `services.engine_retry` retries with backoff rather than 500ing.
* A failed `mr_*` call **aborts the surrounding Postgres transaction**. Any code that
  swallows the exception must `rollback()`, or every later statement on that session
  fails with `InFailedSqlTransaction`. This caused a real bug in `POST /trust` (removing
  a trust entry) and in the simulation teardown loop; both now roll back explicitly, and
  the trust-row delete is committed *before* the best-effort engine delete so the two
  never share a transaction.

### N6.2 A `Session.commit()` invalidates a captured `MeritRank`

`MeritRank` wraps a `Connection`. `Session.commit()` and `.rollback()` return that
connection to the pool, so an adapter built before a commit and used after it raises
`ResourceClosedError`. The adapter is therefore rebuilt at each use site rather than
held in a local. This is easy to reintroduce and was the cause of a 500 on `/rankings`.

### N6.3 `mr_delete_node` does not un-register the node name

Deleting a scratch ego removes its edges but the name stays in `mr_nodelist()` for the
life of the service process — the node registry assigns ids permanently. The
simulation cleanliness test therefore asserts that no scratch **edge** survives, which
is what actually matters for non-destructiveness; asserting on `mr_nodelist` would fail
for a correctly cleaned-up simulation.

`ranking._leave_one_out` leaves a handful of `Uloo_*` edges behind on its scratch ego
in some paths. That file is owned elsewhere and the residue is on a scratch node that is
never used as an ego for user-facing results, so it does not affect any response.

### N6.4 A long warm must not be a FastAPI `BackgroundTask`

A background task runs *inside* the ASGI call, so the server holds that task open until
it finishes — and a warm is minutes of engine time. Worse, Starlette's `TestClient`
waits for background tasks, so a fixture that trusts six papers serialised six full
warms before its first assertion (a trust POST took minutes instead of 0.36 s).
`services.schedule_warm` uses a detached daemon thread instead.

Warming is also debounced (3 s) per profile: building a trust set is a burst of
single-paper POSTs, and warming eagerly on each one queued six increasingly-stale full
rankings, each invalidating the work the previous one had just done.

### N6.5 `SET LOCAL` cannot take a bind parameter

`SET LOCAL pg_trgm.similarity_threshold = :t` is a syntax error. The parameterisable,
transaction-scoped equivalent is
`SELECT set_config('pg_trgm.similarity_threshold', :t, true)`.

Relatedly, with SQLAlchemy `text()` on psycopg 3 the trigram operator is written as a
single `%` — doubling it to `%%` reaches Postgres literally and fails with
`operator does not exist: text %% unknown`.

---

## N7. Smaller decisions

* **`me` as a path id.** `/api/profiles/me/rankings` works as an alias for the
  authenticated profile, so the client need not interpolate its own id everywhere. A
  path id belonging to a *different* profile is 403.
* **Bibtex import adds to the trust set.** The contract's `added` field only makes
  sense if the import does something. Importing a bibliography is a statement of trust,
  so matched entries are added at strength 3; entries already present keep whatever
  strength the user set by hand. The title-match threshold is 0.55 trigram similarity —
  deliberately high, because a wrong match silently poisons a trust set, which is worse
  than reporting the entry unmatched. DOI is tried first and wins outright.
* **Subgraph context filtering mirrors the engine.** For a named entity context the
  response contains `citation ∪ that family`, because that is what the engine's named
  context actually holds (D1.5) — not just the family's own edges, which would show a
  graph that never produced any score.
* **The profile node is synthesised into `/subgraph`.** Trust edges are per-user and so
  are never in `graph_edges`; without them the visualisation shows a graph with no
  starting point.
* **`/rankings.total`** is the size of the candidate pool (papers the ego reaches),
  capped by `services.POOL_FETCH`, not the size of the corpus.
* **`include_stubs`** exists on `ranking.rank_profile` but is not exposed as a query
  parameter, because it is not in the contract and the frontend is being built against
  the contract. Stubs are excluded from all results.

---

## N8. Nothing was stubbed

Every endpoint in `API_CONTRACT.md` is implemented against the live stack and verified
by a request. There are no placeholder handlers, no mocked data and no `TODO`s. The
only contract items deliberately *not* served as described are the two context values
in N1.2 and the three parameters in N1.1, which are refused with an explanation rather
than faked.
