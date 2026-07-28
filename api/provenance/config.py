"""Runtime configuration.

Context weights are the user-facing tuning surface. The decay parameters below are
split deliberately into what the engine actually honours and what it does not --
see KNOWN_ISSUES.md. We never expose a slider that does nothing.
"""
from __future__ import annotations

import os

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:55432/provenance"
)
AUTOLOAD = os.environ.get("PROVENANCE_AUTOLOAD", "0") == "1"

# Relation families. Each becomes a MeritRank context.
# NOTE (DECISIONS.md D1.6): because citation and trust edges are User->User, they are
# replicated into EVERY context by the engine. A named context therefore means
# "citation backbone + trust seeds + this relation family", not an isolated family.
# Only the four entity families are genuinely separable: their edges touch a Beacon
# node and so stay context-local. Every paper->paper relation (cites, couples,
# co_cited) and every trust edge is User->User and is therefore replicated into all
# contexts by the engine -- so they form a common BASELINE that cannot be isolated.
# "citation" is that baseline; each other context is baseline + one entity family.
BASELINE_CONTEXT = "citation"
ENTITY_CONTEXTS = ["author", "topic", "venue", "institution"]
CONTEXTS = [BASELINE_CONTEXT] + ENTITY_CONTEXTS
AGGREGATE = ""  # the engine's aggregate subgraph, which holds every edge

# Base edge weights, tuned in Phase 2 (see DECISIONS.md).
DEFAULT_WEIGHTS: dict[str, float] = {
    "cites": 1.00,
    "cited_by": 0.15,
    "authored_by": 0.60,
    "wrote": 0.60,
    "couples": 0.35,
    "co_cited": 0.30,
    "published_in": 0.15,
    "publishes": 0.15,
    "tagged": 0.20,
    "tags": 0.20,
    "affiliated": 0.10,
    "hosts": 0.10,
    "trusts": 1.00,
}

# Per-context multipliers the user can move in the parameter playground.
DEFAULT_CONTEXT_WEIGHTS: dict[str, float] = {c: 1.0 for c in CONTEXTS}

TRUST_STRENGTH_SCALE = {1: 0.2, 2: 0.45, 3: 0.7, 4: 1.0, 5: 1.4}
DISTRUST_WEIGHT = -1.0

# Epoch decay: mathematics has a very long half-life, so this is gentle by default.
# Applied by US, at graph-construction time, not by the engine (KNOWN_ISSUES.md).
DEFAULT_EPOCH_HALF_LIFE_YEARS = 60.0
EPOCH_REFERENCE_YEAR = 2026

COLD_START_MIN_SEEDS = 5

DISCLAIMER = (
    "This score measures proximity in a weighted trust graph built from your declared "
    "trust set. It is not a measure of quality, correctness or importance. A low score "
    "often means a paper is poorly represented in OpenAlex -- non-English work, "
    "pre-digital literature and some regions are systematically under-covered."
)
