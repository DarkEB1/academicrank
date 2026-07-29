# Design: trust a paper's bibliography by uploading its PDF

**Date:** 2026-07-29 (revised same day after two adversarial reviews)
**Status:** approved direction; revised against review findings; awaiting final user sign-off

## Problem

Building a trust set one paper at a time is slow, and the papers a researcher trusts
most are already enumerated in the bibliographies of their own work. The feature lets a
user upload a PDF of a paper they wrote, review the parsed bibliography, and seed their
trust set from it — with the uploaded paper (and references OpenAlex knows) entering the
graph as real nodes.

**Justification, stated honestly.** Two things and only two things:
1. Bulk trust-set seeding from a document the user already has.
2. The only path by which papers *not in OpenAlex* (preprints, unpublished work) can
   exist in this system at all.

An earlier draft justified this feature as "corpus repair" against the 2,079
empty-reference works and 89,540 leaf stubs. Review (B3) showed that framing to be a
rationalisation — repairing those at scale would require thousands of distinct authors
to upload — and tying it to the failed Phase 1 Gate 2 repeated the exact move DECISIONS
D6 rejected. That framing is withdrawn. If the Gate 2 number moves because users
supplied references, that measures user activity, not corpus quality, and must not be
reported as progress against D6.

## Decision history

| Fork | Decision | Notes |
|---|---|---|
| Input format | Actual PDF upload | over identify-by-DOI, ORCID sweep, BibTeX |
| Unmatched references | Fetch from OpenAlex, add to graph | user decision |
| Uploaded paper itself | Always becomes a node | user instruction |
| Extraction engine | Pure Python `pdfminer.six` | GROBID chosen then reversed; official image is 12.5 GB |
| Self-citations | **Included**, labelled in review | user decision; justification deliberately does NOT lean on sybil tolerance, which this build measured absent (ratio 1.00 ± 0.23) |
| Seed strength | **3/5 (0.7) default**, per-entry promotion to 5/5 | revised after review; see "Trust semantics" |
| Shared-graph writes | **Yes, labelled, with per-user include/exclude** | user decision after review; see "Visibility" |

## What the adversarial reviews changed

Two independent reviews (semantic correctness; implementation method) each returned
"rework the graph-mutation half". Every blocking finding and its design response:

| Finding | Response in this spec |
|---|---|
| B1: 5/5 blanket seeds collapse uncertainty/tie-groups; 43×1.4 vs 5×0.7 = upload is 94% of the ego | 3/5 default; upload counts as **one** leave-one-out unit |
| B2: unauthenticated write primitive into the strongest edge type of a shared graph | labelled works + per-user visibility filter; honest limit stated (see Visibility) |
| B3: coverage-hole justification is D6 in costume | justification withdrawn (above) |
| B4/N6: `build_graph.py` TRUNCATEs `graph_edges`; uploads destroyed on rebuild | **`citations` (+ entity tables) are the durable representation; `graph_edges` and the engine are derived.** Rebuild now *regenerates* uploads and adds their coupling/co-citation edges |
| B7/B3(method): "Postgres rolls back the engine" is false — RPCs are not transactional | **Postgres-first invariant**: commit rows, then push engine edges idempotently; `status='engine_pending'` + background reconcile; restart repairs via `bootstrap.push_graph_to_engine()` |
| B1(method): 165–890 serialised `mr_put_edge` calls at measured 87 ms = 14–77 s blocking everyone | **`mr_put_edges` batched non-clearing RPC** added to the vendored engine (see below) |
| B2(method): `mr_delete_node` removes out-edges only — undo as specified impossible | undo = `DELETE FROM graph_edges/citations WHERE …` + one batched `mr_delete_edge` pass; orphaned node *names* linger harmlessly until restart (documented litter) |
| B5(sem)/N6: uploaded works were second-class (no epoch factor, no reverse edges, no damping) but rendered first-class | confirm path writes **edge parity**: `cites`+`cited_by` with `epoch_factor`, entity edges where OpenAlex metadata exists; `UL…` locals get no entity edges (no reliable metadata) and are labelled |
| B5(method): single `trust.upload_id` column cannot express undo semantics | `trust_sources(profile_id, work_id, upload_id)` join table, `ondelete=CASCADE`; trust row deleted only when no source rows survive |
| B6: `max()+1` local-id allocation collides and reuses ids the engine still remembers | Postgres sequence `work_local_id_seq`, ids never reused; upload dedupe by content hash |
| B4(method): **Alembic is copied into the image but never executed** — new columns will never exist on live DBs | `alembic upgrade head` wired into api startup before `create_all`; `works.source` gets `server_default='openalex'`. This is a live bug in the existing app, fixed in stage 0 regardless of this feature |
| N1: cache invalidation — `max(graph_edges.id)` generation marker has ABA, process-locality, and LRU-skew holes | persisted `graph_meta.version` counter, bumped on every graph mutation, **mixed into both `ranking._CACHE` and `services` pool keys**. Policy chosen deliberately: accept the global invalidation and document that an upload costs other users a cold start |
| N1(sem): `ensure_seeded` loops put_edge on every cold read (3.9 s at 45 seeds) | seed only when trust signature or graph version changed, tracked per profile |
| N3: scorer's 0.5 auto-accept floor is dead code; cv term wrong-signed | scorer redesigned (see Extraction): structural key-sequence check first, margin-based acceptance, discriminative features |
| B6(sem): draft can't record OpenAlex resolutions without violating "nothing before confirm" | `upload_references.resolved_openalex_id` column; `works` rows created only at confirm |
| B6(sem): trigram threshold tuned on curated BibTeX, applied to noisy PDF text, pre-ticked | pre-tick **DOI/arXiv matches only**; every trigram match requires an explicit tick |

## Architecture

```
PDF ──► extract (pdfminer.six, LAParams) ──► split (4 strategies) ──► score/refuse
                                                                        │
                 per entry: DOI → arXiv → corpus trigram → OpenAlex → review
                                                                        │
                                              draft (Postgres only, no graph writes)
                                                                        │
                                                            user reviews / corrects
                                                                        ▼
   confirm:  COMMIT works + citations + entity rows + graph_edges + trust(+sources)
                          then, outside the transaction:
             push edges to engine via mr_put_edges (batched, idempotent)
             on failure → status='engine_pending', background reconcile
```

**Source-of-truth invariant** (same one `bootstrap.py` already enforces): *Postgres
relational tables are the truth; `graph_edges` is a derived materialisation; the engine
is a cache of `graph_edges`.* Confirm writes down the stack in that order; every layer
below Postgres is idempotently re-derivable.

### The engine patch: `mr_put_edges`

`mr_bulk_load_edges` clears all engine state (`clear_walks()` at
`aug_graph/edges.rs:147`; `subgraphs_map.clear()` on the bulk path only). `mr_put_edge`
does not clear, but costs a measured **87 ms per call** (RPC + 6-subgraph fan-out), and
a 50-reference upload needs 165–890 edge writes = 14–77 s on an engine that serialises
requests.

Fix: add `mr_put_edges(src[], dst[], weight[], magnitude[], context[], timeout)` to the
vendored connector + a non-clearing batch op in the service — the same apply loop as
bulk load, minus the `clear_walks()`. We already build and patch this source
(DECISIONS D2/D2.1); this deepens divergence from upstream and we accept that cost
knowingly. **Fallback** if the patch proves troublesome: async confirm returning `202`
+ job id (the `schedule_warm` detached-thread pattern), references capped at 200.
Either way the cap and a specific-reason rejection apply.

### Trust semantics

Default **3/5** (`TRUST_STRENGTH_SCALE[3] = 0.7`) — the encoding of "I cited this".
Review arithmetic: a median bibliography at 5/5 (1.4) would be 60.2 units of outgoing
weight against ~3.5 for five hand-picked seeds — 94% of the ego — and would push ~77%
of profiles past the leave-one-out window (2–12 seeds), collapsing every ranking into
one tie group. Instead:

- per-entry promotion to 5/5 in the review table and afterwards;
- **leave-one-out treats the whole upload as one jackknife unit** (leave-one-upload-out),
  so error bars and tie groups stay meaningful and LOO cost stays bounded;
- the cold-start notice counts an upload as one considered decision, not N.

### Visibility of user-contributed works

Only `UL…` local works (content that exists solely because a user uploaded it) carry
`works.source = 'user_upload'`. Works fetched from OpenAlex during confirm are ordinary
corpus records and are not labelled. Every profile has `include_user_uploads`
(default **false**; uploader always sees their own). The filter applies in rankings,
search, recommendations, blindspots and the graph explorer.

**Honest limit, stated in the UI and KNOWN_ISSUES:** exclusion is *display-level*.
There is one shared graph; walks propagate through uploaded edges for everyone, so an
excluding user's scores are still perturbed by uploads existing. This cannot be fixed
on this engine (one graph, U→U replication into every context). It is bounded —
hundreds of edges among ~550k, under scores that are Monte Carlo estimates — but the
system must never claim exclusion isolates you.

## Data model

```
uploads
  id                 text PK            -- opaque (uuid)
  profile_id         FK profiles        (indexed)
  filename           text
  content_hash       text               UNIQUE(profile_id, content_hash) -- dedupe re-uploads
  work_id            FK works NULL      ondelete=SET NULL  -- created at confirm
  status             text               -- draft | applying | engine_pending | confirmed
  n_parsed / n_matched / n_added / n_unresolved  int
  created_at         timestamptz

upload_references
  upload_id          FK uploads         ondelete=CASCADE
  idx                int
  PRIMARY KEY (upload_id, idx)
  raw                text
  parsed_title/doi/year
  resolved_openalex_id text NULL        -- records OpenAlex resolution WITHOUT creating works rows
  work_id            FK works NULL
  match_method       text               -- doi | arxiv | trigram | openalex | manual | none
  confidence         float
  decision           text               -- pending | accept | reject

trust_sources
  profile_id, work_id  FK trust (composite) ondelete=CASCADE
  upload_id            FK uploads          ondelete=CASCADE  (indexed)
  PRIMARY KEY (profile_id, work_id, upload_id)

works.source        text  server_default 'openalex'   -- 'openalex' | 'user_upload'
graph_meta          (version bigint)   -- persisted graph generation counter

sequence work_local_id_seq   -- 'L' || nextval(); never reused (the engine has no
                             -- transactional memory, so a reused id would inherit
                             -- phantom edges from an abandoned confirm)
```

Undo (`DELETE /api/uploads/{id}`): delete this upload's `trust_sources` rows; delete
each `trust` row only if no other source row survives and it wasn't hand-added; delete
the upload's `citations`/`graph_edges` rows; issue one batched `mr_delete_edge` pass
(verified: propagates to all contexts in one call per edge); bump `graph_meta.version`.
Node names linger in the engine registry until restart — harmless, documented litter.

## Extraction

`pdfminer.six` (MIT; PyMuPDF rejected as AGPL). Use `LAParams` for layout and column
separation — **no hand-rolled x-clustering over characters** (review N4: LAParams
already does this; bespoke k-means invents columns in justified single-column text).
Column sanity check operates on `LTTextBox` midpoints per page: two-column hypothesis
accepted only if the gutter exceeds ~4% of page width and each side holds ≥25% of boxes.

Guards: 25 MB **and** 80-page cap; only the final 40% of pages get full layout analysis
(`extract_pages(page_numbers=…)`); extraction runs in a worker with a wall-clock timeout.
New deps in `requirements.txt`: `pdfminer.six`, `httpx` (the container currently has no
HTTP client at all).

**Heading detection** as before (multilingual set + all-caps/letterspaced variants),
with the dense-run fallback over the tail of the document.

**Entry splitting — four strategies, adjudicated structurally first:**

1. **Structural check** (decisive when it fires): for keyed strategies — bracketed
   numeric `[1]`, alpha keys `[Har77]`, ordinal `1.` — do the extracted keys form a
   monotonically increasing, gap-free sequence from 1 (or a consistent alpha-key set
   each matching `[A-Z][a-zA-Z+]*\d{2}`)? If yes: accept that split at confidence 1.0,
   no scoring. This resolves most CS/maths/Elsevier layouts deterministically.
2. Only on fallthrough, score candidates on **discriminative features**: median entry
   length in **[60, 600]** chars; fraction of entries opening with an author-shaped
   token; fraction containing a plausible year (1800–2030); fraction containing a
   volume/page pattern (`\d+\s*[:(]\s*\d+`, `pp.`, `In:`); and entry count vs the count
   of distinct in-text citation markers found in the body.
3. **Accept on margin, not absolute score:** best candidate must beat the runner-up by
   a named margin constant, else the whole bibliography goes to review. (The previous
   formula's 0.5 floor was provably dead code, and its uniformity term rewarded the
   over-splitting failure mode.)
4. Every threshold is a named module constant with a named test, calibrated against the
   fixture set — presented as calibration, not as derivation.

Per-entry normalisation unchanged: de-hyphenation, ligature normalisation (`ﬁ`→`fi`),
whitespace collapse, trailing page-range strip.

## Matching

Precedence: DOI regex → corpus | DOI → OpenAlex | arXiv id → OpenAlex | title+year →
corpus trigram (`TITLE_THRESHOLD = 0.55` reused, year ±1 required) | title → OpenAlex
search | review queue.

Pre-ticking: **only DOI and arXiv matches (confidence 1.0)**. Every trigram or
OpenAlex-search match requires an explicit tick — the 0.55 threshold was tuned on
curated BibTeX, and PDF-extracted text is materially noisier. Self-citations are
labelled, included, untickable like anything else.

## API

```
POST   /api/uploads                        multipart PDF → draft (202 if async path)
GET    /api/uploads/{id}                   draft + per-reference status
PATCH  /api/uploads/{id}/references/{idx}  correct/choose/reject one entry
POST   /api/uploads/{id}/confirm           apply (Postgres-first; engine reconciled)
DELETE /api/uploads/{id}                   undo batch
GET    /api/profiles/{id}/uploads          list with counts
POST   /api/profiles/{id}/params           gains include_user_uploads
```

## UI

`/#/uploads`: drop zone → parse progress → review table (DOI/arXiv pre-ticked, trigram
candidates untickable-by-default, unmatched with raw string + manual search; editable
own-paper title; self-citations labelled). `Import N seeds at 3/5` with per-row
strength override. Trust screen groups by upload with undo-all. Settings exposes the
`include_user_uploads` toggle with the display-level-exclusion caveat verbatim. After
import, land the user on `/recommendations` with the diversity dial raised — review N2:
post-upload `/rankings` degenerates to "the references of my references", the least
interesting output the system can produce.

## Error handling

Encrypted / image-only / text-less PDFs rejected with the specific reason; 25 MB & 80
page caps; duplicate upload (content hash) rejected as "already uploaded". OpenAlex
unreachable → corpus matching proceeds, affected entries marked *"couldn't check"*
(our failure), never *"not found"* (a claim about the paper). Confirm: Postgres commit
is atomic; engine push is idempotent and reconciled; `engine_pending` uploads are
retried by a background sweep and repaired for free on restart.

## Testing

- **Fixtures:** four real PDFs — modern two-column DOI-rich; **AMS alpha-key paper from
  arXiv `math.AG`** (the corpus cannot supply one — it is statistics; and arXiv is
  licence-safe to commit, unlike bronze-OA publisher PDFs); unnumbered APA; pre-2000
  no-DOI. Licence rule: commit only CC-BY/arXiv; otherwise a URL+SHA256 manifest and
  fetch script, tests skipped when absent.
- **Hermetic scorer tests:** extraction output committed once as layout-line JSON
  (`(text, bbox, font_size)` per line); splitters and scorer unit-tested against JSON,
  immune to pdfminer drift. One slow test per fixture covers PDF → layout-lines.
- **Adversarial synthetic cases** (a rejecter cannot be tested on well-formed input):
  body text with in-text `[3]` markers (over-split trap); entry spanning a column
  break; full-width footnote on a two-column page; bibliography of undated books —
  each must be *refused*, not mis-accepted.
- Stage-1 acceptance: ≥90% of entries correctly delimited on 3 of 4 fixtures **and**
  every adversarial case refused.
- Integration: upload → draft → confirm → rows in `citations`/`graph_edges` **and**
  edges in the engine → `trust_sources` correct → undo unwinds → **negative test:**
  forced failure between Postgres commit and engine push leaves no scoreable orphan
  and reconciles.
- Playwright: upload → review → confirm → seeds appear → ranking changes → toggle
  `include_user_uploads` off in a second profile and assert the `UL…` work vanishes
  from its search/rankings.

## Phasing

0. **Platform fixes that are live bugs today, shipped regardless of this feature:**
   wire `alembic upgrade head` into startup; add `graph_meta.version` and mix it into
   both cache key sets; gate `ensure_seeded` on (trust signature, graph version).
   The engine-write cost spike is already done: 87 ms/mutation, measured.
1. **Extraction library + fixtures** (hermetic JSON artefacts, acceptance numbers above).
2. **Matching + draft persistence** — tables, migration, `POST /api/uploads`, review
   endpoints. No graph writes.
3. **a)** `mr_put_edges` engine patch (or the async fallback) + confirm path writing
   `works`/`citations`/`graph_edges` + engine push + reconcile sweep.
   **b)** trust rows with `trust_sources` + undo + visibility filter.
4. **UI** (+ KNOWN_ISSUES entries land with the stage that makes them true, not batched).
5. **Playwright end-to-end + docs.**

Stage 2 is independently shippable as corpus-only trust seeding if stage 3 stalls.

## Honest consequences (KNOWN_ISSUES entries, landing with their stages)

1. Uploads write into a graph shared by every profile; exclusion is display-level only.
2. A bibliography is not an endorsement; 3/5 + provenance + undo is mitigation, not a
   claim the semantics are exact.
3. Nothing in this system measurably discounts coordinated or self-citation-heavy
   uploads — the sybil result was 1.00 ± 0.23. Labelling and default-exclusion are the
   actual defence.
4. Recall degrades hardest on undated-book bibliographies — pre-2000 work, exactly
   where the corpus is weakest. Failure mode is review, never silent mis-trust.
5. One upload globally invalidates ranking caches: other users' next read is a cold
   one. Chosen deliberately over serving stale scores.
6. Engine node names from deleted uploads persist until restart (registry never shrinks).

## Out of scope

ORCID sweep; per-upload visibility granularity (one global toggle in v1); incremental
coupling/co-citation (regenerated on next full rebuild); moderation tooling; full-text
indexing beyond the bibliography.
