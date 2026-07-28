import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { ArrowDown, ArrowUp, ChevronsUpDown, Sparkles } from 'lucide-react';
import type { ScoredPaper } from '@/lib/types';
import { groupTies, tieSentence } from '@/lib/ties';
import {
  disagreementBand,
  domainFor,
  formatAuthors,
  formatCount,
  formatYear,
} from '@/lib/format';
import { PaperTitle } from './Math';
import { ScoreBar } from './ScoreBar';
import { Badge } from './ui/Badge';
import { Button } from './ui/Button';
import { cn } from '@/lib/cn';

export type SortKey = 'rank' | 'year' | 'citations' | 'trust' | 'disagreement';

const SORTABLE: { key: SortKey; label: string; numeric: boolean }[] = [
  { key: 'rank', label: 'Rank', numeric: true },
  { key: 'year', label: 'Year', numeric: true },
  { key: 'citations', label: 'Cited', numeric: true },
  { key: 'trust', label: 'Trust', numeric: true },
  { key: 'disagreement', label: 'Disagreement', numeric: true },
];

function sortValue(paper: ScoredPaper, key: SortKey): number {
  switch (key) {
    case 'year':
      return paper.year ?? -Infinity;
    case 'citations':
      return paper.cited_by_count;
    case 'trust':
      return paper.trust;
    case 'disagreement':
      return paper.disagreement;
    default:
      return paper.rank;
  }
}

export function RankingTable({
  items,
  onExplain,
  emptyLabel = 'No papers matched these filters.',
}: {
  items: ScoredPaper[];
  onExplain: (paper: ScoredPaper) => void;
  emptyLabel?: string;
}): JSX.Element {
  const [sort, setSort] = useState<{ key: SortKey; dir: 'asc' | 'desc' }>({
    key: 'rank',
    dir: 'asc',
  });

  const domain = useMemo(() => domainFor(items), [items]);

  const sorted = useMemo(() => {
    if (sort.key === 'rank' && sort.dir === 'asc') return items;
    const copy = [...items];
    copy.sort((a, b) => {
      const diff = sortValue(a, sort.key) - sortValue(b, sort.key);
      return sort.dir === 'asc' ? diff : -diff;
    });
    return copy;
  }, [items, sort]);

  const inRankOrder = sort.key === 'rank' && sort.dir === 'asc';
  const runs = useMemo(() => groupTies(sorted), [sorted]);

  const toggle = (key: SortKey) => {
    setSort((prev) =>
      prev.key === key
        ? { key, dir: prev.dir === 'asc' ? 'desc' : 'asc' }
        : { key, dir: key === 'rank' ? 'asc' : 'desc' },
    );
  };

  if (items.length === 0) {
    return <p className="px-1 py-10 text-center text-sm text-ink-muted">{emptyLabel}</p>;
  }

  return (
    <div className="space-y-3">
      {!inRankOrder ? (
        <p className="text-xs leading-relaxed text-caution">
          Sorted by {SORTABLE.find((s) => s.key === sort.key)?.label.toLowerCase()}. Tie brackets
          are only drawn in rank order, because a tie group is a statement about adjacent ranks.
          This sort applies to the loaded page only.
        </p>
      ) : null}

      <div className="overflow-x-auto">
        <table className="w-full min-w-[64rem] border-collapse text-left">
          <caption className="sr-only">
            Papers ranked by proximity to your trust set. Rows sharing a bracket are statistically
            tied and their relative order is arbitrary.
          </caption>
          <thead>
            <tr className="border-b border-rule-strong">
              <SortHeader
                label="Rank"
                sortKey="rank"
                sort={sort}
                onToggle={toggle}
                className="w-[5.5rem] pl-3"
              />
              <th scope="col" className="px-3 py-2 text-2xs font-medium uppercase tracking-[0.08em] text-ink-muted">
                Paper
              </th>
              <SortHeader label="Year" sortKey="year" sort={sort} onToggle={toggle} className="w-[5rem]" />
              <SortHeader
                label="Cited"
                sortKey="citations"
                sort={sort}
                onToggle={toggle}
                className="w-[6rem]"
              />
              <SortHeader
                label="Trust (95% interval)"
                sortKey="trust"
                sort={sort}
                onToggle={toggle}
                className="w-[13rem]"
              />
              <SortHeader
                label="Disagree"
                sortKey="disagreement"
                sort={sort}
                onToggle={toggle}
                className="w-[6.5rem]"
              />
              <th scope="col" className="w-[5.5rem] px-3 py-2">
                <span className="sr-only">Explanation</span>
              </th>
            </tr>
          </thead>

          {runs.map((run) => {
            const tied = inRankOrder && run.tied;
            return (
              <tbody
                key={`${run.tieGroup}-${run.startIndex}`}
                className={cn(
                  'align-top',
                  tied && 'border-l-2 border-l-accent/50 bg-accent-wash/25',
                )}
              >
                {tied ? (
                  <tr>
                    <td colSpan={7} className="px-3 pb-0 pt-3">
                      <p className="text-2xs uppercase tracking-[0.08em] text-accent">
                        {run.items.length} statistically tied — order below is arbitrary
                      </p>
                      <span className="sr-only">{tieSentence(run.items.length)}</span>
                    </td>
                  </tr>
                ) : null}

                {run.items.map((paper, indexInRun) => (
                  <RankingRow
                    key={paper.id}
                    paper={paper}
                    domain={domain}
                    tied={tied}
                    displayRank={tied ? `=${run.items[0].rank}` : String(paper.rank)}
                    showRank={!tied || indexInRun === 0}
                    lastInRun={indexInRun === run.items.length - 1}
                    onExplain={onExplain}
                  />
                ))}
              </tbody>
            );
          })}
        </table>
      </div>
    </div>
  );
}

function SortHeader({
  label,
  sortKey,
  sort,
  onToggle,
  className,
}: {
  label: string;
  sortKey: SortKey;
  sort: { key: SortKey; dir: 'asc' | 'desc' };
  onToggle: (key: SortKey) => void;
  className?: string;
}): JSX.Element {
  const active = sort.key === sortKey;
  return (
    <th
      scope="col"
      aria-sort={active ? (sort.dir === 'asc' ? 'ascending' : 'descending') : 'none'}
      className={cn('px-3 py-2', className)}
    >
      <button
        type="button"
        onClick={() => onToggle(sortKey)}
        className="inline-flex items-center gap-1 text-2xs font-medium uppercase tracking-[0.08em] text-ink-muted hover:text-ink"
      >
        {label}
        {active ? (
          sort.dir === 'asc' ? (
            <ArrowUp aria-hidden className="h-3 w-3" />
          ) : (
            <ArrowDown aria-hidden className="h-3 w-3" />
          )
        ) : (
          <ChevronsUpDown aria-hidden className="h-3 w-3 opacity-40" />
        )}
      </button>
    </th>
  );
}

function RankingRow({
  paper,
  domain,
  tied,
  displayRank,
  showRank,
  lastInRun,
  onExplain,
}: {
  paper: ScoredPaper;
  domain: { min: number; max: number };
  tied: boolean;
  displayRank: string;
  showRank: boolean;
  lastInRun: boolean;
  onExplain: (paper: ScoredPaper) => void;
}): JSX.Element {
  const band = disagreementBand(paper.disagreement);

  return (
    <tr
      className={cn(
        'group',
        lastInRun ? 'border-b border-rule' : 'border-b border-rule/40',
        'hover:bg-raised/50',
      )}
    >
      <td className="px-3 py-3.5 pl-3">
        {showRank ? (
          <span className="font-mono text-sm tnum text-ink-muted">{displayRank}</span>
        ) : (
          <span className="font-mono text-sm tnum text-ink-faint" aria-hidden>
            ⋮
          </span>
        )}
        {!showRank ? <span className="sr-only">tied at {displayRank}</span> : null}
      </td>

      <td className="max-w-[38rem] px-3 py-3.5">
        <Link
          to={`/paper/${paper.id}`}
          className="block rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
        >
          <PaperTitle
            as="span"
            title={paper.title}
            className="block text-[0.95rem] leading-snug text-ink group-hover:text-accent-ink dark:group-hover:text-accent"
          />
        </Link>
        <p className="mt-1 truncate text-xs text-ink-muted">
          {formatAuthors(paper.authors)}
          {paper.venue ? <span className="text-ink-faint"> · {paper.venue.name}</span> : null}
        </p>
        {paper.is_stub ? (
          <Badge tone="caution" className="mt-1.5" title="Metadata is incomplete in the corpus">
            stub record
          </Badge>
        ) : null}
      </td>

      <td className="px-3 py-3.5 font-mono text-xs tnum text-ink-muted">
        {formatYear(paper.year)}
      </td>

      <td className="px-3 py-3.5 font-mono text-xs tnum text-ink-muted">
        {formatCount(paper.cited_by_count)}
        <span className="block text-2xs text-ink-faint" title="Citations from inside this corpus">
          {formatCount(paper.in_corpus_cited_by)} here
        </span>
      </td>

      <td className="px-3 py-3">
        <ScoreBar value={paper.trust} uncertainty={paper.uncertainty} domain={domain} />
        {tied ? <span className="sr-only">Tied with adjacent rows.</span> : null}
      </td>

      <td className="px-3 py-3.5">
        {band === 'concordant' ? (
          <span className="font-mono text-2xs tnum text-ink-faint">
            {paper.disagreement.toFixed(2)}
          </span>
        ) : (
          <Badge
            tone={band === 'stark' ? 'caution' : 'neutral'}
            title="Spread across trust, global merit and citation percentiles"
          >
            {band === 'stark' ? <Sparkles aria-hidden className="h-3 w-3" /> : null}
            {paper.disagreement.toFixed(2)}
          </Badge>
        )}
      </td>

      <td className="px-3 py-3">
        <Button
          size="sm"
          onClick={() => onExplain(paper)}
          aria-label={`Explain the score for ${paper.title ?? 'this paper'}`}
        >
          Explain
        </Button>
      </td>
    </tr>
  );
}
