"""Search, paper detail, and the explanation endpoint."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import text

from .. import config, ranking, schemas, services
from ..deps import DbSession, OwnedProfile
from ..models import Trust, Work, node_to_work_id, profile_node, work_node

router = APIRouter(prefix="/api", tags=["papers"])

# Below this, a trigram match is noise rather than a typo.
TRGM_THRESHOLD = 0.2


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


@router.get("/papers/search", response_model=schemas.SearchResponse)
def search(
    db: DbSession,
    q: str = Query(min_length=2),
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    limit: int = Query(default=25, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
) -> schemas.SearchResponse:
    """Postgres `tsvector` full text, falling back to trigram similarity.

    The fallback is not decoration: OpenAlex titles are full of LaTeX fragments and
    transliterated names, and a user who types "Perelman entropy funcitonal" gets
    nothing at all from `plainto_tsquery`.
    """
    year_sql = ""
    params: dict[str, object] = {"q": q, "lim": limit, "off": offset}
    if year_from is not None:
        year_sql += " AND w.year >= :yf"
        params["yf"] = year_from
    if year_to is not None:
        year_sql += " AND w.year <= :yt"
        params["yt"] = year_to

    total = int(db.execute(text(
        "SELECT count(*) FROM works w "
        "WHERE w.tsv @@ plainto_tsquery('english', :q)" + year_sql
    ), params).scalar_one())

    if total:
        rows = db.execute(text(
            "SELECT w.id FROM works w "
            "WHERE w.tsv @@ plainto_tsquery('english', :q)" + year_sql +
            " ORDER BY ts_rank(w.tsv, plainto_tsquery('english', :q)) DESC,"
            " w.cited_by_count DESC LIMIT :lim OFFSET :off"
        ), params).all()
    else:
        # SET does not accept bind parameters; set_config(..., is_local => true) is
        # the parameterisable, transaction-scoped equivalent.
        db.execute(text("SELECT set_config('pg_trgm.similarity_threshold', :t, true)"),
                   {"t": str(TRGM_THRESHOLD)})
        total = int(db.execute(text(
            "SELECT count(*) FROM works w WHERE w.title % :q" + year_sql
        ), params).scalar_one())
        rows = db.execute(text(
            "SELECT w.id FROM works w WHERE w.title % :q" + year_sql +
            " ORDER BY similarity(w.title, :q) DESC, w.cited_by_count DESC"
            " LIMIT :lim OFFSET :off"
        ), params).all()

    ids = [r[0] for r in rows]
    briefs = services.paper_briefs(db, ids)
    return schemas.SearchResponse(
        total=total,
        items=[services.brief_or_placeholder(briefs, i) for i in ids],
    )


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
            tie_group=0, method="leave_one_out", n_samples=max(pool.seeds, 1),
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
            tie_group=0, method="leave_one_out", n_samples=max(seeds, 1),
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
