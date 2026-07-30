# Merit-Ranked Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `GET /api/papers/search` gains `rank=trust|global` modes that order text matches by Reciprocal Rank Fusion of text relevance and MeritRank, plus a web Search route with a Relevance / Your trust / Global merit toggle.

**Architecture:** Retrieval stays exactly the current tsvector/trigram query (top K=500 for ranked modes); a new pure module `searchrank.py` computes merit ranks and RRF; the router builds `RankedSearchPaper` items (real MeritRank `trust` + `uncertainty` from the profile's cached pool, RRF only decides *order*). The web app adds a Search screen reusing `RankingTable` (extended with an optional actions column) for ranked modes.

**Tech Stack:** FastAPI + SQLAlchemy + pydantic v2 (api), pytest against the live docker stack, React + TanStack Query + vitest (web), Playwright (e2e).

**Spec:** `docs/superpowers/specs/2026-07-30-merit-ranked-search-design.md` — read it first.

## Global Constraints

- **No bare scores:** every displayed score carries `uncertainty` with `tie_group`; RRF values are never displayed as scores.
- `rank=relevance` (the default) must keep today's response **byte-for-byte**.
- Fusion: `RRF(d) = 1/(60 + rank_text) + 1/(60 + rank_merit)`, K=500 candidates, tiebreak merit rank → text rank → work id. Candidates absent from the merit table share last place (`len(table)+1`).
- `rank=trust` uses `services.build_pool(db, profile, context="aggregate", exclude_trusted=False)`; no profile or zero seeds degrades to global with a `cold_start.message` saying so.
- API tests hit the **live stack** (db + mr-service up, Postgres on host port 55432). Do not mock the engine. Do not run engine-touching tests while another session is using the stack (advisory lock 919191001 serialises upload tests; ranked-search tests only read).
- Commit style: imperative subject, body explains why; end with the Co-Authored-By/Claude-Session trailer used on this branch. Never `git add -A` (a parallel session may share the tree); add files by name.

---

### Task 1: RRF fusion module (pure, no stack needed)

**Files:**
- Create: `api/provenance/searchrank.py`
- Test: `api/tests/test_searchrank.py`

**Interfaces:**
- Produces: `searchrank.FETCH_K: int = 500`, `searchrank.RRF_K: int = 60`,
  `searchrank.Fused` (frozen dataclass: `work_id: str, relevance_rank: int, merit_rank: int, rrf: float`),
  `searchrank.merit_ranks(values: dict[str, float]) -> dict[str, int]`,
  `searchrank.fuse(text_ids: list[str], merit_rank: dict[str, int], k: int = RRF_K) -> list[Fused]`.
  Task 2 imports all of these.

- [ ] **Step 1: Write the failing tests**

`api/tests/test_searchrank.py` (pure unit tests — no `client` fixture, no db):

```python
"""Pure unit tests for RRF fusion. No stack required."""
from provenance.searchrank import RRF_K, Fused, fuse, merit_ranks


def test_merit_ranks_orders_desc_and_breaks_ties_by_id():
    ranks = merit_ranks({"W3": 0.5, "W1": 0.9, "W2": 0.5})
    assert ranks == {"W1": 1, "W2": 2, "W3": 3}  # tie 0.5: W2 before W3 by id


def test_fuse_rrf_arithmetic():
    # text order: A(1), B(2); merit: B=1, A=2
    out = fuse(["WA", "WB"], {"WB": 1, "WA": 2})
    by_id = {f.work_id: f for f in out}
    assert by_id["WA"].rrf == 1 / (RRF_K + 1) + 1 / (RRF_K + 2)
    assert by_id["WB"].rrf == 1 / (RRF_K + 2) + 1 / (RRF_K + 1)
    # equal RRF -> tiebreak by merit rank: B (merit 1) first
    assert [f.work_id for f in out] == ["WB", "WA"]


def test_fuse_missing_merit_is_last_place():
    out = fuse(["WA", "WB"], {"WA": 1})  # WB unknown to merit table of size 1
    wb = next(f for f in out if f.work_id == "WB")
    assert wb.merit_rank == 2  # len(merit_rank) + 1
    assert out[0].work_id == "WA"


def test_fuse_good_text_match_beats_weak_match_near_trust():
    # 3rd-best text match with top merit outranks 40th-best text match with merit 2.
    text_ids = [f"W{n:03d}" for n in range(1, 51)]
    out = fuse(text_ids, {"W003": 1, "W040": 2})
    pos = {f.work_id: i for i, f in enumerate(out)}
    assert pos["W003"] < pos["W040"]


def test_fuse_is_deterministic():
    text_ids = [f"W{n:03d}" for n in range(1, 501)]
    merit = {f"W{n:03d}": 1.0 / n for n in range(500, 0, -2)}
    a = fuse(text_ids, merit_ranks(merit))
    b = fuse(list(text_ids), merit_ranks(dict(merit)))
    assert a == b
    assert all(isinstance(f, Fused) for f in a)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd api && python -m pytest tests/test_searchrank.py -v`
Expected: FAIL / error with `ModuleNotFoundError: No module named 'provenance.searchrank'`
(If the import of `provenance` itself fails because config wants env vars, run with the same env the existing suite uses.)

- [ ] **Step 3: Write the module**

`api/provenance/searchrank.py`:

```python
"""Reciprocal Rank Fusion for merit-ranked search.

Spec: docs/superpowers/specs/2026-07-30-merit-ranked-search-design.md.
RRF decides *order* only; the fused value is never a displayed score, so the
"no bare scores" rule is untouched -- displayed numbers remain MeritRank
values with their own uncertainty.
"""
from __future__ import annotations

from dataclasses import dataclass

# Candidate set: top-K text matches feed the fusion. Everything past K is
# invisible to ranked search and the response disclaimer says so.
FETCH_K = 500
# The standard RRF constant (Cormack et al. 2009).
RRF_K = 60


@dataclass(frozen=True)
class Fused:
    work_id: str
    relevance_rank: int  # 1-based position in the text-relevance order
    merit_rank: int      # 1-based position in the merit order; absent = last place
    rrf: float


def merit_ranks(values: dict[str, float]) -> dict[str, int]:
    """1-based ordinal ranks by descending value, ties broken by id.

    Ordinal (not dense) ranks, id-tiebroken, so the fusion is deterministic
    for a given score table regardless of dict insertion order.
    """
    ordered = sorted(values.items(), key=lambda kv: (-kv[1], kv[0]))
    return {wid: n for n, (wid, _v) in enumerate(ordered, start=1)}


def fuse(text_ids: list[str], merit_rank: dict[str, int], k: int = RRF_K) -> list[Fused]:
    last = len(merit_rank) + 1
    out: list[Fused] = []
    for n, wid in enumerate(text_ids, start=1):
        mr = merit_rank.get(wid, last)
        out.append(Fused(wid, n, mr, 1.0 / (k + n) + 1.0 / (k + mr)))
    out.sort(key=lambda f: (-f.rrf, f.merit_rank, f.relevance_rank, f.work_id))
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd api && python -m pytest tests/test_searchrank.py -v`
Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git add api/provenance/searchrank.py api/tests/test_searchrank.py
git commit -m "feat: RRF fusion module for merit-ranked search"
```

---

### Task 2: Ranked modes on `GET /api/papers/search`

**Files:**
- Modify: `api/provenance/schemas.py` (after `SearchResponse`, ~line 150)
- Modify: `api/provenance/routers/papers.py` (the `search` endpoint)
- Modify: `API_CONTRACT.md` (the `/papers/search` section)
- Test: `api/tests/test_search_rrf.py` (new; live stack)

**Interfaces:**
- Consumes: `searchrank.FETCH_K/merit_ranks/fuse` (Task 1); existing
  `services.build_pool`, `services.global_scores`, `services.rank_percentiles`,
  `services.paper_briefs`, `services.brief_or_placeholder`, `services.to_uncertainty`,
  `services.disagreement`, `services.cold_start`, `services.CITATION_PCT`,
  `meritrank.assign_tie_groups`, `meritrank.Uncertainty`, `config.DISCLAIMER`.
- Produces (Tasks 3–5 rely on these wire shapes):
  `RankedSearchPaper = ScoredPaper + {relevance_rank: int, merit_rank: int}`;
  `RankedSearchResponse = {total: int, items: RankedSearchPaper[], cold_start: ColdStart, disclaimer: str, rank: "trust"|"global"}`
  (`rank` is the **effective** mode after any fallback);
  query param `rank=relevance|trust|global` (default `relevance`).

- [ ] **Step 1: Write the failing tests**

`api/tests/test_search_rrf.py`. Follow the house pattern: `client` fixture comes from
`conftest.py`; profile creation and trust seeding exactly as `test_api.py` does it
(open `api/tests/test_api.py`, copy its helper for creating a profile and adding
trust — reuse, don't reinvent). The tests:

```python
"""Ranked search (RRF blend). Live stack; read-mostly, one seeded profile."""
from __future__ import annotations

import pytest
from sqlalchemy import text

from provenance.db import SessionLocal
from provenance.searchrank import FETCH_K, fuse, merit_ranks

QUERY = "graph"  # broad token; assert non-empty and skip if the corpus lacks it


@pytest.fixture()
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _search(client, **params):
    r = client.get("/api/papers/search", params={"q": QUERY, **params})
    assert r.status_code == 200, r.text
    return r.json()


def test_relevance_mode_is_byte_compatible(client):
    default = client.get("/api/papers/search", params={"q": QUERY, "limit": 10})
    explicit = client.get("/api/papers/search",
                          params={"q": QUERY, "limit": 10, "rank": "relevance"})
    assert default.status_code == explicit.status_code == 200
    assert default.content == explicit.content
    body = default.json()
    assert set(body) == {"total", "items"}  # old shape, nothing added


def test_bad_rank_value_is_422(client):
    r = client.get("/api/papers/search", params={"q": QUERY, "rank": "pagerank"})
    assert r.status_code == 422


def test_global_mode_shape_and_order(client, db):
    body = _search(client, rank="global", limit=25)
    if body["total"] == 0:
        pytest.skip(f"corpus has no match for {QUERY!r}")
    assert body["rank"] == "global"
    assert "unpersonalised" in body["disclaimer"]
    items = body["items"]
    assert items, "non-zero total but empty first page"
    for it in items:
        # no bare scores
        assert {"trust", "uncertainty", "global_merit", "rank", "disagreement",
                "relevance_rank", "merit_rank"} <= set(it)
        assert it["uncertainty"]["tie_group"] >= 0
    assert [it["rank"] for it in items] == list(range(1, len(items) + 1))
    # the order is genuinely RRF of (text order, global merit order)
    from provenance import services
    text_ids = [r_[0] for r_ in db.execute(text(
        "SELECT w.id FROM works w "
        "WHERE w.tsv @@ plainto_tsquery('english', :q) "
        "AND w.source <> 'user_upload' "
        "ORDER BY ts_rank(w.tsv, plainto_tsquery('english', :q)) DESC,"
        " w.cited_by_count DESC LIMIT :k"), {"q": QUERY, "k": FETCH_K}).all()]
    expected = [f.work_id for f in
                fuse(text_ids, merit_ranks(services.global_scores(db)))]
    assert [it["id"] for it in items] == expected[:len(items)]


def test_global_mode_pagination_is_stable(client):
    page1 = _search(client, rank="global", limit=5, offset=0)
    page2 = _search(client, rank="global", limit=5, offset=5)
    if page1["total"] < 10:
        pytest.skip("not enough matches to paginate")
    ids1 = [it["id"] for it in page1["items"]]
    ids2 = [it["id"] for it in page2["items"]]
    assert not set(ids1) & set(ids2)
    assert page1["total"] == page2["total"] <= FETCH_K


def test_trust_without_profile_falls_back_to_global(client):
    body = _search(client, rank="trust")  # conftest client sends no auth by default;
    # if the shared client carries a token, build an unauthenticated request instead
    # (see how test_api.py makes anonymous calls).
    assert body["rank"] == "global"
    assert body["cold_start"]["reliable"] is False
    assert body["cold_start"]["message"]  # says why it degraded


def test_trust_mode_with_seeds(client, db, warm_profile):
    """warm_profile: reuse the suite's existing seeded-profile fixture if one
    exists in conftest/test_api.py; otherwise create a profile, trust the 5 most
    in-corpus-cited non-stub works at strength 5, and wait for /rankings to
    return items (SLOW_TIMEOUT pattern from conftest)."""
    pid, token = warm_profile
    r = client.get("/api/papers/search",
                   params={"q": QUERY, "rank": "trust", "limit": 25},
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    body = r.json()
    if body["total"] == 0:
        pytest.skip(f"corpus has no match for {QUERY!r}")
    assert body["rank"] == "trust"
    assert body["cold_start"]["seeds"] >= 5
    # trust values are the profile's MeritRank scores: at least one non-zero,
    # and uncertainty is real (not all zeros) for pooled items.
    assert any(it["trust"] > 0 for it in body["items"])
```

Notes for the implementer:
- If `test_api.py` has no reusable seeded-profile fixture, add one **in this file**
  (module-scoped) using the exact trust-seeding + polling code `test_api.py` uses.
- Do not name the fixture after or reuse the uploads advisory-lock machinery; these
  tests only read the engine.

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose up -d db mr-service` (if not already up), then
`cd api && python -m pytest tests/test_search_rrf.py -v`
Expected: `test_relevance_mode_is_byte_compatible` PASSES already (nothing changed);
every ranked-mode test FAILS — `rank` is silently ignored today, so shape assertions
(`body["rank"]`) fail with KeyError, and `test_bad_rank_value_is_422` fails with 200.

- [ ] **Step 3: Add the schemas**

In `api/provenance/schemas.py`, directly after `class SearchResponse`:

```python
class RankedSearchPaper(ScoredPaper):
    """A search result whose *order* came from RRF; the scores are MeritRank."""
    relevance_rank: int
    merit_rank: int


class RankedSearchResponse(BaseModel):
    total: int
    items: list[RankedSearchPaper]
    cold_start: "ColdStart"
    disclaimer: str
    # The effective mode after fallback: a trust request with no usable profile
    # comes back as "global", and cold_start.message says why.
    rank: Literal["trust", "global"]
```

(`ColdStart` is defined later in the file — the forward reference resolves at
import time because pydantic rebuilds models; if it does not, move these two
classes below `ColdStart` instead. `Literal` is already imported in this file;
if not, add it to the `typing` import.)

- [ ] **Step 4: Implement the endpoint**

In `api/provenance/routers/papers.py`:

1. Add imports: `from .. import searchrank` and
   `from ..meritrank import Uncertainty as MrUncertainty, assign_tie_groups`.
2. Extract the candidate query. The current body of `search()` builds `year_sql`,
   `params`, then runs count + page queries with the trigram fallback. Pull the
   two-branch retrieval into a module-level helper **without changing any SQL
   string**:

```python
def _text_candidates(
    db, params: dict[str, object], year_sql: str, limit: int, offset: int,
) -> tuple[int, list[str]]:
    """Total match count and one page of ids in text-relevance order.

    Exactly the retrieval the relevance mode has always used -- tsvector first,
    trigram fallback -- so ranked modes inherit its behaviour (weighted tsv,
    typo tolerance, visibility filters baked into year_sql/params).
    """
    p = {**params, "lim": limit, "off": offset}
    total = int(db.execute(text(
        "SELECT count(*) FROM works w "
        "WHERE w.tsv @@ plainto_tsquery('english', :q)" + year_sql
    ), p).scalar_one())
    if total:
        rows = db.execute(text(
            "SELECT w.id FROM works w "
            "WHERE w.tsv @@ plainto_tsquery('english', :q)" + year_sql +
            " ORDER BY ts_rank(w.tsv, plainto_tsquery('english', :q)) DESC,"
            " w.cited_by_count DESC LIMIT :lim OFFSET :off"
        ), p).all()
    else:
        db.execute(text("SELECT set_config('pg_trgm.similarity_threshold', :t, true)"),
                   {"t": str(TRGM_THRESHOLD)})
        total = int(db.execute(text(
            "SELECT count(*) FROM works w WHERE w.title % :q" + year_sql
        ), p).scalar_one())
        rows = db.execute(text(
            "SELECT w.id FROM works w WHERE w.title % :q" + year_sql +
            " ORDER BY similarity(w.title, :q) DESC, w.cited_by_count DESC"
            " LIMIT :lim OFFSET :off"
        ), p).all()
    return total, [r[0] for r in rows]
```

3. `search()` gains the param
   `rank: str = Query(default="relevance", pattern="^(relevance|trust|global)$")`
   and its `response_model` becomes
   `Union[schemas.RankedSearchResponse, schemas.SearchResponse]`
   (`RankedSearchResponse` FIRST, so ranked items keep their extra fields;
   verify byte-compatibility with the Task-2 test — if FastAPI's union
   serialisation changes the relevance bytes, drop to `response_model=None`
   and return the models directly). The relevance path calls
   `_text_candidates(db, params, year_sql, limit, offset)` and returns the
   `SearchResponse` exactly as today.
4. Ranked branch, after the visibility/year_sql setup:

```python
if rank != "relevance":
    total, cand = _text_candidates(db, params, year_sql, searchrank.FETCH_K, 0)
    effective, pool = rank, None
    if rank == "trust":
        if maybe_profile is None:
            effective = "global"
            cold = schemas.ColdStart(seeds=0, reliable=False, message=(
                "You asked for trust-ranked search without a profile, so this "
                "ordering is unpersonalised global merit. Create a profile and "
                "trust a few papers to personalise it."))
        else:
            pool = services.build_pool(db, maybe_profile, context="aggregate",
                                       exclude_trusted=False)
            if pool.seeds == 0:
                effective, pool = "global", None
                cold = schemas.ColdStart(seeds=0, reliable=False, message=(
                    "You asked for trust-ranked search but your trust set is "
                    "empty, so this ordering is unpersonalised global merit. "
                    "Trust a few papers to personalise it."))
            else:
                cold = services.cold_start(pool.seeds)
    else:
        cold = schemas.ColdStart(seeds=0, reliable=True, message=(
            "This ordering is unpersonalised: global merit, the same for "
            "everyone, not proximity to your trust set."))

    gvals = services.global_scores(db)
    merit_values = pool.trust_values if pool is not None else gvals
    fused = searchrank.fuse(cand, searchrank.merit_ranks(merit_values))

    # Scores for the whole candidate set, tie groups assigned over the
    # *displayed* (fused) order so brackets are stable across pages.
    by_id = pool.by_id() if pool is not None else {}
    n_samples = max(pool.seeds if pool is not None else 0, 1)
    triples: list[tuple[str, float, MrUncertainty]] = []
    for f in fused:
        item = by_id.get(f.work_id)
        if pool is not None and item is not None:
            triples.append((f.work_id, item.trust, item.uncertainty))
        else:
            v = 0.0 if pool is not None else merit_values.get(f.work_id, 0.0)
            triples.append((f.work_id, v, MrUncertainty(
                abs(v) * 0.5, max(0.0, v * 0.5), v * 1.5, 0,
                "proportional_fallback", n_samples)))
    assign_tie_groups(triples)
    trust_of = {wid: (v, u) for wid, v, u in triples}

    if pool is not None:
        trust_pct, global_pct = pool.trust_pct, pool.global_pct
    else:
        global_pct = services.rank_percentiles(gvals)
        trust_pct = global_pct  # global mode: personal == global by construction

    page = fused[offset:offset + limit]
    briefs = services.paper_briefs(db, [f.work_id for f in page])
    items: list[schemas.RankedSearchPaper] = []
    for n, f in enumerate(page, start=offset + 1):
        brief = services.brief_or_placeholder(briefs, f.work_id)
        v, unc = trust_of[f.work_id]
        p_cit = services.CITATION_PCT.percentile(db, brief.cited_by_count)
        items.append(schemas.RankedSearchPaper(
            **brief.model_dump(),
            trust=v,
            uncertainty=services.to_uncertainty(unc),
            global_merit=gvals.get(f.work_id, 0.0),
            rank=n,
            disagreement=services.disagreement(
                trust_pct.get(f.work_id, 0.0), global_pct.get(f.work_id, 0.0), p_cit),
            relevance_rank=f.relevance_rank,
            merit_rank=f.merit_rank,
        ))

    blend = (" Ordering fuses text relevance with "
             + ("proximity to your trust set"
                if effective == "trust" else "unpersonalised global merit")
             + f" (reciprocal rank fusion); the trust column is the MeritRank "
               f"value, and only the top {searchrank.FETCH_K} text matches "
               f"are ranked.")
    return schemas.RankedSearchResponse(
        total=min(total, searchrank.FETCH_K), items=items, cold_start=cold,
        disclaimer=config.DISCLAIMER + blend, rank=effective)
```

Notes:
- `total` is the fused candidate-set size (`min(total, FETCH_K)`) per the spec.
- Zero matches: `cand == []`, so `fused == []`, `items == []`, `total == 0` — the
  branch needs no special-casing, but confirm the trigram fallback ran (it is inside
  `_text_candidates`).
- The `trust` field in global mode **is** the global value — spec decision; the
  disclaimer and `rank: "global"` carry the honesty.

5. Update `API_CONTRACT.md`'s `/papers/search` section: document `rank`, the
   `RankedSearchResponse` shape (with `relevance_rank`/`merit_rank`, `rank`
   effective-mode field), the K=500 window, and the fallback rule. Follow the
   contract file's existing voice.

- [ ] **Step 5: Run the tests**

Run: `cd api && python -m pytest tests/test_search_rrf.py tests/test_searchrank.py tests/test_search_ranking.py -v`
Expected: all PASS (`test_search_ranking.py` proves relevance retrieval untouched).
The seeded-profile test may take minutes on a cold engine — that is normal
(`SLOW_TIMEOUT` in conftest is 600s).

- [ ] **Step 6: Run the full API suite once**

Run: `cd api && python -m pytest tests/ -v -x --ignore=tests/test_pdfbib_pdf.py`
Expected: PASS (pdfbib PDF tests need fixtures irrelevant here; include them if they
were passing before your change — check `git stash`-free baseline first).

- [ ] **Step 7: Commit**

```bash
git add api/provenance/schemas.py api/provenance/routers/papers.py api/tests/test_search_rrf.py API_CONTRACT.md
git commit -m "feat: rank=trust|global on /papers/search -- RRF of text relevance and MeritRank"
```

---

### Task 3: Web client plumbing (types, api, query hook)

**Files:**
- Modify: `web/src/lib/types.ts` (add types next to `SearchResponse`/`ScoredPaper`)
- Modify: `web/src/lib/api.ts` (extend `searchPapers` args; add `searchPapersRanked`)
- Modify: `web/src/lib/queries.ts` (add `useRankedSearch`, `usePaperSearch`)

**Interfaces:**
- Consumes: Task 2's wire shapes.
- Produces (Task 4 relies on these):
  `type RankMode = 'relevance' | 'trust' | 'global'`;
  `interface RankedSearchPaper extends ScoredPaper { relevance_rank: number; merit_rank: number }`;
  `interface RankedSearchResponse { total: number; items: RankedSearchPaper[]; cold_start: ColdStart; disclaimer: string; rank: 'trust' | 'global' }`;
  `api.searchPapersRanked(args: { q: string; rank: 'trust' | 'global'; year_from?: number; year_to?: number; limit?: number; offset?: number }, signal?: AbortSignal): Promise<RankedSearchResponse>`;
  `useRankedSearch(args, enabled: boolean): UseQueryResult<RankedSearchResponse>`;
  `usePaperSearch(args: { q: string; year_from?: number; year_to?: number; limit?: number; offset?: number }, enabled: boolean): UseQueryResult<SearchResponse>`.

- [ ] **Step 1: Add the types**

In `web/src/lib/types.ts`, next to the existing `SearchResponse` (find `ColdStart` —
it already exists for `RankingsResponse`):

```ts
export type RankMode = 'relevance' | 'trust' | 'global';

/** A search result whose *order* came from RRF; the scores are MeritRank. */
export interface RankedSearchPaper extends ScoredPaper {
  relevance_rank: number;
  merit_rank: number;
}

export interface RankedSearchResponse {
  total: number;
  items: RankedSearchPaper[];
  cold_start: ColdStart;
  disclaimer: string;
  /** Effective mode after fallback — a trust request can come back 'global'. */
  rank: 'trust' | 'global';
}
```

- [ ] **Step 2: Add the client function**

In `web/src/lib/api.ts` (import the two new types from `./types`), next to
`searchPapers`:

```ts
searchPapersRanked: (
  args: {
    q: string;
    rank: 'trust' | 'global';
    year_from?: number;
    year_to?: number;
    limit?: number;
    offset?: number;
  },
  signal?: AbortSignal,
) =>
  request<RankedSearchResponse>(
    `/papers/search${buildQuery({
      q: args.q,
      rank: args.rank,
      year_from: args.year_from,
      year_to: args.year_to,
      limit: args.limit ?? 25,
      offset: args.offset,
    })}`,
    { signal },
  ),
```

- [ ] **Step 3: Add the hooks**

In `web/src/lib/queries.ts`, following the file's existing `useQuery` pattern
(look at `useRankings` for queryKey/enabled/signal style — mirror it exactly,
including how it debounces or gates on `enabled`):

```ts
export function usePaperSearch(
  args: { q: string; year_from?: number; year_to?: number; limit?: number; offset?: number },
  enabled: boolean,
): UseQueryResult<SearchResponse> {
  return useQuery({
    queryKey: ['paper-search', args],
    queryFn: ({ signal }) => api.searchPapers(args, signal),
    enabled: enabled && args.q.trim().length >= 2,
  });
}

export function useRankedSearch(
  args: {
    q: string;
    rank: 'trust' | 'global';
    year_from?: number;
    year_to?: number;
    limit?: number;
    offset?: number;
  },
  enabled: boolean,
): UseQueryResult<RankedSearchResponse> {
  return useQuery({
    queryKey: ['paper-search-ranked', args],
    queryFn: ({ signal }) => api.searchPapersRanked(args, signal),
    enabled: enabled && args.q.trim().length >= 2,
  });
}
```

Add whatever `keys.*` entry the file's convention uses if it centralises query
keys (check the `keys` object near the top; if search keys belong there, follow
suit — consistency over the literal snippet above).

- [ ] **Step 4: Typecheck**

Run: `cd web && npm run typecheck`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/types.ts web/src/lib/api.ts web/src/lib/queries.ts
git commit -m "feat: web client plumbing for ranked search"
```

---

### Task 4: Search screen, nav entry, RankingTable actions column

**Files:**
- Modify: `web/src/components/RankingTable.tsx` (optional `renderActions` prop)
- Create: `web/src/routes/Search.tsx`
- Modify: `web/src/App.tsx` (route) and `web/src/components/AppShell.tsx` (nav link)
- Test: `web/src/test/search.test.tsx`

**Interfaces:**
- Consumes: Task 3's hooks/types; existing `RankingTable`, `StrengthPicker`,
  `useSetTrust`, `useSession`, `ExplainContent`/`SidePanel`, `Honesty` components
  (`Disclaimer`, `Notice`), `States` (`ErrorState`), `ui` primitives, `PaperTitle`.
- Produces: route `/search`; exported `SearchScreen`; exported pure helper
  `describePosition(p: RankedSearchPaper): string` (used by the test);
  `RankingTable` gains `renderActions?: (paper: ScoredPaper) => ReactNode`.

- [ ] **Step 1: Write the failing test**

`web/src/test/search.test.tsx`:

```tsx
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { RankingTable } from '@/components/RankingTable';
import { describePosition } from '@/routes/Search';
import type { RankedSearchPaper, ScoredPaper } from '@/lib/types';

const base: ScoredPaper = {
  id: 'W1',
  title: 'A paper',
  year: 2019,
  authors: [],
  venue: null,
  cited_by_count: 10,
  in_corpus_cited_by: 3,
  is_stub: false,
  doi: null,
  trust: 0.01,
  uncertainty: {
    stderr: 0.001, ci_low: 0.008, ci_high: 0.012,
    tie_group: 1, method: 'leave_one_out', n_samples: 5,
  },
  global_merit: 0.02,
  rank: 1,
  disagreement: 0.1,
  lift: 0,
  lift_uncertainty: null,
};

describe('ranked search presentation', () => {
  it('explains a position from its two component ranks', () => {
    const p: RankedSearchPaper = { ...base, relevance_rank: 2, merit_rank: 14 };
    const s = describePosition(p);
    expect(s).toMatch(/2\w* by text relevance/i);
    expect(s).toMatch(/14\w* by merit/i);
  });

  it('renders an actions column when renderActions is provided', () => {
    render(
      <RankingTable
        items={[base]}
        onExplain={() => undefined}
        renderActions={(paper) => <button type="button">Trust {paper.id}</button>}
      />,
    );
    expect(screen.getByRole('button', { name: 'Trust W1' })).toBeInTheDocument();
  });
});
```

(If `RankingTable` needs a router or query provider in tests, wrap with the same
providers other component tests in `web/src/test/` use — check `lift.test.tsx`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web && npx vitest run src/test/search.test.tsx`
Expected: FAIL — `@/routes/Search` does not exist; `renderActions` is not a prop.

- [ ] **Step 3: Extend RankingTable**

In `web/src/components/RankingTable.tsx`: add to the props
`renderActions?: (paper: ScoredPaper) => ReactNode;` (import `ReactNode` from
react). When provided, append one `<th>` labelled `Actions` (visually-hidden
label is fine if the table style prefers icon columns) and one `<td>` per row
rendering `renderActions(paper)`. Follow the file's existing header/cell
classNames exactly; touch nothing else about sorting or tie rendering.

- [ ] **Step 4: Write the Search screen**

`web/src/routes/Search.tsx`. Model the layout and URL-state handling on
`Rankings.tsx` (read it first; reuse its `update(patch)` pattern verbatim).
Core structure:

```tsx
import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import type { RankedSearchPaper, RankMode, ScoredPaper } from '@/lib/types';
import { usePaperSearch, useRankedSearch, useSeedCount, useSetTrust } from '@/lib/queries';
import { useSession } from '@/lib/session';
import { RankingTable } from '@/components/RankingTable';
import { Disclaimer, Notice } from '@/components/Honesty';
import { ErrorState } from '@/components/States';
import { StrengthPicker } from '@/components/StrengthPicker';
import { SidePanel } from '@/components/ui/Dialog';
import { ExplainContent } from '@/components/ExplainPanel';
import { Field, Input } from '@/components/ui/Input';
import { PaperTitle } from '@/components/Math';

const MODES: { value: RankMode; label: string; gloss: string }[] = [
  { value: 'relevance', label: 'Relevance', gloss: 'Text match alone — the classic picker order.' },
  { value: 'trust', label: 'Your trust', gloss: 'Text matches re-ordered by blending relevance with proximity to your trust set (RRF).' },
  { value: 'global', label: 'Global merit', gloss: 'Text matches re-ordered by blending relevance with unpersonalised merit (RRF).' },
];

/** "#3 — 2nd by text relevance, 14th by merit": why a row sits where it sits. */
export function describePosition(p: RankedSearchPaper): string {
  const ord = (n: number): string => {
    if (n % 100 >= 11 && n % 100 <= 13) return `${n}th`;
    const suffix = { 1: 'st', 2: 'nd', 3: 'rd' }[n % 10] ?? 'th';
    return `${n}${suffix}`;
  };
  return `${ord(p.relevance_rank)} by text relevance, ${ord(p.merit_rank)} by merit`;
}

export function SearchScreen(): JSX.Element {
  // URL state: q, mode, year_from, year_to, offset (mirror Rankings.tsx's
  // useSearchParams + update() helper).
  // - input with its own local state, committed to the URL on submit/debounce
  // - mode toggle: three buttons (segmented control style used elsewhere)
  // - relevance mode: usePaperSearch -> simple result list rows: PaperTitle,
  //   authors/year/venue line, cited_by_count, and a StrengthPicker wired to
  //   useSetTrust (mirror how TrustSet.tsx renders its picker rows)
  // - ranked modes: useRankedSearch -> <RankingTable items={data.items}
  //   onExplain={setExplaining} renderActions={quickTrust} /> plus:
  //   - <Notice> with cold_start.message when non-null
  //   - <Disclaimer text={data.disclaimer} />
  //   - effective-mode banner: if mode === 'trust' && data.rank === 'global',
  //     the cold_start message already explains the fallback — render it, don't invent copy
  //   - per-row secondary line via describePosition(p) (pass through a cell or
  //     title attribute consistent with RankingTable's row rendering; if the
  //     table cannot host it cleanly, show it in the explain side panel header)
  // - explain side panel: same SidePanel + ExplainContent wiring as Rankings.tsx
  //   (only when a profile exists; global-mode rows without a profile get no explain)
  // - pagination: same offset/limit controls as Rankings.tsx
  // - empty state: "No matches." with the min-2-chars hint
}
```

The comment block above is the checklist for the JSX — write real components for
each line, reusing Rankings.tsx and TrustSet.tsx code as the source for idiom
(loading skeletons, error states, focus handling). Keep the file under ~300
lines; extract a `ResultRow` subcomponent for the relevance mode.

- [ ] **Step 5: Wire route and nav**

- `web/src/App.tsx`: import `SearchScreen` from `./routes/Search`; add inside the
  `AppShell` route group, next to the `trust` route:

```tsx
<Route
  path="search"
  element={
    <SessionGate>
      <SearchScreen />
    </SessionGate>
  }
/>
```

- `web/src/components/AppShell.tsx`: add `{ to: '/search', label: 'Search' }` to
  the nav-link list (find the array/JSX the existing `NavLink`s come from, insert
  Search after the Rankings/home link, matching the existing className callback).

- [ ] **Step 6: Run tests and typecheck**

Run: `cd web && npx vitest run && npm run typecheck`
Expected: all PASS, clean typecheck.

- [ ] **Step 7: Commit**

```bash
git add web/src/routes/Search.tsx web/src/components/RankingTable.tsx web/src/App.tsx web/src/components/AppShell.tsx web/src/test/search.test.tsx
git commit -m "feat: Search screen with Relevance / Your trust / Global merit toggle"
```

---

### Task 5: End-to-end flow

**Files:**
- Create: `e2e/tests/11-search.spec.ts`

**Interfaces:**
- Consumes: the warm seeded profile from `e2e/global-setup.ts`
  (`warmProfile()` / `WARM_STATE` in `e2e/helpers/app.ts` — read `01-journey.spec.ts`
  for how specs adopt the warm storage state), the running full stack, route
  `/search` from Task 4.

- [ ] **Step 1: Write the spec**

`e2e/tests/11-search.spec.ts` — follow the structure of `02-screens.spec.ts`
(storage state, screenshots dir, console-error guard if the suite uses one):

```ts
import { expect, test } from '@playwright/test';
import { WARM_STATE } from '../helpers/app';

test.use({ storageState: WARM_STATE });

const QUERY = 'graph';

test('search re-orders under trust mode and shows honest scores', async ({ page }) => {
  await page.goto('/search');
  await page.getByRole('textbox').first().fill(QUERY);
  await page.keyboard.press('Enter');

  // Relevance mode: plain result rows appear.
  const relevanceIds = await page
    .locator('[data-testid="search-result"], table tbody tr')
    .evaluateAll((rows) => rows.map((r) => r.getAttribute('data-work-id') ?? r.textContent ?? ''));
  expect(relevanceIds.length).toBeGreaterThan(0);

  // Trust mode: ranked table with score bars and the blend disclaimer.
  await page.getByRole('button', { name: 'Your trust' }).click();
  await expect(page.getByText(/reciprocal rank fusion/i)).toBeVisible();
  await expect(page.locator('table tbody tr').first()).toBeVisible();
  const trustIds = await page
    .locator('table tbody tr')
    .evaluateAll((rows) => rows.map((r) => r.getAttribute('data-work-id') ?? r.textContent ?? ''));

  // The two orderings genuinely differ somewhere in the visible window.
  expect(trustIds.join('|')).not.toEqual(relevanceIds.join('|'));

  // No bare scores: at least one score bar announces its interval.
  await expect(page.getByRole('img').first()).toHaveAccessibleName(/\[/);

  await page.screenshot({ path: 'screenshots/search-trust-mode.png', fullPage: true });
});
```

Adapt selectors to what Task 4 actually rendered (add `data-testid="search-result"`
/ `data-work-id` attributes in Task 4's rows if missing — that is part of this
task; commit the attribute additions here). If the warm profile's trust set makes
the two orderings identical in the visible window for `QUERY`, pick a query that
overlaps the warm profile's field so trust proximity has something to say —
inspect `e2e/global-setup.ts` to see which papers it seeds.

- [ ] **Step 2: Run it**

Run: `docker compose up -d` (full stack), then `cd e2e && npx playwright test tests/11-search.spec.ts`
Expected: PASS, screenshot written.

- [ ] **Step 3: Run the whole e2e suite**

Run: `cd e2e && npx playwright test`
Expected: all specs PASS (the suite was 29/29 green before this branch).

- [ ] **Step 4: Commit**

```bash
git add e2e/tests/11-search.spec.ts
git commit -m "test: e2e -- trust-mode search re-orders results and keeps scores honest"
```

(Include any `data-testid` edits to `web/src/routes/Search.tsx` in this commit.)

---

### Task 6: Final verification and docs

**Files:**
- Modify: `README.md` (one bullet in "What it does"), `DEMO.md` if it walks screens.

- [ ] **Step 1: Full test sweep**

Run, in order (stack up):
- `cd api && python -m pytest tests/ --ignore=tests/test_pdfbib_pdf.py`
- `cd web && npx vitest run && npm run typecheck && npm run build`
- `cd e2e && npx playwright test`

Expected: everything green. Report any failure verbatim — do not paper over.

- [ ] **Step 2: Docs**

- README "What it does": add a bullet after "Trust set builder":
  `**Merit-ranked search** — search the corpus like Google used PageRank: text
  match picks the candidates, MeritRank (yours or global) re-orders them via
  reciprocal rank fusion, with the same error bars, tie brackets and
  explanations as every other ranking.`
- If `DEMO.md` enumerates screens, add the Search screen where it fits.

- [ ] **Step 3: Commit**

```bash
git add README.md DEMO.md
git commit -m "docs: merit-ranked search in README and demo notes"
```
