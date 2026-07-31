# Provenance API contract (v1)

Base URL `/api`. All responses JSON. Auth is an anonymous profile token sent as
`Authorization: Bearer <token>` **or** the `pv_token` cookie. OpenAPI at `/docs`.

Two rules that shape every response:

1. **No bare scores.** Every score is accompanied by uncertainty (`stderr`, `ci_low`,
   `ci_high`) and a `tie_group` integer. Items sharing a `tie_group` are statistically
   indistinguishable and the UI must present them as tied.
2. **Scores are proximity, not quality.** The field is named `trust` and every list
   response carries a `disclaimer` string the UI renders verbatim.

---

## Core types

```ts
type Uncertainty = {
  stderr: number;
  ci_low: number;
  ci_high: number;
  tie_group: number;      // equal value => statistically tied
  method: "leave_one_out" | "repeat_sample";
  n_samples: number;
};

type PaperBrief = {
  id: string;             // OpenAlex short id, e.g. "W2963757046"
  title: string | null;
  year: number | null;
  authors: { id: string; name: string }[];   // up to 6
  venue: { id: string; name: string } | null;
  cited_by_count: number;
  in_corpus_cited_by: number;
  is_stub: boolean;
  doi: string | null;
};

type ScoredPaper = PaperBrief & {
  trust: number;              // personalised MeritRank score
  uncertainty: Uncertainty;
  global_merit: number;       // unpersonalised MeritRank
  rank: number;
  disagreement: number;       // 0..1, how much trust/global/citations disagree
};
```

`disagreement` is the normalised spread across the percentile ranks of
(personal trust, global merit, raw citation count). High values mark the most
interesting papers in the system and the UI surfaces them.

---

## Endpoints

### `POST /api/profiles`
Body `{ label?: string }` → `201 { id, token, label, created_at, params }`.
Sets the `pv_token` cookie. No password, no email.

### `GET /api/profiles/me` → `{ id, label, params, trust_count, warmed_at }`

### `GET /api/papers/search`
Query: `q` (required, min 2 chars), `year_from`, `year_to`, `limit` (≤50), `offset`,
`rank` (`relevance` default | `trust` | `global`).
Postgres `tsvector` full-text with trigram fallback selects the candidate set;
`rank` only changes how that set is *ordered*.

`rank=relevance` (default, unchanged) → `{ total, items: PaperBrief[] }`.

`rank=trust` or `rank=global` re-order the top `FETCH_K` (500) text matches by
reciprocal rank fusion (RRF) of text-relevance order and MeritRank order, and
return:
```ts
type RankedSearchPaper = ScoredPaper & {
  relevance_rank: number;  // 1-based position in the text-relevance order
  merit_rank: number;      // 1-based position in the merit order
};

type RankedSearchResponse = {
  total: number;           // min(match count, 500)
  items: RankedSearchPaper[];
  cold_start: ColdStart;
  disclaimer: string;
  rank: "trust" | "global"; // the EFFECTIVE mode after any fallback
};
```
`rank=global` orders by unpersonalised global merit and is available with no
profile. `rank=trust` orders by proximity to the caller's trust set and requires
a profile with a non-empty trust set; if the request is anonymous or the trust
set is empty, the response silently falls back to `rank: "global"` and
`cold_start` explains why (`reliable: false`, a message). `trust` (and
`global_merit`) on each item are always the profile's real MeritRank/global
values -- in fallback, `trust` *is* the global value, and the disclaimer plus
`rank: "global"` carry the honesty. Only the top 500 text matches are ever
ranked or paginated; matches beyond that window are invisible to ranked search
(the disclaimer says so). `lift` (inherited from `ScoredPaper`) is never
computed for ranked search and is always `0.0` with `lift_uncertainty: null`;
consumers must not display it.

### `POST /api/profiles/{id}/trust`
Body `{ work_id: string, strength: 1|2|3|4|5, is_distrust?: boolean }`.
`strength: 0` removes the entry. → `{ trust_count, items: TrustEntry[] }`.
Triggers an async warm of the ego's walks.

### `GET /api/profiles/{id}/trust` → `{ items: TrustEntry[] }`
where `TrustEntry = { work: PaperBrief, strength: number, is_distrust: boolean }`.

### `GET /api/profiles/{id}/rankings`
Query: `limit` (≤100, default 25), `offset`, `year_from`, `year_to`, `context`
(one of `aggregate|citation|author|topic|venue|institution|coupling|cocitation`),
`exclude_trusted` (default true).
→
```ts
{
  items: ScoredPaper[];
  total: number;
  cold_start: { seeds: number; reliable: boolean; message: string | null };
  disclaimer: string;
  timing_ms: number;
}
```
`cold_start.reliable` is false below 5 seeds and the UI must say so.

### `GET /api/profiles/{id}/recommendations`
Query: `diversity` 0..1 (0 = exploitation/nearest trust set, 1 = exploration/high
merit but far away), `limit`.
→ `{ items: (ScoredPaper & { novelty: number; reason: string })[], diversity, disclaimer }`.

### `GET /api/profiles/{id}/papers/{pid}`
→ `{ paper: PaperBrief, trust: number, uncertainty, global_merit, cited_by_count,
     percentiles: { trust: number, global: number, citations: number },
     disagreement: number, in_trust_set: TrustEntry | null,
     topics: {id,name,score}[], institutions: {id,name,country}[] }`

### `GET /api/profiles/{id}/papers/{pid}/explain`
**The heart of the product.**
```ts
{
  target: PaperBrief;
  trust: number;
  uncertainty: Uncertainty;
  paths: {
    nodes: { id: string; kind: "paper"|"author"|"topic"|"venue"|"institution"|"profile";
             label: string }[];
    edges: { relation: string; weight: number }[];
    contribution: number;      // share of total, 0..1
    seed: PaperBrief;          // which trusted paper this path starts from
  }[];
  by_context: { context: string; score: number; marginal: number; share: number }[];
  summary: string;             // plain-language sentence
  caveat: string;
}
```
`paths` are reconstructed in Python over `graph_edges` (the engine returns no paths)
and ranked by the product of edge weights along the path. `by_context.marginal` is
`score(ctx) - score(citation)`; see DECISIONS.md D1.6 for why it is a *marginal*, not
an isolated, contribution.

### `GET /api/profiles/{id}/blindspots`
→ `{ items: (ScoredPaper & { gap: number })[] }` — high global merit, low personal trust.

### `GET /api/profiles/{id}/diversity`
→
```ts
{
  entropy: { topics: number; institutions: number; decades: number; countries: number };
  max_entropy: { ... };        // same keys, for normalisation
  concentration: { label: string; share: number }[];   // top concentrations
  echo_chamber_score: number;  // 0..1
  message: string;
}
```

### `POST /api/profiles/{id}/simulate`
Body `{ add?: {work_id, strength, is_distrust}[], remove?: string[], limit?: number }`
→ `{ before: ScoredPaper[], after: ScoredPaper[], moved: {work_id, delta_rank, delta_trust}[] }`.
Non-destructive: runs against a scratch ego node and tears it down.

### `GET /api/profiles/{id}/subgraph`
Query: `focus` (work id, optional), `limit` (≤3000), `context`.
→ `{ nodes: {id,label,kind,trust,year}[], edges: {source,target,relation,weight}[] }`
Shaped for graphology/sigma.js ingestion directly.

### `POST /api/profiles/{id}/params`
Body `{ context_weights?: Record<string,number>, alpha?: number,
        epoch_half_life_years?: number, num_walks?: number }`
→ the stored params. Re-ranks on next read. **Params that the engine does not actually
honour are rejected with 422 rather than silently ignored** — see KNOWN_ISSUES.md.

### `POST /api/import/bibtex`
`multipart/form-data` with `file`. Parses DOIs/titles, resolves against the corpus.
→ `{ matched: PaperBrief[], unmatched: string[], added: number }`

### `GET /api/health`
→ `{ ok, db, meritrank, graph_loaded, nodes, edges }`. `meritrank` is verified with a
real round trip (`mr_create_context`), never `mr_service()` — see DECISIONS.md D1.1.
