"""Persisted graph generation counter: `graph_meta.version`.

This is the single invalidation marker for every score cache in the process
(`ranking._CACHE`, the `services` pool cache, the global-merit cache, the distance
cache). It replaces the old `max(graph_edges.id)` generation marker, which had three
holes found in review (spec N1):

  * ABA -- a TRUNCATE + reload can land on the same max id it started with;
  * process locality -- an id is only observable after the mutating process commits,
    but a mutation that changes *weights* without adding rows never moves it;
  * LRU skew -- max(id) over half a million rows on every pool lookup.

The version lives in Postgres so every process (api container, host scripts, tests)
sees the same counter. Anything that mutates the graph -- `scripts/build_graph.py`,
the upload confirm/undo paths -- must call `bump_graph_version` and commit.

Both helpers accept a Session or a Connection (both expose `.execute`).
"""
from __future__ import annotations

from sqlalchemy import text


def graph_version(db) -> int:
    """Current graph version. 0 when the singleton row is absent (a database from
    before the graph_meta migration ran)."""
    return int(db.execute(text(
        "SELECT coalesce((SELECT version FROM graph_meta WHERE id = 1), 0)"
    )).scalar_one())


def bump_graph_version(db) -> int:
    """Increment and return the version. The caller owns the commit, so the bump
    lands atomically with the graph mutation it describes."""
    return int(db.execute(text(
        "INSERT INTO graph_meta (id, version) VALUES (1, 2) "
        "ON CONFLICT (id) DO UPDATE SET version = graph_meta.version + 1 "
        "RETURNING version"
    )).scalar_one())
