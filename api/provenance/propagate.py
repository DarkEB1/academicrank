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
    positions after each step is the cheap deterministic analogue at the source;
    `scripts/validate_propagate.py` holds it to median Spearman >= 0.90 against the
    engine before anything user-facing may consume trust scores from here.
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
        self._PT = (sp.diags(1.0 / rowsum) @ A).T.tocsr()

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
        sv = np.zeros(len(self._nodes))
        seed_rows = []
        total = sum(abs(v) for v in seeds.values()) or 1.0
        for wid, wt in seeds.items():
            i = self._idx.get("U" + wid)
            if i is not None:
                sv[i] = wt / total
                seed_rows.append(i)
        if not seed_rows:
            return {}
        seed_rows = np.array(seed_rows)

        x = sv.copy()
        out = self._theta[0] * x
        for k in range(1, K + 1):
            x = self._PT @ x
            x[seed_rows] = 0.0          # absorb: returning walks are dead
            out = out + self._theta[k] * x
        out[seed_rows] = 0.0            # a seed never scores itself

        rows = self._paper_rows[out[self._paper_rows] != 0.0]
        return {self._nodes[i][1:]: float(out[i]) for i in rows}

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
