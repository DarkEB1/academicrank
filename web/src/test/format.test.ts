import { describe, expect, it } from 'vitest';
import {
  barGeometry,
  decimalsForStderr,
  disagreementBand,
  domainFor,
  formatAuthors,
  formatInterval,
  formatPercentile,
  formatScore,
  formatScoreWithError,
  normalisePercentile,
  ordinal,
  relativeUncertainty,
  uncertaintyVerdict,
} from '@/lib/format';
import type { Uncertainty } from '@/lib/types';

const u = (over: Partial<Uncertainty> = {}): Uncertainty => ({
  stderr: 0.004,
  ci_low: 0.0063,
  ci_high: 0.0223,
  tie_group: 1,
  method: 'leave_one_out',
  n_samples: 40,
  ...over,
});

describe('precision is bounded by the error bar', () => {
  it('gives one significant place at the magnitude of the error', () => {
    expect(decimalsForStderr(0.004)).toBe(3);
    expect(decimalsForStderr(0.04)).toBe(2);
    expect(decimalsForStderr(0.4)).toBe(1);
    expect(decimalsForStderr(0.00004)).toBe(5);
  });

  it('does not print digits the uncertainty cannot support', () => {
    // 0.0143271 known only to ±0.004 must not be shown to seven places.
    expect(formatScore(0.0143271, 0.004)).toBe('0.014');
    expect(formatScore(0.0143271, 0.04)).toBe('0.01');
  });

  it('falls back to four places when no error is supplied', () => {
    expect(formatScore(0.0143271)).toBe('0.0143');
  });

  it('says "below the resolution" rather than printing a misleading zero', () => {
    expect(formatScore(0.0001, 0.04)).toBe('<0.01');
  });

  it('handles a zero standard error without dividing by zero', () => {
    expect(decimalsForStderr(0)).toBe(4);
    expect(formatScore(0.5, 0)).toBe('0.5000');
  });

  it('renders non-finite input as an em dash rather than NaN', () => {
    expect(formatScore(Number.NaN)).toBe('—');
  });
});

describe('a score always carries its uncertainty', () => {
  it('formats value and error at the same precision', () => {
    expect(formatScoreWithError(0.0143271, u())).toBe('0.014 ± 0.004');
  });

  it('formats the interval at the precision of the error', () => {
    expect(formatInterval(u())).toBe('[0.006, 0.022]');
  });
});

describe('uncertainty verdicts', () => {
  it('computes the relative width', () => {
    expect(relativeUncertainty(0.02, { stderr: 0.002 })).toBeCloseTo(0.1);
    expect(relativeUncertainty(0, { stderr: 0.002 })).toBe(Number.POSITIVE_INFINITY);
  });

  it('calls a tight interval tight', () => {
    expect(uncertaintyVerdict(0.02, { stderr: 0.002 })).toBe('tight');
  });

  it('calls a wide interval loose', () => {
    expect(uncertaintyVerdict(0.02, { stderr: 0.006 })).toBe('loose');
  });

  it('refuses to endorse an estimate swamped by its own error', () => {
    expect(uncertaintyVerdict(0.02, { stderr: 0.02 })).toBe('uninformative');
    expect(uncertaintyVerdict(0, { stderr: 0.02 })).toBe('uninformative');
  });
});

describe('percentiles accept either convention', () => {
  it('treats 0..1 as a fraction', () => {
    expect(normalisePercentile(0.83)).toBeCloseTo(0.83);
  });

  it('treats >1 as a percentage', () => {
    expect(normalisePercentile(83)).toBeCloseTo(0.83);
  });

  it('clamps out-of-range values', () => {
    expect(normalisePercentile(140)).toBe(1);
    expect(normalisePercentile(-3)).toBe(0);
  });

  it('formats an ordinal', () => {
    expect(formatPercentile(0.83)).toBe('83rd');
    expect(formatPercentile(0.11)).toBe('11th');
    expect(ordinal(1)).toBe('1st');
    expect(ordinal(12)).toBe('12th');
    expect(ordinal(22)).toBe('22nd');
  });
});

describe('error-bar geometry', () => {
  const domain = { min: 0, max: 1 };

  it('projects value and interval into the track', () => {
    const geo = barGeometry(0.5, { ci_low: 0.25, ci_high: 0.75 }, domain);
    expect(geo.center).toBeCloseTo(0.5);
    expect(geo.low).toBeCloseTo(0.25);
    expect(geo.high).toBeCloseTo(0.75);
    expect(geo.width).toBeCloseTo(0.5);
    expect(geo.clipped).toBe(false);
  });

  it('keeps a hairline interval visible', () => {
    const geo = barGeometry(0.5, { ci_low: 0.5, ci_high: 0.5 }, domain, 0.02);
    expect(geo.width).toBeCloseTo(0.02);
  });

  it('flags an interval that runs off the end of the scale', () => {
    const geo = barGeometry(0.5, { ci_low: -0.4, ci_high: 1.8 }, domain);
    expect(geo.clipped).toBe(true);
    expect(geo.low).toBe(0);
    expect(geo.high).toBe(1);
  });

  it('tolerates reversed interval bounds', () => {
    const geo = barGeometry(0.5, { ci_low: 0.75, ci_high: 0.25 }, domain);
    expect(geo.low).toBeCloseTo(0.25);
    expect(geo.high).toBeCloseTo(0.75);
  });

  it('does not divide by zero on a degenerate domain', () => {
    const geo = barGeometry(1, { ci_low: 1, ci_high: 1 }, { min: 1, max: 1 });
    expect(Number.isFinite(geo.center)).toBe(true);
  });
});

describe('shared domain across a page of scores', () => {
  it('covers every interval with padding', () => {
    const domain = domainFor([
      { trust: 0.2, uncertainty: { ci_low: 0.1, ci_high: 0.3 } },
      { trust: 0.8, uncertainty: { ci_low: 0.7, ci_high: 0.95 } },
    ]);
    expect(domain.min).toBeLessThan(0.1);
    expect(domain.max).toBeGreaterThan(0.95);
  });

  it('returns a usable domain for an empty page', () => {
    expect(domainFor([])).toEqual({ min: 0, max: 1 });
  });
});

describe('disagreement bands', () => {
  it('separates concordance from a stark split', () => {
    expect(disagreementBand(0.05)).toBe('concordant');
    expect(disagreementBand(0.3)).toBe('mild');
    expect(disagreementBand(0.5)).toBe('notable');
    expect(disagreementBand(0.9)).toBe('stark');
  });
});

describe('author lists', () => {
  it('does not claim a total it does not have', () => {
    const authors = [1, 2, 3, 4, 5, 6].map((n) => ({ name: `A${n}` }));
    expect(formatAuthors(authors, 3)).toBe('A1, A2, A3 and 3 more');
  });

  it('names an unattributed record honestly', () => {
    expect(formatAuthors([])).toBe('Unattributed');
  });
});
