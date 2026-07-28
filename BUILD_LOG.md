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
