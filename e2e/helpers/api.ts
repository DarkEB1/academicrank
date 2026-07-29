/**
 * Thin typed client for the live Provenance API.
 *
 * The browser talks to nginx on :5173 which proxies /api; from Node we go
 * straight to :8000. Both hit the same FastAPI process.
 */
export const API_BASE = process.env.PROVENANCE_API ?? 'http://localhost:8000/api';

export type PaperBrief = {
  id: string;
  title: string | null;
  year: number | null;
  authors: { id: string; name: string }[];
  cited_by_count: number;
  in_corpus_cited_by: number;
  is_stub: boolean;
};

export type Uncertainty = {
  stderr: number;
  ci_low: number;
  ci_high: number;
  tie_group: number;
  method: string;
  n_samples: number;
};

export type ScoredPaper = PaperBrief & {
  trust: number;
  uncertainty: Uncertainty;
  rank: number;
  disagreement: number;
};

export type RankingsResponse = {
  total: number;
  items: ScoredPaper[];
  timing_ms: number;
  disclaimer: string;
  cold_start: { seeds: number; reliable: boolean; message: string | null };
};

export type Profile = {
  id: string;
  token: string;
  params: { context_weights?: Record<string, number> } & Record<string, unknown>;
};

async function req<T>(path: string, init: RequestInit & { token?: string } = {}): Promise<T> {
  const { token, ...rest } = init;
  const headers: Record<string, string> = { Accept: 'application/json' };
  if (token) headers.Authorization = `Bearer ${token}`;
  if (rest.body) headers['Content-Type'] = 'application/json';
  const res = await fetch(`${API_BASE}${path}`, { ...rest, headers });
  const text = await res.text();
  if (!res.ok) {
    throw new Error(`API ${init.method ?? 'GET'} ${path} -> ${res.status}: ${text.slice(0, 400)}`);
  }
  return (text ? JSON.parse(text) : undefined) as T;
}

export const apiClient = {
  health: () =>
    req<{ ok: boolean; db: boolean; graph_loaded: boolean; nodes: number; edges: number }>(
      '/health',
    ),

  createProfile: () => req<Profile>('/profiles', { method: 'POST', body: '{}' }),

  me: (token: string) => req<Profile & { trust_count: number }>('/profiles/me', { token }),

  search: (q: string, limit = 25) =>
    req<{ total: number; items: PaperBrief[] }>(
      `/papers/search?q=${encodeURIComponent(q)}&limit=${limit}`,
    ),

  setTrust: (profileId: string, token: string, workId: string, strength = 3) =>
    req<{ items: { work: PaperBrief; strength: number; is_distrust: boolean }[] }>(
      `/profiles/${profileId}/trust`,
      { method: 'POST', token, body: JSON.stringify({ work_id: workId, strength }) },
    ),

  listTrust: (profileId: string, token: string) =>
    req<{ items: { work: PaperBrief; strength: number; is_distrust: boolean }[] }>(
      `/profiles/${profileId}/trust`,
      { token },
    ),

  rankings: (
    profileId: string,
    token: string,
    q: { limit?: number; offset?: number; context?: string; exclude_trusted?: boolean } = {},
  ) => {
    const search = new URLSearchParams({
      limit: String(q.limit ?? 25),
      offset: String(q.offset ?? 0),
      context: q.context ?? 'aggregate',
      exclude_trusted: String(q.exclude_trusted ?? true),
    });
    return req<RankingsResponse>(`/profiles/${profileId}/rankings?${search}`, { token });
  },

  recommendations: (profileId: string, token: string, diversity = 0.35, limit = 30) =>
    req<{ items: ScoredPaper[] }>(
      `/profiles/${profileId}/recommendations?diversity=${diversity}&limit=${limit}`,
      { token },
    ),

  subgraph: (profileId: string, token: string, limit = 1000, focus?: string) => {
    const search = new URLSearchParams({ limit: String(limit), context: 'aggregate' });
    if (focus) search.set('focus', focus);
    return req<{ nodes: unknown[]; edges: unknown[] }>(
      `/profiles/${profileId}/subgraph?${search}`,
      { token },
    );
  },

  explain: (profileId: string, token: string, paperId: string) =>
    req<{ paths: { seed: PaperBrief; contribution: number }[]; summary: string; caveat: string }>(
      `/profiles/${profileId}/papers/${paperId}/explain`,
      { token },
    ),

  paper: (profileId: string, token: string, paperId: string) =>
    req<Record<string, unknown>>(`/profiles/${profileId}/papers/${paperId}`, { token }),

  setParams: (profileId: string, token: string, params: Record<string, unknown>) =>
    req<Record<string, unknown>>(`/profiles/${profileId}/params`, {
      method: 'POST',
      token,
      body: JSON.stringify(params),
    }),
};

/** Real corpus queries used to seed trust sets. Nothing here is a hardcoded id. */
export const SEED_QUERIES = [
  'algebraic geometry',
  'number theory',
  'optimization',
  'topology',
  'graph theory',
];
