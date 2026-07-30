"""Deterministic scorer over graph_edges. Live-stack, read-only (Postgres only --
no mr-service involvement; that is the point of the module)."""
from __future__ import annotations

import pytest
from sqlalchemy import text

from provenance.db import SessionLocal
from provenance.propagate import PropagationGraph


@pytest.fixture()
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


def test_scores_seed_neighbourhood(db):
    g = PropagationGraph.get(db)
    seed = db.execute(text(
        "SELECT src FROM graph_edges WHERE relation = 'cites' "
        "AND src LIKE 'UW%' LIMIT 1")).scalar_one()[1:]
    scores = g.score({seed: 1.0})
    cited = [r[0][1:] for r in db.execute(text(
        "SELECT dst FROM graph_edges WHERE src = :s AND relation = 'cites' LIMIT 5"),
        {"s": "U" + seed}).all()]
    assert any(scores.get(c, 0) > 0 for c in cited), "no mass reached cited papers"
    # seed-absorbing: the seed itself never scores (deterministic analogue of the
    # engine's unique-visit counting at the source)
    assert scores.get(seed, 0.0) == 0.0


def test_distrust_seed_scores_negative_neighbourhood(db):
    g = PropagationGraph.get(db)
    seed = db.execute(text(
        "SELECT src FROM graph_edges WHERE relation = 'cites' "
        "AND src LIKE 'UW%' LIMIT 1")).scalar_one()[1:]
    pos = g.score({seed: 1.0})
    neg = g.score({seed: -1.0})
    # linearity: a distrust seed is the mirror image
    common = [k for k, v in pos.items() if v > 0][:10]
    assert common and all(neg[k] == pytest.approx(-pos[k]) for k in common)


def test_background_cached_and_covers_corpus(db):
    g = PropagationGraph.get(db)
    bg = g.background()
    assert len(bg) > 5000
    assert g.background() is bg  # per-graph-version cache, object identity
    assert all(v >= 0.0 for v in list(bg.values())[:100])


def test_graph_cache_keyed_on_version(db):
    a = PropagationGraph.get(db)
    b = PropagationGraph.get(db)
    assert a is b
