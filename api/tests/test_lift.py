"""Lift (R3): fame-normalised proximity as a displayed, sortable field.

lift = log(trust + eps) - gamma * log(background + eps), gamma default 0.5.
The background is the deterministic uniform diffusion (propagate.background());
trust stays the engine's score. Lift never redefines `trust`.
"""
from __future__ import annotations

from fastapi.testclient import TestClient


def _rankings(client, seeded, **params):
    r = client.get(f"/api/profiles/{seeded['id']}/rankings",
                   params={"limit": 25, **params}, headers=seeded["auth"])
    assert r.status_code == 200, r.text
    return r.json()


def test_lift_present_and_finite(client: TestClient, seeded: dict) -> None:
    items = _rankings(client, seeded)["items"]
    assert items
    for it in items:
        assert "lift" in it and isinstance(it["lift"], float)
        assert it["lift"] == it["lift"]  # not NaN
        u = it["lift_uncertainty"]
        assert u["stderr"] >= 0.0
        assert u["ci_low"] <= u["ci_high"]


def test_lift_ci_may_go_negative(client: TestClient, seeded: dict) -> None:
    """A below-background paper has negative lift; the interval must be allowed to
    cross zero rather than being clamped at it (the ci_low=max(0,.) clamp is a
    trust-scale convention and is wrong on a log scale)."""
    items = _rankings(client, seeded, limit=100)["items"]
    negs = [i for i in items if i["lift"] < 0.0]
    if not negs:  # corpus-dependent; only assert the invariant when observable
        return
    assert any(i["lift_uncertainty"]["ci_low"] < 0.0 for i in negs)


def test_sort_lift_is_valid_and_ordered(client: TestClient, seeded: dict) -> None:
    body = _rankings(client, seeded, sort="lift")
    lifts = [i["lift"] for i in body["items"]]
    assert lifts == sorted(lifts, reverse=True)
    ranks = [i["rank"] for i in body["items"]]
    assert ranks == list(range(1, len(ranks) + 1))


def test_gamma_zero_matches_trust_order(client: TestClient, seeded: dict) -> None:
    """gamma=0 -> lift = log(trust+eps): a monotone transform, so sorting by lift
    must reproduce the trust ordering."""
    r = client.post(f"/api/profiles/{seeded['id']}/params",
                    json={"lift_gamma": 0.0}, headers=seeded["auth"])
    assert r.status_code == 200, r.text
    try:
        by_trust = [i["id"] for i in _rankings(client, seeded)["items"]]
        by_lift = [i["id"] for i in _rankings(client, seeded, sort="lift")["items"]]
        assert by_trust == by_lift
    finally:
        r = client.post(f"/api/profiles/{seeded['id']}/params",
                        json={"lift_gamma": 0.5}, headers=seeded["auth"])
        assert r.status_code == 200, r.text


def test_gamma_out_of_range_rejected(client: TestClient, seeded: dict) -> None:
    r = client.post(f"/api/profiles/{seeded['id']}/params",
                    json={"lift_gamma": 1.5}, headers=seeded["auth"])
    assert r.status_code == 422
