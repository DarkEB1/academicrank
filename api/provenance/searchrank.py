"""Reciprocal Rank Fusion for merit-ranked search.

Spec: docs/superpowers/specs/2026-07-30-merit-ranked-search-design.md.
RRF decides *order* only; the fused value is never a displayed score, so the
"no bare scores" rule is untouched -- displayed numbers remain MeritRank
values with their own uncertainty.
"""
from __future__ import annotations

from dataclasses import dataclass

# Candidate set: top-K text matches feed the fusion. Everything past K is
# invisible to ranked search and the response disclaimer says so.
FETCH_K = 500
# The standard RRF constant (Cormack et al. 2009).
RRF_K = 60


@dataclass(frozen=True)
class Fused:
    work_id: str
    relevance_rank: int  # 1-based position in the text-relevance order
    merit_rank: int      # 1-based position in the merit order; absent = last place
    rrf: float


def merit_ranks(values: dict[str, float]) -> dict[str, int]:
    """1-based ordinal ranks by descending value, ties broken by id.

    Ordinal (not dense) ranks, id-tiebroken, so the fusion is deterministic
    for a given score table regardless of dict insertion order.
    """
    ordered = sorted(values.items(), key=lambda kv: (-kv[1], kv[0]))
    return {wid: n for n, (wid, _v) in enumerate(ordered, start=1)}


def fuse(text_ids: list[str], merit_rank: dict[str, int], k: int = RRF_K) -> list[Fused]:
    last = len(merit_rank) + 1
    out: list[Fused] = []
    for n, wid in enumerate(text_ids, start=1):
        mr = merit_rank.get(wid, last)
        out.append(Fused(wid, n, mr, 1.0 / (k + n) + 1.0 / (k + mr)))
    out.sort(key=lambda f: (-f.rrf, f.merit_rank, f.relevance_rank, f.work_id))
    return out
