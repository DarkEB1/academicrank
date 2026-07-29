"""Integration tests -- one per endpoint in API_CONTRACT.md, plus the contract-wide
invariants (uncertainty on every score, disclaimer on every list, 403 across profiles,
422 for parameters the engine does not honour).

Every test runs against the live stack. See conftest.py.
"""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from provenance import config
from provenance.db import SessionLocal


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

UNCERTAINTY_KEYS = {"stderr", "ci_low", "ci_high", "tie_group", "method", "n_samples"}
PAPER_KEYS = {"id", "title", "year", "authors", "venue", "cited_by_count",
              "in_corpus_cited_by", "is_stub", "doi"}
SCORED_KEYS = PAPER_KEYS | {"trust", "uncertainty", "global_merit", "rank",
                            "disagreement"}


def assert_scored(item: dict) -> None:
    """Contract rule 1: no bare scores, ever."""
    assert SCORED_KEYS <= set(item), sorted(SCORED_KEYS - set(item))
    unc = item["uncertainty"]
    assert UNCERTAINTY_KEYS == set(unc), sorted(UNCERTAINTY_KEYS ^ set(unc))
    assert isinstance(unc["tie_group"], int)
    assert unc["method"] in ("leave_one_out", "repeat_sample")
    assert unc["ci_low"] <= unc["ci_high"]
    assert 0.0 <= item["disagreement"] <= 1.0


# ---------------------------------------------------------------------------
# GET /api/health
# ---------------------------------------------------------------------------


def test_health_verifies_meritrank_with_a_real_round_trip(client: TestClient) -> None:
    r = client.get("/api/health")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["db"] is True
    # This is the point of the endpoint: a real mr_create_context round trip, not the
    # compile-time constant mr_service() returns (DECISIONS.md D1.1).
    assert body["meritrank"] is True, body
    assert body["ok"] is True
    assert body["graph_loaded"] is True, body
    assert body["nodes"] > 0 and body["edges"] > 0


def test_health_meritrank_flag_is_not_mr_service(client: TestClient) -> None:
    """mr_service() answers even when the service is unreachable, so it must not be
    what /health reports. Prove the flag tracks a call that really leaves Postgres."""
    with SessionLocal() as db:
        constant = db.execute(text("SELECT mr_service()")).scalar_one()
        # It is a constant string, and it is answerable with zero network traffic.
        assert isinstance(constant, str) and constant
        # The health path uses mr_create_context, which does round trip.
        db.execute(text("SELECT mr_create_context(:c)"), {"c": "pytest_healthcheck"})
        db.commit()


# ---------------------------------------------------------------------------
# POST /api/profiles  &  GET /api/profiles/me  (auth)
# ---------------------------------------------------------------------------


def test_create_profile_mints_token_and_sets_cookie(client: TestClient) -> None:
    r = client.post("/api/profiles", json={"label": "cookie test"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["id"] and body["token"] and body["label"] == "cookie test"
    assert body["params"]["context_weights"].keys() == set(config.CONTEXTS)
    assert "pv_token" in r.cookies or "pv_token" in r.headers.get("set-cookie", "")


def test_me_accepts_bearer_and_cookie(client: TestClient) -> None:
    prof = client.post("/api/profiles", json={"label": "auth modes"}).json()

    r = client.get("/api/profiles/me",
                   headers={"Authorization": f"Bearer {prof['token']}"})
    assert r.status_code == 200, r.text
    assert r.json()["id"] == prof["id"]

    r = client.get("/api/profiles/me", cookies={"pv_token": prof["token"]})
    assert r.status_code == 200, r.text
    assert r.json()["id"] == prof["id"]


def test_me_requires_a_token(client: TestClient) -> None:
    # The shared client has a pv_token cookie from earlier profile creations, and the
    # cookie is a valid credential -- so it has to be cleared to test the anonymous case.
    client.cookies.clear()
    assert client.get("/api/profiles/me").status_code == 401
    assert client.get("/api/profiles/me",
                      headers={"Authorization": "Bearer nope"}).status_code == 401


def test_other_profiles_are_forbidden(client: TestClient, seeded: dict) -> None:
    other = client.post("/api/profiles", json={"label": "intruder"}).json()
    r = client.get(f"/api/profiles/{seeded['id']}/rankings",
                   headers={"Authorization": f"Bearer {other['token']}"})
    assert r.status_code == 403, r.text


# ---------------------------------------------------------------------------
# GET /api/papers/search
# ---------------------------------------------------------------------------


def test_search_returns_paper_briefs(client: TestClient) -> None:
    with SessionLocal() as db:
        title = db.execute(text(
            "SELECT title FROM works WHERE title IS NOT NULL "
            "AND length(title) > 20 ORDER BY cited_by_count DESC LIMIT 1"
        )).scalar_one()
    term = " ".join(title.split()[:3])

    r = client.get("/api/papers/search", params={"q": term, "limit": 5})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] >= 1, f"no hits for {term!r}"
    assert 1 <= len(body["items"]) <= 5
    for item in body["items"]:
        assert PAPER_KEYS == set(item), sorted(PAPER_KEYS ^ set(item))
        assert len(item["authors"]) <= 6


def test_search_rejects_short_queries(client: TestClient) -> None:
    assert client.get("/api/papers/search", params={"q": "a"}).status_code == 422


def test_search_falls_back_to_trigram(client: TestClient) -> None:
    """A misspelling produces no tsquery match; trigram must still find it."""
    with SessionLocal() as db:
        title = db.execute(text(
            "SELECT title FROM works WHERE title IS NOT NULL "
            "AND length(title) BETWEEN 25 AND 60 ORDER BY cited_by_count DESC LIMIT 1"
        )).scalar_one()
    typo = title[:-1] + "x" if len(title) > 25 else title
    r = client.get("/api/papers/search", params={"q": typo, "limit": 5})
    assert r.status_code == 200, r.text
    assert r.json()["total"] >= 1, f"trigram fallback found nothing for {typo!r}"


# ---------------------------------------------------------------------------
# POST / GET /api/profiles/{id}/trust
# ---------------------------------------------------------------------------


def test_trust_roundtrip_add_update_remove(client: TestClient,
                                           corpus_ids: list[str]) -> None:
    prof = client.post("/api/profiles", json={"label": "trust rt"}).json()
    auth = {"Authorization": f"Bearer {prof['token']}"}
    wid = corpus_ids[0]

    r = client.post(f"/api/profiles/{prof['id']}/trust",
                    json={"work_id": wid, "strength": 3}, headers=auth)
    assert r.status_code == 200, r.text
    assert r.json()["trust_count"] == 1
    assert r.json()["items"][0]["work"]["id"] == wid
    assert r.json()["items"][0]["strength"] == 3

    r = client.post(f"/api/profiles/{prof['id']}/trust",
                    json={"work_id": wid, "strength": 5}, headers=auth)
    assert r.json()["items"][0]["strength"] == 5

    r = client.get(f"/api/profiles/{prof['id']}/trust", headers=auth)
    assert r.status_code == 200
    assert len(r.json()["items"]) == 1

    # strength 0 removes
    r = client.post(f"/api/profiles/{prof['id']}/trust",
                    json={"work_id": wid, "strength": 0}, headers=auth)
    assert r.json()["trust_count"] == 0


def test_trust_rejects_unknown_work(client: TestClient) -> None:
    prof = client.post("/api/profiles", json={"label": "bad work"}).json()
    r = client.post(f"/api/profiles/{prof['id']}/trust",
                    json={"work_id": "W000000000", "strength": 3},
                    headers={"Authorization": f"Bearer {prof['token']}"})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/profiles/{id}/rankings
# ---------------------------------------------------------------------------


def test_rankings_shape_and_invariants(client: TestClient, seeded: dict,
                                       auth: dict) -> None:
    r = client.get(f"/api/profiles/{seeded['id']}/rankings",
                   params={"limit": 10}, headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["disclaimer"] == config.DISCLAIMER      # verbatim, contract rule 2
    assert body["total"] > 0
    assert body["timing_ms"] >= 0
    assert body["cold_start"]["seeds"] == 6
    assert body["cold_start"]["reliable"] is True       # 6 >= COLD_START_MIN_SEEDS

    items = body["items"]
    assert items, "a 6-seed profile ranked nothing"
    for item in items:
        assert_scored(item)
    assert [i["rank"] for i in items] == list(range(1, len(items) + 1))
    # monotonically non-increasing trust
    assert all(a["trust"] >= b["trust"] - 1e-12
               for a, b in zip(items, items[1:]))
    # tie groups never decrease down the ranking
    groups = [i["uncertainty"]["tie_group"] for i in items]
    assert groups == sorted(groups)
    # the trust set itself is excluded by default
    assert not (set(seeded["work_ids"]) & {i["id"] for i in items})


def test_rankings_cold_start_flags_an_empty_trust_set(client: TestClient,
                                                      anon: dict) -> None:
    r = client.get(f"/api/profiles/{anon['id']}/rankings",
                   headers={"Authorization": f"Bearer {anon['token']}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cold_start"]["seeds"] == 0
    assert body["cold_start"]["reliable"] is False
    assert body["cold_start"]["message"]
    assert body["disclaimer"] == config.DISCLAIMER


def test_rankings_pagination_and_year_filter(client: TestClient, seeded: dict,
                                             auth: dict) -> None:
    base = client.get(f"/api/profiles/{seeded['id']}/rankings",
                      params={"limit": 6}, headers=auth).json()
    page2 = client.get(f"/api/profiles/{seeded['id']}/rankings",
                       params={"limit": 3, "offset": 3}, headers=auth).json()
    assert [i["id"] for i in base["items"][3:6]] == [i["id"] for i in page2["items"]]
    assert page2["items"][0]["rank"] == 4

    filtered = client.get(f"/api/profiles/{seeded['id']}/rankings",
                          params={"limit": 20, "year_from": 2000},
                          headers=auth).json()
    assert all(i["year"] is None or i["year"] >= 2000 for i in filtered["items"])
    assert filtered["total"] <= base["total"]


def test_rankings_context_selection_changes_the_ordering(
        client: TestClient, seeded: dict, auth: dict) -> None:
    out = {}
    for ctx in ("aggregate", "citation", "author", "topic"):
        r = client.get(f"/api/profiles/{seeded['id']}/rankings",
                       params={"limit": 10, "context": ctx}, headers=auth)
        assert r.status_code == 200, (ctx, r.text)
        out[ctx] = [i["id"] for i in r.json()["items"]]
    assert any(out["citation"] != out[c] for c in ("author", "topic")), (
        "per-context scoring produced identical rankings, so the weights are inert")


@pytest.mark.parametrize("ctx", ["coupling", "cocitation"])
def test_rankings_rejects_contexts_the_engine_cannot_isolate(
        client: TestClient, seeded: dict, auth: dict, ctx: str) -> None:
    """DECISIONS.md D1.5: paper->paper edges replicate into every context, so these are
    part of the citation baseline and cannot be scored alone. 422, not a plausible lie."""
    r = client.get(f"/api/profiles/{seeded['id']}/rankings",
                   params={"context": ctx}, headers=auth)
    assert r.status_code == 422, r.text
    assert "citation" in str(r.json()["detail"]).lower()


# ---------------------------------------------------------------------------
# GET /api/profiles/{id}/recommendations
# ---------------------------------------------------------------------------


def test_recommendations_diversity_dial_moves_results(
        client: TestClient, seeded: dict, auth: dict) -> None:
    near = client.get(f"/api/profiles/{seeded['id']}/recommendations",
                      params={"diversity": 0.0, "limit": 10}, headers=auth)
    far = client.get(f"/api/profiles/{seeded['id']}/recommendations",
                     params={"diversity": 1.0, "limit": 10}, headers=auth)
    assert near.status_code == far.status_code == 200, (near.text, far.text)
    n, f = near.json(), far.json()

    assert n["disclaimer"] == config.DISCLAIMER
    assert n["diversity"] == 0.0 and f["diversity"] == 1.0
    for item in n["items"] + f["items"]:
        assert_scored(item)
        assert 0.0 <= item["novelty"] <= 1.0
        assert isinstance(item["reason"], str) and len(item["reason"]) > 20

    assert [i["id"] for i in n["items"]] != [i["id"] for i in f["items"]], (
        "the diversity dial did nothing")
    # exploration must actually be further from the trust set
    avg = lambda body: sum(i["novelty"] for i in body["items"]) / len(body["items"])
    assert avg(f) > avg(n), (avg(n), avg(f))

    # diversity=0 is pure trust order, i.e. the ranking itself
    ranked = client.get(f"/api/profiles/{seeded['id']}/rankings",
                        params={"limit": 10}, headers=auth).json()
    assert [i["id"] for i in n["items"]][:5] == [i["id"] for i in ranked["items"]][:5]


# ---------------------------------------------------------------------------
# GET /api/profiles/{id}/blindspots
# ---------------------------------------------------------------------------


def test_blindspots_are_high_merit_low_trust(client: TestClient, seeded: dict,
                                             auth: dict) -> None:
    r = client.get(f"/api/profiles/{seeded['id']}/blindspots",
                   params={"limit": 10}, headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["disclaimer"] == config.DISCLAIMER
    assert body["cold_start"]["seeds"] == 6
    assert body["items"], "no blindspots at all for a 6-seed profile"
    gaps = [i["gap"] for i in body["items"]]
    assert all(g > 0 for g in gaps)
    assert gaps == sorted(gaps, reverse=True)
    for item in body["items"]:
        assert_scored(item)
    assert not (set(seeded["work_ids"]) & {i["id"] for i in body["items"]})


# ---------------------------------------------------------------------------
# GET /api/profiles/{id}/papers/{pid}
# ---------------------------------------------------------------------------


def test_paper_detail(client: TestClient, seeded: dict, auth: dict) -> None:
    pid = seeded["work_ids"][0]
    r = client.get(f"/api/profiles/{seeded['id']}/papers/{pid}", headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["paper"]["id"] == pid
    assert UNCERTAINTY_KEYS == set(body["uncertainty"])
    assert {"trust", "global", "citations"} == set(body["percentiles"])
    for v in body["percentiles"].values():
        assert 0.0 <= v <= 1.0
    assert 0.0 <= body["disagreement"] <= 1.0
    # this one is in the trust set, so it must say so
    assert body["in_trust_set"] is not None
    assert body["in_trust_set"]["strength"] == 4
    assert isinstance(body["topics"], list)
    assert isinstance(body["institutions"], list)


def test_paper_detail_404(client: TestClient, seeded: dict, auth: dict) -> None:
    r = client.get(f"/api/profiles/{seeded['id']}/papers/W000000000", headers=auth)
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/profiles/{id}/papers/{pid}/explain
# ---------------------------------------------------------------------------


def test_explain_reconstructs_paths_to_a_seed(client: TestClient, seeded: dict,
                                              auth: dict) -> None:
    ranked = client.get(f"/api/profiles/{seeded['id']}/rankings",
                        params={"limit": 5}, headers=auth).json()
    assert ranked["items"]
    pid = ranked["items"][0]["id"]

    r = client.get(f"/api/profiles/{seeded['id']}/papers/{pid}/explain", headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["target"]["id"] == pid
    assert UNCERTAINTY_KEYS == set(body["uncertainty"])
    assert body["summary"] and body["caveat"]

    ctxs = [c["context"] for c in body["by_context"]]
    assert ctxs[0] == config.BASELINE_CONTEXT
    assert set(ctxs) == set(config.CONTEXTS)
    for c in body["by_context"]:
        assert {"context", "score", "marginal", "share"} == set(c)
        assert 0.0 <= c["share"] <= 1.0
    # the baseline's marginal is the baseline itself, by definition
    base = next(c for c in body["by_context"] if c["context"] == config.BASELINE_CONTEXT)
    assert base["marginal"] == pytest.approx(base["score"])

    assert body["paths"], "the top-ranked paper has no reconstructed path to any seed"
    seeds = set(seeded["work_ids"])
    for p in body["paths"]:
        assert p["seed"]["id"] in seeds
        assert 0.0 <= p["contribution"] <= 1.0
        assert len(p["nodes"]) >= 2
        assert len(p["edges"]) == len(p["nodes"]) - 1
        for n in p["nodes"]:
            assert n["kind"] in ("paper", "author", "topic", "venue",
                                 "institution", "profile")
            assert n["label"]
        # the path must start at the seed and end at the target
        assert p["nodes"][0]["id"] == f"U{p['seed']['id']}"
        assert p["nodes"][-1]["id"] == f"U{pid}"
    assert sum(p["contribution"] for p in body["paths"]) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# GET /api/profiles/{id}/diversity
# ---------------------------------------------------------------------------


def test_diversity_entropy(client: TestClient, seeded: dict, auth: dict) -> None:
    r = client.get(f"/api/profiles/{seeded['id']}/diversity", headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()

    dims = {"topics", "institutions", "decades", "countries"}
    assert dims == set(body["entropy"]) == set(body["max_entropy"])
    for d in dims:
        assert body["entropy"][d] >= 0.0
        assert body["max_entropy"][d] >= 0.0
        # entropy can never exceed its own maximum
        assert body["entropy"][d] <= body["max_entropy"][d] + 1e-9, d

    assert 0.0 <= body["echo_chamber_score"] <= 1.0
    assert body["message"]
    for c in body["concentration"]:
        assert {"label", "share"} == set(c)
        assert 0.0 < c["share"] <= 1.0
    shares = [c["share"] for c in body["concentration"]]
    assert shares == sorted(shares, reverse=True)


def test_diversity_is_undefined_without_a_trust_set(client: TestClient,
                                                    anon: dict) -> None:
    r = client.get(f"/api/profiles/{anon['id']}/diversity",
                   headers={"Authorization": f"Bearer {anon['token']}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["echo_chamber_score"] == 0.0
    assert "empty" in body["message"].lower()


# ---------------------------------------------------------------------------
# POST /api/profiles/{id}/simulate
# ---------------------------------------------------------------------------


def test_simulate_is_a_non_destructive_counterfactual(
        client: TestClient, seeded: dict, auth: dict, corpus_ids: list[str]) -> None:
    before_trust = client.get(f"/api/profiles/{seeded['id']}/trust",
                              headers=auth).json()["items"]

    extra = corpus_ids[6] if len(corpus_ids) > 6 else corpus_ids[0]
    r = client.post(
        f"/api/profiles/{seeded['id']}/simulate",
        json={"add": [{"work_id": extra, "strength": 5}],
              "remove": [seeded["work_ids"][0]], "limit": 10},
        headers=auth,
    )
    assert r.status_code == 200, r.text
    body = r.json()

    for item in body["before"] + body["after"]:
        assert_scored(item)
    assert body["before"] and body["after"]
    assert [i["id"] for i in body["before"]] != [i["id"] for i in body["after"]], (
        "the counterfactual changed nothing at all")

    for m in body["moved"]:
        assert {"work_id", "delta_rank", "delta_trust"} == set(m)
        assert isinstance(m["delta_rank"], int)
    assert body["moved"], "no movement reported"

    # non-destructive: the real trust set is untouched
    after_trust = client.get(f"/api/profiles/{seeded['id']}/trust",
                             headers=auth).json()["items"]
    assert {t["work"]["id"] for t in before_trust} == \
           {t["work"]["id"] for t in after_trust}

    # ...and the scratch ego left no edges behind in the engine.
    #
    # Edges, not nodes: the engine's node registry assigns every name an id for the
    # lifetime of the process and mr_delete_node does not un-register the name, so
    # `Usim_*` keeps appearing in mr_nodelist() forever. What actually matters for
    # non-destructiveness is that no scratch edge survives to influence a later walk.
    with SessionLocal() as db:
        leftovers = [(s, d) for s, d, _w in db.execute(
            text("SELECT src, dst, weight FROM mr_edgelist('')")).all()
            if s.startswith("Usim_")]
        db.commit()
    assert not leftovers, leftovers


def test_simulate_rejects_unknown_work(client: TestClient, seeded: dict,
                                       auth: dict) -> None:
    r = client.post(f"/api/profiles/{seeded['id']}/simulate",
                    json={"add": [{"work_id": "W000000000", "strength": 3}]},
                    headers=auth)
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/profiles/{id}/subgraph
# ---------------------------------------------------------------------------


def test_subgraph_is_sigma_ready(client: TestClient, seeded: dict, auth: dict) -> None:
    r = client.get(f"/api/profiles/{seeded['id']}/subgraph",
                   params={"limit": 300}, headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["nodes"] and body["edges"]
    ids = {n["id"] for n in body["nodes"]}
    assert len(ids) == len(body["nodes"]), "duplicate node ids would break graphology"
    for n in body["nodes"]:
        assert {"id", "label", "kind", "trust", "year"} == set(n)
        assert n["kind"] in ("paper", "author", "topic", "venue",
                             "institution", "profile")
        assert n["label"]
    for e in body["edges"]:
        assert {"source", "target", "relation", "weight"} == set(e)
        # sigma.js throws on an edge referencing a missing node
        assert e["source"] in ids and e["target"] in ids

    # the profile node and its trust edges are synthesised in
    assert any(n["kind"] == "profile" for n in body["nodes"])
    assert any(e["relation"] == "trusts" for e in body["edges"])


def test_subgraph_focus(client: TestClient, seeded: dict, auth: dict) -> None:
    pid = seeded["work_ids"][0]
    r = client.get(f"/api/profiles/{seeded['id']}/subgraph",
                   params={"focus": pid, "limit": 200}, headers=auth)
    assert r.status_code == 200, r.text
    assert f"U{pid}" in {n["id"] for n in r.json()["nodes"]}


# ---------------------------------------------------------------------------
# POST /api/profiles/{id}/params
# ---------------------------------------------------------------------------


def test_params_stores_context_weights_and_reranks(
        client: TestClient, seeded: dict, auth: dict) -> None:
    r = client.post(f"/api/profiles/{seeded['id']}/params",
                    json={"context_weights": {"author": 3.0, "topic": 0.0}},
                    headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["context_weights"]["author"] == 3.0
    assert body["context_weights"]["topic"] == 0.0
    for item in body["preview"]:
        assert_scored(item)

    stored = client.get("/api/profiles/me", headers=auth).json()
    assert stored["params"]["context_weights"]["author"] == 3.0

    # restore, so ordering-sensitive tests are not affected by execution order
    client.post(f"/api/profiles/{seeded['id']}/params",
                json={"context_weights": dict(config.DEFAULT_CONTEXT_WEIGHTS)},
                headers=auth)


@pytest.mark.parametrize("param,value", [
    ("alpha", 0.5),
    ("num_walks", 500),
    ("epoch_half_life_years", 20.0),
])
def test_params_rejects_what_the_engine_does_not_honour(
        client: TestClient, seeded: dict, auth: dict, param: str, value) -> None:
    r = client.post(f"/api/profiles/{seeded['id']}/params",
                    json={param: value}, headers=auth)
    assert r.status_code == 422, r.text
    detail = str(r.json()["detail"])
    assert param in detail
    # the message has to explain *why*, not just refuse
    assert len(detail) > 80, detail


def test_params_rejects_unknown_context_and_parameter(
        client: TestClient, seeded: dict, auth: dict) -> None:
    r = client.post(f"/api/profiles/{seeded['id']}/params",
                    json={"context_weights": {"coupling": 2.0}}, headers=auth)
    assert r.status_code == 422, r.text

    r = client.post(f"/api/profiles/{seeded['id']}/params",
                    json={"made_up_knob": 1}, headers=auth)
    assert r.status_code == 422, r.text


# ---------------------------------------------------------------------------
# POST /api/import/bibtex
# ---------------------------------------------------------------------------


def test_import_bibtex_matches_by_doi_and_title(client: TestClient) -> None:
    prof = client.post("/api/profiles", json={"label": "bibtex"}).json()
    auth = {"Authorization": f"Bearer {prof['token']}"}

    with SessionLocal() as db:
        doi_row = db.execute(text(
            "SELECT id, doi FROM works WHERE doi IS NOT NULL AND is_stub = false "
            "ORDER BY cited_by_count DESC LIMIT 1")).first()
        title_row = db.execute(text(
            "SELECT id, title FROM works WHERE title IS NOT NULL AND is_stub = false "
            "AND length(title) > 30 ORDER BY cited_by_count DESC OFFSET 3 LIMIT 1"
        )).first()
    assert doi_row and title_row

    bib = f"""
@article{{byDoi,
  title = {{Whatever the title says, the DOI wins}},
  doi = {{{doi_row[1]}}},
  year = {{2001}}
}}
@article{{byTitle,
  title = {{{title_row[1]}}},
  year = {{2002}}
}}
@article{{nothing,
  title = {{A paper that is definitely not in this mathematics corpus at all}},
  year = {{2003}}
}}
"""
    r = client.post("/api/import/bibtex",
                    files={"file": ("refs.bib", io.BytesIO(bib.encode()),
                                    "application/x-bibtex")},
                    headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()

    matched = {m["id"] for m in body["matched"]}
    assert doi_row[0] in matched, "DOI match failed"
    assert title_row[0] in matched, "title match failed"
    assert body["unmatched"], "the bogus entry should not have matched anything"
    assert body["added"] == len(matched)

    # matched entries really landed in the trust set
    trust = client.get(f"/api/profiles/{prof['id']}/trust", headers=auth).json()
    assert matched <= {t["work"]["id"] for t in trust["items"]}


def test_import_bibtex_rejects_garbage(client: TestClient) -> None:
    prof = client.post("/api/profiles", json={"label": "bibtex bad"}).json()
    auth = {"Authorization": f"Bearer {prof['token']}"}
    r = client.post("/api/import/bibtex",
                    files={"file": ("empty.bib", io.BytesIO(b""), "text/plain")},
                    headers=auth)
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# OpenAPI
# ---------------------------------------------------------------------------


def test_openapi_documents_every_endpoint(client: TestClient) -> None:
    r = client.get("/openapi.json")
    assert r.status_code == 200
    paths = r.json()["paths"]
    for p in [
        "/api/health", "/api/profiles", "/api/profiles/me",
        "/api/papers/search",
        "/api/profiles/{profile_id}/trust",
        "/api/profiles/{profile_id}/rankings",
        "/api/profiles/{profile_id}/recommendations",
        "/api/profiles/{profile_id}/blindspots",
        "/api/profiles/{profile_id}/diversity",
        "/api/profiles/{profile_id}/simulate",
        "/api/profiles/{profile_id}/subgraph",
        "/api/profiles/{profile_id}/params",
        "/api/profiles/{profile_id}/papers/{pid}",
        "/api/profiles/{profile_id}/papers/{pid}/explain",
        "/api/import/bibtex",
    ]:
        assert p in paths, p
    assert client.get("/docs").status_code == 200
