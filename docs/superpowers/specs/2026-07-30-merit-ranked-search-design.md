# Merit-ranked search (design)

**Date:** 2026-07-30 · **Status:** approved (interactive session)

Search the corpus for papers on a topic and get results ordered the way Google
used PageRank: text match decides the *candidate set*, the trust graph decides
the *order*. Ordering fuses text relevance with MeritRank via Reciprocal Rank
Fusion (RRF); displayed numbers remain genuine MeritRank scores with real
uncertainty, so the "no bare scores" house rule stays honest.

## Decisions taken

- **Blended ordering (RRF), not pure merit.** User's call. RRF is an *ordering*
  mechanism only; the fused value is never displayed as a score.
- **Scope: API + web UI.** New Search route; the TrustSet picker is untouched.
- **Rejected:** topic-node search as primary path (coarse OpenAlex topics, no
  free text); tunable fusion weight (defer to params playground if ever);
  changes to pool computation.

## API

Extend `GET /api/papers/search` with `rank=relevance|trust|global`
(default `relevance` — current behaviour and response shape are preserved
byte-for-byte).

For `rank=trust` and `rank=global`:

- **Retrieval:** top **K=500** candidates by text relevance — `ts_rank` on the
  weighted tsv, with the existing trigram fallback path ranked by
  `similarity()`. Year filters and the user-upload visibility rules apply
  exactly as today.
- **Merit source:** `trust` → the profile's cached pool
  (`services.build_pool`, aggregate context, `exclude_trusted=False` — a paper
  you trust is still a search result). `global` → `services.global_scores`.
  Candidates absent from the 12k-row pool/table share last place.
- **Fusion:** `RRF(d) = 1/(60 + rank_text(d)) + 1/(60 + rank_merit(d))`,
  k = 60. Deterministic tiebreak: merit rank, then text rank, then work id.
  `limit`/`offset` paginate the fused list. `total` = candidate-set size
  (≤ K), not the raw match count — the response says so in the disclaimer.
- **Response (ranked modes):** items are `ScoredPaper` (real `trust`,
  `uncertainty`, `tie_group`, `global_merit`, `disagreement` — same plumbing
  as `/rankings`) **plus** `relevance_rank` and `merit_rank` per item, so
  every position is explainable. Response carries `cold_start` and a
  `disclaimer` naming the blend: ordering fuses text relevance with trust
  proximity; the trust column is the MeritRank value.
- **Fallbacks:** `rank=trust` with no profile **or zero seeds** degrades to
  `global`, and `cold_start.message` says so. Fewer than 5 seeds → standard
  unreliable warning. Engine failures go through the existing `engine_retry`.
  For `rank=global` the `trust` field is the global value and the disclaimer
  says the ranking is unpersonalised.

## Web UI

New **Search** route in the nav:

- Query box (min 2 chars) + year filters.
- Three-way sort toggle: **Relevance / Your trust / Global merit** → maps to
  the `rank` param. Relevance mode renders plain brief rows (today's picker
  look); ranked modes render via the existing `RankingTable`/`ScoreBar`
  components with the disagreement column.
- Each row: add-to-trust-set action (StrengthPicker), link to paper page and
  its explanation.
- Cold-start and fallback messages rendered verbatim, per house style.

## Error handling

- `q` under 2 chars → 422 (unchanged). Unknown `rank` value → 422.
- Engine down: `engine_retry` semantics; on final failure the API returns the
  existing 503 shape and the UI shows the standard error state.
- Zero text matches: empty ranked response with `total: 0` (no merit-only
  results — retrieval defines the candidate set).

## Testing

- **API:** fusion determinism (fixed inputs → fixed order); each `rank` mode;
  no-profile / zero-seed / <5-seed fallbacks; pagination over the fused list;
  `rank=relevance` byte-compatibility with today's response; user-upload
  visibility in ranked modes.
- **Web:** component test for the toggle and both row shapes.
- **E2E:** seed trust → search a topic → assert relevance order ≠ trust order
  and that scores carry error bars.
- Engine-touching tests run deferred, not interleaved (shared-stack rule;
  advisory lock 919191001).
