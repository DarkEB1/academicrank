"""Search, paper detail, and the explanation endpoint."""
from __future__ import annotations

from typing import Optional, Union

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import text

from .. import config, ranking, schemas, searchrank, services
from ..deps import DbSession, OptionalProfile, OwnedProfile
from ..meritrank import Uncertainty as MrUncertainty, assign_tie_groups
from ..models import Trust, Work, node_to_work_id, profile_node, work_node

router = APIRouter(prefix="/api", tags=["papers"])

# Below this, a trigram match is noise rather than a typo.
TRGM_THRESHOLD = 0.2


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


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


@router.get("/papers/search", response_model=None)
def search(
    db: DbSession,
    maybe_profile: OptionalProfile,
    q: str = Query(min_length=2),
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    limit: int = Query(default=25, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
    rank: str = Query(default="relevance", pattern="^(relevance|trust|global)$"),
) -> Union[schemas.RankedSearchResponse, schemas.SearchResponse]:
    """Postgres `tsvector` full text, falling back to trigram similarity.

    The fallback is not decoration: OpenAlex titles are full of LaTeX fragments and
    transliterated names, and a user who types "Perelman entropy funcitonal" gets
    nothing at all from `plainto_tsquery`.

    `rank=trust|global` re-orders the same candidate set by reciprocal rank fusion
    of text relevance and MeritRank (see `searchrank.py`); `rank=relevance` (the
    default) is the untouched original behaviour.
    """
    year_sql = ""
    params: dict[str, object] = {"q": q, "lim": limit, "off": offset}
    if year_from is not None:
        year_sql += " AND w.year >= :yf"
        params["yf"] = year_from
    if year_to is not None:
        year_sql += " AND w.year <= :yt"
        params["yt"] = year_to

    # Visibility of user-contributed works (display-level, spec): hidden unless
    # the profile opted in; the uploader always sees their own; anonymous
    # callers never see them (the default is off).
    if maybe_profile is None:
        year_sql += " AND w.source <> 'user_upload'"
    elif not ranking.include_user_uploads(maybe_profile):
        year_sql += (" AND (w.source <> 'user_upload' OR EXISTS ("
                     "SELECT 1 FROM uploads u WHERE u.work_id = w.id "
                     "AND u.profile_id = :vis_me))")
        params["vis_me"] = maybe_profile.id

    if rank == "relevance":
        total, ids = _text_candidates(db, params, year_sql, limit, offset)
        briefs = services.paper_briefs(db, ids)
        return schemas.SearchResponse(
            total=total,
            items=[services.brief_or_placeholder(briefs, i) for i in ids],
        )

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


# ---------------------------------------------------------------------------
# Paper detail
# ---------------------------------------------------------------------------


@router.get("/profiles/{profile_id}/papers/{pid}", response_model=schemas.PaperDetail)
def paper_detail(pid: str, profile: OwnedProfile, db: DbSession) -> schemas.PaperDetail:
    work = db.get(Work, pid)
    if work is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Unknown work id {pid!r}.")

    # exclude_trusted=False: a paper you already trust still has a detail page.
    pool = services.build_pool(db, profile, context="aggregate", exclude_trusted=False)
    item = pool.by_id().get(pid)

    briefs = services.paper_briefs(db, [pid])
    brief = services.brief_or_placeholder(briefs, pid)

    if item is not None:
        trust = item.trust
        unc = services.to_uncertainty(item.uncertainty)
    else:
        # Reachable but below the pool cutoff, or unreachable. Ask the engine directly
        # rather than reporting 0 for a paper that simply fell off the page.
        trust = services.mr_of(db).node_score(
            profile_node(profile.id), work_node(pid), config.AGGREGATE)
        db.commit()
        unc = schemas.Uncertainty(
            stderr=abs(trust) * 0.5, ci_low=max(0.0, trust * 0.5), ci_high=trust * 1.5,
            tie_group=0, method="proportional_fallback", n_samples=max(pool.seeds, 1),
        )

    global_merit = pool.global_values.get(pid)
    if global_merit is None:
        global_merit = services.global_scores(db).get(pid, 0.0)

    p_trust = pool.trust_pct.get(pid, 0.0)
    p_global = pool.global_pct.get(pid, 0.0)
    p_cit = services.CITATION_PCT.percentile(db, brief.cited_by_count)

    trust_row = db.get(Trust, {"profile_id": profile.id, "work_id": pid})
    in_trust_set = (
        schemas.TrustEntry(work=brief, strength=int(trust_row.strength),
                           is_distrust=bool(trust_row.is_distrust))
        if trust_row is not None else None
    )

    topics = [
        schemas.TopicRef(id=r[0], name=r[1] or r[0], score=float(r[2] or 0.0))
        for r in db.execute(text(
            "SELECT t.id, t.display_name, wt.score FROM work_topics wt "
            "JOIN topics t ON t.id = wt.topic_id WHERE wt.work_id = :w "
            "ORDER BY wt.score DESC"), {"w": pid}).all()
    ]
    institutions = [
        schemas.InstitutionRef(id=r[0], name=r[1] or r[0], country=r[2])
        for r in db.execute(text(
            "SELECT i.id, i.display_name, i.country_code FROM work_institutions wi "
            "JOIN institutions i ON i.id = wi.institution_id WHERE wi.work_id = :w"),
            {"w": pid}).all()
    ]

    return schemas.PaperDetail(
        paper=brief,
        trust=trust,
        uncertainty=unc,
        global_merit=global_merit,
        cited_by_count=brief.cited_by_count,
        percentiles=schemas.Percentiles(
            trust=p_trust, **{"global": p_global}, citations=p_cit),
        disagreement=services.disagreement(p_trust, p_global, p_cit),
        in_trust_set=in_trust_set,
        topics=topics,
        institutions=institutions,
    )


# ---------------------------------------------------------------------------
# Explain -- the heart of the product
# ---------------------------------------------------------------------------

_RELATION_PHRASE = {
    "cites": "cites",
    "cited_by": "is cited by",
    "authored_by": "was written by",
    "wrote": "who also wrote",
    "couples": "shares references with",
    "co_cited": "is co-cited with",
    "published_in": "appeared in",
    "publishes": "which also published",
    "tagged": "is tagged",
    "tags": "which also tags",
    "affiliated": "is affiliated with",
    "hosts": "whose authors also wrote",
    "trusts": "trusts",
}

CAVEAT = (
    "Paths are reconstructed over the same edge list the scores were computed from, "
    "but MeritRank scores come from random walks: a path shown here is a plausible "
    "route by which trust reached this paper, not the single route the estimator "
    "took. Per-context figures are *marginal* -- score(context) minus score(citation "
    "baseline) -- because the engine replicates every paper-to-paper and trust edge "
    "into every context, so no relation family can be isolated (DECISIONS.md D1.6)."
)


@router.get("/profiles/{profile_id}/papers/{pid}/explain",
            response_model=schemas.ExplainResponse)
def explain(pid: str, profile: OwnedProfile, db: DbSession) -> schemas.ExplainResponse:
    work = db.get(Work, pid)
    if work is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Unknown work id {pid!r}.")

    ranking.ensure_seeded(db, profile)
    db.commit()

    raw_paths = ranking.explain_paths(db, profile, pid)
    by_context = ranking.context_breakdown(db, profile, pid)
    db.commit()

    trust = services.mr_of(db).node_score(
        profile_node(profile.id), work_node(pid), config.AGGREGATE)
    db.commit()

    seed_ids = [p["seed"] for p in raw_paths]
    all_nodes = [n for p in raw_paths for n in p["nodes"]]
    labels = services.node_labels(db, all_nodes)
    briefs = services.paper_briefs(db, seed_ids + [pid])
    target = services.brief_or_placeholder(briefs, pid)

    paths: list[schemas.ExplainPath] = []
    for p in raw_paths:
        nodes = []
        for n in p["nodes"]:
            kind, label, _year = labels.get(n, (services.node_kind(n), n, None))
            nodes.append(schemas.ExplainNode(id=n, kind=kind, label=label))
        paths.append(schemas.ExplainPath(
            nodes=nodes,
            edges=[schemas.ExplainEdge(relation=rel, weight=w)
                   for rel, w, _ctx in p["edges"]],
            contribution=float(p["contribution"]),
            seed=services.brief_or_placeholder(briefs, p["seed"]),
        ))

    seeds = db.query(Trust).filter(
        Trust.profile_id == profile.id, Trust.is_distrust.is_(False)).count()

    return schemas.ExplainResponse(
        target=target,
        trust=trust,
        uncertainty=schemas.Uncertainty(
            stderr=abs(trust) * 0.5, ci_low=max(0.0, trust * 0.5), ci_high=trust * 1.5,
            tie_group=0, method="proportional_fallback", n_samples=max(seeds, 1),
        ),
        paths=paths,
        by_context=[schemas.ContextContribution(**c) for c in by_context],
        summary=_summary(target, paths, by_context, trust, seeds),
        caveat=CAVEAT,
    )


def _summary(
    target: schemas.PaperBrief,
    paths: list[schemas.ExplainPath],
    by_context: list[dict],
    trust: float,
    seeds: int,
) -> str:
    title = target.title or target.id
    if seeds == 0:
        return (f"You have not trusted anything yet, so {title!r} has no derivation: "
                "every score in the system is relative to a trust set.")
    if not paths:
        return (f"{title!r} scores {trust:.6g} for you, but no path of length 3 or "
                "less connects it to anything you trust -- whatever score it has "
                "arrives over longer, weaker routes.")

    top = paths[0]
    seed_title = top.seed.title or top.seed.id
    hops = max(len(top.nodes) - 1, 1)
    # Interleave the intermediate node labels with the relation phrases. Joining the
    # phrases alone produced sentences like "was written by -> who also wrote", which
    # names nobody -- the whole point of the explanation is saying *who* or *what* the
    # trust travelled through. The final hop lands on the target, whose title is already
    # the subject of the sentence, so it is not repeated.
    _parts: list[str] = []
    for i, e in enumerate(top.edges):
        phrase = _RELATION_PHRASE.get(e.relation, e.relation)
        via = top.nodes[i + 1].label if i + 1 < len(top.nodes) else ""
        last = i == len(top.edges) - 1
        # The last hop lands on the target, which is already the subject of the
        # sentence, so it takes a pronoun rather than repeating its own title.
        _parts.append(f"{phrase} it" if last else (f"{phrase} {via}" if via else phrase))
    rels = ", ".join(_parts)
    strongest = max(
        (c for c in by_context if c["context"] != config.BASELINE_CONTEXT),
        key=lambda c: abs(c["marginal"]), default=None,
    )
    ctx_bit = ""
    if strongest is not None and abs(strongest["marginal"]) > 0:
        direction = "adds" if strongest["marginal"] > 0 else "removes"
        ctx_bit = (f" Of the relation families, {strongest['context']} {direction} the "
                   f"most over the citation baseline ({strongest['marginal']:+.4g}).")
    return (
        f"{title!r} scores {trust:.6g} for you. The strongest single route "
        f"({top.contribution:.0%} of the top paths' weight) runs {hops} "
        f"hop{'s' if hops != 1 else ''} from {seed_title!r}, which you trust: {rels}."
        + ctx_bit
    )
