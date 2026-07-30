"""Deterministic truncated propagation over the persisted `graph_edges` table.

This is NOT MeritRank (README: "the ranking algorithm is never reimplemented in this
codebase" -- this module is the reported exception, per the v2 spec and the
`RankingBackend` docstring's requirement that any such thing "must be reported as
such"). It exists because the engine cannot supply:

  * a profile-independent background vector (needed for the `lift` field) without a
    40-90s cold start per ego, and
  * exact, sampling-noise-free scores for evaluation.

Two design constraints, from the v2 spec (R2), both load-bearing:

  * **Seed-absorbing propagation, not the naive series `sum_k a^k P^k s`.** The
    engine counts each walk's visit to a node AT MOST ONCE (unique-visit counting,
    `core/src/counter.rs`), which suppresses hub re-entry -- measured at 37% of all
    visit mass, 49% of entity-hop mass (experiments doc). The naive series would add
    that mass back and worsen the exact symptom the product is fixing. Zeroing seed
    positions after each step is the cheap analogue at the source, and the
    non-backtracking correction in `_kernel` removes first-order 2-cycle returns.

    **Validation outcome (scripts/validate_propagate.py, 2026-07-30): FAIL against
    the 0.90 gate** -- median Spearman ~0.84 vs the engine, top-100 overlap ~0.85.
    The residual is the engine's revisit-dedup at all lags, which no per-node
    monotone correction can close (Spearman is rank-based). Consequence, honoured
    by this codebase: **trust scores from this module are not user-facing**. The
    engine remains the only trust scorer; the product consumes only `background()`
    (the lift denominator), whose fitness rests on the E5/E6 measurements rather
    than on engine agreement.
  * **No per-edge-type constants inside the row-normalised transition matrix.**
    Entity nodes carry out-edges of exactly one relation type, so a per-type factor
    cancels under row normalisation (the Cause-3 no-op, measured 14,801/14,801).
    Weights come from `graph_edges.weight` as persisted, where per-edge variation is
    real.

Distrust seeds (weight -1.0) flow through linearly: the operator is linear, so a
negative seed contributes the mirror image of a positive one. That is an
approximation of the engine's negative-subsegment semantics (KNOWN_ISSUES #7 already
marks distrust itself as our extension with undefined paper semantics).

The graph is cached per `graph_meta.version` -- one CSR build (~2s, ~50MB) per graph
generation per process, shared across requests under a lock.
"""
from __future__ import annotations

import logging
import threading
import time

import numpy as np
import scipy.sparse as sp
from sqlalchemy import text
from sqlalchemy.orm import Session

from .graphmeta import graph_version

log = logging.getLogger("provenance.propagate")

ALPHA = 0.85   # matches MERITRANK_ALPHA (docker-compose.yml); KNOWN_ISSUES #1
K = 5          # truncation depth; alpha^6 = 0.38 of tail mass discarded, accepted
               # and validated against the engine by scripts/validate_propagate.py

_lock = threading.Lock()
_cache: dict[int, "PropagationGraph"] = {}


class PropagationGraph:
    """graph_edges as a row-normalised CSR transition matrix, papers indexed."""

    def __init__(self, db: Session, version: int):
        t0 = time.time()
        self.version = version
        rows = db.execute(text(
            "SELECT src, dst, weight FROM graph_edges")).all()
        stubs = {r[0] for r in db.execute(text(
            "SELECT id FROM works WHERE is_stub = true")).all()}

        nodes = sorted({r[0] for r in rows} | {r[1] for r in rows})
        self._idx = {n: i for i, n in enumerate(nodes)}
        self._nodes = nodes
        n = len(nodes)

        src = np.fromiter((self._idx[r[0]] for r in rows), np.int32, len(rows))
        dst = np.fromiter((self._idx[r[1]] for r in rows), np.int32, len(rows))
        w = np.fromiter((float(r[2]) for r in rows), np.float64, len(rows))
        A = sp.csr_matrix((w, (src, dst)), shape=(n, n))

        rowsum = np.asarray(A.sum(axis=1)).ravel()
        rowsum[rowsum == 0] = 1.0
        P = (sp.diags(1.0 / rowsum) @ A).tocsr()
        self._PT = P.T.tocsr()
        # 2-step return diagonal: D[p] = sum_m P[p,m]*P[m,p], the probability of
        # bouncing straight back through any intermediary (mutual citations,
        # singleton entities). The engine's unique-visit counting discards exactly
        # this flow (a walk cannot revisit its previous node in the counters), and
        # it is 37% of all visit mass on this graph -- leaving it in is what held
        # the engine correlation at rho ~0.85. Subtracting D * x_{k-1} at each step
        # makes the propagation non-backtracking to first order.
        self._D = np.asarray(P.multiply(P.T).sum(axis=1)).ravel()

        self._is_paper = np.array([m.startswith("UW") for m in nodes])
        self._nonstub_paper = np.array([
            p and (m[1:] not in stubs) for m, p in zip(nodes, self._is_paper)])
        self._paper_rows = np.where(self._is_paper)[0]
        self._theta = ALPHA ** np.arange(K + 1)
        self._background: dict[str, float] | None = None
        log.info("propagation graph v%d: %d nodes, %d edges, %.1fs",
                 version, n, len(rows), time.time() - t0)

    # -- construction ------------------------------------------------------------

    @classmethod
    def get(cls, db: Session) -> "PropagationGraph":
        v = graph_version(db)
        with _lock:
            hit = _cache.get(v)
        if hit is not None:
            return hit
        built = cls(db, v)
        with _lock:
            # keep only the current generation; old graphs are dead weight
            _cache.clear()
            _cache[v] = built
        return built

    # -- scoring -----------------------------------------------------------------

    def score(self, seeds: dict[str, float]) -> dict[str, float]:
        """work_id -> score. Papers only. Seed-absorbing (see module docstring).

        `seeds` maps work_id to signed weight (trust strength scale, or
        DISTRUST_WEIGHT for distrust).
        """
        total = sum(abs(v) for v in seeds.values()) or 1.0
        pos: dict[int, float] = {}
        neg: dict[int, float] = {}
        for wid, wt in seeds.items():
            i = self._idx.get("U" + wid)
            if i is None:
                continue
            (pos if wt >= 0 else neg)[i] = abs(wt) / total
        if not pos and not neg:
            return {}
        # Positive and negative seeds propagate separately on non-negative mass
        # (the non-backtracking clamp is only valid on a non-negative flow), then
        # subtract -- linearity gives distrust as the exact mirror image.
        out = self._kernel(pos)
        if neg:
            out = out - self._kernel(neg)
        seed_rows = np.array(list(pos) + list(neg))
        out[seed_rows] = 0.0            # a seed never scores itself

        rows = self._paper_rows[out[self._paper_rows] != 0.0]
        return {self._nodes[i][1:]: float(out[i]) for i in rows}

    def _kernel(self, seed_mass: dict[int, float]) -> np.ndarray:
        """Seed-absorbing, non-backtracking truncated diffusion of non-negative
        mass. See module docstring for why both properties exist."""
        sv = np.zeros(len(self._nodes))
        if not seed_mass:
            return sv
        for i, m in seed_mass.items():
            sv[i] = m
        seed_rows = np.array(list(seed_mass))
        x_prev = np.zeros_like(sv)
        x = sv.copy()
        out = self._theta[0] * x
        for k in range(1, K + 1):
            x_next = self._PT @ x - self._D * x_prev   # non-backtracking correction
            np.maximum(x_next, 0.0, out=x_next)        # numerical guard
            x_next[seed_rows] = 0.0     # absorb: walks returning to a seed are dead
            out = out + self._theta[k] * x_next
            x_prev, x = x, x_next
        return out

    def background(self) -> dict[str, float]:
        """Propagation from uniform-over-non-stub-papers: the denominator of `lift`.
        Profile-independent, cached for the life of this graph generation. NOT
        seed-absorbing -- with every non-stub paper a seed, absorption would zero
        the whole vector; the background is the plain diffusion."""
        if self._background is not None:
            return self._background
        sv = np.zeros(len(self._nodes))
        ns = np.where(self._nonstub_paper)[0]
        sv[ns] = 1.0 / len(ns)
        # PLAIN diffusion, deliberately -- no absorption (with every non-stub paper
        # a seed it would zero the vector) and no non-backtracking correction: the
        # measured evidence for the lift operating point (gamma=0.5, experiments
        # E5/E6) was produced with exactly this denominator, and the denominator
        # must match its evidence, not score()'s engine-approximation choices.
        x = sv.copy()
        out = self._theta[0] * x
        for k in range(1, K + 1):
            x = self._PT @ x
            out = out + self._theta[k] * x
        self._background = {
            self._nodes[i][1:]: float(out[i])
            for i in self._paper_rows[out[self._paper_rows] > 0.0]
        }
        return self._background
