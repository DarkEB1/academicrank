import {
  useMutation,
  useQueries,
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
  RankedSearchResponse,
  RecommendationsResponse,
  SearchResponse,
  SimulateBody,
  SimulateResponse,
  SubgraphResponse,
  TrustListResponse,
  TrustMutationResponse,
  Upload,
  UploadConfirmResponse,
  UploadListResponse,
  UploadPatch,
  UploadReference,
  UploadReferencePatch,
  UploadUndoResponse,
} from './types';

export const keys = {
  health: ['health'] as const,
  me: ['me'] as const,
  search: (q: string, yf?: number, yt?: number) => ['search', q, yf ?? null, yt ?? null] as const,
  paperSearch: (args: {
    q: string;
    year_from?: number;
    year_to?: number;
    limit?: number;
    offset?: number;
  }) => ['paper-search', args] as const,
  rankedSearch: (args: {
    q: string;
    rank: 'trust' | 'global';
    year_from?: number;
    year_to?: number;
    limit?: number;
    offset?: number;
  }) => ['paper-search-ranked', args] as const,
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
  uploads: (p: string) => ['uploads', p] as const,
  upload: (id: string) => ['upload', id] as const,
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
  args: { q: string; year_from?: number; year_to?: number; limit?: number; offset?: number },
  enabled: boolean,
): UseQueryResult<SearchResponse> {
  return useQuery({
    queryKey: keys.paperSearch(args),
    queryFn: ({ signal }) => api.searchPapers({ ...args, limit: args.limit ?? 25 }, signal),
    enabled: enabled && args.q.trim().length >= 2,
    placeholderData: (prev) => prev,
    staleTime: 60_000,
  });
}

export function useRankedSearch(
  args: {
    q: string;
    rank: 'trust' | 'global';
    year_from?: number;
    year_to?: number;
    limit?: number;
    offset?: number;
  },
  enabled: boolean,
): UseQueryResult<RankedSearchResponse> {
  return useQuery({
    queryKey: keys.rankedSearch(args),
    queryFn: ({ signal }) => api.searchPapersRanked({ ...args, limit: args.limit ?? 25 }, signal),
    enabled: enabled && args.q.trim().length >= 2,
    placeholderData: (prev) => prev,
    staleTime: 60_000,
  });
}

export function useTrustSet(profileId: string): UseQueryResult<TrustListResponse> {
  return useQuery({
    queryKey: keys.trust(profileId),
    queryFn: ({ signal }) => api.listTrust(profileId, signal),
    staleTime: 10_000,
  });
}

/**
 * Live seed count. The session profile's `trust_count` is fetched once at
 * bootstrap and goes stale the moment a seed is added, so anything that gates
 * on "do you have seeds yet" reads the trust set itself.
 */
export function useSeedCount(profileId: string): {
  count: number;
  distrusted: number;
  isLoading: boolean;
} {
  const trust = useTrustSet(profileId);
  const items = trust.data?.items ?? [];
  return {
    count: items.filter((entry) => !entry.is_distrust).length,
    distrusted: items.filter((entry) => entry.is_distrust).length,
    isLoading: trust.isLoading,
  };
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

/* ------------------------------------------------------------------ */
/* Uploads                                                             */
/* ------------------------------------------------------------------ */

export function useUploads(profileId: string): UseQueryResult<UploadListResponse> {
  return useQuery({
    queryKey: keys.uploads(profileId),
    queryFn: ({ signal }) => api.listUploads(profileId, signal),
    staleTime: 10_000,
  });
}

export function useUpload(uploadId: string | null): UseQueryResult<Upload> {
  return useQuery({
    queryKey: keys.upload(uploadId ?? ''),
    queryFn: ({ signal }) => api.upload(uploadId as string, signal),
    enabled: Boolean(uploadId),
    staleTime: 10_000,
  });
}

/**
 * One detail query per upload, so the trust screen can attribute trust rows to
 * the upload that seeded them. The list is small (a user's own uploads).
 */
export function useUploadDetails(uploadIds: string[]): UseQueryResult<Upload>[] {
  return useQueries({
    queries: uploadIds.map((id) => ({
      queryKey: keys.upload(id),
      queryFn: ({ signal }: { signal?: AbortSignal }) => api.upload(id, signal),
      staleTime: 10_000,
    })),
  });
}

export function useCreateUpload(
  profileId: string,
): UseMutationResult<Upload, Error, File> {
  const qc = useQueryClient();
  return useMutation({
    // No retry: the POST parses and matches synchronously (10–60 s) and a
    // duplicate retry would just 409 against the content hash.
    retry: false,
    mutationFn: (file: File) => api.createUpload(file),
    onSuccess: (upload) => {
      qc.setQueryData(keys.upload(upload.id), upload);
      void qc.invalidateQueries({ queryKey: keys.uploads(profileId) });
    },
  });
}

export function usePatchUpload(
  uploadId: string,
): UseMutationResult<Upload, Error, UploadPatch> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: UploadPatch) => api.patchUpload(uploadId, body),
    onSuccess: (upload) => {
      qc.setQueryData(keys.upload(upload.id), upload);
      void qc.invalidateQueries({ queryKey: ['uploads'] });
    },
  });
}

export function usePatchUploadReference(
  uploadId: string,
): UseMutationResult<UploadReference, Error, { idx: number; body: UploadReferencePatch }> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ idx, body }) => api.patchUploadReference(uploadId, idx, body),
    onSuccess: (ref) => {
      qc.setQueryData<Upload>(keys.upload(uploadId), (prev) =>
        prev
          ? {
              ...prev,
              references: prev.references.map((r) => (r.idx === ref.idx ? ref : r)),
            }
          : prev,
      );
    },
  });
}

export function useConfirmUpload(
  profileId: string,
): UseMutationResult<UploadConfirmResponse, Error, string> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (uploadId: string) => api.confirmUpload(uploadId),
    onSuccess: (_data, uploadId) => {
      // A confirm is a bulk trust edit: everything downstream of the trust
      // set is stale, exactly as in useSetTrust.
      void qc.invalidateQueries({ queryKey: keys.upload(uploadId) });
      void qc.invalidateQueries({ queryKey: keys.uploads(profileId) });
      void qc.invalidateQueries({ queryKey: keys.trust(profileId) });
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

export function useUndoUpload(
  profileId: string,
): UseMutationResult<UploadUndoResponse, Error, string> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (uploadId: string) => api.undoUpload(uploadId),
    onSuccess: (_data, uploadId) => {
      qc.removeQueries({ queryKey: keys.upload(uploadId) });
      void qc.invalidateQueries({ queryKey: keys.uploads(profileId) });
      void qc.invalidateQueries({ queryKey: keys.trust(profileId) });
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
