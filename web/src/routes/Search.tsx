import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { Search as SearchIcon, X } from 'lucide-react';
import type { ColdStart, PaperBrief, RankedSearchPaper, RankMode, ScoredPaper } from '@/lib/types';
import { usePaperSearch, useRankedSearch, useSeedCount, useSetTrust } from '@/lib/queries';
import { useSession } from '@/lib/session';
import { useDebounced } from '@/lib/hooks';
import { formatAuthors, formatCount, formatYear } from '@/lib/format';
import { RankingTable } from '@/components/RankingTable';
import { Disclaimer, Notice } from '@/components/Honesty';
import { ErrorState } from '@/components/States';
import { TableRowSkeleton } from '@/components/ui/Skeleton';
import { StrengthPicker, STRENGTH_LABELS } from '@/components/StrengthPicker';
import { SidePanel } from '@/components/ui/Dialog';
import { ExplainContent } from '@/components/ExplainPanel';
import { Field, Input } from '@/components/ui/Input';
import { Button, buttonClass } from '@/components/ui/Button';
import { PaperTitle } from '@/components/Math';
import { cn } from '@/lib/cn';

const DEFAULT_STRENGTH = 3;

const MODES: { value: RankMode; label: string; gloss: string }[] = [
  { value: 'relevance', label: 'Relevance', gloss: 'Text match alone — the classic picker order.' },
  {
    value: 'trust',
    label: 'Your trust',
    gloss: 'Text matches re-ordered by blending relevance with proximity to your trust set (RRF).',
  },
  {
    value: 'global',
    label: 'Global merit',
    gloss: 'Text matches re-ordered by blending relevance with unpersonalised merit (RRF).',
  },
];

/** "2nd by text relevance, 14th by merit": why a row sits where it sits. */
export function describePosition(p: RankedSearchPaper): string {
  const ord = (n: number): string => {
    if (n % 100 >= 11 && n % 100 <= 13) return `${n}th`;
    const suffix = { 1: 'st', 2: 'nd', 3: 'rd' }[n % 10] ?? 'th';
    return `${n}${suffix}`;
  };
  return `${ord(p.relevance_rank)} by text relevance, ${ord(p.merit_rank)} by merit`;
}

export type ColdStartBanner = { tone: 'caution' | 'neutral'; title: string };

/**
 * How to present `cold_start` above a ranked-search table, if at all.
 *
 * The server's `reliable` flag is the only thing allowed to trigger the
 * caution/"not reliable" framing — a `global` response is `reliable: true`
 * with an informational message ("this ordering is unpersonalised") even
 * though `message` is non-null, and that must never be dressed up as a
 * warning the server did not send.
 */
export function coldStartBanner(
  mode: RankMode,
  rank: 'trust' | 'global',
  coldStart: ColdStart,
): ColdStartBanner | null {
  if (!coldStart.message) return null;
  if (!coldStart.reliable) {
    return {
      tone: 'caution',
      title:
        mode === 'trust' && rank === 'global'
          ? 'Showing global merit — your trust set is not ready yet'
          : `${coldStart.seeds} seed${coldStart.seeds === 1 ? '' : 's'}: this ranking is not reliable`,
    };
  }
  return { tone: 'neutral', title: 'How this ordering works' };
}

function NoMatches({ hint }: { hint?: string }): JSX.Element {
  return (
    <p className="px-1 py-10 text-center text-sm text-ink-muted">
      No matches.{hint ? ` ${hint}` : ''}
    </p>
  );
}

export function SearchScreen(): JSX.Element {
  const { profile } = useSession();
  const profileId = profile?.id ?? '';
  const setTrust = useSetTrust(profileId);
  const seedCount = useSeedCount(profileId);

  const [params, setParams] = useSearchParams();
  const [explaining, setExplaining] = useState<ScoredPaper | RankedSearchPaper | null>(null);
  const [pendingStrength, setPendingStrength] = useState<Record<string, number>>({});

  const q = params.get('q') ?? '';
  const mode = (params.get('mode') as RankMode | null) ?? 'relevance';
  const limit = Number(params.get('limit') ?? 25);
  const offset = Number(params.get('offset') ?? 0);
  const yearFrom = params.get('year_from');
  const yearTo = params.get('year_to');

  const [inputValue, setInputValue] = useState(q);
  const debouncedQ = useDebounced(inputValue, 250);
  // Tracks the last `q` this component itself pushed to the URL, so an
  // external change to the URL (back/forward, a pasted link) can be told
  // apart from our own debounced write and reconciled back into the input.
  const lastWrittenQ = useRef(q);

  const update = (patch: Record<string, string | null>) => {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(patch)) {
      if (value === null || value === '') next.delete(key);
      else next.set(key, value);
    }
    if (!('offset' in patch)) next.delete('offset');
    setParams(next, { replace: true });
  };

  // Local input -> URL: the input is local so every keystroke does not
  // rewrite the URL; only the debounced value lands there (and drives the
  // queries).
  useEffect(() => {
    if (debouncedQ === lastWrittenQ.current) return;
    lastWrittenQ.current = debouncedQ;
    if (debouncedQ !== q) update({ q: debouncedQ || null });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedQ]);

  // URL -> local input: browser back/forward (or a deep link) changes `q`
  // without ever touching the input, so reconcile the other direction too —
  // otherwise the box (and the results it drives) go stale after navigation.
  useEffect(() => {
    if (q === lastWrittenQ.current) return;
    lastWrittenQ.current = q;
    setInputValue(q);
  }, [q]);

  const searchArgs = useMemo(
    () => ({
      q: debouncedQ,
      year_from: yearFrom ? Number(yearFrom) : undefined,
      year_to: yearTo ? Number(yearTo) : undefined,
      limit,
      offset,
    }),
    [debouncedQ, yearFrom, yearTo, limit, offset],
  );

  const relevance = usePaperSearch(searchArgs, mode === 'relevance');
  const ranked = useRankedSearch(
    { ...searchArgs, rank: mode === 'trust' ? 'trust' : 'global' },
    mode !== 'relevance',
  );
  const active = mode === 'relevance' ? relevance : ranked;

  const trimmed = debouncedQ.trim();
  const tooShort = trimmed.length > 0 && trimmed.length < 2;
  const rankedData = ranked.data;
  const relevanceData = relevance.data;
  const banner = rankedData
    ? coldStartBanner(mode, rankedData.rank, rankedData.cold_start)
    : null;
  const total = mode === 'relevance' ? relevanceData?.total ?? 0 : rankedData?.total ?? 0;
  const itemsLength =
    mode === 'relevance' ? relevanceData?.items.length ?? 0 : rankedData?.items.length ?? 0;

  const addTrust = (paper: PaperBrief, strength: number): void =>
    setTrust.mutate({ work_id: paper.id, strength });

  const quickTrust = (paper: ScoredPaper): JSX.Element => (
    <div className="flex items-center gap-1.5">
      <StrengthPicker
        value={pendingStrength[paper.id] ?? DEFAULT_STRENGTH}
        onChange={(value) => setPendingStrength((prev) => ({ ...prev, [paper.id]: value }))}
        size="sm"
        disabled={setTrust.isPending}
        label={`Strength for ${paper.title ?? 'this paper'}`}
      />
      <Button
        size="sm"
        variant="primary"
        onClick={() => addTrust(paper, pendingStrength[paper.id] ?? DEFAULT_STRENGTH)}
        disabled={setTrust.isPending}
        title="Add to your trust set"
      >
        Trust
      </Button>
    </div>
  );

  return (
    <div className="space-y-8">
      <header className="space-y-3">
        <h1 className="font-serif text-2xl tracking-tight text-ink">Search</h1>
        <p className="max-w-measure text-sm leading-relaxed text-ink-muted">
          Full-text search over the corpus, optionally re-ordered by proximity to your trust set
          or to unpersonalised merit via Reciprocal Rank Fusion — it never invents a score for a
          paper the text search itself did not surface.
        </p>
      </header>

      <section
        aria-label="Search controls"
        className="space-y-4 rounded-sm border border-rule bg-surface px-5 py-4"
      >
        <Input
          type="search"
          value={inputValue}
          onChange={(e) => setInputValue(e.target.value)}
          placeholder="Search titles and abstracts…"
          aria-label="Search papers"
          leading={<SearchIcon aria-hidden className="h-4 w-4" />}
        />

        {tooShort ? <p className="text-xs text-ink-muted">Type at least two characters.</p> : null}

        <div className="flex flex-wrap items-end justify-between gap-4">
          <div
            role="radiogroup"
            aria-label="Ranking mode"
            className="inline-flex overflow-hidden rounded-sm border border-rule-strong"
          >
            {MODES.map((m) => (
              <button
                key={m.value}
                type="button"
                role="radio"
                aria-checked={mode === m.value}
                title={m.gloss}
                onClick={() => update({ mode: m.value === 'relevance' ? null : m.value })}
                className={cn(
                  'border-r border-rule-strong px-3 py-1.5 text-xs font-medium transition-colors last:border-r-0',
                  mode === m.value
                    ? 'bg-accent text-canvas'
                    : 'bg-surface text-ink-muted hover:bg-raised hover:text-ink',
                )}
              >
                {m.label}
                {m.value === 'trust' ? (
                  <span className="ml-1 font-mono text-2xs tnum opacity-75">({seedCount.count})</span>
                ) : null}
              </button>
            ))}
          </div>

          <div className="flex gap-3">
            <Field label="From">
              {(id) => (
                <Input
                  id={id}
                  type="number"
                  inputMode="numeric"
                  placeholder="any"
                  className="w-24"
                  value={yearFrom ?? ''}
                  onChange={(e) => update({ year_from: e.target.value })}
                />
              )}
            </Field>
            <Field label="To">
              {(id) => (
                <Input
                  id={id}
                  type="number"
                  inputMode="numeric"
                  placeholder="any"
                  className="w-24"
                  value={yearTo ?? ''}
                  onChange={(e) => update({ year_to: e.target.value })}
                />
              )}
            </Field>
          </div>
        </div>
      </section>

      {trimmed.length === 0 ? (
        <NoMatches hint="Type at least two characters to search." />
      ) : tooShort ? null : active.isError ? (
        <ErrorState error={active.error} onRetry={() => void active.refetch()} />
      ) : active.isLoading ? (
        <TableRowSkeleton rows={8} />
      ) : mode === 'relevance' ? (
        !relevanceData || relevanceData.items.length === 0 ? (
          <NoMatches />
        ) : (
          <div aria-busy={relevance.isFetching} className="space-y-2">
            <p className="text-2xs uppercase tracking-[0.08em] text-ink-faint">
              {formatCount(relevanceData.total)} match{relevanceData.total === 1 ? '' : 'es'}
            </p>
            <ul className="divide-y divide-rule">
              {relevanceData.items.map((paper) => (
                <ResultRow
                  key={paper.id}
                  paper={paper}
                  strength={pendingStrength[paper.id] ?? DEFAULT_STRENGTH}
                  onStrength={(value) =>
                    setPendingStrength((prev) => ({ ...prev, [paper.id]: value }))
                  }
                  onAdd={() => addTrust(paper, pendingStrength[paper.id] ?? DEFAULT_STRENGTH)}
                  busy={setTrust.isPending}
                />
              ))}
            </ul>
          </div>
        )
      ) : !rankedData || rankedData.items.length === 0 ? (
        <NoMatches />
      ) : (
        <>
          {banner ? (
            <Notice tone={banner.tone} title={banner.title}>
              <p>{rankedData.cold_start.message}</p>
            </Notice>
          ) : null}

          <div aria-busy={ranked.isFetching}>
            <RankingTable
              items={rankedData.items}
              onExplain={setExplaining}
              renderActions={quickTrust}
              rowProps={(paper) => ({ 'data-testid': 'search-result', 'data-work-id': paper.id })}
              emptyLabel="No matches."
              hideLift
              tieBannerText={(size) =>
                `${size} statistically tied — order below reflects text relevance, not trust`
              }
            />
          </div>

          <Disclaimer text={rankedData.disclaimer} />
        </>
      )}

      {itemsLength > 0 && trimmed.length >= 2 ? (
        <div className="flex flex-wrap items-center justify-between gap-4 border-t border-rule pt-4">
          <span className="font-mono text-2xs tnum text-ink-muted">
            {offset + 1}–{offset + itemsLength} of {formatCount(total)}
          </span>
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              disabled={offset === 0}
              onClick={() => update({ offset: String(Math.max(0, offset - limit)) })}
            >
              Previous
            </Button>
            <Button
              size="sm"
              disabled={offset + itemsLength >= total}
              onClick={() => update({ offset: String(offset + limit) })}
            >
              Next
            </Button>
          </div>
        </div>
      ) : null}

      {/* SessionGate keeps this screen from mounting before a profile exists,
          so every row -- trust-ranked or global-merit-ranked -- can always be
          explained against it. */}
      <SidePanel
        open={Boolean(explaining)}
        onClose={() => setExplaining(null)}
        title="Explanation"
        width="max-w-2xl"
      >
        {explaining ? (
          <>
            <div className="flex items-start justify-between gap-4 border-b border-rule px-5 py-3.5">
              <div className="min-w-0">
                <p className="text-2xs uppercase tracking-[0.08em] text-ink-faint">Why this ranks here</p>
                <PaperTitle as="p" title={explaining.title} className="mt-0.5 truncate text-sm text-ink" />
                {'relevance_rank' in explaining ? (
                  <p className="mt-1 text-xs text-ink-muted">{describePosition(explaining)}</p>
                ) : null}
              </div>
              <div className="flex shrink-0 items-center gap-2">
                <Link
                  to={`/paper/${explaining.id}`}
                  className={buttonClass('secondary', 'sm')}
                  onClick={() => setExplaining(null)}
                >
                  Open paper
                </Link>
                <Button variant="ghost" size="icon" onClick={() => setExplaining(null)} aria-label="Close">
                  <X aria-hidden className="h-4 w-4" />
                </Button>
              </div>
            </div>
            <div className="flex-1 overflow-y-auto">
              <ExplainContent profileId={profileId} paperId={explaining.id} />
            </div>
          </>
        ) : null}
      </SidePanel>
    </div>
  );
}

function ResultRow({
  paper,
  strength,
  onStrength,
  onAdd,
  busy,
}: {
  paper: PaperBrief;
  strength: number;
  onStrength: (value: number) => void;
  onAdd: () => void;
  busy: boolean;
}): JSX.Element {
  return (
    <li data-testid="search-result" data-work-id={paper.id} className="py-3.5">
      <Link to={`/paper/${paper.id}`} className="block">
        <PaperTitle
          as="span"
          title={paper.title}
          className="block text-[0.95rem] leading-snug text-ink hover:text-accent"
        />
      </Link>
      <p className="mt-1 text-xs text-ink-muted">
        {formatAuthors(paper.authors)} · {formatYear(paper.year)}
        {paper.venue ? ` · ${paper.venue.name}` : ''}
      </p>
      <p className="mt-1 font-mono text-2xs tnum text-ink-faint">
        {formatCount(paper.cited_by_count)} citations · {formatCount(paper.in_corpus_cited_by)}{' '}
        inside this corpus
        {paper.is_stub ? ' · stub record' : ''}
      </p>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <StrengthPicker
          value={strength}
          onChange={onStrength}
          size="sm"
          disabled={busy}
          label={`Strength for ${paper.title ?? 'this paper'}`}
        />
        <span className="mr-auto text-2xs text-ink-faint">{STRENGTH_LABELS[strength]}</span>
        <Button size="sm" variant="primary" onClick={onAdd} disabled={busy}>
          Trust this
        </Button>
      </div>
    </li>
  );
}
