import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { RankingTable } from '@/components/RankingTable';
import { describePosition } from '@/routes/Search';
import type { RankedSearchPaper, ScoredPaper } from '@/lib/types';

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
});
