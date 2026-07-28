import type {
  BibtexImportResponse,
  BlindspotsResponse,
  Context,
  DiversityResponse,
  ExplainResponse,
  HealthResponse,
  PaperDetail,
  Params,
  ProfileCreated,
  ProfileMe,
  RankingsQuery,
  RankingsResponse,
  RecommendationsResponse,
  SearchResponse,
  SimulateBody,
  SimulateResponse,
  SubgraphResponse,
  TrustListResponse,
  TrustMutationResponse,
} from './types';

/** `/api` in dev (proxied to :8000); override with VITE_API_BASE for other deployments. */
export const API_BASE = import.meta.env.VITE_API_BASE ?? '/api';

export class ApiError extends Error {
  readonly status: number;
  readonly detail: unknown;

  constructor(status: number, message: string, detail?: unknown) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }

  /** 422 from POST /params means the engine does not honour that parameter. */
  get isUnhonouredParam(): boolean {
    return this.status === 422;
  }
}

/** Thrown when the API cannot be reached at all (server down, DNS, offline). */
export class NetworkError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'NetworkError';
  }
}

/* ------------------------------------------------------------------ */
/* Session token                                                       */
/* ------------------------------------------------------------------ */

const TOKEN_KEY = 'provenance.token';
const PROFILE_KEY = 'provenance.profileId';

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function getStoredProfileId(): string | null {
  try {
    return localStorage.getItem(PROFILE_KEY);
  } catch {
    return null;
  }
}

export function storeSession(profileId: string, token: string): void {
  try {
    localStorage.setItem(TOKEN_KEY, token);
    localStorage.setItem(PROFILE_KEY, profileId);
  } catch {
    /* Private browsing: the pv_token cookie set by the server still carries us. */
  }
}

export function clearSession(): void {
  try {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(PROFILE_KEY);
  } catch {
    /* nothing to clear */
  }
}

/* ------------------------------------------------------------------ */
/* Request plumbing                                                    */
/* ------------------------------------------------------------------ */

type QueryValue = string | number | boolean | undefined | null;

export function buildQuery(params: Record<string, QueryValue>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue;
    search.set(key, String(value));
  }
  const s = search.toString();
  return s ? `?${s}` : '';
}

async function parseError(res: Response): Promise<ApiError | NetworkError> {
  let detail: unknown;
  let message = `${res.status} ${res.statusText}`;

  // A 5xx that is not JSON did not come from the application: it is the dev
  // proxy (or a gateway) reporting that nothing is listening. Saying "the
  // server failed" there would send people debugging the wrong process.
  const contentType = res.headers.get('content-type') ?? '';
  if (res.status >= 500 && !contentType.includes('json')) {
    return new NetworkError(
      'Could not reach the Provenance API. Is the backend running on port 8000?',
    );
  }

  try {
    const body = await res.json();
    detail = body;
    if (body && typeof body === 'object') {
      const d = (body as Record<string, unknown>).detail;
      if (typeof d === 'string') message = d;
      else if (Array.isArray(d) && d.length > 0) {
        const first = d[0] as Record<string, unknown>;
        if (typeof first?.msg === 'string') message = first.msg;
      } else if (typeof (body as Record<string, unknown>).message === 'string') {
        message = (body as Record<string, string>).message;
      }
    }
  } catch {
    /* non-JSON error body; keep the status line */
  }
  return new ApiError(res.status, message, detail);
}

async function request<T>(
  path: string,
  init: RequestInit & { signal?: AbortSignal } = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  const token = getToken();
  if (token) headers.set('Authorization', `Bearer ${token}`);
  if (init.body && !(init.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  headers.set('Accept', 'application/json');

  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers,
      credentials: 'include',
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') throw err;
    throw new NetworkError(
      'Could not reach the Provenance API. Is the backend running on port 8000?',
    );
  }

  if (!res.ok) throw await parseError(res);
  if (res.status === 204) return undefined as T;

  const text = await res.text();
  if (!text) return undefined as T;
  return JSON.parse(text) as T;
}

const json = (body: unknown): string => JSON.stringify(body);

/* ------------------------------------------------------------------ */
/* Endpoints — one function per contract entry                         */
/* ------------------------------------------------------------------ */

export const api = {
  health: (signal?: AbortSignal) => request<HealthResponse>('/health', { signal }),

  createProfile: (label?: string) =>
    request<ProfileCreated>('/profiles', {
      method: 'POST',
      body: json(label ? { label } : {}),
    }),

  me: (signal?: AbortSignal) => request<ProfileMe>('/profiles/me', { signal }),

  searchPapers: (
    args: { q: string; year_from?: number; year_to?: number; limit?: number; offset?: number },
    signal?: AbortSignal,
  ) =>
    request<SearchResponse>(
      `/papers/search${buildQuery({
        q: args.q,
        year_from: args.year_from,
        year_to: args.year_to,
        limit: args.limit ?? 20,
        offset: args.offset,
      })}`,
      { signal },
    ),

  listTrust: (profileId: string, signal?: AbortSignal) =>
    request<TrustListResponse>(`/profiles/${profileId}/trust`, { signal }),

  setTrust: (
    profileId: string,
    body: { work_id: string; strength: number; is_distrust?: boolean },
  ) =>
    request<TrustMutationResponse>(`/profiles/${profileId}/trust`, {
      method: 'POST',
      body: json(body),
    }),

  rankings: (profileId: string, q: RankingsQuery, signal?: AbortSignal) =>
    request<RankingsResponse>(
      `/profiles/${profileId}/rankings${buildQuery({
        limit: q.limit,
        offset: q.offset,
        year_from: q.year_from,
        year_to: q.year_to,
        context: q.context,
        exclude_trusted: q.exclude_trusted,
      })}`,
      { signal },
    ),

  recommendations: (
    profileId: string,
    q: { diversity: number; limit?: number },
    signal?: AbortSignal,
  ) =>
    request<RecommendationsResponse>(
      `/profiles/${profileId}/recommendations${buildQuery({
        diversity: q.diversity,
        limit: q.limit,
      })}`,
      { signal },
    ),

  paper: (profileId: string, paperId: string, signal?: AbortSignal) =>
    request<PaperDetail>(`/profiles/${profileId}/papers/${paperId}`, { signal }),

  explain: (profileId: string, paperId: string, signal?: AbortSignal) =>
    request<ExplainResponse>(`/profiles/${profileId}/papers/${paperId}/explain`, { signal }),

  blindspots: (profileId: string, signal?: AbortSignal) =>
    request<BlindspotsResponse>(`/profiles/${profileId}/blindspots`, { signal }),

  diversity: (profileId: string, signal?: AbortSignal) =>
    request<DiversityResponse>(`/profiles/${profileId}/diversity`, { signal }),

  simulate: (profileId: string, body: SimulateBody, signal?: AbortSignal) =>
    request<SimulateResponse>(`/profiles/${profileId}/simulate`, {
      method: 'POST',
      body: json(body),
      signal,
    }),

  subgraph: (
    profileId: string,
    q: { focus?: string; limit?: number; context?: Context },
    signal?: AbortSignal,
  ) =>
    request<SubgraphResponse>(
      `/profiles/${profileId}/subgraph${buildQuery({
        focus: q.focus,
        limit: q.limit,
        context: q.context,
      })}`,
      { signal },
    ),

  setParams: (profileId: string, params: Params) =>
    request<Params>(`/profiles/${profileId}/params`, {
      method: 'POST',
      body: json(params),
    }),

  importBibtex: (file: File) => {
    const form = new FormData();
    form.append('file', file);
    return request<BibtexImportResponse>('/import/bibtex', {
      method: 'POST',
      body: form,
    });
  },
};
