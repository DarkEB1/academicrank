# DECISIONS

Every fork, the option taken, the option rejected, and why. Append-only.

---

## D0. Name: **Provenance**

Kept the proposed name. It states what the system actually measures — where a claim
to trust *comes from* — rather than implying quality. Rejected: "AcademicRank" (the
directory name), because "rank" implies an objective ordering, which is precisely the
claim this product refuses to make.

---

## D1. Ranking engine — findings that contradict the build prompt

The prompt's API sketch was explicitly flagged as possibly stale. It was. Everything
below is verified against the cloned source at `vendor/meritrank-rust` and against a
live `\df` on a running container.

### D1.1 `mr_service()` is **not** a health check
The prompt suggests `SELECT mr_service();` proves the round trip. It does not.

```rust
//  D3 (JOURNAL): Return connector version; no network call needed.
#[pg_extern(immutable)]
fn mr_service() -> &'static str { VERSION }
```

`mr_service()` and `mr_connector()` both return the *connector crate version* as a
constant, and `mr_service_url()` returns the configured URL string. None of the three
touches the network. **The first call that actually proves connectivity is
`mr_create_context()`.** Our health check uses that.

### D1.2 Node kind is derived from the **first character of the node name**
`service/src/node_registry.rs::node_kind_from_prefix`:

| Prefix | Kind |
|---|---|
| `U` | User |
| `B` | Beacon |
| `C` | Comment |
| `O` | Opinion |
| `V` | PollVariant |
| `P` | Poll |

Any other first character yields `None`. This is Tentura's social-network domain model
baked into the engine. The prompt did not anticipate it, and it constrains every node
name in the system.

### D1.3 Edge endpoint kinds are **validated and rejected**
`service/src/aug_graph/edges.rs::reg_owner_and_get_ids` permits only:

- `(User, User)`
- `(NonUser, User)` — additionally registers src *owned by* dst
- `(User, NonUser)`

Everything else returns `AugGraphError::IncorrectNodeKinds` and the edge is **silently
skipped** (logged server-side, no client error). A node name with an unrecognised
prefix also fails.

**Consequence: `Beacon -> Beacon` is illegal.** The prompt's proposed schema has
`Paper --cites--> Paper` as the strongest signal. If papers were Beacons, every
citation edge would be silently dropped and the product would be built on an empty
graph.

### D1.4 Only `User` nodes may be an ego
`service/src/aug_graph/calc.rs`:
> `Non-user node used as ego for calculation (rejected)`

So anything we ever want to rank *from* must be a `U` node.

### D1.5 `User->User` edges are replicated into **every** context
`service/src/state_manager.rs` (bulk load path) partitions edges into `user_user_edges`
and `context_non_user_edges`, then:

- the aggregate context `""` receives **all** edges;
- **each named context receives `user_user_edges` in full — the edge's own declared
  context is ignored — plus only that context's non-user edges.**

### D1.6 Decision forced by D1.2–D1.5: the node typing scheme

| Domain object | Node | Rationale |
|---|---|---|
| Paper | `U` + OpenAlex id, e.g. `UW2963757046` | Only `(User,User)` supports Paper→Paper citation (D1.3), and only Users can be egos (D1.4). |
| Trust profile | `U` + `profile_<uuid>` | Ego must be a User. Trust edges are `U→U`, so they replicate into every context (D1.5) — which is exactly what we need for per-context scoring to work at all. |
| Author / Institution / Topic / Venue | `B` + typed id, e.g. `BA5023888391` | `Paper(U)→Entity(B)` and `Entity(B)→Paper(U)` are both legal and stay **context-local**. |

**Rejected alternative:** papers as Beacons with authors as Users. This reads more
naturally (people are users), but `Paper→Paper` would then be `B→B` and every citation
edge — the entire strong signal — would be silently discarded. Non-starter.

**Consequence for the context architecture, stated honestly:** because citation and
trust edges are `U→U`, they appear in *every* context. A named context therefore means
"the citation backbone + the trust seeds + this one relation family", **not** an
isolated relation family. So:

- Context `citation` ≈ the pure citation backbone (it has no extra non-user edges).
- Context `author` = citation backbone + authorship edges.
- The per-context decomposition we show the user is the *marginal* contribution of a
  relation family: `score(ctx) - score(citation)`. That is a real, defensible quantity,
  but it is **not** "trust arriving purely through topic", and the UI must not claim it
  is. Recorded in KNOWN_ISSUES.md as a semantic caveat.

### D1.7 `weight` and `magnitude` semantics (from source, not guessed)
`service/src/vsids.rs`. `Magnitude = u32`. On every edge write:

```
scale        = VSIDS_BUMP ^ (new_magnitude - current_mag_scale)   // VSIDS_BUMP default 1.03
scaled_weight = weight * scale
```

`magnitude` is an **exponential bump exponent**, a recency/importance lever borrowed
from SAT-solver VSIDS — not a count and not a multiplier. **Decision: pass
`magnitude = 0` uniformly**, so `scale = 1.03^0 = 1` and the weight we send is the
weight the engine stores. Our weighting is expressed entirely through `weight`, where
it is legible and tunable, rather than split across two interacting knobs.

Also in VSIDS: edges are **auto-deleted** when
`|weight| <= deletion_ratio * max_weight_from_that_source`, with
`deletion_ratio = 1e-3` (hardcoded, not an env var). Our weakest-to-strongest weight
ratio is `0.10 / 1.00 = 0.1`, two orders of magnitude above the threshold, so no
intended edge is silently pruned. Worth knowing before anyone tries a 0.0005 weight.

---

## D2. Runtime images: build from source, not the published v0.4.0 images

The repo ships `docker-compose.test-local.yml` referencing
`vbulavintsev/meritrank-service:v0.4.0` and `vbulavintsev/postgres-tentura:v0.4.0`.
Those pull in seconds versus a slow pgrx build, so they were tried first.

Two blocking defects were found in them, by execution:

1. **No `mr_bulk_load_edges`.** A live `\df mr_*` against the v0.4.0 connector lists 24
   functions and that is not one of them. The prompt is explicit that loading edges one
   at a time via `mr_put_edge` is too slow, and our graph has hundreds of thousands of
   edges. Disqualifying on its own.
2. **The service URL must be a numeric `IP:port`.** `tcp://meritrank:10234` (the form
   the repo's own compose file uses) fails with `ERROR: invalid socket address syntax`,
   and so does a bare hostname `meritrank:10234`. Only `172.28.0.10:10234` works. The
   v0.4.0 connector parses with a strict `SocketAddr` parse — no scheme, no DNS.
   Main-HEAD source has since fixed both halves (`strip_scheme()` then
   `to_socket_addrs()`, which does resolve hostnames).

Main HEAD builds `meritrank_service v0.9.0` — five minor versions ahead of the
published images, which explains the drift.

**Decision: `docker-compose.yml` builds both images from the vendored source** at
`vendor/meritrank-rust`. The pgrx build turned out to be far cheaper than feared
because `psql-connector/Dockerfile` starts `FROM ghcr.io/intersubjective/pgrx-toolchain`,
a prebuilt toolchain image — so there is no `cargo pgrx init` on the critical path.

**Rejected:** shipping the prebuilt images and looping `mr_put_edge`. Rejected on the
bulk-load requirement.

**Retained anyway:** the static-IP wiring (`172.28.0.10`) on the compose network. It is
free, it removes a DNS dependency at startup, and it keeps the stack working against
either connector generation.

---

## D3. Data source parameters

- Mathematics field resolved at runtime from `/fields` (`fields/26` today) rather than
  hardcoded, per the prompt.
- Corpus filter `primary_topic.field.id` rather than `topics.field.id`: the former is
  the work's *primary* field, which keeps the corpus recognisably mathematics instead of
  sweeping in physics and CS papers that merely carry a maths topic.
- Abstracts are reconstructed from `abstract_inverted_index` and truncated at 8,000
  chars.
- Raw JSON is retained alongside the normalised tables so fields can be re-derived
  without re-scraping.

---

## D2.1 The vendored checkout is patched

`vendor/meritrank-rust` is not a pristine clone. Two changes were necessary to build
on Windows:

1. **Line endings.** The clone picked up CRLF, and `generate_scripts.sh` then died with
   `syntax error: unexpected end of file (expecting "then")`. All vendored `.sh` files
   converted to LF, plus a repo `.gitattributes` (`* text=auto eol=lf`) so it cannot
   regress.
2. **`psql-connector/20_pgmer2.sh`.** Upstream calls `"${psql[@]}"`, a bash array that
   the postgres entrypoint defines only when it *sources* an init file — which it does
   only for non-executable files. A Windows checkout marks the file 755, Docker `COPY`
   preserves that, the entrypoint runs it as a subprocess, the array is empty, and init
   fails with `--dbname=provenance: command not found`, leaving the extension
   uninstalled. Patched to invoke `psql` directly, which works whether sourced or run.

Neither patch changes engine behaviour. Both are marked in-file.

---

## D4. Per-user context weights are composed in Python, not reloaded into the engine

`mr_bulk_load_edges` **clears and replaces all engine state**, and the engine holds one
graph shared by every user. So per-user context weights cannot be implemented by
rebuilding the graph with different weights — that would be global, and would take
minutes per adjustment.

**Decision:** query each context separately (`mr_scores(ego, context=c)`, one call per
context) and compose in Python:

```
score(p) = baseline(p) + Σ_c  w_c · ( score_c(p) − baseline(p) )
```

where `baseline` is the `citation` context. Because each named context is
"baseline + one entity family" (D1.6), the bracketed term is that family's **marginal**
contribution, and the weights are a genuine per-user, live control — which is what makes
the parameter playground honest rather than decorative.

**Rejected:** baking context weights into edge weights at build time. Simpler and it
would let the engine do the combining, but it makes weights global to all users and
turns every slider drag into a multi-minute full graph reload.

**Cost:** N+1 `mr_scores` calls per ranking instead of one. Acceptable, and it is the
only design that yields per-user weighting on shared engine state.

---

## D5. Uncertainty via leave-one-out

The service exposes no per-call walk count and no sampling seed, so repeated
independent estimates of the same ego are not available cheaply, and a true Monte Carlo
standard error cannot be reported. **Decision: leave-one-out over the trust set**,
jackknife-scaled by `sqrt(n-1)`, with tie groups assigned by overlapping confidence
intervals.

The prompt offered this as the fallback; it is being used as the primary because the
preferred option is genuinely unavailable. It also answers the more useful question —
how much does this ranking depend on any one of my trust decisions — but it is not a
sampling error bar and KNOWN_ISSUES.md says so.

---

## D6. Phase 1 Gate 2 is not met, and I chose not to make it met

The Phase 1 gate asks for **≥90% of full papers having at least one resolved in-corpus
reference**. Measured: **69.7%** before fixing the stub bug below. The ceiling for this
corpus is **71.2%**, so 90% was unreachable by any loader.

Cause, verified against the raw OpenAlex JSONL independently of the database:
**2,079 of 7,211 full works (28.8%) arrive from OpenAlex with `referenced_works: []`.**
The key is present and the list is empty. They are concentrated in books and in
pre-2000 articles. Of the 5,132 papers that *do* carry a reference list, **97.9%**
resolve at least one in-corpus target — i.e. the resolution logic is essentially at its
theoretical maximum.

Two ways to make the gate go green were available:

1. **Filter the seed query on `has_references:true`.** Rejected. It would pass the gate
   by deleting the evidence. In mathematics specifically it would strip out books, and
   books are not marginal here — Griffiths & Harris, *Principles of Algebraic Geometry*
   is in the top of our own corpus by in-corpus citations. A mathematics corpus that
   excludes monographs is a worse product, and the gate exists to protect the product,
   not the other way round.
2. **Lower the threshold.** Rejected as self-serving: moving a gate you just failed is
   not passing it.

**Decision: keep the corpus, report the miss, and report the metric that is actually
diagnostic** (97.9% of papers that have reference lists resolve one). This is recorded
in KNOWN_ISSUES.md as an open item rather than quietly dropped.

### D6.1 A real defect this exposed, now fixed

`scripts/scrape.py` built its snowball `referrer_count` from the 3,000 **seed** works
only. The 4,211 **promoted** works are full nodes too, so their references also needed
stubs — without them every citation from a promoted paper to an unseen target dangled
and was dropped at load time. That silently discarded **55,889 of 190,987 citations**
(29%) and left 43,609 distinct reference targets unstubbed.

Fixed by unioning the promoted works' references into the stub set before hydration.
This does not move Gate 2 (it does nothing for papers with no reference list at all)
but it materially increases citation density, which is the strongest signal in the
graph.

---

## D7. Upload feature, Phase 0: alembic wiring stamps legacy databases first

The implementation brief stated "the live DB is already stamped at head". Verified
false: `alembic_version` did not exist on the running database (checked 2026-07-29
before writing any code). Per the brief's own rule, reality wins.

**Decision:** `provenance/migrations.py` (called from the lifespan hook before
`create_all`) stamps a database at the initial-schema revision `bec852712a4a` when it
has the schema (`works` exists) but no `alembic_version` table, then runs
`alembic upgrade head`. A fresh `down -v` database has neither, so it takes the plain
upgrade path through every migration.

**Rejected:** manually stamping the live DB once from the host and shipping only
`command.upgrade(cfg, "head")`. It would work for *this* database and break for any
other pre-existing one (the point of Phase 0 is that column additions must reach
every live DB, not the one on this machine).

Also deliberate: the alembic `Config` is built without `alembic.ini`, because env.py
calls `logging.config.fileConfig()` when an ini is present, which would clobber
uvicorn's logging from inside a startup hook. env.py resolves the database URL itself.

---

## D8. pdfbib (Phase 1) forks, decided by execution against the fixtures

### D8.1 The timeout worker is a subprocess, not multiprocessing

`multiprocessing` spawn re-imports the parent's MAIN module in the child. Under
`python -m pytest` (and under uvicorn in the container) that re-runs the host program
inside the worker — verified: the happy-path worker test hung for the full 120 s
budget on Windows. The worker is now `python -m provenance.pdfbib.worker` via
`subprocess.run(timeout=…)`, PDF on stdin, JSON on stdout. **Rejected:** a thread
(cannot be killed; a hostile PDF pins a core for minutes).

### D8.2 Structural check needs 6 entries, not 3

A gap-free `[1]..[4]` sequence occurs in the wild inside numbered appendix lists (the
lme4 fixture served one up immediately) and in crafted prose. `STRUCTURAL_MIN_ENTRIES=6`
sends anything smaller to the scored path, whose failure mode is review, not silent
mis-trust. No real paper cites fewer than 6 works.

### D8.3 Non-structural numeric splits must have ordered keys

A sheared reading order (failed column detection) produces plausible entries with
scrambled keys ([1],[4],[2],[5]…) that pass every per-entry feature gate — found by the
column-break adversarial case, which was *accepted* until this gate existed.
`MIN_KEY_ORDER_FRACTION = 0.8` of adjacent pairs must increase; one misread key in a
real bibliography still leaves ~98%.

### D8.4 Full-width lines on two-column pages are dropped from the region

On a page with column-assigned lines, a column-less line is a footnote, rule or
caption; keeping it splices footnote text into whichever entry spans the column break
(and, when the footnote carries its own `[1]` markers, corrupts the key sequence). A
real reference line on such a page always has a column.

### D8.5 Alpha keys without digits fall through to scoring

The structural alpha check demands `[A-Z][a-zA-Z+-]*\d{2}[a-z]?` (`[Har77]`, `[BCHM10]`).
Keys like `[KM]`/`[Kaw]` (checked: Hacking–Prokhorov, de Fernex–Ein–Mustață) are real
but shape-ambiguous — one capital letter plus letters matches ordinary bracketed
asides too, so they are not *decisive*; those bibliographies still split via the
scored path. Recorded because the fixture hunt hit both styles.

### D7.1 ensure_seeded gating and the engine-restart blind spot

`ranking.ensure_seeded` is now gated on (trust signature, graph_meta.version): zero
`mr_put_edge` calls when nothing changed, full re-seed when the trust set or graph
changes. One case the version counter cannot see: an mr-service restart wipes its
in-memory graph with no Postgres change. Covered by a retry in `_scores_cached` --
if a profile with seeds reads back an empty score set, the seed marker is dropped,
the ego re-seeded, and the read retried once (the same pattern `global_scores`
already used). **Rejected:** probing engine emptiness before every read -- that is a
per-read engine round-trip to optimise a failure mode that occurs only on container
restart.
