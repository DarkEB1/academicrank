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
from .graphmeta import graph_version
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
    # Fame-normalised proximity (R3): log(trust+eps) - gamma*log(background+eps).
    # A separate displayed field, never a redefinition of `trust`.
    lift: float = 0.0
    lift_uncertainty: Uncertainty | None = None


LIFT_EPS = 1e-9


def lift_gamma_of(profile: Profile) -> float:
    """Per-profile background exponent, 0..1, default 0.5 -- the operating point
    measured in the experiments (E6): recall holds while the top-25 popularity
    percentile drops. 0 reproduces the raw trust ordering; 1 is full lift, which
    measurably over-corrects."""
    params = profile.params or {}
    try:
        v = float(params.get("lift_gamma", 0.5)) if isinstance(params, dict) else 0.5
    except (TypeError, ValueError):
        v = 0.5
    return min(1.0, max(0.0, v))


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
# survive a graph rebuild. ensure_seeded() used to re-put every trust edge on every
# cold read to cover that -- at a measured ~87ms per serialised mr_put_edge, 3.9s of
# engine time at 45 seeds, paid by everyone queueing behind it. It is now gated on
# (trust signature, graph version): edges are re-written only when the trust set
# changed or the graph was rebuilt (build_graph.py bumps graph_meta.version).
# put_edge is an RPC through the connector, effective immediately regardless of the
# surrounding Postgres transaction, so recording the seed marker right after the
# loop is correct even if the caller later rolls back.

_SEEDED: dict[str, tuple[str, int]] = {}


def _sig_of_rows(rows: list[Trust]) -> str:
    return "|".join(sorted(
        f"{t.work_id}:{t.strength}:{int(t.is_distrust)}" for t in rows))


def ensure_seeded(
    db: Session, profile: Profile,
    sig: str | None = None, version: int | None = None,
) -> int:
    rows = db.query(Trust).filter(Trust.profile_id == profile.id).all()
    if sig is None:
        sig = _sig_of_rows(rows)
    if version is None:
        version = graph_version(db)
    n_seeds = len([r for r in rows if not r.is_distrust])
    if _SEEDED.get(profile.id) == (sig, version):
        return n_seeds
    mr = _mr(db)
    ego = profile_node(profile.id)
    for t in rows:
        w = (config.DISTRUST_WEIGHT if t.is_distrust
             else config.TRUST_STRENGTH_SCALE.get(t.strength, 0.7))
        mr.put_edge(ego, work_node(t.work_id), w, config.AGGREGATE)
    _SEEDED[profile.id] = (sig, version)
    return n_seeds


def forget_seeded(profile_id: str) -> None:
    """Force the next ensure_seeded to re-write the engine edges. Used when the
    engine is discovered to have lost state the version counter cannot see (an
    mr-service restart wipes its in-memory graph without any Postgres change)."""
    _SEEDED.pop(profile_id, None)


def mark_seeded(db: Session, profile: Profile) -> None:
    """Record that the profile's CURRENT trust edges are already in the engine.
    The upload confirm path writes them itself (inside one mr_put_edges batch,
    where they cost one RPC instead of ~87ms each); this stops the next read
    from re-putting every one of them."""
    _SEEDED[profile.id] = (trust_signature(db, profile), graph_version(db))


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
    """score = weighted MEAN of the per-context scores a node actually has.

    Replaced the marginal sum `w_base*b + sum_c w_c*(s_c - b)` on 2026-07-30
    (v2 plan R4), for two measured reasons -- and explicitly NOT for hubness,
    which no combiner moves (experiments E5: every one lands at the 86th-87th
    popularity percentile):

      * **Variance.** Each context's score is an independent Monte Carlo estimate
        (separate walk sets per subgraph). The marginal sum had variance
        ~13*sigma^2 against sigma^2/5 for the mean -- most of what it computed in
        the tail was amplified sampling noise, on scores of 1-5 walk visits.
      * **Honesty of the tail.** The old max(0, .) clamp collapsed the ~640
        citation-reachable papers whose noisy marginals summed negative into one
        indistinguishable block at exactly 0. A mean of non-negative scores needs
        no clamp, so those papers keep their ordering.

    Contexts a node was never returned in contribute nothing and are excluded
    from the denominator: "not in this window" is "unknown", not "zero"
    (same imputation stance as before). A context with no edges of its own is
    degenerate (the engine returns just the ego) and is skipped entirely.

    Each named context is still 'baseline + one entity family' (DECISIONS.md
    D1.6); the *displayed* per-context decomposition remains the marginal
    (`context_breakdown`), which this function no longer computes.

    Zeroing a slider removes that context from the mean, so every slider --
    including `citation` -- genuinely reorders. We never show a control that
    does nothing.
    """
    weights = weights or config.DEFAULT_CONTEXT_WEIGHTS

    active = [
        c for c in [config.BASELINE_CONTEXT] + config.ENTITY_CONTEXTS
        if len(per_context.get(c, {})) > 1 and float(weights.get(c, 1.0)) > 0.0
    ]

    nodes: set[str] = set()
    for c in active:
        nodes |= set(per_context.get(c, {}))

    out: dict[str, float] = {}
    for n in nodes:
        num = 0.0
        den = 0.0
        for c in active:
            sc = per_context.get(c, {}).get(n)
            if sc is None:
                continue
            w = float(weights.get(c, 1.0))
            num += w * sc
            den += w
        if den > 0.0:
            out[n] = num / den
    return out


# --- cache -----------------------------------------------------------------------
# A full ranking costs one mr_scores call per context, plus one per context per seed
# for leave-one-out -- ~30 calls for a 5-seed profile, and each builds walks lazily on
# the engine side. Measured cold: ~16.5s. The Phase 3 gate is <500ms warm, so the raw
# per-context scores are cached per (profile, trust-set) and only the (cheap) weighted
# composition is redone when the user drags a slider. That also makes the parameter
# playground genuinely live.
# value: (trust_signature, graph_version, per_context_scores, leave_one_out_scores)
_CACHE: dict[str, tuple[str, int, dict[str, dict[str, float]], dict[str, dict[str, float]]]] = {}
_CACHE_MAX = 64
# Window for leave-one-out replicates (uncertainty), much smaller than the ranking pool.
LOO_FETCH = 2500

# Leave-one-out costs one mr_scores call per context PER REPLICATE, so the number of
# REPLICATES is what has to be bounded -- not the size of the trust set. The previous
# rule (`2 <= seeds <= 12`) bounded the trust set instead, which meant every profile
# with 13 or more seeds silently fell through to the crude proportional band. That band
# is `stderr = |v| * 0.5`, i.e. relative uncertainty of exactly 0.5, which the UI grades
# `uninformative` and which collapses the whole page into one tie group -- across the
# ENTIRE 10-50 seed range this product is built for. Bounding replicates instead keeps
# a real jackknife at every trust-set size for the same worst-case cost.
LOO_MAX_REPLICATES = 12


def trust_signature(db: Session, profile: Profile) -> str:
    rows = db.query(Trust).filter(Trust.profile_id == profile.id).all()
    return _sig_of_rows(rows)


def include_user_uploads(profile: Profile) -> bool:
    params = profile.params or {}
    return bool(params.get("include_user_uploads")) if isinstance(params, dict) else False


def hidden_upload_ids(db: Session, profile: Profile) -> set[str]:
    """UL... local works this profile must not see in results: every
    user-contributed work except the profile's own, unless the profile opted in
    via include_user_uploads (default false; spec Visibility)."""
    if include_user_uploads(profile):
        return set()
    rows = db.execute(text(
        "SELECT w.id FROM works w WHERE w.source = 'user_upload' "
        "AND NOT EXISTS (SELECT 1 FROM uploads u "
        "  WHERE u.work_id = w.id AND u.profile_id = :p)"),
        {"p": profile.id}).all()
    return {r[0] for r in rows}


def trust_units(db: Session, profile: Profile) -> list[tuple[str, list[Trust]]]:
    """Group the (non-distrust) trust set into leave-one-out units: every
    hand-added seed is its own unit; ALL seeds sourced from one upload form a
    single unit (spec B1: a 40-reference upload is one considered decision, not
    40 -- jackknifing it seed-by-seed would collapse every ranking into one tie
    group and make LOO cost scale with bibliography size). A work sourced by
    several uploads joins the lowest upload id; a work both hand-added and
    upload-sourced counts with the upload (removing the upload removes its
    influence in the replicate either way).
    """
    rows = [t for t in db.query(Trust).filter(
        Trust.profile_id == profile.id, Trust.is_distrust.is_(False)).all()]
    rows.sort(key=lambda t: t.work_id)
    upload_of: dict[str, str] = {}
    for wid, uid in db.execute(text(
        "SELECT work_id, min(upload_id) FROM trust_sources "
        "WHERE profile_id = :p GROUP BY work_id"), {"p": profile.id}).all():
        upload_of[wid] = uid
    units: dict[str, list[Trust]] = {}
    for t in rows:
        key = (f"upload:{upload_of[t.work_id]}" if t.work_id in upload_of
               else f"seed:{t.work_id}")
        units.setdefault(key, []).append(t)
    return sorted(units.items())


def invalidate(profile_id: str) -> None:
    _CACHE.pop(profile_id, None)


def _scores_cached(
    db: Session, profile: Profile, fetch: int
) -> tuple[dict[str, dict[str, float]], dict[str, dict[str, float]], int]:
    """Returns (per_context, leave_one_out, considered_decisions), cached on
    (trust set, graph version) -- a graph mutation bumps graph_meta.version and
    thereby invalidates every profile's cached scores in this layer too.

    `considered_decisions` counts leave-one-out UNITS, not trust rows: an
    upload's whole bibliography is one decision (spec B1), so it feeds the
    cold-start notice and the jackknife n, where seed-counting would overstate
    both.
    """
    sig = trust_signature(db, profile)
    version = graph_version(db)
    units = len(trust_units(db, profile))
    hit = _CACHE.get(profile.id)
    if hit and hit[0] == sig and hit[1] == version:
        return hit[2], hit[3], units

    mr = _mr(db)
    seeds = ensure_seeded(db, profile, sig=sig, version=version)
    per_ctx = _context_scores(mr, profile_node(profile.id), fetch)
    # An mr-service restart wipes the in-memory graph without moving the version
    # counter. If a seeded ego comes back empty, assume lost engine state, force a
    # re-seed and retry once -- the same pattern services.global_scores uses.
    if seeds and not any(len(v) > 1 for v in per_ctx.values()):
        forget_seeded(profile.id)
        ensure_seeded(db, profile, sig=sig, version=version)
        per_ctx = _context_scores(mr, profile_node(profile.id), fetch)
    # Leave-one-out costs one mr_scores call per context PER SEED, so it dominates cold
    # start. It only feeds uncertainty on the rows a user actually sees, so it runs on a
    # much smaller window than the ranking pool. At fetch=12000 the full-width version
    # pushed a 5-seed cold start past 300s; capped, it is back to ~1 minute.
    loo_fetch = min(fetch, LOO_FETCH)
    loo = _leave_one_out(db, profile, None, loo_fetch) if units >= 2 else {}
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[profile.id] = (sig, version, per_ctx, loo)
    return per_ctx, loo, units


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
    lift_gamma: float = 0.5,
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
    # Display-level exclusion of user-contributed works (spec Visibility):
    # UL... locals are hidden unless the profile opted in -- except the
    # uploader's own, which they always see. This is display-level ONLY: the
    # shared graph still carries the edges and every score is already
    # perturbed by them (documented in KNOWN_ISSUES, stated in the UI).
    stub_ids |= hidden_upload_ids(db, profile)

    # leave-one-out replicates are cached as raw per-context scores, so re-composing
    # them under the current weights is pure arithmetic.
    loo = {seed: compose(per_ctx_variant, weights)
           for seed, per_ctx_variant in loo_raw.items()}
    unc = leave_one_out_uncertainty(loo, composed, n_seeds=seeds) if loo else {
        n: Uncertainty(abs(v) * 0.5, max(0.0, v * 0.5), v * 1.5, 0,
                       "proportional_fallback", max(seeds, 1))
        for n, v in composed.items()
    }

    # --- lift (R3): fame-normalised proximity over the deterministic background.
    # Lazy import: propagate needs scipy, which the running api image may not carry
    # until its next rebuild. Missing scipy degrades lift to 0 with a labelled
    # fallback band rather than taking the app down.
    bg: dict[str, float] = {}
    try:
        from .propagate import PropagationGraph
        bg = PropagationGraph.get(db).background()
    except ImportError:  # pragma: no cover - container without scipy
        import logging
        logging.getLogger("provenance.ranking").warning(
            "scipy unavailable; lift degraded to 0 for this request")

    def _lift(wid: str, v: float) -> float:
        return math.log(v + LIFT_EPS) - lift_gamma * math.log(bg.get(wid, 0.0) + LIFT_EPS)

    lift_unc: dict[str, Uncertainty] = {}
    if bg and loo:
        lift_full: dict[str, float] = {}
        lift_loo: dict[str, dict[str, float]] = {}
        for node, val in composed.items():
            w = node_to_work_id(node)
            if w:
                lift_full[node] = _lift(w, val)
        for unit, variant in loo.items():
            tv: dict[str, float] = {}
            for node, val in variant.items():
                w = node_to_work_id(node)
                if w:
                    tv[node] = _lift(w, val)
            lift_loo[unit] = tv
        # The denominator is profile-independent, so leave-one-out cannot move it:
        # this band carries numerator uncertainty only (disclosed in the UI copy).
        lift_unc = leave_one_out_uncertainty(
            lift_loo, lift_full, n_seeds=seeds, clamp_nonneg=False)

    rows: list[tuple[str, float, Uncertainty, float, Uncertainty]] = []
    for node, val in composed.items():
        wid = node_to_work_id(node)
        if not wid or (exclude_trusted and wid in trusted) or wid in stub_ids:
            continue
        lv = _lift(wid, val) if bg else 0.0
        lu = lift_unc.get(node, Uncertainty(
            abs(lv) * 0.5, lv - abs(lv) * 0.98, lv + abs(lv) * 0.98, 0,
            "proportional_fallback", max(seeds, 1)))
        # A zero-stderr default would be graded `tight` by the UI -- maximum apparent
        # precision from no information at all. Label it for what it is.
        rows.append((wid, val, unc.get(
            node, Uncertainty(0, 0, 0, 0, "proportional_fallback", 1)), lv, lu))
    rows.sort(key=lambda r: -r[1])
    assign_tie_groups(rows)

    total = len(rows)
    page = rows[offset:offset + limit]
    items = [RankedItem(wid, val, u, offset + i + 1, lift=lv, lift_uncertainty=lu)
             for i, (wid, val, u, lv, lu) in enumerate(page)]
    return items, total, seeds, (time.time() - t0) * 1000.0


def _leave_one_out(
    db: Session, profile: Profile, weights: dict[str, float] | None, fetch: int
) -> dict[str, dict[str, dict[str, float]]]:
    """Re-rank with each UNIT removed (leave-one-upload-out: an upload's whole
    seed set is one jackknife unit, spec B1), using a scratch ego so the user's
    own ego is never mutated.

    Returns RAW per-context scores per removed unit (not composed), so the cache
    can re-compose them under whatever context weights the user later chooses.

    For unit counts larger than LOO_MAX_REPLICATES, a deterministic evenly-spaced
    subsample of units is left out rather than every unit in turn. Evenly spaced
    over the sorted unit labels: arbitrary with respect to score (so it does not
    bias the spread toward strong or weak seeds) but stable across calls, which
    matters because the result is cached and a wobbling subsample would make the
    error bars jitter between page views for an unchanged trust set.
    """
    mr = _mr(db)
    units = trust_units(db, profile)
    rows = [t for _label, members in units for t in members]
    n_units = len(units)
    if n_units > LOO_MAX_REPLICATES:
        step = n_units / LOO_MAX_REPLICATES
        picks = [units[min(int(i * step), n_units - 1)]
                 for i in range(LOO_MAX_REPLICATES)]
    else:
        picks = units
    out: dict[str, dict[str, dict[str, float]]] = {}
    scratch = f"Uloo_{profile.id}"
    for label, members in picks:
        skip_ids = {t.work_id for t in members}
        # try/finally, because a client timeout or engine error mid-replicate used to
        # abandon the scratch edges in the engine. They are harmless (nothing uses
        # Uloo_* as an ego) but they accumulate, and a stale scratch ego perturbs
        # nothing only by luck. Always tear down what this iteration added.
        added: list[str] = []
        try:
            for t in rows:
                if t.work_id in skip_ids:
                    continue
                node = work_node(t.work_id)
                mr.put_edge(scratch, node,
                            config.TRUST_STRENGTH_SCALE.get(t.strength, 0.7),
                            config.AGGREGATE)
                added.append(node)
            out[label] = _context_scores(mr, scratch, fetch)
        finally:
            for node in added:
                try:
                    mr.delete_edge(scratch, node, config.AGGREGATE)
                except Exception:  # noqa: BLE001 - teardown must not mask the real error
                    pass
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
