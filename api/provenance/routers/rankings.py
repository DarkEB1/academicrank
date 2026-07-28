"""Rankings, recommendations, blindspots, diversity, simulation, subgraph."""
from __future__ import annotations

import collections
import math
import secrets
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import text

from .. import config, ranking, schemas, services
from ..deps import DbSession, OwnedProfile
from ..meritrank import (
    Uncertainty, assign_tie_groups, leave_one_out_uncertainty,
)
from ..models import Profile, Trust, node_to_work_id, profile_node, work_node

router = APIRouter(prefix="/api", tags=["rankings"])

# `coupling` and `cocitation` appear in the contract's context enum but they are not
# engine contexts: build_graph.py emits `couples`/`co_cited` as paper->paper edges,
# which are User->User and are therefore replicated by the engine into *every* context
# (DECISIONS.md D1.5). Scoring "just the coupling context" is not a thing the engine
# can do, so we say so with a 422 instead of returning a plausible-looking lie.
_FOLDED_CONTEXTS = {
    "coupling": "couples",
    "cocitation": "co_cited",
}


def _check_context(ctx: str) -> str:
    if ctx in _FOLDED_CONTEXTS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=[{
                "loc": ["query", "context"],
                "msg": (
                    f"{ctx!r} is not a separable context. Bibliographic coupling and "
                    f"co-citation are paper-to-paper ({_FOLDED_CONTEXTS[ctx]}) edges, "
                    "and the engine replicates every paper-to-paper edge into every "
                    "context regardless of the context declared on the edge "
                    "(DECISIONS.md D1.5). They are part of the 'citation' baseline and "
                    "cannot be scored in isolation. Use context=citation."
                ),
                "type": "value_error.context_not_separable",
            }],
        )
    return ctx


# ---------------------------------------------------------------------------
# /rankings
# ---------------------------------------------------------------------------


@router.get("/profiles/{profile_id}/rankings", response_model=schemas.RankingsResponse)
def rankings(
    profile: OwnedProfile,
    db: DbSession,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    context: schemas.RankingContext = "aggregate",
    exclude_trusted: bool = True,
) -> schemas.RankingsResponse:
    _check_context(context)
    pool = services.build_pool(db, profile, context=context,
                               exclude_trusted=exclude_trusted)

    items = pool.items
    keep = services.year_filter(db, [i.work_id for i in items], year_from, year_to)
    if keep is not None:
        items = [i for i in items if i.work_id in keep]

    total = len(items)
    page = items[offset:offset + limit]
    return schemas.RankingsResponse(
        items=services.scored_page(db, page, pool, start_rank=offset + 1),
        total=total,
        cold_start=services.cold_start(pool.seeds),
        disclaimer=config.DISCLAIMER,
        timing_ms=pool.elapsed_ms,
    )


# ---------------------------------------------------------------------------
# /recommendations
# ---------------------------------------------------------------------------


@router.get("/profiles/{profile_id}/recommendations",
            response_model=schemas.RecommendationsResponse)
def recommendations(
    profile: OwnedProfile,
    db: DbSession,
    diversity: float = Query(default=0.3, ge=0.0, le=1.0),
    limit: int = Query(default=25, ge=1, le=100),
) -> schemas.RecommendationsResponse:
    """The diversity dial.

    `diversity = 0` is pure exploitation: rank by personal trust alone, i.e. whatever
    sits nearest the trust set. `diversity = 1` is pure exploration: high global merit
    *and* far from the trust set, which is the only combination that is actually
    informative -- distant-and-obscure is just noise, and near-and-famous is what you
    would have found anyway. Everything between interpolates linearly.
    """
    pool = services.build_pool(db, profile, context="aggregate", exclude_trusted=True)
    if not pool.items:
        return schemas.RecommendationsResponse(
            items=[], diversity=diversity, disclaimer=config.DISCLAIMER,
            cold_start=services.cold_start(pool.seeds),
        )

    seed_ids = [t.work_id for t in db.query(Trust).filter(
        Trust.profile_id == profile.id, Trust.is_distrust.is_(False)).all()]
    dist = services.trust_set_distances(db, seed_ids)

    scored: list[tuple[float, float, ranking.RankedItem]] = []
    for item in pool.items:
        wid = item.work_id
        nov = services.novelty_of(dist, wid)
        p_trust = pool.trust_pct.get(wid, 0.0)
        p_global = pool.global_pct.get(wid, 0.0)
        blended = (1.0 - diversity) * p_trust + diversity * (p_global * nov)
        scored.append((blended, nov, item))

    scored.sort(key=lambda r: -r[0])
    top = scored[:limit]

    briefs = services.paper_briefs(db, [it.work_id for _b, _n, it in top])
    out: list[schemas.RecommendedPaper] = []
    for rank_no, (blended, nov, item) in enumerate(top, start=1):
        brief = services.brief_or_placeholder(briefs, item.work_id)
        base = services.scored_paper(db, item, brief, pool, rank=rank_no)
        out.append(schemas.RecommendedPaper(
            **base.model_dump(),
            novelty=nov,
            reason=_reason(item.work_id, dist, pool, nov, diversity, base),
        ))

    return schemas.RecommendationsResponse(
        items=out, diversity=diversity, disclaimer=config.DISCLAIMER,
        cold_start=services.cold_start(pool.seeds),
    )


def _reason(
    work_id: str,
    dist: dict[str, int],
    pool: services.Pool,
    novelty: float,
    diversity: float,
    scored: schemas.ScoredPaper,
) -> str:
    hops = dist.get(work_id)
    if hops is None:
        prox = f"no route under {services.MAX_NOVELTY_DEPTH} hops from anything you trust"
    elif hops <= 1:
        prox = "directly connected to a paper you trust"
    else:
        prox = f"{hops} hops from your trust set"

    p_global = pool.global_pct.get(work_id, 0.0)
    merit = f"global merit in the top {max(1, round((1 - p_global) * 100))}% of the corpus"

    if diversity >= 0.66:
        stance = "Exploration pick"
    elif diversity <= 0.33:
        stance = "Closest to what you already trust"
    else:
        stance = "Balanced pick"

    tail = ""
    if scored.disagreement >= 0.4:
        tail = (f" Trust, global merit and raw citations disagree sharply here "
                f"(spread {scored.disagreement:.0%}), which usually means it is "
                "either underrated or narrowly specialised.")
    return f"{stance}: {prox}, {merit}.{tail}"


# ---------------------------------------------------------------------------
# /blindspots
# ---------------------------------------------------------------------------


@router.get("/profiles/{profile_id}/blindspots", response_model=schemas.BlindspotsResponse)
def blindspots(
    profile: OwnedProfile,
    db: DbSession,
    limit: int = Query(default=25, ge=1, le=100),
) -> schemas.BlindspotsResponse:
    """High global merit, low personal trust.

    Candidates are drawn from the *global* score list rather than from the personal
    pool, because the sharpest blindspot is a paper your ego does not reach at all --
    and such a paper is by definition absent from your pool.
    """
    pool = services.build_pool(db, profile, context="aggregate", exclude_trusted=True)
    gvals = services.global_scores(db)
    if not gvals:
        return schemas.BlindspotsResponse(
            items=[], disclaimer=config.DISCLAIMER,
            cold_start=services.cold_start(pool.seeds))

    gpct = services.rank_percentiles(gvals)
    trusted = {t.work_id for t in db.query(Trust).filter(Trust.profile_id == profile.id)}
    by_id = pool.by_id()

    rows: list[tuple[float, str, float, Uncertainty]] = []
    for wid, gp in gpct.items():
        if wid in trusted:
            continue
        tp = pool.trust_pct.get(wid, 0.0)
        gap = gp - tp
        if gap <= 0:
            continue
        item = by_id.get(wid)
        trust_val = item.trust if item else 0.0
        unc = item.uncertainty if item else Uncertainty(
            0.0, 0.0, 0.0, 0, "leave_one_out", max(pool.seeds, 1))
        rows.append((gap, wid, trust_val, unc))

    rows.sort(key=lambda r: -r[0])
    top = rows[:limit]
    # Tie groups are assigned over the order actually presented.
    assign_tie_groups([(wid, trust_val, unc) for _g, wid, trust_val, unc in top])

    briefs = services.paper_briefs(db, [wid for _g, wid, _t, _u in top])
    items: list[schemas.BlindspotPaper] = []
    for n, (gap, wid, trust_val, unc) in enumerate(top, start=1):
        brief = services.brief_or_placeholder(briefs, wid)
        p_cit = services.CITATION_PCT.percentile(db, brief.cited_by_count)
        tp = pool.trust_pct.get(wid, 0.0)
        gp = gpct[wid]
        items.append(schemas.BlindspotPaper(
            **brief.model_dump(),
            trust=trust_val,
            uncertainty=services.to_uncertainty(unc),
            global_merit=gvals[wid],
            rank=n,
            disagreement=services.disagreement(tp, gp, p_cit),
            gap=gap,
        ))

    return schemas.BlindspotsResponse(
        items=items, disclaimer=config.DISCLAIMER,
        cold_start=services.cold_start(pool.seeds))


# ---------------------------------------------------------------------------
# /diversity
# ---------------------------------------------------------------------------

_DIM_SQL = {
    "topics": (
        "SELECT t.display_name, count(*) FROM trust tr "
        "JOIN work_topics wt ON wt.work_id = tr.work_id "
        "JOIN topics t ON t.id = wt.topic_id "
        "WHERE tr.profile_id = :p AND tr.is_distrust = false "
        "GROUP BY t.display_name",
        "SELECT count(*) FROM topics",
    ),
    "institutions": (
        "SELECT i.display_name, count(*) FROM trust tr "
        "JOIN work_institutions wi ON wi.work_id = tr.work_id "
        "JOIN institutions i ON i.id = wi.institution_id "
        "WHERE tr.profile_id = :p AND tr.is_distrust = false "
        "GROUP BY i.display_name",
        "SELECT count(*) FROM institutions",
    ),
    "decades": (
        "SELECT ((w.year / 10) * 10)::text || 's', count(*) FROM trust tr "
        "JOIN works w ON w.id = tr.work_id "
        "WHERE tr.profile_id = :p AND tr.is_distrust = false AND w.year IS NOT NULL "
        "GROUP BY 1",
        "SELECT count(DISTINCT (year / 10)) FROM works WHERE year IS NOT NULL",
    ),
    "countries": (
        "SELECT i.country_code, count(*) FROM trust tr "
        "JOIN work_institutions wi ON wi.work_id = tr.work_id "
        "JOIN institutions i ON i.id = wi.institution_id "
        "WHERE tr.profile_id = :p AND tr.is_distrust = false "
        "AND i.country_code IS NOT NULL GROUP BY i.country_code",
        "SELECT count(DISTINCT country_code) FROM institutions "
        "WHERE country_code IS NOT NULL",
    ),
}

_DIM_LABEL = {"topics": "Topic", "institutions": "Institution",
              "decades": "Decade", "countries": "Country"}


@router.get("/profiles/{profile_id}/diversity", response_model=schemas.DiversityResponse)
def diversity(profile: OwnedProfile, db: DbSession) -> schemas.DiversityResponse:
    """Real Shannon entropy over the trust set, normalised by the maximum entropy a
    trust set of this size could have (see services.max_shannon for why the bound is
    min(#observations, #categories) rather than #categories)."""
    entropy: dict[str, float] = {}
    max_entropy: dict[str, float] = {}
    normalised: list[float] = []
    concentration: list[schemas.Concentration] = []

    for dim, (sql, corpus_sql) in _DIM_SQL.items():
        rows = db.execute(text(sql), {"p": profile.id}).all()
        counts = [int(r[1]) for r in rows]
        n_obs = sum(counts)
        n_cats = int(db.execute(text(corpus_sql)).scalar_one() or 0)

        h = services.shannon(counts)
        hmax = services.max_shannon(n_obs, n_cats)
        entropy[dim] = h
        max_entropy[dim] = hmax
        if hmax > 0:
            normalised.append(min(1.0, h / hmax))
        elif n_obs > 0:
            # One observation, or every observation in one category: zero diversity.
            normalised.append(0.0)

        if n_obs > 0:
            for label, c in sorted(rows, key=lambda r: -int(r[1]))[:4]:
                concentration.append(schemas.Concentration(
                    label=f"{_DIM_LABEL[dim]}: {label}", share=int(c) / n_obs))

    concentration.sort(key=lambda c: -c.share)
    concentration = concentration[:8]

    seeds = db.query(Trust).filter(
        Trust.profile_id == profile.id, Trust.is_distrust.is_(False)).count()

    if not normalised or seeds == 0:
        return schemas.DiversityResponse(
            entropy=schemas.EntropyBreakdown(**{k: entropy.get(k, 0.0) for k in
                                                ("topics", "institutions", "decades",
                                                 "countries")}),
            max_entropy=schemas.EntropyBreakdown(
                **{k: max_entropy.get(k, 0.0) for k in
                   ("topics", "institutions", "decades", "countries")}),
            concentration=concentration,
            echo_chamber_score=0.0,
            message=("Nothing to measure yet: diversity is computed over your trust "
                     "set, and it is empty. Trust some papers first."),
        )

    echo = 1.0 - (sum(normalised) / len(normalised))
    echo = max(0.0, min(1.0, echo))

    if echo >= 0.7:
        verdict = ("Your trust set is highly concentrated. Rankings built from it will "
                   "mostly return more of the same.")
    elif echo >= 0.4:
        verdict = ("Your trust set leans towards a few clusters. Expect the ranking to "
                   "reinforce them.")
    else:
        verdict = "Your trust set is spread reasonably evenly across the dimensions we can see."

    top = concentration[0].label if concentration else None
    detail = f" The single largest concentration is {top} ({concentration[0].share:.0%})." \
        if concentration else ""
    caveat = (" Note that this measures the OpenAlex metadata on your trust set, so "
              "under-covered work -- non-English, pre-digital, some regions -- is "
              "invisible to it and cannot count towards diversity.")

    return schemas.DiversityResponse(
        entropy=schemas.EntropyBreakdown(**{k: entropy[k] for k in
                                            ("topics", "institutions", "decades",
                                             "countries")}),
        max_entropy=schemas.EntropyBreakdown(**{k: max_entropy[k] for k in
                                                ("topics", "institutions", "decades",
                                                 "countries")}),
        concentration=concentration,
        echo_chamber_score=echo,
        message=verdict + detail + caveat,
    )


# ---------------------------------------------------------------------------
# /simulate
# ---------------------------------------------------------------------------


class _ScratchEgo:
    """A throwaway ego node. Every edge written is recorded and deleted on exit, so a
    counterfactual never touches the user's real ego -- and never leaves debris in the
    engine if the request raises."""

    def __init__(self, db, name: str):
        self.db = db
        self.name = name
        self._written: list[str] = []

    def __enter__(self) -> "_ScratchEgo":
        return self

    def seed(self, work_id: str, weight: float) -> None:
        node = work_node(work_id)
        services.mr_of(self.db).put_edge(self.name, node, weight, config.AGGREGATE)
        self._written.append(node)

    def clear(self) -> None:
        mr = services.mr_of(self.db)
        for node in self._written:
            try:
                mr.delete_edge(self.name, node, config.AGGREGATE)
            except Exception:  # noqa: BLE001
                pass
        self._written.clear()

    def __exit__(self, *exc) -> None:
        try:
            self.clear()
            services.mr_of(self.db).delete_node(self.name, config.AGGREGATE)
            self.db.commit()
        except Exception:  # noqa: BLE001
            self.db.rollback()


def _seed_weights(rows: list[tuple[str, int, bool]]) -> dict[str, float]:
    return {
        wid: (config.DISTRUST_WEIGHT if dis
              else config.TRUST_STRENGTH_SCALE.get(strength, 0.7))
        for wid, strength, dis in rows
    }


def _score_scratch(
    db, prefix: str, seeds: dict[str, float], weights: dict[str, float]
) -> tuple[dict[str, float], dict[str, Uncertainty]]:
    """Composed scores + leave-one-out uncertainty for an arbitrary seed set.

    This is the same recipe ranking.rank_profile uses, applied to a hypothetical trust
    set instead of the stored one -- so the counterfactual is measured on exactly the
    same footing as the real ranking, uncertainty included.
    """
    mr = services.mr_of(db)
    with _ScratchEgo(db, f"U{prefix}") as ego:
        for wid, w in seeds.items():
            ego.seed(wid, w)
        composed = ranking.compose(
            ranking._context_scores(mr, ego.name, services.POOL_FETCH), weights)

    positives = [w for w, v in seeds.items() if v > 0]
    loo: dict[str, dict[str, float]] = {}
    if 2 <= len(positives) <= 12:
        for skip in positives:
            with _ScratchEgo(db, f"U{prefix}_loo") as sub:
                for wid, w in seeds.items():
                    if wid != skip:
                        sub.seed(wid, w)
                loo[skip] = ranking.compose(
                    ranking._context_scores(mr, sub.name, services.POOL_FETCH), weights)

    if loo:
        unc = leave_one_out_uncertainty(loo, composed)
    else:
        unc = {n: Uncertainty(abs(v) * 0.5, max(0.0, v * 0.5), v * 1.5, 0,
                              "leave_one_out", max(len(positives), 1))
               for n, v in composed.items()}
    return composed, unc


@router.post("/profiles/{profile_id}/simulate", response_model=schemas.SimulateResponse)
def simulate(
    body: schemas.SimulateRequest, profile: OwnedProfile, db: DbSession
) -> schemas.SimulateResponse:
    """Non-destructive counterfactual: 'what would my ranking look like if...'."""
    rows = [(t.work_id, int(t.strength), bool(t.is_distrust)) for t in
            db.query(Trust).filter(Trust.profile_id == profile.id).all()]
    current = _seed_weights(rows)

    hypothetical = dict(current)
    for wid in body.remove:
        hypothetical.pop(wid, None)
    for add in body.add:
        hypothetical[add.work_id] = (
            config.DISTRUST_WEIGHT if add.is_distrust
            else config.TRUST_STRENGTH_SCALE.get(add.strength, 0.7))

    unknown = sorted(set(hypothetical) - set(current))
    if unknown:
        found = {r[0] for r in db.execute(
            text("SELECT id FROM works WHERE id = ANY(:ids)"), {"ids": unknown}).all()}
        missing = sorted(set(unknown) - found)
        if missing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Unknown work ids: {', '.join(missing)}")

    weights = services.stored_weights(profile)
    nonce = secrets.token_hex(4)

    def _rank(seeds: dict[str, float], tag: str) -> list[tuple[str, float, Uncertainty]]:
        if not seeds:
            return []
        composed, unc = _score_scratch(db, f"sim_{tag}_{profile.id}_{nonce}",
                                       seeds, weights)
        db.commit()
        out: list[tuple[str, float, Uncertainty]] = []
        for node, val in composed.items():
            wid = node_to_work_id(node)
            if wid and wid not in seeds:
                out.append((wid, val, unc.get(
                    node, Uncertainty(0, 0, 0, 0, "leave_one_out", 1))))
        out.sort(key=lambda r: -r[1])
        assign_tie_groups(out)
        return out

    before_all = _rank(current, "before")
    after_all = _rank(hypothetical, "after")

    before_rank = {wid: i + 1 for i, (wid, _v, _u) in enumerate(before_all)}
    after_rank = {wid: i + 1 for i, (wid, _v, _u) in enumerate(after_all)}
    before_val = {wid: v for wid, v, _u in before_all}
    after_val = {wid: v for wid, v, _u in after_all}

    before = before_all[:body.limit]
    after = after_all[:body.limit]

    pool = services.build_pool(db, profile, context="aggregate", exclude_trusted=True)
    briefs = services.paper_briefs(
        db, [w for w, _v, _u in before] + [w for w, _v, _u in after])

    def _shape(rows_: list[tuple[str, float, Uncertainty]]) -> list[schemas.ScoredPaper]:
        out: list[schemas.ScoredPaper] = []
        for n, (wid, val, unc) in enumerate(rows_, start=1):
            brief = services.brief_or_placeholder(briefs, wid)
            p_cit = services.CITATION_PCT.percentile(db, brief.cited_by_count)
            tp = pool.trust_pct.get(wid, 0.0)
            gp = pool.global_pct.get(wid, 0.0)
            out.append(schemas.ScoredPaper(
                **brief.model_dump(), trust=val,
                uncertainty=services.to_uncertainty(unc),
                global_merit=pool.global_values.get(wid, 0.0),
                rank=n, disagreement=services.disagreement(tp, gp, p_cit),
            ))
        return out

    # A paper that leaves or enters the visible window is the interesting case, so
    # movement is computed over the union of both full rankings, not just the pages.
    horizon = len(before_all) + len(after_all) + 1
    moved: list[schemas.Moved] = []
    for wid in set(before_rank) | set(after_rank):
        br = before_rank.get(wid, horizon)
        ar = after_rank.get(wid, horizon)
        d_rank = br - ar          # positive => moved up
        d_trust = after_val.get(wid, 0.0) - before_val.get(wid, 0.0)
        if d_rank == 0 and abs(d_trust) < 1e-12:
            continue
        moved.append(schemas.Moved(work_id=wid, delta_rank=d_rank, delta_trust=d_trust))
    moved.sort(key=lambda m: (-abs(m.delta_rank), -abs(m.delta_trust)))
    moved = moved[:100]

    return schemas.SimulateResponse(before=_shape(before), after=_shape(after),
                                    moved=moved)


# ---------------------------------------------------------------------------
# /subgraph
# ---------------------------------------------------------------------------


@router.get("/profiles/{profile_id}/subgraph", response_model=schemas.SubgraphResponse)
def subgraph(
    profile: OwnedProfile,
    db: DbSession,
    focus: Optional[str] = None,
    limit: int = Query(default=600, ge=10, le=3000),
    context: schemas.RankingContext = "aggregate",
) -> schemas.SubgraphResponse:
    """Nodes and edges shaped for graphology/sigma.js ingestion, no client transform.

    Context filtering mirrors the engine's semantics rather than the edge's declared
    context: a named entity context is 'citation baseline + that family' (D1.5), so
    that is what gets returned.
    """
    _check_context(context)

    if context == "aggregate":
        ctx_filter, ctx_params = "", {}
    elif context == config.BASELINE_CONTEXT:
        ctx_filter, ctx_params = " AND context = :ctx", {"ctx": config.BASELINE_CONTEXT}
    else:
        ctx_filter = " AND context = ANY(:ctxs)"
        ctx_params = {"ctxs": [config.BASELINE_CONTEXT, context]}

    trusted = [t.work_id for t in db.query(Trust).filter(Trust.profile_id == profile.id)]
    pool = services.build_pool(db, profile, context=context, exclude_trusted=False)

    if focus:
        core_ids = [focus] + [i.work_id for i in pool.items[:max(limit // 6, 20)]]
    else:
        core_ids = trusted + [i.work_id for i in pool.items[:max(limit // 4, 25)]]
    core = list(dict.fromkeys(work_node(w) for w in core_ids))
    if not core:
        return schemas.SubgraphResponse(nodes=[], edges=[])

    rows = db.execute(
        text("SELECT src, dst, weight, relation FROM graph_edges "
             "WHERE (src = ANY(:core) OR dst = ANY(:core))" + ctx_filter +
             " ORDER BY weight DESC LIMIT :lim"),
        {"core": core, "lim": limit, **ctx_params},
    ).all()

    edges = [schemas.GraphEdgeOut(source=s, target=d, relation=rel, weight=float(w))
             for s, d, w, rel in rows]

    node_ids: list[str] = list(core)
    for e in edges:
        node_ids.append(e.source)
        node_ids.append(e.target)
    node_ids = list(dict.fromkeys(node_ids))

    # The profile node and its trust edges are per-user and therefore never in
    # graph_edges; synthesised here so the viz can show where the walks start.
    ego = profile_node(profile.id)
    if trusted:
        node_ids.append(ego)
        for t in db.query(Trust).filter(Trust.profile_id == profile.id).all():
            tn = work_node(t.work_id)
            if tn in node_ids:
                edges.append(schemas.GraphEdgeOut(
                    source=ego, target=tn,
                    relation="distrusts" if t.is_distrust else "trusts",
                    weight=(config.DISTRUST_WEIGHT if t.is_distrust
                            else config.TRUST_STRENGTH_SCALE.get(t.strength, 0.7)),
                ))

    labels = services.node_labels(db, node_ids)
    nodes: list[schemas.GraphNodeOut] = []
    for n in node_ids:
        kind, label, year = labels.get(n, (services.node_kind(n), n, None))
        wid = node_to_work_id(n)
        nodes.append(schemas.GraphNodeOut(
            id=n, label=label, kind=kind,
            trust=pool.trust_values.get(wid, 0.0) if wid else 0.0,
            year=year,
        ))

    present = {n.id for n in nodes}
    edges = [e for e in edges if e.source in present and e.target in present]
    return schemas.SubgraphResponse(nodes=nodes, edges=edges)
