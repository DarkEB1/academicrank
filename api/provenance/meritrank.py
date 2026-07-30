"""Adapter over the MeritRank pgmer2 SQL surface.

This module is deliberately thin: the algorithm lives in the Rust engine and is not
reimplemented anywhere in this codebase. Everything here is either (a) a typed wrapper
over an `mr_*` SQL function, or (b) something the engine genuinely does not provide
(uncertainty, path reconstruction), clearly marked as ours.

Engine constraints that shape this file (all verified, see DECISIONS.md D1):
  * node kind comes from the first character of the node name;
  * only (User,User), (NonUser,User), (User,NonUser) edges are accepted -- anything
    else is silently skipped by the service;
  * only User nodes may be an ego;
  * User->User edges are replicated into every context regardless of the context
    declared on the edge.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Iterable, Protocol, Sequence

from sqlalchemy import text
from sqlalchemy.engine import Connection

from . import config


@dataclass(frozen=True)
class Edge:
    src: str
    dst: str
    weight: float
    context: str
    relation: str = ""


@dataclass
class Score:
    node: str
    value: float


@dataclass
class Uncertainty:
    stderr: float
    ci_low: float
    ci_high: float
    tie_group: int
    method: str
    n_samples: int


class RankingBackend(Protocol):
    """Kept as an explicit seam. If the Rust stack ever becomes unavailable, a
    NetworkX personalised-PageRank implementation can satisfy this protocol -- but it
    would NOT be MeritRank and must be reported as such."""

    def bulk_load(self, edges: Sequence[Edge]) -> None: ...
    def scores(self, ego: str, context: str = "", limit: int = 100, offset: int = 0) -> list[Score]: ...
    def node_score(self, ego: str, target: str, context: str = "") -> float: ...


class MeritRank:
    """Thin typed wrapper over the pgmer2 functions."""

    def __init__(self, conn: Connection):
        self.conn = conn

    # -- health ----------------------------------------------------------------
    def health(self) -> tuple[bool, str]:
        """`mr_service()` returns a compile-time constant and never touches the
        network (DECISIONS.md D1.1), so it proves nothing. `mr_create_context` is the
        cheapest call that actually round-trips to the service."""
        try:
            self.conn.execute(text("SELECT mr_create_context(:c)"), {"c": "healthcheck"})
            v = self.conn.execute(text("SELECT mr_connector()")).scalar_one()
            return True, str(v)
        except Exception as e:  # noqa: BLE001 - surfaced to /health verbatim
            return False, str(e)

    def version(self) -> str:
        return str(self.conn.execute(text("SELECT mr_connector()")).scalar_one())

    # -- writes ----------------------------------------------------------------
    def create_context(self, context: str) -> None:
        self.conn.execute(text("SELECT mr_create_context(:c)"), {"c": context})

    def bulk_load(self, edges: Sequence[Edge], timeout_msec: int = 600_000) -> None:
        """One call. Never loop mr_put_edge -- the service clears walks and defers
        computation for the bulk path, and walks are then built lazily per ego on
        first read.

        magnitude is pinned to 0 for every edge: it is a VSIDS exponential bump
        exponent (weight * VSIDS_BUMP**magnitude), not a count. Pinning it to 0 keeps
        the weight we send equal to the weight the engine stores. See DECISIONS.md D1.7.
        """
        if not edges:
            return
        self.conn.execute(
            text(
                # NB: `:param::type` is ambiguous to SQLAlchemy's text() bind parser
                # (it eats the parameter). CAST(...) is unambiguous.
                "SELECT mr_bulk_load_edges("
                " CAST(:src AS text[]), CAST(:dst AS text[]), CAST(:w AS float8[]),"
                " CAST(:mag AS bigint[]), CAST(:ctx AS text[]), :t)"
            ),
            {
                "src": [e.src for e in edges],
                "dst": [e.dst for e in edges],
                "w": [float(e.weight) for e in edges],
                "mag": [0] * len(edges),
                "ctx": [e.context for e in edges],
                "t": timeout_msec,
            },
        )

    def put_edge(self, src: str, dst: str, weight: float, context: str = "") -> None:
        self.conn.execute(
            text("SELECT mr_put_edge(:s, :d, :w, :c)"),
            {"s": src, "d": dst, "w": float(weight), "c": context},
        )

    def delete_edge(self, src: str, dst: str, context: str = "") -> None:
        self.conn.execute(
            text("SELECT mr_delete_edge(:s, :d, :c)"), {"s": src, "d": dst, "c": context}
        )

    def delete_node(self, node: str, context: str = "") -> None:
        self.conn.execute(text("SELECT mr_delete_node(:n, :c)"), {"n": node, "c": context})

    def sync(self, timeout_msec: int = 600_000) -> None:
        self.conn.execute(text("SELECT mr_sync(:t)"), {"t": timeout_msec})

    # -- reads -----------------------------------------------------------------
    def scores(
        self,
        ego: str,
        context: str = "",
        limit: int = 100,
        offset: int = 0,
        kind: str = "",
        gt: float | None = None,
    ) -> list[Score]:
        rows = self.conn.execute(
            text(
                "SELECT dst, score_value_of_dst FROM mr_scores("
                " src => :ego, context => :ctx, kind => :kind,"
                " gt => :gt, index => :off, count => :lim)"
            ),
            {"ego": ego, "ctx": context, "kind": kind, "gt": gt, "off": offset, "lim": limit},
        ).all()
        return [Score(node=r[0], value=float(r[1])) for r in rows]

    def node_score(self, ego: str, target: str, context: str = "") -> float:
        row = self.conn.execute(
            text(
                "SELECT score_value_of_dst FROM mr_node_score("
                " src => :ego, dst => :dst, context => :ctx)"
            ),
            {"ego": ego, "dst": target, "ctx": context},
        ).first()
        return float(row[0]) if row and row[0] is not None else 0.0

    def graph(self, ego: str, focus: str, context: str = "", limit: int = 256) -> list[tuple]:
        """Edges around `focus` as seen from `ego`. Note the engine filters out steps
        whose source is a non-User node, so entity hops (author/topic/...) do not
        appear here -- which is exactly why /explain reconstructs paths from our own
        graph_edges table instead of relying on this."""
        return self.conn.execute(
            text(
                "SELECT src, dst, weight, score_value_of_dst FROM mr_graph("
                " ego => :ego, focus => :focus, context => :ctx, count => :lim)"
            ),
            {"ego": ego, "focus": focus, "ctx": context, "lim": limit},
        ).all()

    def neighbors(
        self, ego: str, focus: str, direction: int = 0, context: str = "", limit: int = 128
    ) -> list[tuple]:
        return self.conn.execute(
            text(
                "SELECT src, dst, score_value_of_dst FROM mr_neighbors("
                " ego => :ego, focus => :focus, direction => :dir,"
                " context => :ctx, count => :lim)"
            ),
            {"ego": ego, "focus": focus, "dir": direction, "ctx": context, "lim": limit},
        ).all()

    def nodelist(self, context: str = "") -> list[str]:
        return [r[0] for r in self.conn.execute(
            text("SELECT node FROM mr_nodelist(:c)"), {"c": context}).all()]

    def edge_count(self, context: str = "") -> int:
        return int(self.conn.execute(
            text("SELECT count(*) FROM mr_edgelist(:c)"), {"c": context}).scalar_one())


# ---------------------------------------------------------------------------
# Uncertainty. This is OURS, not the engine's.
# ---------------------------------------------------------------------------
# MeritRank scores are Monte Carlo estimates, so a confident total ordering is a lie:
# ranks 7-12 are usually noise. The service exposes no per-call walk count and no
# sampling seed, so we cannot cheaply take repeated independent estimates of the same
# ego. Leave-one-out over the trust set is used instead, and it is arguably the more
# useful measure anyway: it reports how much the ranking depends on any single trust
# decision the user made.

def leave_one_out_uncertainty(
    per_seed_scores: dict[str, dict[str, float]],
    full_scores: dict[str, float],
    n_seeds: int | None = None,
) -> dict[str, Uncertainty]:
    """`per_seed_scores[seed_removed][node] = score`. Returns per-node uncertainty.

    `n_seeds` is the true size of the trust set, which may exceed the number of
    replicates when they have been subsampled (see ranking.LOO_MAX_REPLICATES). The
    jackknife inflation factor depends on the trust-set size, not on how many
    delete-one replicates we could afford to draw: each replicate estimates the same
    per-seed spread either way. Defaults to the replicate count, which is correct when
    every seed was left out in turn.
    """
    out: dict[str, Uncertainty] = {}
    variants = list(per_seed_scores.values())
    m = len(variants)
    n = n_seeds if n_seeds is not None else m
    for node, base in full_scores.items():
        samples = [v.get(node, 0.0) for v in variants]
        if m >= 2:
            sd = statistics.pstdev(samples)
            # jackknife scaling: LOO replicates understate spread by ~sqrt(n-1)
            stderr = sd * math.sqrt(max(n - 1, 1))
        else:
            stderr = abs(base) * 0.5  # single seed: the ranking is entirely that seed
        out[node] = Uncertainty(
            stderr=stderr,
            ci_low=max(0.0, base - 1.96 * stderr),
            ci_high=base + 1.96 * stderr,
            tie_group=0,
            method="leave_one_out",
            n_samples=max(m, 1),
        )
    return out


def assign_tie_groups(ordered: list[tuple[str, float, Uncertainty]]) -> None:
    """Group adjacent items that are not separable given their spread. Mutates in place.

    Pairwise rather than anchored to the head of the group: leave-one-out spreads on
    small trust sets are large enough that an anchored test collapses the entire
    ranking into one tie group, which is technically defensible and completely useless
    to read. Two adjacent items are called tied when their gap is smaller than their
    mean standard error.
    """
    group = 0
    for i, (_node, value, unc) in enumerate(ordered):
        if i > 0:
            prev_value, prev_unc = ordered[i - 1][1], ordered[i - 1][2]
            tol = (unc.stderr + prev_unc.stderr) / 2.0
            if (prev_value - value) > tol:
                group += 1
        unc.tie_group = group
