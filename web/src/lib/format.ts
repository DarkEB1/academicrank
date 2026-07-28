import type { Uncertainty } from './types';

/**
 * Honest number formatting.
 *
 * The governing rule: never print more precision than the error bar supports.
 * A score of 0.0143271 with a standard error of 0.004 is "0.014"; the further
 * digits are noise and printing them is a lie about what we know.
 */

/** Decimal places justified by a given standard error. */
export function decimalsForStderr(stderr: number): number {
  if (!Number.isFinite(stderr) || stderr <= 0) return 4;
  // One digit past the leading digit of the error: 0.004 -> 3 dp, 0.04 -> 2 dp.
  const magnitude = Math.floor(Math.log10(stderr));
  return Math.min(8, Math.max(0, -magnitude));
}

/** Format a raw score to the precision its uncertainty justifies. */
export function formatScore(value: number, stderr?: number): string {
  if (!Number.isFinite(value)) return '—';
  const dp = stderr === undefined ? 4 : decimalsForStderr(stderr);
  if (value !== 0 && Math.abs(value) < Math.pow(10, -dp)) {
    // Too small to show at the justified precision; say so rather than "0.000".
    return `<${Math.pow(10, -dp).toFixed(dp)}`;
  }
  return value.toFixed(dp);
}

/** "0.0143 ± 0.0040" — a score is never rendered without this. */
export function formatScoreWithError(value: number, u: Pick<Uncertainty, 'stderr'>): string {
  return `${formatScore(value, u.stderr)} ± ${formatScore(u.stderr, u.stderr)}`;
}

/** "[0.0103, 0.0183]" */
export function formatInterval(u: Pick<Uncertainty, 'ci_low' | 'ci_high' | 'stderr'>): string {
  return `[${formatScore(u.ci_low, u.stderr)}, ${formatScore(u.ci_high, u.stderr)}]`;
}

/**
 * How wide the interval is relative to the estimate. Above ~0.5 the estimate is
 * doing very little work and the UI says so out loud.
 */
export function relativeUncertainty(value: number, u: Pick<Uncertainty, 'stderr'>): number {
  if (!Number.isFinite(value) || value === 0) return Number.POSITIVE_INFINITY;
  return Math.abs(u.stderr / value);
}

export type UncertaintyVerdict = 'tight' | 'loose' | 'uninformative';

export function uncertaintyVerdict(value: number, u: Pick<Uncertainty, 'stderr'>): UncertaintyVerdict {
  const r = relativeUncertainty(value, u);
  if (r < 0.15) return 'tight';
  if (r < 0.5) return 'loose';
  return 'uninformative';
}

export const UNCERTAINTY_COPY: Record<UncertaintyVerdict, string> = {
  tight: 'The interval is narrow relative to the estimate.',
  loose: 'The interval is wide: treat the exact position with scepticism.',
  uninformative:
    'The interval is as large as the estimate itself. This number should not be used to order anything.',
};

export const METHOD_COPY: Record<Uncertainty['method'], string> = {
  leave_one_out:
    'Uncertainty estimated by leave-one-out over your trust seeds: each seed was removed in turn and the score recomputed.',
  repeat_sample:
    'Uncertainty estimated by repeated random-walk sampling: the walk was rerun and the spread of results measured.',
};

/* ------------------------------------------------------------------ */
/* Percentiles                                                         */
/* ------------------------------------------------------------------ */

/**
 * The contract types percentiles as plain `number` without stating a range.
 * Accept either convention and normalise to 0..1 (see FRONTEND_NOTES.md).
 */
export function normalisePercentile(p: number): number {
  if (!Number.isFinite(p)) return 0;
  const v = p > 1 ? p / 100 : p;
  return Math.min(1, Math.max(0, v));
}

export function formatPercentile(p: number): string {
  const v = normalisePercentile(p) * 100;
  if (v >= 99.5 && v < 100) return '99th';
  return `${ordinal(Math.round(v))}`;
}

export function ordinal(n: number): string {
  const rem100 = n % 100;
  if (rem100 >= 11 && rem100 <= 13) return `${n}th`;
  switch (n % 10) {
    case 1:
      return `${n}st`;
    case 2:
      return `${n}nd`;
    case 3:
      return `${n}rd`;
    default:
      return `${n}th`;
  }
}

/* ------------------------------------------------------------------ */
/* Error-bar geometry                                                  */
/* ------------------------------------------------------------------ */

export type BarGeometry = {
  /** 0..1 position of the point estimate within the domain */
  center: number;
  /** 0..1 position of the interval start */
  low: number;
  /** 0..1 position of the interval end */
  high: number;
  /** high - low, never below `minWidth` so a hairline interval stays visible */
  width: number;
  /** true when the interval was clipped by the domain */
  clipped: boolean;
};

/**
 * Map a value and its interval into a 0..1 track. Used by ScoreBar so that every
 * score on a page shares one scale and the error bars are comparable.
 */
export function barGeometry(
  value: number,
  u: Pick<Uncertainty, 'ci_low' | 'ci_high'>,
  domain: { min: number; max: number },
  minWidth = 0.01,
): BarGeometry {
  const span = domain.max - domain.min;
  const project = (v: number): number => {
    if (!Number.isFinite(v)) return 0;
    if (span <= 0) return 0.5;
    return (v - domain.min) / span;
  };
  const rawLow = project(Math.min(u.ci_low, u.ci_high));
  const rawHigh = project(Math.max(u.ci_low, u.ci_high));
  const clipped = rawLow < 0 || rawHigh > 1;
  const low = Math.min(1, Math.max(0, rawLow));
  let high = Math.min(1, Math.max(0, rawHigh));
  if (high - low < minWidth) high = Math.min(1, low + minWidth);
  return {
    center: Math.min(1, Math.max(0, project(value))),
    low,
    high,
    width: high - low,
    clipped,
  };
}

/** Domain covering every interval in a list, padded so nothing touches the edge. */
export function domainFor(
  items: { trust: number; uncertainty: Pick<Uncertainty, 'ci_low' | 'ci_high'> }[],
  pad = 0.06,
): { min: number; max: number } {
  if (items.length === 0) return { min: 0, max: 1 };
  let min = Number.POSITIVE_INFINITY;
  let max = Number.NEGATIVE_INFINITY;
  for (const item of items) {
    min = Math.min(min, item.uncertainty.ci_low, item.trust);
    max = Math.max(max, item.uncertainty.ci_high, item.trust);
  }
  if (!Number.isFinite(min) || !Number.isFinite(max)) return { min: 0, max: 1 };
  if (max === min) return { min: min - 0.5, max: max + 0.5 };
  const padding = (max - min) * pad;
  return { min: min - padding, max: max + padding };
}

/* ------------------------------------------------------------------ */
/* Misc display helpers                                                */
/* ------------------------------------------------------------------ */

const counter = new Intl.NumberFormat('en-GB');

export function formatCount(n: number): string {
  if (!Number.isFinite(n)) return '—';
  return counter.format(Math.round(n));
}

export function formatAuthors(
  authors: { name: string }[],
  max = 3,
): string {
  if (authors.length === 0) return 'Unattributed';
  const names = authors.slice(0, max).map((a) => a.name);
  const rest = authors.length - names.length;
  // The contract caps `authors` at 6, so "+n more" is a floor, not a total.
  return rest > 0 ? `${names.join(', ')} and ${rest} more` : names.join(', ');
}

export function formatYear(year: number | null): string {
  return year === null ? 'undated' : String(year);
}

export function formatMillis(ms: number): string {
  if (!Number.isFinite(ms)) return '—';
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(1)} s`;
}

export function formatSignedRank(delta: number): string {
  if (delta === 0) return 'no change';
  return delta > 0 ? `+${delta}` : String(delta);
}

/* ------------------------------------------------------------------ */
/* Disagreement                                                        */
/* ------------------------------------------------------------------ */

export type DisagreementBand = 'concordant' | 'mild' | 'notable' | 'stark';

export function disagreementBand(d: number): DisagreementBand {
  if (!Number.isFinite(d) || d < 0.2) return 'concordant';
  if (d < 0.45) return 'mild';
  if (d < 0.7) return 'notable';
  return 'stark';
}

export const DISAGREEMENT_COPY: Record<DisagreementBand, string> = {
  concordant:
    'Your trust graph, unpersonalised merit and raw citations broadly agree about this paper.',
  mild: 'Your trust graph and the field disagree slightly about this paper.',
  notable:
    'Your trust graph and the field disagree substantially about this paper. Worth a look at why.',
  stark:
    'Your trust graph and the field disagree sharply about this paper. These are the most interesting papers in the system — and the ones most likely to expose a flaw in the model.',
};
