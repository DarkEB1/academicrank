# Implementation prompt — PDF upload / bibliography trust feature

> Paste everything below the line into a fresh Claude Code session opened in
> `C:\Users\nicho\Documents\academicrank`. The stack should be running
> (`docker compose up -d`) before you start.

---

## MISSION

Implement the approved spec at
`docs/superpowers/specs/2026-07-29-own-papers-bibliography-trust-design.md`:
upload a PDF of your own paper, review its parsed bibliography, seed the trust set at
3/5 per reference, and add the paper (and OpenAlex-resolvable references) to the graph —
labelled `user_upload` and excludable per profile.

**The spec is authoritative.** It was hardened by two adversarial reviews and the
findings→response table at its top is binding. Where this prompt and the spec disagree,
the spec wins. Where the spec and reality disagree, follow reality and record the
discrepancy in `DECISIONS.md`.

## REQUIRED READING, in order, before any code

1. The spec, in full.
2. `DECISIONS.md` — D1 (engine constraints: node kind from the first character of the
   node name; only `(User,User)`/`(NonUser,User)`/`(User,NonUser)` edges are legal;
   only User nodes can be egos; U→U edges replicate into every context), D1.7
   (magnitude must always be 0), D2/D2.1 (we build patched vendored Rust), D4, D6.
3. `KNOWN_ISSUES.md` — §8 (bulk load is global shared state), §14 (engine serialises;
   cold ranking 40–90 s), §16 (alembic never runs — you fix this in Phase 0).
4. `api/provenance/`: `models.py`, `ranking.py`, `meritrank.py`, `services.py`,
   `bootstrap.py`, `routers/imports.py` (existing DOI+trigram matching — reuse it).
5. `scripts/build_graph.py` — edge weights, `epoch_factor`, hub damping. The confirm
   path must mirror these exactly or uploaded edges will be systematically mis-weighted.

## ENVIRONMENT FACTS — verified; do not rediscover them the hard way

- **DB from the host is port 55432, not 5432.** A host PostgreSQL 18 service occupies
  5432 and silently shadows the container; symptoms are auth failures against the wrong
  server. Host: `postgresql+psycopg://postgres:postgres@localhost:55432/provenance`.
  Inside compose: `postgres:5432`. Sanity check you are on the right DB:
  `select extversion from pg_extension where extname='pgmer2'` → `0.8.0`.
- Windows host. `.gitattributes` forces LF — keep shell scripts LF. Multi-line commit
  messages via `git commit -F <file>`; inline `-m` with quotes breaks in this shell.
- **Engine facts, already measured — trust these:**
  - `mr_put_edge` ≈ **87 ms per call, flat**; the engine serialises all requests.
  - `mr_delete_node` removes **out-edges only**; in-edges need one `mr_delete_edge`
    each (which *does* propagate to all contexts in a single call).
  - The engine's node registry **never shrinks**; deleted node names linger harmlessly
    until restart.
  - `mr_bulk_load_edges` **clears all engine state**. Never call it outside a full
    graph rebuild.
  - U→U edges written via `put_edge` fan out to every context that exists at that
    moment; pass `context=''` and always `magnitude=0`.
- `e2e/` holds a 25-test Playwright suite that currently passes. It must still pass
  when you are done. The first ranking for a new profile legitimately takes 40–90 s —
  time out generously.
- OpenAlex API key is in `.env`; a disk-cached client exists at `scripts/openalex.py`.
  Reuse it or its pattern. Never write the key to disk or logs.
- The api container currently has **no HTTP client and no PDF library** — both are new
  dependencies you add to `api/requirements.txt` (`pdfminer.six`, `httpx`).

## PHASES AND GATES — one commit per gate, never commit red

### Phase 0 — platform fixes (live bugs; ship these regardless of the feature)

1. Wire `alembic upgrade head` into api startup **before** `create_all`. The live DB is
   already stamped at head. Every schema change from here on is a real migration.
2. Add `graph_meta(version bigint)`, bumped on every graph mutation, and mix it into
   **both** `ranking._CACHE` keys and the `services.py` pool-cache keys. This replaces
   `services.graph_generation()` / `_check_generation()` (`max(graph_edges.id)` — it
   has ABA and process-locality holes). Integrate, don't duplicate.
3. Gate `ranking.ensure_seeded` on (trust signature, graph version) instead of
   re-putting every trust edge on every cold read.

**Gate:** existing pytest + full e2e green; demonstrate that a manual `graph_edges`
insert + version bump invalidates another profile's cached ranking.

### Phase 1 — extraction library

New package under `api/provenance/` (e.g. `pdfbib/`). LAParams-based layout — **no
hand-rolled character clustering**; two-column check per page on `LTTextBox` midpoints
(gutter > 4% of page width, ≥ 25% of boxes each side). Four splitters with the
**structural key-sequence check decisive** (gap-free increasing keys ⇒ accept at 1.0,
no scoring); otherwise margin-based acceptance over discriminative features. Every
threshold a named constant with a named test. 25 MB **and** 80-page caps; full layout
analysis only on the final ~40% of pages; wall-clock timeout in a worker.

Fixtures per the spec's licence rules: four real PDFs — the AMS alpha-key one **from
arXiv math.AG** (the corpus is statistics and cannot supply it) — plus committed
layout-line JSON artefacts for hermetic scorer tests, plus adversarial synthetic cases
(over-split trap, column-break entry, full-width footnote, undated-book bibliography)
that must be **refused**.

**Gate (print the numbers):** ≥ 90% of entries correctly delimited on 3 of 4 fixtures
AND every adversarial case refused.

### Phase 2 — matching + draft persistence (independently shippable)

Tables exactly per the spec's data model — via a real Alembic migration.
`POST /api/uploads`, `GET /api/uploads/{id}`, `PATCH .../references/{idx}`. Matching
precedence per spec; `TITLE_THRESHOLD = 0.55` reused with year ±1 required; pre-tick
**DOI/arXiv matches only**. Upload dedupe by `content_hash`.

**Gate:** integration tests against the live stack; a real PDF round-trips to a
reviewable draft; provably zero writes to `works`/`citations`/`graph_edges`/engine.

### Phase 3a — engine batch + confirm

Add **`mr_put_edges`** (non-clearing batch: the bulk-load apply loop minus
`clear_walks()`) to the vendored connector + service; rebuild both images. Confirm
path: **Postgres-first** commit of `works`/`citations`/entity rows/`graph_edges`/
`trust`+`trust_sources`, then idempotent engine push from the committed `graph_edges`
rows; on failure `status='engine_pending'` + background reconcile; a restart repairs
for free via `bootstrap.push_graph_to_engine()`. Edge parity with `build_graph.py`:
`cites`+`cited_by` with `epoch_factor`; entity edges only where OpenAlex metadata
exists. Local ids from `work_local_id_seq` (never reused), `source='user_upload'`,
`is_stub=false`.

**Fallback** if the Rust patch exceeds ~90 minutes of genuine debugging: async confirm
(202 + job id on a detached thread — the `schedule_warm` pattern), references capped at
200. Log whichever path you take in `DECISIONS.md`.

**Gate:** upload→confirm lands edges in `citations` AND the engine; a forced failure
between the Postgres commit and the engine push leaves no scoreable orphan and the
reconcile sweep repairs it; caches invalidate; **run `python scripts/build_graph.py`
and verify the upload survives the full rebuild** (this is the test that would have
caught the worst pre-review design bug).

### Phase 3b — trust, undo, visibility

3/5 default with per-entry strength; **the upload counts as one leave-one-out unit** in
`ranking.py`; undo per the `trust_sources` survivorship semantics; `include_user_uploads`
profile flag filtering rankings/search/recommendations/blindspots/subgraph, default
false, uploader always sees their own.

**Gate:** undo leaves zero residue in Postgres and zero upload edges in
`mr_edgelist('')`; a second profile with the toggle off never sees a `UL…` work
anywhere.

### Phase 4 — UI

Per the spec: `/#/uploads` review flow, per-row strength override, land on
`/recommendations` after import (post-upload `/rankings` is degenerate — spec explains),
trust-screen grouping with undo-all, settings toggle carrying the
display-level-exclusion caveat **verbatim**. Match the existing design language:
serious instrument, serif titles, no confidence theatre, both themes, keyboard nav.

### Phase 5 — end-to-end + docs

New Playwright specs including the two-profile visibility assertion. KNOWN_ISSUES
entries land with the phase that makes them true (the spec lists all six). Update
README features + limitations.

**FINAL GATE:** full e2e suite (old 25 + new) green; then
`docker compose down -v && docker compose up -d` and the suite again — bootstrap must
replay uploaded works/edges from the committed tables.

## RULES — the same autonomy contract as the original build

1. **Never ask a question.** Decide, and log every fork in `DECISIONS.md` with the
   rejected alternative and why.
2. **No mocked data, no placeholder components, no `TODO`.** If something cannot be
   made real, cut it and say so in `KNOWN_ISSUES.md` — bluntly.
3. **Verify by execution, not inspection.** Run it, curl it, measure it. Claims about
   behaviour come with numbers.
4. **Do not degrade the working app.** The existing e2e suite is green at every gate.
5. One commit per gate; the message states what was verified.
6. Append to `BUILD_LOG.md` as you go — timestamp, phase, what broke, what you did.
