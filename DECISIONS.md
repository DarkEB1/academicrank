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
