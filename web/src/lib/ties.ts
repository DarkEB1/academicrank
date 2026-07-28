/**
 * Tie-group logic.
 *
 * The contract: items sharing a `tie_group` are statistically indistinguishable
 * and the UI *must* present them as tied. Ordering within a tie group is
 * arbitrary and the UI must never imply otherwise.
 */

export type TieAble = {
  uncertainty: { tie_group: number };
};

export type TieRun<T> = {
  tieGroup: number;
  /** index into the original list where this run begins */
  startIndex: number;
  items: T[];
  /** true when more than one item shares the group */
  tied: boolean;
  /** first row of the run */
  isFirst: (index: number) => boolean;
  /** last row of the run */
  isLast: (index: number) => boolean;
};

/**
 * Split a ranked list into contiguous runs of equal `tie_group`.
 *
 * Runs are contiguous by construction: a well-formed ranking sorts by score, so
 * a tie group cannot legitimately be interrupted. If the server ever returns an
 * interrupted group we render it as two separate runs rather than drawing a
 * bracket across unrelated rows — see `hasNonContiguousGroups`.
 */
export function groupTies<T extends TieAble>(items: T[]): TieRun<T>[] {
  const runs: TieRun<T>[] = [];
  let current: T[] = [];
  let currentGroup: number | null = null;
  let startIndex = 0;

  const flush = () => {
    if (currentGroup === null || current.length === 0) return;
    const group = currentGroup;
    const start = startIndex;
    const members = current;
    runs.push({
      tieGroup: group,
      startIndex: start,
      items: members,
      tied: members.length > 1,
      isFirst: (index: number) => index === start,
      isLast: (index: number) => index === start + members.length - 1,
    });
  };

  items.forEach((item, index) => {
    const group = item.uncertainty.tie_group;
    if (group !== currentGroup) {
      flush();
      current = [item];
      currentGroup = group;
      startIndex = index;
    } else {
      current.push(item);
    }
  });
  flush();

  return runs;
}

/** True if a tie group appears in more than one contiguous run (a server anomaly). */
export function hasNonContiguousGroups<T extends TieAble>(items: T[]): boolean {
  const runs = groupTies(items);
  const seen = new Set<number>();
  for (const run of runs) {
    if (seen.has(run.tieGroup)) return true;
    seen.add(run.tieGroup);
  }
  return false;
}

/**
 * Per-row tie metadata, keyed by position in the list. This is what the table
 * uses to draw brackets and to decide whether to show a rank number at all.
 */
export type TieMark = {
  tieGroup: number;
  tied: boolean;
  size: number;
  positionInRun: number;
  isFirst: boolean;
  isLast: boolean;
  /** Rank to display: the run's first rank, shared by everything in the run. */
  displayRankIndex: number;
};

export function tieMarks<T extends TieAble>(items: T[]): TieMark[] {
  const marks: TieMark[] = [];
  for (const run of groupTies(items)) {
    run.items.forEach((_, i) => {
      marks.push({
        tieGroup: run.tieGroup,
        tied: run.tied,
        size: run.items.length,
        positionInRun: i,
        isFirst: i === 0,
        isLast: i === run.items.length - 1,
        displayRankIndex: run.startIndex,
      });
    });
  }
  return marks;
}

export function areTied(a: TieAble, b: TieAble): boolean {
  return a.uncertainty.tie_group === b.uncertainty.tie_group;
}

/* ------------------------------------------------------------------ */
/* Copy                                                                */
/* ------------------------------------------------------------------ */

const NUMBER_WORDS = [
  'zero',
  'one',
  'two',
  'three',
  'four',
  'five',
  'six',
  'seven',
  'eight',
  'nine',
  'ten',
  'eleven',
  'twelve',
];

export function numberWord(n: number): string {
  return n >= 0 && n < NUMBER_WORDS.length ? NUMBER_WORDS[n] : String(n);
}

/** "these five are statistically tied" — the sentence the brief asks for. */
export function tieSentence(size: number): string {
  if (size <= 1) return 'This paper is not tied with its neighbours.';
  return `These ${numberWord(size)} are statistically tied — their intervals overlap, so the order between them is arbitrary.`;
}

export function tieBadgeLabel(size: number): string {
  return size <= 1 ? '' : `tied ×${size}`;
}

/**
 * Human-readable rank for a row: tied rows share the run's rank and are prefixed
 * with "=" in the manner of a league table.
 */
export function displayRank(items: { rank: number }[], marks: TieMark[], index: number): string {
  const mark = marks[index];
  if (!mark) return String(items[index]?.rank ?? index + 1);
  const rank = items[mark.displayRankIndex]?.rank ?? mark.displayRankIndex + 1;
  return mark.tied ? `=${rank}` : String(rank);
}

/** How many distinct, genuinely separable positions the list actually contains. */
export function distinguishablePositions<T extends TieAble>(items: T[]): number {
  return new Set(items.map((i) => i.uncertainty.tie_group)).size;
}
