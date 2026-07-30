# Ranking Repair v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement R1–R5 of `docs/superpowers/specs/2026-07-30-ranking-repair-v2-design.md`: fix retrieval, add a deterministic scorer, expose fame-normalisation (`lift`) at γ=0.5 as a displayed field, replace `compose()`'s marginal sum with a weighted mean, and drop degree-1 entity nodes from the graph.

**Architecture:** The deterministic scorer (`propagate.py`) reads the persisted `graph_edges` table into scipy sparse and becomes the substrate for the background/lift computation; the MeritRank engine stays the default trust scorer until the correlation gate passes. Retrieval is fixed in Postgres (weighted tsvector + trigram boost). Every scoring change is gated by `scripts/eval_ranking.py` (three numbers: recall@25 CI, d≥2 recall, top-25 popularity percentile).

**Tech Stack:** FastAPI + SQLAlchemy + Alembic, Postgres (tsvector/pg_trgm), scipy/numpy (new api deps), React/TS frontend, pytest against the live compose stack (the repo's own convention, `api/tests/conftest.py`).

## Global Constraints

- **Shared working tree:** a parallel session builds the upload feature. NEVER `git add -A` / `git add .`; stage named files only, and inspect `git diff <file> | grep ^@@` for foreign hunks (`mark_seeded`, `put_edges`, `w.source`, vendor/*.rs) before staging. If a file is mixed, stage per-hunk with a filtered patch (`git apply --cached`).
- Tests run against the live compose stack (`docker compose up -d db mr-service`, then `cd api && python -m pytest`); that is by design (`conftest.py` docstring). `DATABASE_URL` default is port **55432**.
- API contract rule 1: **no bare scores** — any new displayed number ships with an `Uncertainty` and a tie/derivation story (`API_CONTRACT.md`).
- Copy rule: never show a control that does nothing (`config.py` docstring).
- Eval protocol (spec): recall@25 [bootstrap CI] + d≥2 recall + top-25 popularity percentile, reported together, never a single scalar. A change that trades recall vs hubness goes to the user with both numbers.
- Deterministic scorer requirements (spec R2): unique-visit semantics (NOT naive `Σ α^k Pᵏ s`), and no per-edge-type constants inside row-stochastic P for single-relation-type nodes.
- `γ` default **0.5**; `ε = 1e-9`; engine correlation gate for R2: median Spearman ≥ **0.90** over 40 profiles on the top-2500 window.
- Graph mutations must call `bump_graph_version` (see `api/provenance/graphmeta.py`) and commit.
- Commit messages end with the Claude Code trailer (see repo history for format).

## File Structure

| Path | Status | Responsibility |
|---|---|---|
| `scripts/eval_ranking.py` | new | Offline eval harness: trust-set sampling, leakage ablation, reference scorers, three-number report |
| `api/alembic/versions/a9d4f0c1b3e5_weighted_tsv.py` | new | Rewrites `works.tsv` weighted (title=A, abstract=B) |
| `scripts/load_db.py` | modify | Same weighted tsv for future corpus loads |
| `api/provenance/routers/papers.py` | modify | Search ORDER BY: weighted ts_rank + trigram title boost |
| `api/tests/test_search_ranking.py` | new | Exact-title-first property test + KNOWN_ISSUES #13 regressions |
| `api/provenance/propagate.py` | new | Deterministic scorer over `graph_edges` (scipy), background vector, per-graph-version cache |
| `api/requirements.txt`, `api/pyproject.toml` | modify | add `numpy`, `scipy` |
| `scripts/validate_propagate.py` | new | Correlation gate vs engine (40 profiles) |
| `api/provenance/ranking.py` | modify | `compose()` weighted mean; lift computation on ranked rows |
| `api/provenance/schemas.py` | modify | `ScoredPaper.lift`, `lift_uncertainty`; params `lift_gamma` |
| `api/provenance/routers/rankings.py` | modify | `sort=trust|lift` query param |
| `api/provenance/services.py` | modify | thread lift through `scored_paper` / pool |
| `web/src/lib/types.ts`, `web/src/components/RankingTable.tsx`, `web/src/routes/Params.tsx` | modify | lift column + sort, γ slider |
| `scripts/build_graph.py` | modify | Skip degree-1 entity nodes (R5) |

---

### Task 0: Eval harness (`scripts/eval_ranking.py`)

Port the session harness (scratchpad `harness.py` + `e4_variants.py`, already validated in the experiments doc) into the repo so every later task can run the gate. Offline, DB read-only, no engine calls.

**Files:**
- Create: `scripts/eval_ranking.py`
- Test: self-checking via `--selftest` flag (asserts reference-scorer ordering: propagation > adjacency > popularity > random on 30 sets)

**Interfaces:**
- Produces CLI: `python scripts/eval_ranking.py [--sets 200] [--seed 20260729] [--scorer geom|adjacency|popularity|random] [--gamma 0.0] [--json out.json]`
- Produces functions later scripts import: `load_graph() -> Graph`, `bibliography_trust_sets(g, conn, n, rng) -> list[np.ndarray]`, `evaluate(g, sets, scorer_fn, gamma) -> dict` returning `{"recall25": (mean, lo, hi), "recall_d2": float, "pop_pctile": (mean, lo, hi)}`

- [ ] **Step 1: Write the harness.** Single file, contents = the scratchpad `harness.py` `Graph`/`propagate` classes plus the evaluation loop from `e4_variants.py` with these fixed decisions baked in: bibliography trust sets (18–60 in-corpus refs, ≥18 resolvable), 20% held out, per-fold ablation of `couples`/`co_cited` between held and kept, stub+entity exclusion identical to the product filter, reference scorers `random`/`popularity`/`adjacency1hop` always computed, bootstrap R=4000, fixed default seed 20260729. Geometric θ (α=0.85, K=5) is the default propagation — matches the engine per KNOWN_ISSUES #1, and heat-kernel measured no gain. Gamma applied as `log(score+1e-9) − γ·log(bg+1e-9)` with bg = propagation from uniform-over-non-stub.
- [ ] **Step 2: Run `python scripts/eval_ranking.py --selftest`** — expected: ordering assertion passes, prints the three-number table for all four scorers.
- [ ] **Step 3: Capture the pre-change baseline:** `python scripts/eval_ranking.py --sets 200 --json docs/superpowers/specs/eval-baseline-2026-07-30.json`
- [ ] **Step 4: Commit** (`scripts/eval_ranking.py` + baseline json only, named files).

### Task 1: Weighted tsvector (R1a)

**Files:**
- Create: `api/alembic/versions/a9d4f0c1b3e5_weighted_tsv.py` (down_revision `'f3c9e1d7b5a4'` — verify it is still head with `ls api/alembic/versions` first; the parallel session adds migrations)
- Modify: `scripts/load_db.py` (the `works.ref_count / in_corpus_cited_by / tsv` block)

**Interfaces:**
- Produces: `works.tsv` = `setweight(to_tsvector('english', coalesce(title,'')), 'A') || setweight(to_tsvector('english', coalesce(abstract,'')), 'B')`; consumed by Task 2's `ts_rank`.

- [ ] **Step 1: Migration** — `upgrade()` runs exactly:

```python
def upgrade() -> None:
    op.execute(
        "UPDATE works SET tsv = "
        "setweight(to_tsvector('english', coalesce(title, '')), 'A') || "
        "setweight(to_tsvector('english', coalesce(abstract, '')), 'B')"
    )

def downgrade() -> None:
    op.execute(
        "UPDATE works SET tsv = to_tsvector('english', "
        "coalesce(title, '') || ' ' || coalesce(abstract, ''))"
    )
```

- [ ] **Step 2: Mirror in `load_db.py`** — replace the `tsv =` expression inside the existing UPDATE with the same two-part setweight expression (keep the surrounding ref_count/in_corpus join intact).
- [ ] **Step 3: Apply:** `cd api && alembic upgrade head`. Verify: `SELECT tsv FROM works WHERE NOT is_stub LIMIT 1` shows `'word':1A,...` weights.
- [ ] **Step 4: Commit.**

### Task 2: Search ranking + tests (R1b)

**Files:**
- Modify: `api/provenance/routers/papers.py` (`search()`, the `if total:` ORDER BY only — do NOT touch the `w.source` visibility SQL, it is the parallel session's)
- Create: `api/tests/test_search_ranking.py`

- [ ] **Step 1: Failing test:**

```python
"""Retrieval quality gate for KNOWN_ISSUES #13. Live-stack, read-only."""
import pytest
from sqlalchemy import text

def test_exact_title_lands_first(client, db):
    rows = db.execute(text(
        "SELECT id, title FROM works WHERE NOT is_stub AND title IS NOT NULL "
        "AND length(title) BETWEEN 30 AND 120 ORDER BY id LIMIT 20")).all()
    hits = 0
    for wid, title in rows:
        r = client.get("/api/papers/search", params={"q": title, "limit": 3})
        items = r.json()["items"]
        if items and items[0]["id"] == wid:
            hits += 1
    assert hits >= 18, f"exact-title top-1 rate {hits}/20 (gate: >=18)"

def test_known_issue_13_em_paper(client, db):
    row = db.execute(text(
        "SELECT id, title FROM works WHERE title ILIKE "
        "'Maximum Likelihood from Incomplete Data%' AND NOT is_stub LIMIT 1")).first()
    if row is None:
        pytest.skip("EM paper not in corpus")
    r = client.get("/api/papers/search", params={"q": row.title, "limit": 3})
    assert r.json()["items"][0]["id"] == row.id
```

- [ ] **Step 2: Run** `cd api && python -m pytest tests/test_search_ranking.py -v` — expected FAIL (title lands ~3rd today).
- [ ] **Step 3: Implement** — in `search()`, replace the ranked SELECT's ORDER BY with:

```sql
ORDER BY ts_rank(w.tsv, plainto_tsquery('english', :q))
       + 0.5 * similarity(coalesce(w.title, ''), :q) DESC,
       w.cited_by_count DESC
```

(`ts_rank` default weight vector scores A=1.0 vs B=0.4, so title hits dominate; the trigram term breaks ties toward near-exact titles and needs no threshold config since it is not the `%` operator.)
- [ ] **Step 4: Run the test** — expected PASS. Also run the existing suite's search tests: `python -m pytest tests/test_api.py -k search -v`.
- [ ] **Step 5: Update `KNOWN_ISSUES.md` #13** (mark fixed with date + one-line what changed) and `DEMO.md` if it references result positions. **Commit** (named files; papers.py may carry foreign hunks — check).

### Task 3: Deterministic scorer (`api/provenance/propagate.py`) (R2a)

**Files:**
- Create: `api/provenance/propagate.py`
- Modify: `api/requirements.txt` + `api/pyproject.toml` (add `numpy>=1.26`, `scipy>=1.11`)
- Create: `api/tests/test_propagate.py`

**Interfaces:**
- Produces:
  - `class PropagationGraph` — cached per `graph_version`; `PropagationGraph.get(db) -> PropagationGraph` (module-level cache keyed on `graph_version(db)`)
  - `.score(seeds: dict[str, float], K: int = 5, alpha: float = 0.85) -> dict[str, float]` — work_id→score, papers only, **seed-absorbing** (mass at seed nodes is zeroed after each step: walks that return to a seed are dead, the deterministic analogue of unique-visit counting at the source; the correlation gate in Task 4 decides whether this approximation suffices)
  - `.background() -> dict[str, float]` — propagation from uniform-over-non-stub-papers, cached per graph version
  - Signed seed weights flow through linearly (distrust −1.0); documented as an approximation of the engine's negative-subsegment semantics.
- No per-edge-type multipliers are applied inside the row-normalised transition matrix (Global Constraints); weights come from `graph_edges.weight` as persisted.

- [ ] **Step 1: Failing test** (`api/tests/test_propagate.py`, live-stack read-only):

```python
import pytest
from sqlalchemy import text
from provenance.propagate import PropagationGraph

def test_scores_seed_neighbourhood(db):
    g = PropagationGraph.get(db)
    seed = db.execute(text(
        "SELECT src FROM graph_edges WHERE relation='cites' "
        "AND src LIKE 'UW%' LIMIT 1")).scalar_one()[1:]
    scores = g.score({seed: 1.0})
    cited = [r[0][1:] for r in db.execute(text(
        "SELECT dst FROM graph_edges WHERE src = :s AND relation='cites' LIMIT 5"),
        {"s": "U" + seed}).all()]
    assert any(scores.get(c, 0) > 0 for c in cited)
    assert scores.get(seed, 0.0) == 0.0  # seed-absorbing: no self-score

def test_background_cached_and_uniformish(db):
    g = PropagationGraph.get(db)
    bg = g.background()
    assert len(bg) > 5000
    assert g.background() is bg  # cached object identity
```

- [ ] **Step 2: Run** — FAIL (module missing).
- [ ] **Step 3: Implement** `propagate.py` — structure (adapt the harness's `Graph`/`propagate`, DB-loaded via the Session's connection, CSR row-normalised, `theta = alpha ** np.arange(K+1)`, seed positions zeroed after each `P.T @ x`; papers-only dict comprehension on output; module cache `{version: PropagationGraph}` with a lock, keyed on `graphmeta.graph_version`).
- [ ] **Step 4: Run tests** — PASS. **Commit** (propagate.py, requirements, pyproject, test — named files).

### Task 4: Correlation gate (`scripts/validate_propagate.py`) (R2b)

**Files:**
- Create: `scripts/validate_propagate.py`

**Interfaces:**
- Consumes `PropagationGraph` and the engine via `provenance.meritrank.MeritRank.scores`.
- Produces a printed verdict line: `PASS median spearman=0.XX (gate 0.90, n=40)` and exit code 0/1. Uses scratch egos (`Uval_*`), always deleted in `finally` (pattern: `ranking._leave_one_out`).

- [ ] **Step 1: Write it** — 40 bibliography-derived seed sets (reuse `eval_ranking.bibliography_trust_sets`), for each: put scratch-ego trust edges, `mr.scores(ego, context="", limit=2500, kind="User")`, deterministic `g.score(seeds)`, Spearman over the union of both top-2500 (missing=0), teardown. Report median + IQR.
- [ ] **Step 2: Run** `python scripts/validate_propagate.py` against the compose stack. **Decision point (logged, not asked):** if median ≥ 0.90 → the scorer is validated as a substrate for lift/background (it does NOT become the trust scorer default in this plan). If < 0.90 → iterate on the self-return handling (entity 2-step diagonal removal is the next candidate, measured in E1/E2) before Task 5 proceeds.
- [ ] **Step 3: Commit** script + a results line appended to `docs/superpowers/specs/2026-07-29-ranking-experiments-results.md`.

### Task 5: Lift end-to-end, backend (R3)

**Files:**
- Modify: `api/provenance/schemas.py` — `ScoredPaper.lift: float`, `lift_uncertainty: Uncertainty`; `StoredParams.lift_gamma: float = 0.5`; `ParamsUpdate.lift_gamma: Optional[float]` (range-check 0–1 in the router)
- Modify: `api/provenance/ranking.py` — after `composed` is built in `rank_profile`: `lift = log(v+EPS) − γ·log(bg.get(wid, 0)+EPS)` per row, `EPS = 1e-9`; LOO replicates transformed identically for `lift_uncertainty` (same jackknife; denominator held fixed — disclosed)
- Modify: `api/provenance/services.py` — `scored_paper()` passes both through; `stored_weights`-style accessor for `lift_gamma`
- Modify: `api/provenance/routers/rankings.py` — `sort: Literal["trust","lift"] = "trust"` query param on `/rankings`; sorting by lift re-ranks and re-runs `assign_tie_groups` on the lift ordering with `lift_uncertainty`
- Modify: `api/provenance/routers/profiles.py` — accept/store `lift_gamma`, 422 unchanged for the still-forbidden keys
- Test: `api/tests/test_lift.py`

**Interfaces:**
- Consumes: `PropagationGraph.get(db).background()` (Task 3).
- Produces wire fields: `ScoredPaper.lift`, `ScoredPaper.lift_uncertainty`, `GET /rankings?sort=lift`, `params.lift_gamma`. Method copy for lift uncertainty discloses the fixed denominator (Task 6 renders it).

- [ ] **Step 1: Failing tests:** `test_lift_present_and_finite` (every ranked item has finite `lift` and a `lift_uncertainty` with `stderr >= 0`), `test_sort_lift_reorders_or_equals` (200 response, order differs from `sort=trust` OR identical-but-valid), `test_gamma_zero_matches_trust_order` (γ=0 → lift order == trust order among items with distinct scores), `test_gamma_rejected_out_of_range` (`lift_gamma=1.5` → 422).
- [ ] **Step 2: Run — FAIL.** **Step 3: Implement.** **Step 4: Run — PASS**, plus full `tests/test_api.py`.
- [ ] **Step 5: Gate:** `python scripts/eval_ranking.py --sets 200 --gamma 0.5` vs the Task-0 baseline: recall CI must not degrade, pop-percentile must drop (E6 predicts 0.81→0.75 thin-region). Paste the three numbers into the commit message. **Commit.**

### Task 6: Lift frontend (R3 UI)

**Files:**
- Modify: `web/src/lib/types.ts` — `ScoredPaper` gains `lift: number; lift_uncertainty: Uncertainty;`; `Params` gains `lift_gamma?: number;`
- Modify: `web/src/components/RankingTable.tsx` — `SortKey` gains `'lift'`; column `{ key: 'lift', label: 'Lift', numeric: true }`; cell renders `ScoreBar` with `paper.lift_uncertainty`; header tooltip copy: *"Proximity relative to how reachable this paper is for everyone. Positive: closer to you than to a generic reader. The denominator is a fixed background and carries no error bar of its own."*
- Modify: `web/src/routes/Params.tsx` — γ slider 0–1 step 0.05 default 0.5, wired to `lift_gamma` via the existing params PATCH + live preview mechanics (same idiom as context weights; the control does something: preview re-sorts by lift)
- Modify: `web/src/lib/format.ts` — no changes to METHOD_COPY needed (lift_uncertainty reuses existing methods)
- Test: extend `web/src/test/format.test.ts` companion — new `web/src/test/lift.test.tsx` asserting RankingTable renders a Lift column and sorts by it

- [ ] **Step 1: Failing test → Step 2: FAIL → Step 3: implement → Step 4: `npx vitest run` + `npx tsc --noEmit` PASS → Step 5: Commit** (web files are currently clean of foreign WIP, but re-check `types.ts` — the parallel session touched it for `include_user_uploads`).

### Task 7: `compose()` as weighted mean (R4)

**Files:**
- Modify: `api/provenance/ranking.py::compose`
- Test: `api/tests/test_compose.py` (pure unit, no DB)

- [ ] **Step 1: Failing test:**

```python
from provenance.ranking import compose

def test_weighted_mean_no_zero_clamp_block():
    per_ctx = {
        "citation": {"UW1": 0.5, "UW2": 0.010},
        "author": {"UW1": 0.4}, "topic": {"UW1": 0.6, "UW2": 0.012},
        "venue": {"UW1": 0.5}, "institution": {"UW1": 0.5},
    }
    out = compose(per_ctx)
    # weighted mean over present contexts, never negative, citation-only paper
    # keeps a score proportional to its baseline instead of clamping to 0
    assert out["UW1"] == pytest.approx((0.5+0.4+0.6+0.5+0.5)/5)
    assert out["UW2"] == pytest.approx((0.010+0.012)/2)

def test_zero_weight_drops_context():
    per_ctx = {"citation": {"UW1": 0.5}, "author": {"UW1": 0.9},
               "topic": {"UW1": 0.5, "UW2": 0.1}, "venue": {}, "institution": {}}
    w = {"citation": 1.0, "author": 0.0, "topic": 1.0, "venue": 1.0, "institution": 1.0}
    out = compose(per_ctx, w)
    assert out["UW1"] == pytest.approx((0.5+0.5)/2)  # author zeroed out
```

- [ ] **Step 2: FAIL.** **Step 3: Implement:** score = `Σ_c w_c·s_c(n) / Σ_c w_c` over contexts where n is present with w_c > 0 (baseline participates with its own weight; absent-from-window contexts contribute nothing — same imputation stance as today, documented in the docstring with the variance rationale ≈13σ²→σ²/5 and the deleted `max(0,·)` clamp). Update the module docstring and the `ContextBars` marginal note only if wording depends on the sum formula.
- [ ] **Step 4: PASS** + full API suite (compose feeds LOO recompose, `/params` preview, `/simulate`). **Step 5: Gate** `eval_ranking.py --sets 200` (E5 predicts ≈no recall change), paste numbers, **Commit.**

### Task 8: Singleton-entity cleanup (R5)

**Files:**
- Modify: `scripts/build_graph.py` (authors/topics/venues/institutions loops: `if deg <= 1: continue` — both edge directions; entity never enters `edges`)
- Modify: `KNOWN_ISSUES.md` (new entry: entity apparatus measured at +2.3 recall points; degree-1 entities dropped from the ranking graph, retained in Postgres for display)
- Modify: `README.md` if it claims meta-path discovery as a headline (soften to the measured claim)

- [ ] **Step 1: Implement the skips.** Display surfaces (`paper_detail` topics/institutions, `/explain` over `graph_edges`) read Postgres tables, not the entity edges, except `/explain` paths — which will simply no longer route through dropped entities, consistent with them carrying no trust. (Spec open question 2 resolved as: display data stays, ranking graph drops them — log the decision.)
- [ ] **Step 2: Rebuild** (`python scripts/build_graph.py`) — **coordinate first**: this wipes engine state (KNOWN_ISSUES #9) and the parallel session's put_edges work; run only when their session is idle, or defer this task to last and note it. `bump_graph_version` fires inside `persist()` already.
- [ ] **Step 3: Gate:** `eval_ranking.py --sets 200` before/after (expected ≈no recall change — the mass was wasted); engine sanity: one profile's rankings still return.
- [ ] **Step 4: Commit** (build_graph.py + docs).

## Self-Review

- **Spec coverage:** R1→Tasks 1–2, R2→Tasks 3–4, R3→Tasks 5–6, R4→Task 7, R5→Task 8, eval protocol→Task 0. Spec open questions: γ global default (Q1) = shipped as global 0.5 slider; R5 display scope (Q2) = resolved display-stays; seed paper (Q3) = still user-blocked, unaffected.
- **Types:** `PropagationGraph.get(db)` / `.score(seeds)` / `.background()` used identically in Tasks 3, 4, 5. `lift`/`lift_uncertainty`/`lift_gamma` names consistent across Tasks 5–6. `SortKey 'lift'` matches API `sort=lift`.
- **Placeholders:** none — every code step carries the code or the exact expression; Task 3 Step 3's "adapt the harness" points at committed experiment code plus an explicit structural recipe.
