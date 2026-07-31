import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, useNavigate } from 'react-router-dom';
import type { UseMutationResult, UseQueryResult } from '@tanstack/react-query';
import { RankingTable } from '@/components/RankingTable';
import { coldStartBanner, describePosition, SearchScreen } from '@/routes/Search';
import { usePaperSearch, useRankedSearch, useSeedCount, useSetTrust } from '@/lib/queries';
import { useSession } from '@/lib/session';
import type {
  ColdStart,
  ProfileMe,
  RankedSearchPaper,
  RankedSearchResponse,
  ScoredPaper,
  SearchResponse,
} from '@/lib/types';

vi.mock('@/lib/queries', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/queries')>();
  return {
    ...actual,
    usePaperSearch: vi.fn(),
    useRankedSearch: vi.fn(),
    useSetTrust: vi.fn(),
    useSeedCount: vi.fn(),
  };
});

vi.mock('@/lib/session', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/session')>();
  return { ...actual, useSession: vi.fn() };
});

const base: ScoredPaper = {
  id: 'W1',
  title: 'A paper',
  year: 2019,
  authors: [],
  venue: null,
  cited_by_count: 10,
  in_corpus_cited_by: 3,
  is_stub: false,
  doi: null,
  trust: 0.01,
  uncertainty: {
    stderr: 0.001, ci_low: 0.008, ci_high: 0.012,
    tie_group: 1, method: 'leave_one_out', n_samples: 5,
  },
  global_merit: 0.02,
  rank: 1,
  disagreement: 0.1,
  lift: 0,
  lift_uncertainty: null,
};

const rankedPaper: RankedSearchPaper = { ...base, relevance_rank: 1, merit_rank: 1 };

const profile: ProfileMe = { id: 'p1', label: null, params: {}, trust_count: 0, warmed_at: null };

function queryResult<T>(over: { data?: T } & Record<string, unknown> = {}): UseQueryResult<T> {
  return {
    data: undefined,
    isLoading: false,
    isError: false,
    isFetching: false,
    error: null,
    refetch: vi.fn(),
    ...over,
  } as unknown as UseQueryResult<T>;
}

function mutationResult<T, V>(over: Record<string, unknown> = {}): UseMutationResult<T, Error, V> {
  return {
    mutate: vi.fn(),
    isPending: false,
    isError: false,
    error: null,
    ...over,
  } as unknown as UseMutationResult<T, Error, V>;
}

/** Wires up the mocked hooks with sane defaults every SearchScreen test needs. */
function mockScreenDefaults(): void {
  vi.mocked(useSession).mockReturnValue({
    status: 'ready',
    profile,
    error: null,
    profileId: profile.id,
    retry: vi.fn(),
    reset: vi.fn(),
  } as never);
  vi.mocked(useSeedCount).mockReturnValue({ count: 0, distrusted: 0, isLoading: false });
  vi.mocked(useSetTrust).mockReturnValue(mutationResult());
  vi.mocked(usePaperSearch).mockReturnValue(queryResult<SearchResponse>());
}

function rankedResponse(over: Partial<RankedSearchResponse> = {}): RankedSearchResponse {
  return {
    total: 1,
    items: [rankedPaper],
    cold_start: { seeds: 0, reliable: true, message: null },
    disclaimer: 'Scores measure proximity in a trust graph, not quality.',
    rank: 'global',
    ...over,
  };
}

describe('ranked search presentation', () => {
  it('explains a position from its two component ranks', () => {
    const p: RankedSearchPaper = { ...base, relevance_rank: 2, merit_rank: 14 };
    const s = describePosition(p);
    expect(s).toMatch(/2\w* by text relevance/i);
    expect(s).toMatch(/14\w* by merit/i);
  });

  it('renders an actions column when renderActions is provided', () => {
    render(
      <MemoryRouter>
        <RankingTable
          items={[base]}
          onExplain={() => undefined}
          renderActions={(paper) => <button type="button">Trust {paper.id}</button>}
        />
      </MemoryRouter>,
    );
    expect(screen.getByRole('button', { name: 'Trust W1' })).toBeInTheDocument();
  });

  it('tags each ranked row with data-testid and data-work-id for the e2e spec', () => {
    render(
      <MemoryRouter>
        <RankingTable
          items={[base]}
          onExplain={() => undefined}
          rowProps={(paper) => ({ 'data-testid': 'search-result', 'data-work-id': paper.id })}
        />
      </MemoryRouter>,
    );
    const row = screen.getByTestId('search-result');
    expect(row.tagName).toBe('TR');
    expect(row).toHaveAttribute('data-work-id', 'W1');
  });

  it('shows the Lift column by default (existing Rankings/Recommendations behaviour)', () => {
    render(
      <MemoryRouter>
        <RankingTable items={[base]} onExplain={() => undefined} />
      </MemoryRouter>,
    );
    expect(screen.getByRole('button', { name: /lift/i })).toBeInTheDocument();
    expect(screen.getByText('+0.00')).toBeInTheDocument();
  });

  it('omits the Lift column with hideLift, so ranked search never fabricates a score', () => {
    render(
      <MemoryRouter>
        <RankingTable items={[base]} onExplain={() => undefined} hideLift />
      </MemoryRouter>,
    );
    expect(screen.queryByRole('button', { name: /lift/i })).not.toBeInTheDocument();
    expect(screen.queryByText('+0.00')).not.toBeInTheDocument();
  });
});

describe('coldStartBanner: never synthesizes reliability copy the server did not send', () => {
  it('renders a neutral banner with no invented title when the server says reliable:true', () => {
    const coldStart: ColdStart = { seeds: 0, reliable: true, message: 'This ordering is unpersonalised.' };
    expect(coldStartBanner('global', 'global', coldStart)).toEqual({
      tone: 'neutral',
      title: 'How this ordering works',
    });
  });

  it('uses the seed-count caution framing when reliable:false and not a trust->global fallback', () => {
    const coldStart: ColdStart = { seeds: 2, reliable: false, message: 'x' };
    const banner = coldStartBanner('global', 'global', coldStart);
    expect(banner?.tone).toBe('caution');
    expect(banner?.title).toMatch(/2 seeds: this ranking is not reliable/);
  });

  it('names the fallback explicitly when a trust request degrades to global', () => {
    const coldStart: ColdStart = { seeds: 0, reliable: false, message: 'x' };
    const banner = coldStartBanner('trust', 'global', coldStart);
    expect(banner?.tone).toBe('caution');
    expect(banner?.title).toMatch(/your trust set is not ready yet/i);
  });

  it('returns null when there is no message to show', () => {
    expect(coldStartBanner('global', 'global', { seeds: 0, reliable: true, message: null })).toBeNull();
  });
});

describe('SearchScreen: ranked-mode banner honesty and result tagging', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockScreenDefaults();
  });

  it('renders the informational message with no "not reliable" wording for a plain, reliable global response', () => {
    const message = 'This ordering is unpersonalised: it blends text relevance with graph-wide merit.';
    vi.mocked(useRankedSearch).mockReturnValue(
      queryResult<RankedSearchResponse>({
        data: rankedResponse({ cold_start: { seeds: 0, reliable: true, message } }),
      }),
    );

    render(
      <MemoryRouter initialEntries={['/search?q=neural+networks&mode=global']}>
        <SearchScreen />
      </MemoryRouter>,
    );

    expect(screen.getByText(message)).toBeInTheDocument();
    expect(screen.queryByText(/not reliable/i)).not.toBeInTheDocument();
    expect(screen.getByRole('note').className).not.toMatch(/caution/);
  });

  it('renders the fallback message when a trust request degrades to global (reliable:false)', () => {
    const message = 'You have no trust seeds yet, so results are ordered by global merit instead.';
    vi.mocked(useRankedSearch).mockReturnValue(
      queryResult<RankedSearchResponse>({
        data: rankedResponse({ cold_start: { seeds: 0, reliable: false, message } }),
      }),
    );

    render(
      <MemoryRouter initialEntries={['/search?q=neural+networks&mode=trust']}>
        <SearchScreen />
      </MemoryRouter>,
    );

    expect(screen.getByText(message)).toBeInTheDocument();
    expect(screen.getByText(/your trust set is not ready yet/i)).toBeInTheDocument();
    expect(screen.getByRole('note').className).toMatch(/caution/);
  });

  it('renders ranked rows tagged with data-testid/data-work-id for the e2e spec', () => {
    vi.mocked(useRankedSearch).mockReturnValue(
      queryResult<RankedSearchResponse>({ data: rankedResponse() }),
    );

    render(
      <MemoryRouter initialEntries={['/search?q=neural+networks&mode=global']}>
        <SearchScreen />
      </MemoryRouter>,
    );

    const rows = screen.getAllByTestId('search-result');
    expect(rows).toHaveLength(1);
    expect(rows[0]).toHaveAttribute('data-work-id', rankedPaper.id);
  });
});

describe('SearchScreen: the URL q param stays in sync in both directions', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockScreenDefaults();
    vi.mocked(useRankedSearch).mockReturnValue(queryResult<RankedSearchResponse>());
  });

  function NavigateButton({ to }: { to: string }): JSX.Element {
    const navigate = useNavigate();
    return (
      <button type="button" onClick={() => navigate(to)}>
        simulate back/forward
      </button>
    );
  }

  it('reconciles the input when the URL q changes externally, not just written to it', async () => {
    const user = userEvent.setup();

    render(
      <MemoryRouter initialEntries={['/search?q=alpha&mode=global']}>
        <NavigateButton to="/search?q=beta&mode=global" />
        <SearchScreen />
      </MemoryRouter>,
    );

    expect(screen.getByLabelText('Search papers')).toHaveValue('alpha');

    await user.click(screen.getByText('simulate back/forward'));

    expect(screen.getByLabelText('Search papers')).toHaveValue('beta');
  });
});
