import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from '@tanstack/react-query';
import { api } from './api';
import type {
  BibtexImportResponse,
  Context,
  DiversityResponse,
  ExplainResponse,
  HealthResponse,
  PaperDetail,
  Params,
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

export const keys = {
  health: ['health'] as const,
  me: ['me'] as const,
  search: (q: string, yf?: number, yt?: number) => ['search', q, yf ?? null, yt ?? null] as const,
  trust: (p: string) => ['trust', p] as const,
  rankings: (p: string, q: RankingsQuery) => ['rankings', p, q] as const,
  recommendations: (p: string, d: number, limit: number) =>
    ['recommendations', p, d, limit] as const,
  paper: (p: string, id: string) => ['paper', p, id] as const,
  explain: (p: string, id: string) => ['explain', p, id] as const,
  blindspots: (p: string) => ['blindspots', p] as const,
  diversity: (p: string) => ['diversity', p] as const,
  subgraph: (p: string, focus: string | undefined, limit: number, ctx: Context) =>
    ['subgraph', p, focus ?? null, limit, ctx] as const,
};

/**
 * The first ranking query for a cold profile genuinely takes seconds: the ego's
 * walks are warmed on demand. Retries must not stack on top of that.
 */
const SLOW = {
  staleTime: 30_000,
  gcTime: 5 * 60_000,
  retry: 1,
};

export function useHealth(): UseQueryResult<HealthResponse> {
  return useQuery({
    queryKey: keys.health,
    queryFn: ({ signal }) => api.health(signal),
    staleTime: 60_000,
    retry: false,
  });
}

export function usePaperSearch(
  q: string,
  opts: { yearFrom?: number; yearTo?: number; enabled?: boolean } = {},
): UseQueryResult<SearchResponse> {
  const trimmed = q.trim();
  return useQuery({
    queryKey: keys.search(trimmed, opts.yearFrom, opts.yearTo),
    queryFn: ({ signal }) =>
      api.searchPapers(
        { q: trimmed, year_from: opts.yearFrom, year_to: opts.yearTo, limit: 25 },
        signal,
      ),
    enabled: (opts.enabled ?? true) && trimmed.length >= 2,
    staleTime: 60_000,
    placeholderData: (prev) => prev,
  });
}

export function useTrustSet(profileId: string): UseQueryResult<TrustListResponse> {
  return useQuery({
    queryKey: keys.trust(profileId),
    queryFn: ({ signal }) => api.listTrust(profileId, signal),
    staleTime: 10_000,
  });
}

export function useSetTrust(
  profileId: string,
): UseMutationResult<
  TrustMutationResponse,
  Error,
  { work_id: string; strength: number; is_distrust?: boolean }
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { work_id: string; strength: number; is_distrust?: boolean }) =>
      api.setTrust(profileId, body),
    onSuccess: (data) => {
      qc.setQueryData(keys.trust(profileId), { items: data.items });
      // Every derived view depends on the trust set; the warm is async, so a
      // refetch is the only honest way to reflect it.
      void qc.invalidateQueries({ queryKey: ['rankings'] });
      void qc.invalidateQueries({ queryKey: ['recommendations'] });
      void qc.invalidateQueries({ queryKey: ['blindspots'] });
      void qc.invalidateQueries({ queryKey: ['diversity'] });
      void qc.invalidateQueries({ queryKey: ['explain'] });
      void qc.invalidateQueries({ queryKey: ['paper'] });
      void qc.invalidateQueries({ queryKey: keys.me });
    },
  });
}

export function useRankings(
  profileId: string,
  query: RankingsQuery,
  enabled = true,
): UseQueryResult<RankingsResponse> {
  return useQuery({
    queryKey: keys.rankings(profileId, query),
    queryFn: ({ signal }) => api.rankings(profileId, query, signal),
    enabled,
    placeholderData: (prev) => prev,
    ...SLOW,
  });
}

export function useRecommendations(
  profileId: string,
  diversity: number,
  limit = 25,
): UseQueryResult<RecommendationsResponse> {
  return useQuery({
    queryKey: keys.recommendations(profileId, diversity, limit),
    queryFn: ({ signal }) => api.recommendations(profileId, { diversity, limit }, signal),
    placeholderData: (prev) => prev,
    ...SLOW,
  });
}

export function usePaper(profileId: string, paperId: string): UseQueryResult<PaperDetail> {
  return useQuery({
    queryKey: keys.paper(profileId, paperId),
    queryFn: ({ signal }) => api.paper(profileId, paperId, signal),
    ...SLOW,
  });
}

export function useExplain(
  profileId: string,
  paperId: string | null,
): UseQueryResult<ExplainResponse> {
  return useQuery({
    queryKey: keys.explain(profileId, paperId ?? ''),
    queryFn: ({ signal }) => api.explain(profileId, paperId as string, signal),
    enabled: Boolean(paperId),
    ...SLOW,
  });
}

export function useBlindspots(profileId: string, enabled = true) {
  return useQuery({
    queryKey: keys.blindspots(profileId),
    queryFn: ({ signal }) => api.blindspots(profileId, signal),
    enabled,
    ...SLOW,
  });
}

export function useDiversityProfile(profileId: string): UseQueryResult<DiversityResponse> {
  return useQuery({
    queryKey: keys.diversity(profileId),
    queryFn: ({ signal }) => api.diversity(profileId, signal),
    ...SLOW,
  });
}

export function useSubgraph(
  profileId: string,
  args: { focus?: string; limit: number; context: Context },
  enabled = true,
): UseQueryResult<SubgraphResponse> {
  return useQuery({
    queryKey: keys.subgraph(profileId, args.focus, args.limit, args.context),
    queryFn: ({ signal }) => api.subgraph(profileId, args, signal),
    enabled,
    placeholderData: (prev) => prev,
    ...SLOW,
  });
}

export function useSimulate(
  profileId: string,
): UseMutationResult<SimulateResponse, Error, SimulateBody> {
  return useMutation({
    mutationFn: (body: SimulateBody) => api.simulate(profileId, body),
  });
}

export function useSetParams(profileId: string): UseMutationResult<Params, Error, Params> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (params: Params) => api.setParams(profileId, params),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['rankings'] });
      void qc.invalidateQueries({ queryKey: ['recommendations'] });
      void qc.invalidateQueries({ queryKey: keys.me });
    },
  });
}

export function useImportBibtex(
  profileId: string,
): UseMutationResult<BibtexImportResponse, Error, File> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => api.importBibtex(file),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.trust(profileId) });
      void qc.invalidateQueries({ queryKey: ['rankings'] });
      void qc.invalidateQueries({ queryKey: keys.me });
    },
  });
}
