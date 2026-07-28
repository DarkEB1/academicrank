import { describe, expect, it } from 'vitest';
import {
  areTied,
  displayRank,
  distinguishablePositions,
  groupTies,
  hasNonContiguousGroups,
  numberWord,
  tieBadgeLabel,
  tieMarks,
  tieSentence,
} from '@/lib/ties';

type Row = { id: string; rank: number; uncertainty: { tie_group: number } };

const row = (id: string, rank: number, tie_group: number): Row => ({
  id,
  rank,
  uncertainty: { tie_group },
});

const list: Row[] = [
  row('a', 1, 7),
  row('b', 2, 7),
  row('c', 3, 7),
  row('d', 4, 8),
  row('e', 5, 9),
  row('f', 6, 9),
];

describe('grouping a ranked list into tie runs', () => {
  it('collects contiguous rows sharing a tie group', () => {
    const runs = groupTies(list);
    expect(runs).toHaveLength(3);
    expect(runs[0].items.map((r) => r.id)).toEqual(['a', 'b', 'c']);
    expect(runs[0].tied).toBe(true);
    expect(runs[1].items.map((r) => r.id)).toEqual(['d']);
    expect(runs[1].tied).toBe(false);
    expect(runs[2].tied).toBe(true);
  });

  it('records where each run starts', () => {
    const runs = groupTies(list);
    expect(runs.map((r) => r.startIndex)).toEqual([0, 3, 4]);
  });

  it('reports run boundaries by absolute index', () => {
    const [first] = groupTies(list);
    expect(first.isFirst(0)).toBe(true);
    expect(first.isLast(2)).toBe(true);
    expect(first.isLast(1)).toBe(false);
  });

  it('handles an empty list', () => {
    expect(groupTies([])).toEqual([]);
  });

  it('handles a list where everything is tied', () => {
    const all = [row('a', 1, 3), row('b', 2, 3), row('c', 3, 3)];
    const runs = groupTies(all);
    expect(runs).toHaveLength(1);
    expect(runs[0].items).toHaveLength(3);
  });

  it('never draws a bracket across an interrupted group', () => {
    const interrupted = [row('a', 1, 1), row('b', 2, 2), row('c', 3, 1)];
    const runs = groupTies(interrupted);
    expect(runs).toHaveLength(3);
    expect(hasNonContiguousGroups(interrupted)).toBe(true);
    expect(hasNonContiguousGroups(list)).toBe(false);
  });
});

describe('per-row tie marks', () => {
  it('produces one mark per row, in order', () => {
    const marks = tieMarks(list);
    expect(marks).toHaveLength(list.length);
    expect(marks.map((m) => m.tied)).toEqual([true, true, true, false, true, true]);
    expect(marks.map((m) => m.size)).toEqual([3, 3, 3, 1, 2, 2]);
  });

  it('points every row in a run at the first rank of that run', () => {
    const marks = tieMarks(list);
    expect(marks.map((m) => m.displayRankIndex)).toEqual([0, 0, 0, 3, 4, 4]);
  });

  it('marks first and last rows of each run', () => {
    const marks = tieMarks(list);
    expect(marks[0].isFirst).toBe(true);
    expect(marks[2].isLast).toBe(true);
    expect(marks[1].isFirst).toBe(false);
  });
});

describe('displayed rank', () => {
  it('shares one rank across a tie run and marks it with =', () => {
    const marks = tieMarks(list);
    expect(displayRank(list, marks, 0)).toBe('=1');
    expect(displayRank(list, marks, 2)).toBe('=1');
    expect(displayRank(list, marks, 3)).toBe('4');
    expect(displayRank(list, marks, 5)).toBe('=5');
  });
});

describe('how much order the list actually contains', () => {
  it('counts distinguishable positions, not rows', () => {
    expect(distinguishablePositions(list)).toBe(3);
    expect(distinguishablePositions([])).toBe(0);
  });
});

describe('tie copy', () => {
  it('says "these five are statistically tied"', () => {
    expect(tieSentence(5)).toContain('These five are statistically tied');
    expect(tieSentence(5)).toContain('arbitrary');
  });

  it('does not claim a single row is tied', () => {
    expect(tieSentence(1)).toBe('This paper is not tied with its neighbours.');
    expect(tieBadgeLabel(1)).toBe('');
    expect(tieBadgeLabel(4)).toBe('tied ×4');
  });

  it('spells small numbers and falls back to digits', () => {
    expect(numberWord(3)).toBe('three');
    expect(numberWord(40)).toBe('40');
  });
});

describe('pairwise ties', () => {
  it('is true only for equal tie groups', () => {
    expect(areTied(list[0], list[1])).toBe(true);
    expect(areTied(list[0], list[3])).toBe(false);
  });
});
