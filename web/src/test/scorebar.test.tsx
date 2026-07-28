import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ScoreBar, ScoreReadout } from '@/components/ScoreBar';
import { Disclaimer } from '@/components/Honesty';
import type { Uncertainty } from '@/lib/types';

const uncertainty: Uncertainty = {
  stderr: 0.004,
  ci_low: 0.0063,
  ci_high: 0.0223,
  tie_group: 3,
  method: 'leave_one_out',
  n_samples: 40,
};

describe('no bare scores', () => {
  it('announces the interval alongside the value', () => {
    render(
      <ScoreBar value={0.0143} uncertainty={uncertainty} domain={{ min: 0, max: 0.05 }} />,
    );
    const bar = screen.getByRole('img');
    expect(bar).toHaveAccessibleName(/0\.014/);
    expect(bar).toHaveAccessibleName(/\[0\.006, 0\.022\]/);
  });

  it('warns in the accessible name when the interval leaves the visible range', () => {
    render(
      <ScoreBar value={0.0143} uncertainty={uncertainty} domain={{ min: 0.01, max: 0.015 }} />,
    );
    expect(screen.getByRole('img')).toHaveAccessibleName(/extends beyond the visible range/);
  });

  it('prints the error next to the value in the readout', () => {
    render(<ScoreReadout value={0.0143} uncertainty={uncertainty} />);
    expect(screen.getByText(/± 0\.004/)).toBeInTheDocument();
  });
});

describe('server disclaimers', () => {
  it('renders the string verbatim', () => {
    const text = 'Scores measure proximity in a trust graph, not quality.';
    render(<Disclaimer text={text} />);
    expect(screen.getByTestId('disclaimer')).toHaveTextContent(text);
  });

  it('renders nothing when the server sent no disclaimer', () => {
    const { container } = render(<Disclaimer text={null} />);
    expect(container).toBeEmptyDOMElement();
  });
});
