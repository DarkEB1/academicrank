"""compose() as a weighted mean (R4). Pure unit tests, no DB, no engine.

Why the change (measured, experiments doc E5): the old marginal sum
`w_base*b + sum_c w_c*(s_c - b)` amplified independent Monte Carlo noise ~13x
(each context is a separate walk set) and its max(0,.) clamp collapsed ~640
citation-reachable papers into one tie block at exactly 0. The weighted mean
divides variance by the context count instead. It does NOT change hubness --
every combiner measured within noise of the others on popularity percentile --
and is justified on variance and honesty grounds only.
"""
from __future__ import annotations

import pytest

from provenance.ranking import compose


def test_weighted_mean_over_present_contexts():
    per_ctx = {
        "citation": {"UW1": 0.5, "UW2": 0.010},
        "author": {"UW1": 0.4, "UW9": 0.1},
        "topic": {"UW1": 0.6, "UW2": 0.012},
        "venue": {"UW1": 0.5, "UW9": 0.1},
        "institution": {"UW1": 0.5, "UW9": 0.1},
    }
    out = compose(per_ctx)
    # all five contexts present for UW1
    assert out["UW1"] == pytest.approx((0.5 + 0.4 + 0.6 + 0.5 + 0.5) / 5)
    # only citation+topic saw UW2: mean over those two, no negative-marginal
    # penalty and no clamp to zero
    assert out["UW2"] == pytest.approx((0.010 + 0.012) / 2)
    assert out["UW2"] > 0.0


def test_zero_weight_drops_context_from_the_mean():
    # Every real window includes the ego node, so a live context always has >1
    # entries; a window holding ONLY the ego is degenerate and is skipped.
    per_ctx = {
        "citation": {"Uego": 0.4, "UW1": 0.5},
        "author": {"Uego": 0.1, "UW1": 0.9},
        "topic": {"Uego": 0.4, "UW1": 0.5, "UW2": 0.1},
        "venue": {},
        "institution": {},
    }
    w = {"citation": 1.0, "author": 0.0, "topic": 1.0, "venue": 1.0,
         "institution": 1.0}
    out = compose(per_ctx, w)
    # author zeroed: UW1 is the mean of citation and topic only
    assert out["UW1"] == pytest.approx((0.5 + 0.5) / 2)


def test_weights_reorder():
    per_ctx = {
        "citation": {"UW1": 0.5, "UW2": 0.5},
        "author": {"UW1": 0.9, "UW2": 0.1},
        "topic": {}, "venue": {}, "institution": {},
    }
    even = compose(per_ctx, {"citation": 1.0, "author": 1.0, "topic": 1.0,
                             "venue": 1.0, "institution": 1.0})
    author_heavy = compose(per_ctx, {"citation": 0.1, "author": 5.0,
                                     "topic": 1.0, "venue": 1.0,
                                     "institution": 1.0})
    assert even["UW1"] > even["UW2"]
    assert (author_heavy["UW1"] - author_heavy["UW2"]) > (
        even["UW1"] - even["UW2"])  # the slider genuinely moves the contrast


def test_never_negative_and_no_zero_clamp_block():
    per_ctx = {
        "citation": {"Uego": 0.9, "UW1": 0.001},
        "author": {"Uego": 0.9, "UW1": 0.0005},
        "topic": {"Uego": 0.9, "UW1": 0.0002},
        "venue": {}, "institution": {},
    }
    out = compose(per_ctx)
    assert out["UW1"] > 0.0
