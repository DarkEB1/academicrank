"""Read-side helpers shared by the routers.

Nothing here reimplements the ranking algorithm -- it composes `ranking.py` (which
owns seeding/composition/uncertainty) with the corpus tables to produce the exact
shapes in API_CONTRACT.md.

Two conventions worth stating once:

* **Percentiles.** `trust` and `global` percentiles are computed *within the candidate
  pool* -- the set of papers your ego reaches with a non-zero score. A corpus-wide
  percentile for a personalised score would be meaningless, since the overwhelming
  majority of the corpus scores exactly 0 for any given ego. `citations` percentiles
  are corpus-wide (they are ego-independent), matching `ranking.percentile_of`.
* **Disagreement.** The normalised spread `max - min` over those three percentile
  ranks, so it lands in [0, 1] by construction.
"""
from __future__ import annotations

import bisect
import datetime as dt
import hashlib
import logging
import math
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, Sequence, TypeVar

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import Session

log = logging.getLogger("provenance.services")

from . import config, ranking, schemas
from .meritrank import MeritRank, Uncertainty
from .models import (
    Profile, Trust, node_to_work_id, profile_node, work_node,
)

# How many scored nodes we pull from the engine per context. The corpus is ~60k works
# but a single ego reaches far fewer with non-negligible weight.
POOL_FETCH = 4000
GLOBAL_FETCH = 4000


def mr_of(db: Session) -> MeritRank:
    return MeritRank(db.connection())


# ---------------------------------------------------------------------------
# Engine resilience
# ---------------------------------------------------------------------------
# The service builds a fresh ego's random walks lazily on first read, and while it is
# doing that over a 74k-node graph a concurrent call can come back as
# `Service returned Fail` (the connector's catch-all -- the service itself logs
# nothing). It is transient, so retry it rather than 500 at the user.

T = TypeVar("T")

_RETRY_DELAYS = (1.0, 3.0, 6.0)


def engine_retry(db: Session, fn: Callable[[], T], what: str = "engine call") -> T:
    last: Exception | None = None
    for i, delay in enumerate((*_RETRY_DELAYS, None)):
        try:
            return fn()
        except (DBAPIError, OperationalError) as e:
            last = e
            db.rollback()
            if delay is None:
                break
            log.warning("%s failed (attempt %d), retrying in %.0fs: %s",
                        what, i + 1, delay, str(e)[:160])
            time.sleep(delay)
    assert last is not None
    raise last


# ---------------------------------------------------------------------------
# Paper briefs
# ---------------------------------------------------------------------------

_BRIEF_SQL = text(
    """
    SELECT w.id, w.title, w.year, w.cited_by_count, w.in_corpus_cited_by,
           w.is_stub, w.doi, v.id AS venue_id, v.display_name AS venue_name
    FROM works w
    LEFT JOIN venues v ON v.id = w.venue_id
    WHERE w.id = ANY(:ids)
    """
)

_AUTHORS_SQL = text(
    """
    SELECT wa.work_id, a.id, a.display_name, wa.position
    FROM work_authors wa
    JOIN authors a ON a.id = wa.author_id
    WHERE wa.work_id = ANY(:ids)
    ORDER BY wa.work_id, wa.position
    """
)


def paper_briefs(db: Session, ids: Sequence[str]) -> dict[str, schemas.PaperBrief]:
    """Batch-load PaperBrief for a set of work ids. Two queries, never N+1."""
    ids = list(dict.fromkeys(ids))
    if not ids:
        return {}

    authors: dict[str, list[schemas.AuthorRef]] = {}
    for wid, aid, name, _pos in db.execute(_AUTHORS_SQL, {"ids": ids}).all():
        lst = authors.setdefault(wid, [])
        if len(lst) < 6:  # contract: up to 6
            lst.append(schemas.AuthorRef(id=aid, name=name or aid))

    out: dict[str, schemas.PaperBrief] = {}
    for row in db.execute(_BRIEF_SQL, {"ids": ids}).all():
        (wid, title, year, cited, in_corpus, is_stub, doi, ven_id, ven_name) = row
        out[wid] = schemas.PaperBrief(
            id=wid,
            title=title,
            year=year,
            authors=authors.get(wid, []),
            venue=(schemas.VenueRef(id=ven_id, name=ven_name or ven_id) if ven_id else None),
            cited_by_count=int(cited or 0),
            in_corpus_cited_by=int(in_corpus or 0),
            is_stub=bool(is_stub),
            doi=doi,
        )
    return out


def brief_or_placeholder(briefs: dict[str, schemas.PaperBrief], wid: str) -> schemas.PaperBrief:
    """A work id can be present in the graph but absent from `works` only if the graph
    is stale relative to the corpus. Rather than 500, surface it as an id-only brief."""
    return briefs.get(wid) or schemas.PaperBrief(id=wid)


# ---------------------------------------------------------------------------
# Percentiles
# ---------------------------------------------------------------------------


def rank_percentiles(values: dict[str, float]) -> dict[str, float]:
    """Fraction of the pool scoring <= each item. Ties share a percentile."""
    if not values:
        return {}
    ordered = sorted(values.values())
    n = len(ordered)
    return {k: bisect.bisect_right(ordered, v) / n for k, v in values.items()}


class _CitationPercentiles:
    """Corpus-wide citation distribution, cached with a short TTL because the corpus
    loader may still be appending rows while the API is up."""

    def __init__(self) -> None:
        self._sorted: list[int] = []
        self._at: float = 0.0
        self._lock = threading.Lock()

    def get(self, db: Session, ttl: float = 120.0) -> list[int]:
        now = time.time()
        with self._lock:
            if self._sorted and now - self._at < ttl:
                return self._sorted
        vals = [
            int(r[0] or 0)
            for r in db.execute(
                text("SELECT cited_by_count FROM works WHERE is_stub = false")
            ).all()
        ]
        vals.sort()
        with self._lock:
            self._sorted = vals
            self._at = now
        return vals

    def percentile(self, db: Session, value: int) -> float:
        vals = self.get(db)
        if not vals:
            return 0.0
        return bisect.bisect_right(vals, value) / len(vals)


CITATION_PCT = _CitationPercentiles()


def disagreement(p_trust: float, p_global: float, p_cit: float) -> float:
    return max(p_trust, p_global, p_cit) - min(p_trust, p_global, p_cit)


# ---------------------------------------------------------------------------
# Global merit
# ---------------------------------------------------------------------------

_global_lock = threading.Lock()
_global_cache: dict[int, tuple[float, dict[str, float]]] = {}
_GLOBAL_TTL = 600.0


def global_scores(db: Session, limit: int = GLOBAL_FETCH) -> dict[str, float]:
    """work_id -> unpersonalised merit, from the synthetic global ego.

    `mr_bulk_load_edges` clears engine state, so the global ego can vanish under us at
    any time. If the read comes back empty we re-seed and retry -- that is the only
    reliable way to detect a rebuild without polling. Seeding is 200 `mr_put_edge`
    calls, so the result is cached against the graph generation.
    """
    generation = graph_generation(db)
    now = time.time()
    with _global_lock:
        hit = _global_cache.get(generation)
        if hit is not None and now - hit[0] < _GLOBAL_TTL:
            return hit[1]

    # NB: the adapter is rebuilt on every call rather than captured. `Session.commit()`
    # and `.rollback()` release the underlying Connection back to the pool, so a
    # MeritRank instance that outlives either one is holding a closed connection.
    def _read() -> dict[str, float]:
        rows = mr_of(db).scores(ranking.GLOBAL_EGO, context=config.AGGREGATE,
                                limit=limit, kind="User")
        out: dict[str, float] = {}
        for s in rows:
            wid = node_to_work_id(s.node)
            if wid:
                out[wid] = s.value
        return out

    scores = engine_retry(db, _read, "global scores")
    if not scores:
        engine_retry(db, lambda: ranking.ensure_global_ego(db), "ensure_global_ego")
        db.commit()
        scores = engine_retry(db, _read, "global scores (after seeding)")

    with _global_lock:
        _global_cache.clear()
        _global_cache[generation] = (now, scores)
    return scores


# ---------------------------------------------------------------------------
# Weights / context selection
# ---------------------------------------------------------------------------


def stored_weights(profile: Profile) -> dict[str, float]:
    params = profile.params or {}
    cw = params.get("context_weights") if isinstance(params, dict) else None
    weights = dict(config.DEFAULT_CONTEXT_WEIGHTS)
    if isinstance(cw, dict):
        for k, v in cw.items():
            if k in weights:
                weights[k] = float(v)
    return weights


def weights_for_context(profile: Profile, context: str) -> dict[str, float]:
    """Express single-context selection through `ranking.compose` weights.

    compose() computes `base + sum_c w_c * (score_c - base)`, so:
      * every entity weight 0        -> exactly the citation baseline;
      * w_ctx = 1 and the rest 0     -> exactly score(ctx);
      * the profile's stored weights -> the composed aggregate.
    No second code path, and therefore no risk of the two drifting apart.
    """
    if context == "aggregate":
        return stored_weights(profile)
    if context == config.BASELINE_CONTEXT:
        return {c: 0.0 for c in config.CONTEXTS}
    if context in config.ENTITY_CONTEXTS:
        w = {c: 0.0 for c in config.CONTEXTS}
        w[context] = 1.0
        return w
    raise ValueError(context)


# ---------------------------------------------------------------------------
# The candidate pool
# ---------------------------------------------------------------------------


@dataclass
class Pool:
    """One full pass of the ranking engine for a profile, plus everything derived from
    it. Built once per request and shared by every field of the response."""

    items: list[ranking.RankedItem]
    total: int
    seeds: int
    elapsed_ms: float
    trust_values: dict[str, float] = field(default_factory=dict)
    trust_pct: dict[str, float] = field(default_factory=dict)
    global_values: dict[str, float] = field(default_factory=dict)
    global_pct: dict[str, float] = field(default_factory=dict)

    def by_id(self) -> dict[str, ranking.RankedItem]:
        return {i.work_id: i for i in self.items}


# A full pass costs ~5s per context set, and rank_profile also runs one leave-one-out
# replicate per seed, so a cold 6-seed profile is ~30s of engine time. Every endpoint
# in the contract needs the same pool, and several need it more than once, so it is
# cached on the exact inputs that determine it: the trust set, the weights, the
# exclusion flag, and the graph generation. Any change to any of those is a new key --
# there is no staleness window to reason about.
_POOL_TTL = 600.0
_POOL_MAX = 64
_pool_cache: dict[tuple, tuple[float, Pool]] = {}
_pool_lock = threading.Lock()


def graph_generation(db: Session) -> int:
    """Changes whenever scripts/build_graph.py reloads the edge list, which also clears
    engine state -- so it invalidates every cached score."""
    return int(db.execute(text("SELECT coalesce(max(id), 0) FROM graph_edges"))
               .scalar_one())


def trust_signature(db: Session, profile: Profile) -> tuple[str, int]:
    rows = db.execute(
        text("SELECT work_id, strength, is_distrust FROM trust "
             "WHERE profile_id = :p ORDER BY work_id"),
        {"p": profile.id},
    ).all()
    digest = hashlib.sha1(repr([tuple(r) for r in rows]).encode()).hexdigest()[:16]
    return digest, len(rows)


def invalidate_pool(profile_id: str) -> None:
    with _pool_lock:
        for key in [k for k in _pool_cache if k[0] == profile_id]:
            _pool_cache.pop(key, None)


def build_pool(
    db: Session,
    profile: Profile,
    context: str = "aggregate",
    exclude_trusted: bool = True,
) -> Pool:
    sig, n_trust = trust_signature(db, profile)
    # An ego with no edges does not exist in the engine, so asking it for scores is a
    # question about a node that was never registered. Short-circuit instead.
    if n_trust == 0:
        return Pool(items=[], total=0, seeds=0, elapsed_ms=0.0)

    weights = weights_for_context(profile, context)
    key = (
        profile.id, sig, exclude_trusted, graph_generation(db),
        tuple(sorted(weights.items())),
    )
    now = time.time()
    with _pool_lock:
        hit = _pool_cache.get(key)
        if hit is not None and now - hit[0] < _POOL_TTL:
            return hit[1]

    def _run():
        return ranking.rank_profile(
            db, profile, limit=POOL_FETCH, offset=0,
            weights=weights, exclude_trusted=exclude_trusted, fetch=POOL_FETCH,
        )

    items, total, seeds, elapsed = engine_retry(db, _run, "rank_profile")
    db.commit()

    trust_values = {i.work_id: i.trust for i in items}
    gvals_all = global_scores(db, GLOBAL_FETCH)
    gvals = {wid: gvals_all.get(wid, 0.0) for wid in trust_values}
    pool = Pool(
        items=items,
        total=total,
        seeds=seeds,
        elapsed_ms=elapsed,
        trust_values=trust_values,
        trust_pct=rank_percentiles(trust_values),
        global_values=gvals,
        global_pct=rank_percentiles(gvals),
    )

    with _pool_lock:
        if len(_pool_cache) >= _POOL_MAX:
            oldest = min(_pool_cache, key=lambda k: _pool_cache[k][0])
            _pool_cache.pop(oldest, None)
        _pool_cache[key] = (now, pool)
    return pool


def to_uncertainty(u: Uncertainty) -> schemas.Uncertainty:
    return schemas.Uncertainty(
        stderr=u.stderr,
        ci_low=u.ci_low,
        ci_high=u.ci_high,
        tie_group=u.tie_group,
        method="leave_one_out" if u.method == "leave_one_out" else "repeat_sample",
        n_samples=u.n_samples,
    )


def scored_paper(
    db: Session,
    item: ranking.RankedItem,
    brief: schemas.PaperBrief,
    pool: Pool,
    rank: int | None = None,
) -> schemas.ScoredPaper:
    p_cit = CITATION_PCT.percentile(db, brief.cited_by_count)
    p_trust = pool.trust_pct.get(item.work_id, 0.0)
    p_global = pool.global_pct.get(item.work_id, 0.0)
    return schemas.ScoredPaper(
        **brief.model_dump(),
        trust=item.trust,
        uncertainty=to_uncertainty(item.uncertainty),
        global_merit=pool.global_values.get(item.work_id, 0.0),
        rank=rank if rank is not None else item.rank,
        disagreement=disagreement(p_trust, p_global, p_cit),
    )


def scored_page(
    db: Session, items: Sequence[ranking.RankedItem], pool: Pool, start_rank: int = 1
) -> list[schemas.ScoredPaper]:
    briefs = paper_briefs(db, [i.work_id for i in items])
    return [
        scored_paper(db, it, brief_or_placeholder(briefs, it.work_id), pool,
                     rank=start_rank + n)
        for n, it in enumerate(items)
    ]


# ---------------------------------------------------------------------------
# Cold start
# ---------------------------------------------------------------------------


def cold_start(seeds: int) -> schemas.ColdStart:
    reliable = seeds >= config.COLD_START_MIN_SEEDS
    if reliable:
        return schemas.ColdStart(seeds=seeds, reliable=True, message=None)
    if seeds == 0:
        msg = (
            "You have not trusted any papers yet, so there is nothing to rank from. "
            "Trust a few papers you know well and the ranking becomes meaningful."
        )
    else:
        msg = (
            f"Only {seeds} seed{'s' if seeds != 1 else ''} in your trust set; "
            f"{config.COLD_START_MIN_SEEDS} are needed before these results are "
            "statistically meaningful. Treat this ordering as a sketch, not a result."
        )
    return schemas.ColdStart(seeds=seeds, reliable=False, message=msg)


# ---------------------------------------------------------------------------
# Year filtering
# ---------------------------------------------------------------------------


def year_filter(
    db: Session, ids: Sequence[str], year_from: int | None, year_to: int | None
) -> set[str] | None:
    """None means 'no filter requested'."""
    if year_from is None and year_to is None:
        return None
    if not ids:
        return set()
    clauses = ["id = ANY(:ids)"]
    params: dict[str, object] = {"ids": list(ids)}
    if year_from is not None:
        clauses.append("year >= :yf")
        params["yf"] = year_from
    if year_to is not None:
        clauses.append("year <= :yt")
        params["yt"] = year_to
    q = text(f"SELECT id FROM works WHERE {' AND '.join(clauses)}")
    return {r[0] for r in db.execute(q, params).all()}


# ---------------------------------------------------------------------------
# Graph distance (novelty)
# ---------------------------------------------------------------------------

MAX_NOVELTY_DEPTH = 4
_FRONTIER_CAP = 6000


def trust_set_distances(
    db: Session, seed_work_ids: Sequence[str], max_depth: int = MAX_NOVELTY_DEPTH
) -> dict[str, int]:
    """BFS over `graph_edges` from the trust set. Returns work_id -> hop count.

    This is a real graph distance over exactly the edges the scores were computed
    from, not a proxy. Frontiers are capped by descending edge weight so a hub topic
    node cannot make one level cost the whole corpus.
    """
    dist: dict[str, int] = {}
    frontier = [work_node(w) for w in seed_work_ids]
    seen: set[str] = set(frontier)
    for w in seed_work_ids:
        dist[w] = 0
    depth = 0
    while frontier and depth < max_depth:
        depth += 1
        rows = db.execute(
            text(
                "SELECT dst FROM graph_edges WHERE src = ANY(:nodes) "
                "ORDER BY weight DESC LIMIT :cap"
            ),
            {"nodes": frontier, "cap": _FRONTIER_CAP},
        ).all()
        nxt: list[str] = []
        for (dst,) in rows:
            if dst in seen:
                continue
            seen.add(dst)
            nxt.append(dst)
            wid = node_to_work_id(dst)
            if wid is not None and wid not in dist:
                dist[wid] = depth
        frontier = nxt
    return dist


def novelty_of(dist: dict[str, int], work_id: str) -> float:
    """0 = in the trust set, 1 = unreachable within MAX_NOVELTY_DEPTH hops."""
    d = dist.get(work_id)
    if d is None:
        return 1.0
    return min(d, MAX_NOVELTY_DEPTH) / MAX_NOVELTY_DEPTH


# ---------------------------------------------------------------------------
# Node labelling (subgraph + explain)
# ---------------------------------------------------------------------------


def node_kind(node: str) -> str:
    if node.startswith("Uprofile_"):
        return "profile"
    if node.startswith("BA"):
        return "author"
    if node.startswith("BI"):
        return "institution"
    if node.startswith("BT"):
        return "topic"
    if node.startswith("BS"):
        return "venue"
    if node.startswith("U"):
        return "paper"
    return "paper"


def _entity_source_id(node: str) -> str:
    """Engine node name -> OpenAlex id. `BA5023888391` -> `A5023888391`."""
    return node[1:]


def node_labels(db: Session, nodes: Iterable[str]) -> dict[str, tuple[str, str, int | None]]:
    """node -> (kind, label, year). One query per entity family, never per node."""
    nodes = list(dict.fromkeys(nodes))
    buckets: dict[str, list[str]] = {}
    for n in nodes:
        buckets.setdefault(node_kind(n), []).append(n)

    out: dict[str, tuple[str, str, int | None]] = {}

    papers = buckets.get("paper", [])
    if papers:
        ids = [node_to_work_id(n) or n for n in papers]
        rows = {
            r[0]: (r[1], r[2])
            for r in db.execute(
                text("SELECT id, title, year FROM works WHERE id = ANY(:ids)"),
                {"ids": ids},
            ).all()
        }
        for n in papers:
            wid = node_to_work_id(n) or n
            title, year = rows.get(wid, (None, None))
            out[n] = ("paper", title or wid, year)

    for kind, table in (
        ("author", "authors"), ("institution", "institutions"),
        ("topic", "topics"), ("venue", "venues"),
    ):
        group = buckets.get(kind, [])
        if not group:
            continue
        ids = [_entity_source_id(n) for n in group]
        rows = {
            r[0]: r[1]
            for r in db.execute(
                text(f"SELECT id, display_name FROM {table} WHERE id = ANY(:ids)"),
                {"ids": ids},
            ).all()
        }
        for n in group:
            sid = _entity_source_id(n)
            out[n] = (kind, rows.get(sid) or sid, None)

    for n in buckets.get("profile", []):
        out[n] = ("profile", "Your trust profile", None)

    for n in nodes:
        out.setdefault(n, (node_kind(n), n, None))
    return out


# ---------------------------------------------------------------------------
# Trust entries
# ---------------------------------------------------------------------------


def trust_entries(db: Session, profile: Profile) -> list[schemas.TrustEntry]:
    rows = (
        db.query(Trust)
        .filter(Trust.profile_id == profile.id)
        .order_by(Trust.created_at.desc())
        .all()
    )
    briefs = paper_briefs(db, [r.work_id for r in rows])
    return [
        schemas.TrustEntry(
            work=brief_or_placeholder(briefs, r.work_id),
            strength=int(r.strength),
            is_distrust=bool(r.is_distrust),
        )
        for r in rows
    ]


def warm_profile(profile_id: str) -> None:
    """Background warm: re-seed the ego and force the engine to build its walks.

    Called after every trust mutation. Uses its own session because it outlives the
    request that scheduled it.
    """
    from .db import SessionLocal

    with SessionLocal() as db:
        prof = db.get(Profile, profile_id)
        if prof is None:
            return
        try:
            ranking.ensure_seeded(db, prof)
            # A scores() call is what actually forces walk construction: the engine
            # builds walks lazily per ego on first read after a bulk load.
            mr_of(db).scores(profile_node(prof.id), context=config.BASELINE_CONTEXT,
                             limit=1, kind="User")
            db.commit()
            # Then pay the full ranking cost here, off the request path, so the user's
            # next read comes out of the pool cache instead of taking ~90s.
            invalidate_pool(prof.id)
            build_pool(db, prof, context="aggregate", exclude_trusted=True)
            prof.warmed_at = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
            db.commit()
        except Exception:  # noqa: BLE001 - a failed warm must never break the request
            log.warning("warm failed for %s", profile_id, exc_info=True)
            db.rollback()


# ---------------------------------------------------------------------------
# Shannon entropy
# ---------------------------------------------------------------------------


def shannon(counts: Sequence[int]) -> float:
    total = sum(counts)
    if total <= 0:
        return 0.0
    h = 0.0
    for c in counts:
        if c <= 0:
            continue
        p = c / total
        h -= p * math.log(p)
    return h


def max_shannon(n_observations: int, n_categories_in_corpus: int) -> float:
    """The most entropy a trust set of this size *could* have.

    Bounded by the number of observations as well as by the number of categories:
    with 5 papers you cannot touch more than 5 distinct topics, so normalising by
    log(#topics in corpus) would report every small trust set as an echo chamber.
    """
    k = min(max(n_observations, 1), max(n_categories_in_corpus, 1))
    return math.log(k) if k > 1 else 0.0
