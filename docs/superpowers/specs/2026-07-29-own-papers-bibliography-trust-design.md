# Design: trust a paper's bibliography by uploading its PDF

**Date:** 2026-07-29
**Status:** approved, not implemented

## Problem

Building a trust set one paper at a time is slow, and the papers a researcher trusts most
are already enumerated somewhere: in the bibliographies of their own work. The feature
lets a user upload a PDF of a paper they wrote and seed their trust set from everything it
cites, at strength 5/5.

### This is not duplicating linkage we already have

Bibliographies of corpus papers are *already* fully materialised — 181,388 citation edges,
the highest-weighted relation in the graph. The upload path exists because that source has
two structural holes it cannot fill:

| Hole | Size | What upload does |
|---|---|---|
| Full works where OpenAlex returns an **empty** `referenced_works` | **2,079 of 7,211 (29%)** | gives them a bibliography for the first time |
| Stubs, which have no outgoing references by construction | 89,540 | promotes a leaf to an interior node |
| Papers not in OpenAlex at all (preprints, unpublished) | unbounded | adds them as `UL…` nodes |

The first of these is the direct cause of the failed Phase 1 Gate 2 (71% against a 90%
target, ceiling 71.2%), and it is concentrated in books and pre-2000 work — which is also
where `KNOWN_ISSUES.md` §5 says the corpus is weakest. So this feature is, incidentally,
the only user-driven mechanism in the product for repairing the corpus.

## Decisions taken during brainstorming

| Fork | Chosen | Rejected |
|---|---|---|
| Input format | **Actual PDF upload** | Identify-by-DOI (no parsing); ORCID sweep; BibTeX of own papers |
| References not in the corpus | **Fetch from OpenAlex and add to the graph** | Report only; fetch metadata for display but don't trust |
| Trust semantics | **5/5, tagged with its upload and reversible as a batch** | Plain untagged 5/5; default 4/5 with 5/5 opt-in |
| Extraction engine | **Pure Python (`pdfminer.six`)** | GROBID container — chosen, then reversed by the user |
| Uploaded paper itself | **Always added to the graph**, whether uploaded for checking or for trusting | (user instruction, not a fork) |

### Why not GROBID

It was selected and then reversed. Worth recording what the reversal costs and what it
avoided: the official `grobid/grobid:0.8.1` is **12.51 GB compressed** (the deep-learning
build) — untenable on this 16 GB machine. `lfoppiano/grobid:0.8.1` is 0.50 GB and would
have been the viable option. The pure-Python path avoids a new service entirely at the
cost of recall on messy bibliographies, absorbed by routing uncertainty to human review.

### Why `pdfminer.six`

MIT-licensed, pure Python, and exposes character positions and font sizes — required for
column detection, hanging-indent splitting and title guessing. `pypdf` gives text without
geometry, which is not enough. **PyMuPDF is deliberately avoided: it is AGPL and this
repository is MIT.**

## Architecture

No new service. Everything runs in the existing `api` container.

```
PDF ──► extract (pdfminer.six) ──► locate bibliography ──► split entries
                                                              │
                    ┌─────────────────────────────────────────┘
                    ▼
        per entry: DOI ─► arXiv id ─► title+year vs corpus ─► OpenAlex search ─► review
                    │
                    ▼
        draft (nothing written to graph or trust yet)
                    │
              user reviews / corrects
                    ▼
        confirm ──► insert works ──► insert graph_edges ──► mr_put_edge ──► trust @ 5/5
```

### Graph mutation is incremental, never a bulk reload

`mr_bulk_load_edges` **clears all engine state** and the engine holds one graph shared by
every profile. Using it here would mean a ~25s global reload on every upload.
`mr_put_edge` does not clear state, and the volumes involved are tens of edges, not the
hundreds of thousands the "never loop put_edge" guidance was written for.

New edges are also written to `graph_edges`, which is what `bootstrap.py` replays after a
restart. Without that, uploads would silently vanish whenever `mr-service` restarts,
because it holds the graph in memory.

### Node identity

The engine derives node kind from the first character of the node name and rejects any
edge that is not `(User,User)`, `(NonUser,User)` or `(User,NonUser)` (DECISIONS.md D1).
Papers are `U`+work-id.

Works with an OpenAlex id keep `UW…`. Works that exist only because a user uploaded them
(preprints, unpublished, anything OpenAlex does not know) get a local id `L1, L2, …` and
therefore node names `UL1, UL2, …`. `models.node_to_work_id` widens from `^UW\d+$` to
`^U[WL]\d+$`.

This keeps user-contributed works type-safe against the engine's prefix rules while
remaining visibly distinct from OpenAlex records in every log line and API response.

## Extraction, designed for format variety

The requirement is to catch as many bibliography styles as possible. The approach is to
run several candidate strategies and score them, rather than tune one parser.

### Heading detection

Case-insensitive, matched against a run of layout lines:
`References`, `Bibliography`, `Literature Cited`, `Works Cited`, `Reference List`,
`Références`, `Literaturverzeichnis`, `Referencias`, plus all-caps and letter-spaced
variants. If no heading matches, fall back to scanning the final 40% of the document for
a dense run of citation-shaped lines.

### Column detection

Two-column layout is the norm in mathematics and CS journals, and interleaving the columns
is the single most common way a naive parser produces gibberish on exactly those papers.
`pdfminer` gives x-positions; text boxes are clustered by x to detect column boundaries and
read in the correct order.

### Entry splitting — four strategies, scored

| Strategy | Catches |
|---|---|
| Bracketed numeric `[1]`, `[12]` | most CS/maths journals |
| Alpha keys `[Har77]`, `[GH78]` | AMS / mathematics style |
| Ordinal `1.`, `(1)` | Elsevier, older styles |
| Hanging indent (geometry) | APA / Chicago, unnumbered |

Each candidate split is scored, with explicit thresholds so this is testable rather than
a matter of taste:

| Signal | Rule |
|---|---|
| Entry count | reject `< 3` or `> 500` |
| Entry length | reject if median < 30 chars; score on low coefficient of variation |
| Citation shape | score = fraction of entries containing a 4-digit year in 1800–2030 **or** a DOI; require `>= 0.6` |

`score = 0.5 * citation_shape + 0.3 * (1 - min(cv, 1)) + 0.2 * count_plausibility`.

Highest score wins. **If the best score is below `0.5`, no entry is auto-accepted and the
whole bibliography goes to review.** Guessing badly here would silently poison a trust
set, which is worse than asking.

### Per-entry normalisation

Before matching: de-hyphenate line-wrapped words, normalise ligatures (`ﬁ`→`fi`), collapse
whitespace, strip trailing page ranges.

### Matching precedence

1. DOI regex (`10.\d{4,9}/\S+`) → corpus lookup by normalised DOI.
2. DOI → OpenAlex fetch if not in corpus.
3. arXiv id → OpenAlex.
4. Title + year → corpus trigram (`ix_works_title_trgm` already exists) at
   **`TITLE_THRESHOLD = 0.55`**, reusing the constant already tuned in
   `routers/imports.py` rather than inventing a second one. Where a year was parsed it
   must match within ±1, which cheaply kills same-title-different-paper collisions.
5. Title → OpenAlex search.
6. Otherwise → review queue with the raw string.

Confidence per entry drives the review UI:

| Confidence | Source | UI |
|---|---|---|
| `1.0` | DOI or arXiv id | pre-ticked |
| `0.7–0.9` | trigram ≥ 0.55 with year agreement | pre-ticked |
| `0.4–0.7` | trigram without year, or OpenAlex search hit | shown unticked with candidates |
| `< 0.4` | nothing usable | raw string + manual search box |

Only entries at `>= 0.7` are pre-ticked. Everything else requires a human decision before
it can become a 5/5 seed.

## Data model

```
uploads
  id                text PK
  profile_id        FK profiles
  filename          text
  work_id           FK works NULL  -- the uploaded paper itself, as a node.
                                   -- NULL while a draft: the paper is only created on
                                   -- confirm, after the user has approved the title.
  status            text           -- draft | confirmed
  n_parsed          int
  n_matched         int
  n_added           int
  n_unresolved      int
  created_at        timestamptz

upload_references                  -- the draft, one row per parsed entry
  upload_id         FK uploads
  idx               int
  raw              text
  parsed_title      text
  parsed_doi        text
  parsed_year       int
  work_id           FK works NULL  -- resolved target, once known
  match_method      text           -- doi | arxiv | trigram | openalex | manual | none
  confidence        float
  decision          text           -- pending | accept | reject

works.source        text           -- 'openalex' | 'user_upload'
trust.upload_id     FK uploads NULL -- provenance; enables grouping and undo-all
```

`trust.upload_id` is what makes "38 of your 43 seeds came from my-paper.pdf", one-click
undo, and per-entry demotion possible.

## API

```
POST   /api/uploads                       multipart PDF -> draft
GET    /api/uploads/{id}                  draft state + per-reference match status
PATCH  /api/uploads/{id}/references/{idx} correct one entry (choose a paper, or drop it)
POST   /api/uploads/{id}/confirm          apply: add nodes, edges, trust at 5/5
DELETE /api/uploads/{id}                  undo the batch
GET    /api/profiles/{id}/uploads         list with counts, for the trust screen
```

Two-phase by design: parsing produces a draft, and **nothing touches the graph or the
trust set until `confirm`**.

`DELETE` removes the trust rows created by that upload and removes added nodes *only where
nothing else references them* — a work that has since been cited by another upload, or
trusted directly, stays.

## UI

New screen `/#/uploads`, plus a grouped section on the existing trust screen.

- Drop zone → parse progress → review table.
- Header row shows the detected title of the user's own paper, **editable**, because
  without GROBID the title is a font-size heuristic and will sometimes be wrong.
- Confident matches pre-ticked; ambiguous entries show candidates; unmatched entries show
  the raw string with a manual search box.
- `Import N seeds` applies.
- Trust screen groups seeds by upload with undo-all.

Existing UI conventions carry over: no bare numbers without uncertainty, disclaimer
rendered verbatim, keyboard navigable, both themes.

## Error handling

- Encrypted, scanned/image-only, or text-less PDFs rejected up front **with the specific
  reason**, not a generic failure.
- 25 MB size cap.
- OpenAlex unreachable → corpus matching still runs; OpenAlex-dependent entries are marked
  *"couldn't check"*, never *"not found"*. The distinction matters: one is our failure, the
  other is a claim about the paper.
- `confirm` is transactional in Postgres; a failure applying edges to the engine rolls the
  whole thing back.

## Testing

**Fixtures are real open-access PDFs, not synthetic ones.** The corpus already flags
`is_oa`, so fixtures are drawn from it to span the hard cases:

1. modern two-column DOI-rich paper,
2. AMS-style mathematics paper with `[Har77]` alpha keys,
3. unnumbered APA/Chicago style,
4. pre-2000 paper with no DOIs.

Each fixture gets an expected-parse assertion (entry count and a few known entries).

- Unit: the splitter scorer, column clustering, per-entry normalisation, DOI/arXiv regexes.
- Integration against the live stack: upload → draft → confirm → new nodes exist in
  `graph_edges` *and* in the engine → trust rows carry `upload_id` → `DELETE` unwinds it.
- Playwright: upload → review → confirm → seeds appear and the ranking changes.

## Honest consequences to document in KNOWN_ISSUES.md

1. **Uploads write into a graph shared by every profile.** One user's upload changes other
   users' rankings. This is inherent to the engine holding a single graph; this is simply
   the first feature that lets a user write to it.
2. **A bibliography is not an endorsement.** People cite work they are refuting. Blanket
   5/5 overstates trust; the mitigation is provenance tagging and one-click undo, not a
   claim that the semantics are correct.
3. **Recall will be uneven.** Strong on modern DOI-bearing bibliographies, materially worse
   on older, non-DOI, or unusually formatted ones. The failure mode is deliberately
   "N entries need your eyes", never silent mis-trust.
4. **Self-citations are included**, like any other reference. Citing your own prior work
   is normally you pointing at the thing you most stand behind, so excluding it was
   over-cautious. They are still *labelled* in the review table so the choice is visible
   and a user can untick them, but nothing is dropped automatically.

   Note on the justification, because it matters: the argument "MeritRank is sybil
   tolerant so this is safe" is the one claim this build measured and **could not
   confirm** — the citation-ring experiment came back at a ratio of 1.00 +/- 0.23, no
   measurable suppression versus plain personalised PageRank (README, measurements).
   Including self-citations is still the right call on its own merits, but it should not
   be justified by a sybil-tolerance property we have not demonstrated. If someone later
   uploads a large body of heavily self-citing work, nothing in this system is known to
   discount it.

## Implementation phasing

This is a large feature for one plan, so it is staged. Each stage is independently
verifiable, and stage 1 is where the risk lives — if extraction is poor on real fixtures,
that is worth knowing before any UI exists.

1. **Extraction library + fixtures.** `pdf_extract.py`, the four splitters, the scorer,
   column clustering, normalisation. Verified against the four real OA PDFs. No API, no DB.
2. **Matching + draft persistence.** `uploads`/`upload_references` tables, the matching
   precedence, `POST /api/uploads` and `GET /api/uploads/{id}`. Draft only, nothing applied.
3. **Confirm path.** Node creation (including `UL…` local ids), `graph_edges` writes,
   incremental `mr_put_edge`, trust rows with `upload_id`, and `DELETE` unwind.
4. **UI.** `/#/uploads`, review table, trust-screen grouping, undo-all.
5. **Playwright + KNOWN_ISSUES entries.**

## Out of scope

ORCID sweep of an author's whole corpus; BibTeX of one's own papers; PDF full-text
indexing beyond the bibliography; re-running the graph build after upload (edges are
applied incrementally instead).
