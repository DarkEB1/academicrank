/**
 * Types transcribed from API_CONTRACT.md (v1). This file is the single source of
 * truth for the shape of every server response. If it disagrees with the
 * contract, the contract wins.
 */

export type UncertaintyMethod =
  | 'leave_one_out'
  | 'repeat_sample'
  | 'proportional_fallback';

export type Uncertainty = {
  stderr: number;
  ci_low: number;
  ci_high: number;
  /** Equal value => statistically tied. */
  tie_group: number;
  method: UncertaintyMethod;
  n_samples: number;
};

export type AuthorRef = { id: string; name: string };
export type VenueRef = { id: string; name: string };

export type PaperBrief = {
  /** OpenAlex short id, e.g. "W2963757046" */
  id: string;
  title: string | null;
  year: number | null;
  /** up to 6 */
  authors: AuthorRef[];
  venue: VenueRef | null;
  cited_by_count: number;
  in_corpus_cited_by: number;
  is_stub: boolean;
  doi: string | null;
};

export type ScoredPaper = PaperBrief & {
  /** personalised MeritRank score */
  trust: number;
  uncertainty: Uncertainty;
  /** unpersonalised MeritRank */
  global_merit: number;
  rank: number;
  /** 0..1, how much trust/global/citations disagree */
  disagreement: number;
};

/* ------------------------------------------------------------------ */
/* Contexts                                                            */
/* ------------------------------------------------------------------ */

export const CONTEXTS = [
  'aggregate',
  'citation',
  'author',
  'topic',
  'venue',
  'institution',
  'coupling',
  'cocitation',
] as const;

export type Context = (typeof CONTEXTS)[number];

/** Contexts that carry their own non-user edge family (i.e. tunable weights). */
export const WEIGHTED_CONTEXTS: Context[] = [
  'author',
  'topic',
  'venue',
  'institution',
  'coupling',
  'cocitation',
];

/* ------------------------------------------------------------------ */
/* Profiles                                                            */
/* ------------------------------------------------------------------ */

export type Params = {
  context_weights?: Record<string, number>;
  alpha?: number;
  epoch_half_life_years?: number;
  num_walks?: number;
} & Record<string, unknown>;

export type ProfileCreated = {
  id: string;
  token: string;
  label: string | null;
  created_at: string;
  params: Params;
};

export type ProfileMe = {
  id: string;
  label: string | null;
  params: Params;
  trust_count: number;
  warmed_at: string | null;
};

/* ------------------------------------------------------------------ */
/* Search & trust set                                                  */
/* ------------------------------------------------------------------ */

export type SearchResponse = {
  total: number;
  items: PaperBrief[];
};

export type TrustStrength = 1 | 2 | 3 | 4 | 5;

export type TrustEntry = {
  work: PaperBrief;
  strength: number;
  is_distrust: boolean;
};

export type TrustMutationResponse = {
  trust_count: number;
  items: TrustEntry[];
};

export type TrustListResponse = {
  items: TrustEntry[];
};

/* ------------------------------------------------------------------ */
/* Rankings                                                            */
/* ------------------------------------------------------------------ */

export type ColdStart = {
  seeds: number;
  reliable: boolean;
  message: string | null;
};

export type RankingsResponse = {
  items: ScoredPaper[];
  total: number;
  cold_start: ColdStart;
  disclaimer: string;
  timing_ms: number;
};

export type RankingsQuery = {
  limit?: number;
  offset?: number;
  year_from?: number;
  year_to?: number;
  context?: Context;
  exclude_trusted?: boolean;
};

/* ------------------------------------------------------------------ */
/* Recommendations                                                     */
/* ------------------------------------------------------------------ */

export type Recommendation = ScoredPaper & {
  novelty: number;
  reason: string;
};

export type RecommendationsResponse = {
  items: Recommendation[];
  diversity: number;
  disclaimer: string;
};

/* ------------------------------------------------------------------ */
/* Paper detail                                                        */
/* ------------------------------------------------------------------ */

export type Percentiles = {
  trust: number;
  global: number;
  citations: number;
};

export type TopicRef = { id: string; name: string; score: number };
export type InstitutionRef = { id: string; name: string; country: string | null };

export type PaperDetail = {
  paper: PaperBrief & {
    /**
     * Not in API_CONTRACT v1. Rendered (with KaTeX) only when the server sends
     * it; never substituted. See FRONTEND_NOTES.md.
     */
    abstract?: string | null;
  };
  trust: number;
  uncertainty: Uncertainty;
  global_merit: number;
  cited_by_count: number;
  percentiles: Percentiles;
  disagreement: number;
  in_trust_set: TrustEntry | null;
  topics: TopicRef[];
  institutions: InstitutionRef[];
};

/* ------------------------------------------------------------------ */
/* Explanation                                                         */
/* ------------------------------------------------------------------ */

export type NodeKind = 'paper' | 'author' | 'topic' | 'venue' | 'institution' | 'profile';

export type PathNode = {
  id: string;
  kind: NodeKind;
  label: string;
};

export type PathEdge = {
  relation: string;
  weight: number;
};

export type ContributingPath = {
  nodes: PathNode[];
  edges: PathEdge[];
  /** share of total, 0..1 */
  contribution: number;
  /** which trusted paper this path starts from */
  seed: PaperBrief;
};

export type ContextContribution = {
  context: string;
  score: number;
  /** score(ctx) - score(citation); a marginal, not an isolated contribution */
  marginal: number;
  share: number;
};

export type ExplainResponse = {
  target: PaperBrief;
  trust: number;
  uncertainty: Uncertainty;
  paths: ContributingPath[];
  by_context: ContextContribution[];
  summary: string;
  caveat: string;
};

/* ------------------------------------------------------------------ */
/* Blindspots & diversity                                              */
/* ------------------------------------------------------------------ */

export type Blindspot = ScoredPaper & { gap: number };

export type BlindspotsResponse = {
  items: Blindspot[];
};

export type EntropyBlock = {
  topics: number;
  institutions: number;
  decades: number;
  countries: number;
};

export type DiversityResponse = {
  entropy: EntropyBlock;
  max_entropy: EntropyBlock;
  concentration: { label: string; share: number }[];
  echo_chamber_score: number;
  message: string;
};

/* ------------------------------------------------------------------ */
/* Simulate                                                            */
/* ------------------------------------------------------------------ */

export type SimulateBody = {
  add?: { work_id: string; strength: number; is_distrust?: boolean }[];
  remove?: string[];
  limit?: number;
};

export type MovedItem = {
  work_id: string;
  delta_rank: number;
  delta_trust: number;
};

export type SimulateResponse = {
  before: ScoredPaper[];
  after: ScoredPaper[];
  moved: MovedItem[];
};

/* ------------------------------------------------------------------ */
/* Subgraph                                                            */
/* ------------------------------------------------------------------ */

export type SubgraphNode = {
  id: string;
  label: string;
  kind: NodeKind;
  trust: number;
  year: number | null;
};

export type SubgraphEdge = {
  source: string;
  target: string;
  relation: string;
  weight: number;
};

export type SubgraphResponse = {
  nodes: SubgraphNode[];
  edges: SubgraphEdge[];
};

/* ------------------------------------------------------------------ */
/* Import                                                              */
/* ------------------------------------------------------------------ */

export type BibtexImportResponse = {
  matched: PaperBrief[];
  unmatched: string[];
  added: number;
};

/* ------------------------------------------------------------------ */
/* Health                                                              */
/* ------------------------------------------------------------------ */

export type HealthResponse = {
  ok: boolean;
  db: boolean | string;
  meritrank: boolean | string;
  graph_loaded: boolean;
  nodes: number;
  edges: number;
};
