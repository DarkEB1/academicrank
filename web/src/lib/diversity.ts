/**
 * The diversity dial.
 *
 * `diversity` is a single number 0..1 sent to /recommendations. 0 means
 * exploitation (nearest neighbours of your trust set); 1 means exploration
 * (high global merit, far from anything you already trust). The UI must make
 * the trade-off explicit rather than hiding it behind a word.
 */

export const DIVERSITY_MIN = 0;
export const DIVERSITY_MAX = 1;
export const DIVERSITY_STEP = 0.05;

export function clampDiversity(v: number): number {
  if (!Number.isFinite(v)) return 0.5;
  return Math.min(DIVERSITY_MAX, Math.max(DIVERSITY_MIN, v));
}

/** Snap to the slider's step so the URL and the request agree exactly. */
export function quantizeDiversity(v: number, step = DIVERSITY_STEP): number {
  const clamped = clampDiversity(v);
  const snapped = Math.round(clamped / step) * step;
  // Kill binary float dust: 0.30000000000000004 -> 0.3
  return Number(snapped.toFixed(4));
}

export type DiversityBand = 'exploit' | 'lean-exploit' | 'balanced' | 'lean-explore' | 'explore';

export function diversityBand(v: number): DiversityBand {
  const d = clampDiversity(v);
  if (d < 0.2) return 'exploit';
  if (d < 0.4) return 'lean-exploit';
  if (d <= 0.6) return 'balanced';
  if (d <= 0.8) return 'lean-explore';
  return 'explore';
}

export type DiversityDescription = {
  band: DiversityBand;
  label: string;
  /** What you get. */
  gain: string;
  /** What you give up. Always stated — this is the point of the control. */
  cost: string;
};

const DESCRIPTIONS: Record<DiversityBand, Omit<DiversityDescription, 'band'>> = {
  exploit: {
    label: 'Exploitation',
    gain: 'Papers sitting directly next to your trust set: the citations of the citations you already chose.',
    cost: 'You will see almost nothing you could not have found by hand. This setting reinforces what you already believe.',
  },
  'lean-exploit': {
    label: 'Mostly exploitation',
    gain: 'Close neighbours, with the occasional step outside the immediate neighbourhood.',
    cost: 'Still strongly bounded by your seeds. Whole subfields adjacent to yours stay invisible.',
  },
  balanced: {
    label: 'Balanced',
    gain: 'A mix of near neighbours and well-supported work further out in the graph.',
    cost: 'Neither the most relevant nor the most surprising list. Halfway is a choice, not a neutral default.',
  },
  'lean-explore': {
    label: 'Mostly exploration',
    gain: 'Work with high unpersonalised merit that your trust set does not reach directly.',
    cost: 'Precision drops. More of these will be irrelevant to what you are actually working on.',
  },
  explore: {
    label: 'Exploration',
    gain: 'Distant, highly-supported work — the parts of the literature your seeds are blind to.',
    cost: 'Relevance to your own work is largely abandoned. Expect a majority of misses, and judge the hits on their merits, not on their rank.',
  },
};

export function describeDiversity(v: number): DiversityDescription {
  const band = diversityBand(v);
  return { band, ...DESCRIPTIONS[band] };
}

/**
 * Two complementary percentages for the trade-off readout. Deliberately literal:
 * the dial is a single parameter and we show both ends of it rather than
 * inventing a composite "quality" number.
 */
export function tradeoffSplit(v: number): { exploitation: number; exploration: number } {
  const d = clampDiversity(v);
  const exploration = Math.round(d * 100);
  return { exploitation: 100 - exploration, exploration };
}

/** Accessible value text for the slider. */
export function diversityValueText(v: number): string {
  const { exploitation, exploration } = tradeoffSplit(v);
  const { label } = describeDiversity(v);
  return `${clampDiversity(v).toFixed(2)} — ${label}: ${exploitation}% exploitation, ${exploration}% exploration`;
}

/**
 * Novelty (returned per recommendation) described in words, so the list can be
 * read without decoding a second unexplained 0..1 number.
 */
export function noveltyLabel(novelty: number): string {
  if (!Number.isFinite(novelty)) return 'unknown distance';
  if (novelty < 0.25) return 'adjacent to your trust set';
  if (novelty < 0.5) return 'two steps out';
  if (novelty < 0.75) return 'distant from your trust set';
  return 'far outside your trust set';
}
