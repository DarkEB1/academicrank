"""Pure unit tests for RRF fusion. No stack required."""
from provenance.searchrank import RRF_K, Fused, fuse, merit_ranks


def test_merit_ranks_orders_desc_and_breaks_ties_by_id():
    ranks = merit_ranks({"W3": 0.5, "W1": 0.9, "W2": 0.5})
    assert ranks == {"W1": 1, "W2": 2, "W3": 3}  # tie 0.5: W2 before W3 by id


def test_fuse_rrf_arithmetic():
    # text order: A(1), B(2); merit: B=1, A=2
    out = fuse(["WA", "WB"], {"WB": 1, "WA": 2})
    by_id = {f.work_id: f for f in out}
    assert by_id["WA"].rrf == 1 / (RRF_K + 1) + 1 / (RRF_K + 2)
    assert by_id["WB"].rrf == 1 / (RRF_K + 2) + 1 / (RRF_K + 1)
    # equal RRF -> tiebreak by merit rank: B (merit 1) first
    assert [f.work_id for f in out] == ["WB", "WA"]


def test_fuse_missing_merit_is_last_place():
    out = fuse(["WA", "WB"], {"WA": 1})  # WB unknown to merit table of size 1
    wb = next(f for f in out if f.work_id == "WB")
    assert wb.merit_rank == 2  # len(merit_rank) + 1
    assert out[0].work_id == "WA"


def test_fuse_good_text_match_beats_weak_match_near_trust():
    # 3rd-best text match with top merit outranks 40th-best text match with merit 2.
    text_ids = [f"W{n:03d}" for n in range(1, 51)]
    out = fuse(text_ids, {"W003": 1, "W040": 2})
    pos = {f.work_id: i for i, f in enumerate(out)}
    assert pos["W003"] < pos["W040"]


def test_fuse_is_deterministic():
    text_ids = [f"W{n:03d}" for n in range(1, 501)]
    merit = {f"W{n:03d}": 1.0 / n for n in range(500, 0, -2)}
    a = fuse(text_ids, merit_ranks(merit))
    b = fuse(list(text_ids), merit_ranks(dict(merit)))
    assert a == b
    assert all(isinstance(f, Fused) for f in a)
