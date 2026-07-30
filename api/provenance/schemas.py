"""Pydantic v2 response/request models.

These mirror API_CONTRACT.md exactly: field names and nesting are part of the contract
the web client is built against, so nothing here may be renamed for convenience.

Two invariants from the contract are encoded structurally rather than by convention:
  * no score is ever returned without an :class:`Uncertainty` beside it;
  * every list endpoint carries the disclaimer string.
"""
from __future__ import annotations

import datetime as dt
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Core types
# ---------------------------------------------------------------------------

UncertaintyMethod = Literal[
    "leave_one_out", "repeat_sample", "proportional_fallback",
]

NodeKind = Literal["paper", "author", "topic", "venue", "institution", "profile"]

RankingContext = Literal[
    "aggregate", "citation", "author", "topic", "venue", "institution",
    "coupling", "cocitation",
]


class Uncertainty(BaseModel):
    stderr: float
    ci_low: float
    ci_high: float
    tie_group: int
    method: UncertaintyMethod
    n_samples: int


class AuthorRef(BaseModel):
    id: str
    name: str


class VenueRef(BaseModel):
    id: str
    name: str


class PaperBrief(BaseModel):
    id: str
    title: Optional[str] = None
    year: Optional[int] = None
    authors: list[AuthorRef] = Field(default_factory=list)
    venue: Optional[VenueRef] = None
    cited_by_count: int = 0
    in_corpus_cited_by: int = 0
    is_stub: bool = False
    doi: Optional[str] = None


class ScoredPaper(PaperBrief):
    trust: float
    uncertainty: Uncertainty
    global_merit: float
    rank: int
    disagreement: float
    # Fame-normalised proximity: log(trust+eps) - lift_gamma*log(background+eps).
    # A separate displayed field; `trust` keeps its meaning. Optional uncertainty
    # only on paths that never computed lift (simulate scratch rows) -- the
    # rankings path always carries one.
    lift: float = 0.0
    lift_uncertainty: Optional[Uncertainty] = None


# ---------------------------------------------------------------------------
# Profiles / auth
# ---------------------------------------------------------------------------


class ProfileCreate(BaseModel):
    label: Optional[str] = None


class StoredParams(BaseModel):
    """The only per-profile knob the engine actually honours, plus the
    display-level visibility toggle for user-contributed works.

    `alpha`, `num_walks` and `epoch_half_life_years` are deliberately absent: see
    API_NOTES.md and the 422 raised by POST /api/profiles/{id}/params.
    """
    context_weights: dict[str, float]
    # Display-level ONLY: there is one shared graph and walks propagate through
    # uploaded edges for everyone; excluding them from your results does not
    # isolate your scores from their existence (KNOWN_ISSUES).
    include_user_uploads: bool = False
    # Background exponent for the lift field, 0..1. Honoured live (recomposed per
    # request), so it is a legitimate parameter, unlike alpha/num_walks.
    lift_gamma: float = 0.5


class ProfileCreated(BaseModel):
    id: str
    token: str
    label: Optional[str] = None
    created_at: dt.datetime
    params: StoredParams


class ProfileMe(BaseModel):
    id: str
    label: Optional[str] = None
    params: StoredParams
    trust_count: int
    warmed_at: Optional[dt.datetime] = None


class ParamsUpdate(BaseModel):
    """Everything except `context_weights` is rejected with 422 rather than ignored."""
    model_config = ConfigDict(extra="allow")

    context_weights: Optional[dict[str, float]] = None
    include_user_uploads: Optional[bool] = None
    lift_gamma: Optional[float] = None
    alpha: Optional[float] = None
    epoch_half_life_years: Optional[float] = None
    num_walks: Optional[int] = None


class ParamsResponse(BaseModel):
    context_weights: dict[str, float]
    include_user_uploads: bool = False
    lift_gamma: float = 0.5
    # Proof the weights are live rather than stored-and-forgotten: the top of the
    # ranking recomputed under the new weights, via ranking.compose().
    preview: list[ScoredPaper] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Search / trust
# ---------------------------------------------------------------------------


class SearchResponse(BaseModel):
    total: int
    items: list[PaperBrief]


class TrustEntry(BaseModel):
    work: PaperBrief
    strength: int
    is_distrust: bool


class TrustUpdate(BaseModel):
    work_id: str
    strength: int = Field(ge=0, le=5)
    is_distrust: bool = False


class TrustListResponse(BaseModel):
    items: list[TrustEntry]


class TrustMutateResponse(BaseModel):
    trust_count: int
    items: list[TrustEntry]


# ---------------------------------------------------------------------------
# Rankings
# ---------------------------------------------------------------------------


class ColdStart(BaseModel):
    seeds: int
    reliable: bool
    message: Optional[str] = None


class RankingsResponse(BaseModel):
    items: list[ScoredPaper]
    total: int
    cold_start: ColdStart
    disclaimer: str
    timing_ms: float


class RecommendedPaper(ScoredPaper):
    novelty: float
    reason: str


class RecommendationsResponse(BaseModel):
    items: list[RecommendedPaper]
    diversity: float
    disclaimer: str
    cold_start: ColdStart


class BlindspotPaper(ScoredPaper):
    gap: float


class BlindspotsResponse(BaseModel):
    items: list[BlindspotPaper]
    disclaimer: str
    cold_start: ColdStart


# ---------------------------------------------------------------------------
# Paper detail / explain
# ---------------------------------------------------------------------------


class Percentiles(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    trust: float
    # `global` is a Python keyword; FastAPI serialises by alias, so the wire name is
    # exactly the contract's `global`.
    global_: float = Field(alias="global")
    citations: float


class TopicRef(BaseModel):
    id: str
    name: str
    score: float


class InstitutionRef(BaseModel):
    id: str
    name: str
    country: Optional[str] = None


class PaperDetail(BaseModel):
    paper: PaperBrief
    trust: float
    uncertainty: Uncertainty
    global_merit: float
    cited_by_count: int
    percentiles: Percentiles
    disagreement: float
    in_trust_set: Optional[TrustEntry] = None
    topics: list[TopicRef] = Field(default_factory=list)
    institutions: list[InstitutionRef] = Field(default_factory=list)


class ExplainNode(BaseModel):
    id: str
    kind: NodeKind
    label: str


class ExplainEdge(BaseModel):
    relation: str
    weight: float


class ExplainPath(BaseModel):
    nodes: list[ExplainNode]
    edges: list[ExplainEdge]
    contribution: float
    seed: PaperBrief


class ContextContribution(BaseModel):
    context: str
    score: float
    marginal: float
    share: float


class ExplainResponse(BaseModel):
    target: PaperBrief
    trust: float
    uncertainty: Uncertainty
    paths: list[ExplainPath]
    by_context: list[ContextContribution]
    summary: str
    caveat: str


# ---------------------------------------------------------------------------
# Diversity
# ---------------------------------------------------------------------------


class EntropyBreakdown(BaseModel):
    topics: float
    institutions: float
    decades: float
    countries: float


class Concentration(BaseModel):
    label: str
    share: float


class DiversityResponse(BaseModel):
    entropy: EntropyBreakdown
    max_entropy: EntropyBreakdown
    concentration: list[Concentration]
    echo_chamber_score: float
    message: str


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------


class SimulateAdd(BaseModel):
    work_id: str
    strength: int = Field(default=3, ge=1, le=5)
    is_distrust: bool = False


class SimulateRequest(BaseModel):
    add: list[SimulateAdd] = Field(default_factory=list)
    remove: list[str] = Field(default_factory=list)
    limit: int = Field(default=25, ge=1, le=100)


class Moved(BaseModel):
    work_id: str
    delta_rank: int
    delta_trust: float


class SimulateResponse(BaseModel):
    before: list[ScoredPaper]
    after: list[ScoredPaper]
    moved: list[Moved]


# ---------------------------------------------------------------------------
# Subgraph (graphology / sigma.js ingestion shape)
# ---------------------------------------------------------------------------


class GraphNodeOut(BaseModel):
    id: str
    label: str
    kind: NodeKind
    trust: float
    year: Optional[int] = None


class GraphEdgeOut(BaseModel):
    source: str
    target: str
    relation: str
    weight: float


class SubgraphResponse(BaseModel):
    nodes: list[GraphNodeOut]
    edges: list[GraphEdgeOut]


# ---------------------------------------------------------------------------
# Uploads (PDF bibliography -> trust seeding)
# ---------------------------------------------------------------------------

MatchMethod = Literal["doi", "arxiv", "trigram", "openalex", "manual", "none"]
ReferenceDecision = Literal["pending", "accept", "reject"]
UploadStatus = Literal["draft", "applying", "engine_pending", "confirmed"]


class UploadReferenceOut(BaseModel):
    idx: int
    raw: str
    parsed_title: Optional[str] = None
    parsed_doi: Optional[str] = None
    parsed_year: Optional[int] = None
    match_method: MatchMethod
    confidence: float
    decision: ReferenceDecision
    strength: int
    is_self_citation: bool
    # OpenAlex resolution recorded on the draft; a works row exists only after
    # confirm. The client shows "will be added to the corpus" for these.
    resolved_openalex_id: Optional[str] = None
    # Set when the reference matched an EXISTING corpus work.
    work: Optional[PaperBrief] = None
    # True when OpenAlex was unreachable for this entry: display "couldn't
    # check" (our failure), never "not found" (a claim about the paper).
    couldnt_check: bool = False


class UploadOut(BaseModel):
    id: str
    filename: Optional[str] = None
    title: Optional[str] = None
    status: UploadStatus
    n_parsed: int
    n_matched: int
    n_added: int
    n_unresolved: int
    created_at: dt.datetime
    references: list[UploadReferenceOut] = Field(default_factory=list)


class UploadListItem(BaseModel):
    id: str
    filename: Optional[str] = None
    title: Optional[str] = None
    status: UploadStatus
    n_parsed: int
    n_matched: int
    n_added: int
    n_unresolved: int
    created_at: dt.datetime


class UploadListResponse(BaseModel):
    items: list[UploadListItem]


class UploadReferencePatch(BaseModel):
    """Correct/choose/reject one entry in review."""
    decision: Optional[ReferenceDecision] = None
    strength: Optional[int] = Field(default=None, ge=1, le=5)
    # Manual match to a corpus work; clears any OpenAlex resolution.
    work_id: Optional[str] = None
    # Corrected fields; a title/year change re-runs matching for this entry.
    parsed_title: Optional[str] = None
    parsed_year: Optional[int] = None


class UploadPatch(BaseModel):
    """Edit the upload's own-paper title before confirm."""
    title: Optional[str] = None


class UploadUndoResponse(BaseModel):
    n_edges_removed: int
    n_trust_removed: int
    n_engine_deleted: int
    removed_local_work: bool


class UploadConfirmResponse(BaseModel):
    status: UploadStatus
    # The uploaded paper's work id (existing corpus id, OpenAlex id fetched at
    # confirm, or a UL-labelled L... local id).
    work_id: str
    n_cited: int
    n_added: int
    n_trust: int
    n_skipped_unavailable: int = 0
    # engine_pending is not an error: the Postgres truth is committed and the
    # background sweep (or the next restart) repairs the engine for free.
    detail: Optional[str] = None


# ---------------------------------------------------------------------------
# Import / health
# ---------------------------------------------------------------------------


class BibtexImportResponse(BaseModel):
    matched: list[PaperBrief]
    unmatched: list[str]
    added: int


class HealthResponse(BaseModel):
    ok: bool
    db: bool
    meritrank: bool
    graph_loaded: bool
    nodes: int
    edges: int
    detail: Optional[str] = None
