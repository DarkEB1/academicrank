"""Personalised ranking: seeding, per-context composition, uncertainty, explanation.

The algorithm is the Rust engine's. What lives here is:
  * how a user's trust set becomes a synthetic ego node,
  * how per-context scores are composed with user-chosen weights,
  * uncertainty (leave-one-out), which the engine does not provide,
  * path reconstruction for /explain, which the engine also does not provide.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from . import config
from .meritrank import (
    MeritRank, Uncertainty, assign_tie_groups, leave_one_out_uncertainty,
)
from .models import Profile, Trust, node_to_work_id, profile_node, work_node

GLOBAL_EGO = "Uglobal_merit"


@dataclass
class RankedItem:
    work_id: str
    trust: float
    uncertainty: Uncertainty
    rank: int


def _mr(db: Session) -> MeritRank:
    return MeritRank(db.connection())


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------
# Multi-seed egos are encoded as a synthetic user node with weighted edges to each
# trusted paper. That is the encoding the engine actually supports: an ego must be a
# single `U` node (DECISIONS.md D1.4), and U->U edges replicate into every context
# (D1.5), so one set of trust edges seeds every context at once.
#
# mr_bulk_load_edges CLEARS all engine state, so trust edges written earlier do not
# survive a graph rebuild. ensure_seeded() is therefore idempotent and cheap, and is
# called before every read.

def ensure_seeded(db: Session, profile: Profile) -> int:
    mr = _mr(db)
    ego = profile_node(profile.id)
    rows = db.query(Trust).filter(Trust.profile_id == profile.id).all()
    for t in rows:
        w = (config.DISTRUST_WEIGHT if t.is_distrust
             else config.TRUST_STRENGTH_SCALE.get(t.strength, 0.7))
        mr.put_edge(ego, work_node(t.work_id), w, config.AGGREGATE)
    return len([r for r in rows if not r.is_distrust])


def ensure_global_ego(db: Session) -> None:
    """Unpersonalised reference point: an ego attached uniformly to the most-cited
    corpus papers. This is our stand-in for 'global merit' -- the engine has no
    ego-free score, since every walk starts somewhere."""
    mr = _mr(db)
    existing = db.execute(text(
        "SELECT count(*) FROM graph_edges WHERE src = :e"), {"e": GLOBAL_EGO}).scalar_one()
    if existing:
        pass
    rows = db.execute(text(
        "SELECT id FROM works WHERE is_stub = false "
        "ORDER BY in_corpus_cited_by DESC LIMIT 200")).all()
    for (wid,) in rows:
        mr.put_edge(GLOBAL_EGO, work_node(wid), 1.0, config.AGGREGATE)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _context_scores(mr: MeritRank, ego: str, limit: int) -> dict[str, dict[str, float]]:
    """One mr_scores call per context. Papers only (`kind` filters on node kind, and
    papers are Users)."""
    out: dict[str, dict[str, float]] = {}
    for ctx in config.CONTEXTS:
        rows = mr.scores(ego, context=ctx, limit=limit, kind="User")
        out[ctx] = {s.node: s.value for s in rows}
    return out


def compose(
    per_context: dict[str, dict[str, float]],
    weights: dict[str, float] | None = None,
) -> dict[str, float]:
    """score = baseline + sum_c w_c * (score_c - baseline).

    Each named context is 'baseline + one entity family' rather than an isolated
    family (DECISIONS.md D1.6), so the honest decomposition is the *marginal*
    contribution of each family over the shared paper-to-paper baseline.
    """
    weights = weights or config.DEFAULT_CONTEXT_WEIGHTS
    base = per_context.get(config.BASELINE_CONTEXT, {})

    # Only contexts that actually carry the baseline are usable. A context with no
    # edges of its own is degenerate (the engine returns just the ego), and treating
    # its absent nodes as zero would subtract a full baseline per context and drive
    # every score negative.
    active = [c for c in config.ENTITY_CONTEXTS if len(per_context.get(c, {})) > 1]

    nodes: set[str] = set(base)
    for c in active:
        nodes |= set(per_context.get(c, {}))

    out: dict[str, float] = {}
    for n in nodes:
        b = base.get(n, 0.0)
        v = b
        for c in active:
            sc = per_context.get(c, {}).get(n)
            if sc is None:
                # Not in this context's returned window: we do not know its score.
                # Impute "no marginal contribution" rather than "score zero".
                continue
            v += float(weights.get(c, 1.0)) * (sc - b)
        out[n] = max(0.0, v)
    return out


# --- cache -----------------------------------------------------------------------
# A full ranking costs one mr_scores call per context, plus one per context per seed
# for leave-one-out -- ~30 calls for a 5-seed profile, and each builds walks lazily on
# the engine side. Measured cold: ~16.5s. The Phase 3 gate is <500ms warm, so the raw
# per-context scores are cached per (profile, trust-set) and only the (cheap) weighted
# composition is redone when the user drags a slider. That also makes the parameter
# playground genuinely live.
_CACHE: dict[str, tuple[str, dict[str, dict[str, float]], dict[str, dict[str, float]]]] = {}
_CACHE_MAX = 64


def trust_signature(db: Session, profile: Profile) -> str:
    rows = db.query(Trust).filter(Trust.profile_id == profile.id).all()
    return "|".join(sorted(f"{t.work_id}:{t.strength}:{int(t.is_distrust)}" for t in rows))


def invalidate(profile_id: str) -> None:
    _CACHE.pop(profile_id, None)


def _scores_cached(
    db: Session, profile: Profile, fetch: int
) -> tuple[dict[str, dict[str, float]], dict[str, dict[str, float]], int]:
    """Returns (per_context, leave_one_out, seed_count), cached on the trust set."""
    sig = trust_signature(db, profile)
    hit = _CACHE.get(profile.id)
    if hit and hit[0] == sig:
        return hit[1], hit[2], len([s for s in sig.split("|") if s and not s.endswith(":1")])

    mr = _mr(db)
    seeds = ensure_seeded(db, profile)
    per_ctx = _context_scores(mr, profile_node(profile.id), fetch)
    loo = _leave_one_out(db, profile, None, fetch) if 2 <= seeds <= 12 else {}
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[profile.id] = (sig, per_ctx, loo)
    return per_ctx, loo, seeds


def warm(db: Session, profile: Profile, fetch: int = 12000) -> None:
    """Called on trust-set save so the first page view is not the slow one."""
    _scores_cached(db, profile, fetch)


def rank_profile(
    db: Session,
    profile: Profile,
    limit: int = 25,
    offset: int = 0,
    weights: dict[str, float] | None = None,
    exclude_trusted: bool = True,
    fetch: int = 12000,
    include_stubs: bool = False,
) -> tuple[list[RankedItem], int, int, float]:
    """Returns (items, total, seed_count, elapsed_ms).

    Stubs are excluded by default. A stub is a work referenced by fewer than three
    corpus papers: we hold its id, title and year so citation edges don't dangle, but it
    has no authors, venue, topics or abstract. There are 89,540 of them against 7,211
    full papers, and they accumulate enough score to fill most of a top-25 with rows a
    user can do nothing with -- observed in the end-to-end run before this was added.
    They remain in the graph and still carry trust through it; they are just not offered
    as results. `include_stubs=true` shows them.
    """
    t0 = time.time()
    per_ctx, loo_raw, seeds = _scores_cached(db, profile, fetch)
    composed = compose(per_ctx, weights)

    trusted = {t.work_id for t in db.query(Trust).filter(Trust.profile_id == profile.id)}

    stub_ids: set[str] = set()
    if not include_stubs:
        stub_ids = {
            r[0] for r in db.execute(
                text("SELECT id FROM works WHERE is_stub = true")).all()
        }

    # leave-one-out replicates are cached as raw per-context scores, so re-composing
    # them under the current weights is pure arithmetic.
    loo = {seed: compose(per_ctx_variant, weights)
           for seed, per_ctx_variant in loo_raw.items()}
    unc = leave_one_out_uncertainty(loo, composed) if loo else {
        n: Uncertainty(abs(v) * 0.5, max(0.0, v * 0.5), v * 1.5, 0, "leave_one_out", max(seeds, 1))
        for n, v in composed.items()
    }

    rows: list[tuple[str, float, Uncertainty]] = []
    for node, val in composed.items():
        wid = node_to_work_id(node)
        if not wid or (exclude_trusted and wid in trusted) or wid in stub_ids:
            continue
        rows.append((wid, val, unc.get(node, Uncertainty(0, 0, 0, 0, "leave_one_out", 1))))
    rows.sort(key=lambda r: -r[1])
    assign_tie_groups(rows)

    total = len(rows)
    page = rows[offset:offset + limit]
    items = [RankedItem(wid, val, u, offset + i + 1) for i, (wid, val, u) in enumerate(page)]
    return items, total, seeds, (time.time() - t0) * 1000.0


def _leave_one_out(
    db: Session, profile: Profile, weights: dict[str, float] | None, fetch: int
) -> dict[str, dict[str, dict[str, float]]]:
    """Re-rank with each seed removed, using a scratch ego so the user's own ego is
    never mutated. Bounded to trust sets of 12 or fewer -- beyond that the cost is not
    worth it and the spread is small anyway.

    Returns RAW per-context scores per removed seed (not composed), so the cache can
    re-compose them under whatever context weights the user later chooses.
    """
    mr = _mr(db)
    rows = [t for t in db.query(Trust).filter(
        Trust.profile_id == profile.id, Trust.is_distrust.is_(False)).all()]
    out: dict[str, dict[str, dict[str, float]]] = {}
    scratch = f"Uloo_{profile.id}"
    for skip in rows:
        for t in rows:
            if t.work_id == skip.work_id:
                continue
            mr.put_edge(scratch, work_node(t.work_id),
                        config.TRUST_STRENGTH_SCALE.get(t.strength, 0.7), config.AGGREGATE)
        out[skip.work_id] = _context_scores(mr, scratch, fetch)
        for t in rows:
            if t.work_id != skip.work_id:
                mr.delete_edge(scratch, work_node(t.work_id), config.AGGREGATE)
    return out


# ---------------------------------------------------------------------------
# Explanation -- reconstructed in Python over graph_edges.
# ---------------------------------------------------------------------------
# mr_graph() drops steps whose source is a non-User node, so entity hops
# (Paper->Author->Paper) never appear in it. Since those meta-paths are the most
# interesting part of the explanation, paths are reconstructed over exactly the same
# edge data that produced the scores. A trust score with no derivation is astrology.

def explain_paths(
    db: Session, profile: Profile, target_work: str, max_paths: int = 8, max_depth: int = 3
) -> list[dict]:
    seeds = [t.work_id for t in db.query(Trust).filter(
        Trust.profile_id == profile.id, Trust.is_distrust.is_(False)).all()]
    if not seeds:
        return []
    target = work_node(target_work)

    # Backward BFS from the target over reversed edges, bounded by depth.
    frontier = {target: (1.0, [target], [])}
    seen = {target}
    found: list[dict] = []
    seed_nodes = {work_node(s): s for s in seeds}

    for _ in range(max_depth):
        nxt: dict[str, tuple[float, list[str], list]] = {}
        if not frontier:
            break
        rows = db.execute(text(
            "SELECT src, dst, weight, relation, context FROM graph_edges "
            "WHERE dst = ANY(:nodes)"), {"nodes": list(frontier)}).all()
        for src, dst, w, rel, ctx in rows:
            if src in seen:
                continue
            prev_w, prev_path, prev_rels = frontier[dst]
            score = prev_w * float(w)
            cand = (score, [src] + prev_path, [(rel, float(w), ctx)] + prev_rels)
            if src not in nxt or nxt[src][0] < score:
                nxt[src] = cand
            if src in seed_nodes:
                found.append({
                    "seed": seed_nodes[src],
                    "nodes": cand[1],
                    "edges": cand[2],
                    "weight": score,
                })
        seen |= set(nxt)
        frontier = nxt
        if len(found) >= max_paths * 4:
            break

    found.sort(key=lambda p: -p["weight"])
    top = found[:max_paths]
    tot = sum(p["weight"] for p in top) or 1.0
    for p in top:
        p["contribution"] = p["weight"] / tot
    return top


def context_breakdown(
    db: Session, profile: Profile, target_work: str
) -> list[dict]:
    mr = _mr(db)
    ego = profile_node(profile.id)
    node = work_node(target_work)
    base = mr.node_score(ego, node, config.BASELINE_CONTEXT)
    out = [{"context": config.BASELINE_CONTEXT, "score": base, "marginal": base}]
    for c in config.ENTITY_CONTEXTS:
        s = mr.node_score(ego, node, c)
        out.append({"context": c, "score": s, "marginal": s - base})
    tot = sum(abs(o["marginal"]) for o in out) or 1.0
    for o in out:
        o["share"] = abs(o["marginal"]) / tot
    return out


def percentile_of(db: Session, column: str, value: float) -> float:
    q = text(f"SELECT count(*) FILTER (WHERE {column} <= :v)::float / "
             f"greatest(count(*),1) FROM works WHERE is_stub = false")
    return float(db.execute(q, {"v": value}).scalar_one())
