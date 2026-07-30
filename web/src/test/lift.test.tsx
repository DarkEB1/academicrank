import { describe, expect, it } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { RankingTable } from '@/components/RankingTable';
import type { ScoredPaper, Uncertainty } from '@/lib/types';

function renderTable(items: ScoredPaper[]) {
  return render(
    <MemoryRouter>
      <RankingTable items={items} onExplain={() => undefined} />
    </MemoryRouter>,
  );
}

const unc = (tie: number): Uncertainty => ({
  stderr: 0.001,
  ci_low: 0.001,
  ci_high: 0.005,
  tie_group: tie,
  method: 'leave_one_out',
  n_samples: 5,
});

const liftUnc: Uncertainty = {
  stderr: 0.3,
  ci_low: -0.2,
  ci_high: 1.0,
  tie_group: 0,
  method: 'leave_one_out',
  n_samples: 5,
};

function paper(id: string, rank: number, trust: number, lift: number): ScoredPaper {
  return {
    id,
    title: `Paper ${id}`,
    year: 2001,
    authors: [],
    venue: null,
    cited_by_count: 10,
    in_corpus_cited_by: 4,
    is_stub: false,
    doi: null,
    trust,
    uncertainty: unc(rank),
    global_merit: 0.001,
    rank,
    disagreement: 0.1,
    lift,
    lift_uncertainty: liftUnc,
  };
}

describe('lift column', () => {
  it('renders a signed lift value with its spread', () => {
    renderTable([paper('W1', 1, 0.01, 0.42), paper('W2', 2, 0.005, -0.17)]);
    expect(screen.getByText('+0.42')).toBeInTheDocument();
    expect(screen.getByText('-0.17')).toBeInTheDocument();
    expect(screen.getAllByText('± 0.30').length).toBe(2);
  });

  it('sorts by lift when the header is toggled', () => {
    renderTable([paper('W1', 1, 0.01, 0.1), paper('W2', 2, 0.005, 0.9)]);
    fireEvent.click(screen.getByRole('button', { name: /lift/i }));
    const cells = screen.getAllByText(/Paper W/);
    // descending lift puts W2 (0.9) first
    expect(cells[0]).toHaveTextContent('Paper W2');
  });
});
