import { useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { CONTEXTS, type Context, type ScoredPaper } from '@/lib/types';
import { useRankings, useSeedCount } from '@/lib/queries';
import { useSession } from '@/lib/session';
import { distinguishablePositions, hasNonContiguousGroups } from '@/lib/ties';
import { formatMillis } from '@/lib/format';
import { RankingTable } from '@/components/RankingTable';
import { CoverageNote, Disclaimer, Notice } from '@/components/Honesty';
import { ErrorState, NoTrustSetYet } from '@/components/States';
import { TableRowSkeleton } from '@/components/ui/Skeleton';
import { Button, buttonClass } from '@/components/ui/Button';
import { Field, Input } from '@/components/ui/Input';
import { Select } from '@/components/ui/Select';
import { SidePanel } from '@/components/ui/Dialog';
import { ExplainContent } from '@/components/ExplainPanel';
import { PaperTitle } from '@/components/Math';
import { X } from 'lucide-react';

const PAGE_SIZES = [25, 50, 100];

const CONTEXT_GLOSS: Record<Context, string> = {
  aggregate: 'Every relation family at once. The default view.',
  citation: 'The citation backbone alone: who cites whom, plus your trust edges.',
  author: 'Citation backbone plus authorship. Trust flows through people.',
  topic: 'Citation backbone plus topic membership.',
  venue: 'Citation backbone plus venue. Journals and conferences carry trust.',
  institution:
    'Citation backbone plus institutional affiliation. Treat with particular suspicion — see the parameter playground.',
  coupling: 'Citation backbone plus bibliographic coupling: papers that cite the same things.',
  cocitation: 'Citation backbone plus co-citation: papers cited together by the same works.',
};

export function RankingsScreen(): JSX.Element {
  const { profile } = useSession();
  const profileId = profile?.id ?? '';
  const [params, setParams] = useSearchParams();
  const [explaining, setExplaining] = useState<ScoredPaper | null>(null);

  const context = (params.get('context') as Context | null) ?? 'aggregate';
  const limit = Number(params.get('limit') ?? 25);
  const offset = Number(params.get('offset') ?? 0);
  const yearFrom = params.get('year_from');
  const yearTo = params.get('year_to');
  const excludeTrusted = params.get('exclude_trusted') !== 'false';

  const query = useMemo(
    () => ({
      context,
      limit,
      offset,
      year_from: yearFrom ? Number(yearFrom) : undefined,
      year_to: yearTo ? Number(yearTo) : undefined,
      exclude_trusted: excludeTrusted,
    }),
    [context, limit, offset, yearFrom, yearTo, excludeTrusted],
  );

  const rankings = useRankings(profileId, query, Boolean(profileId));

  const update = (patch: Record<string, string | null>) => {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(patch)) {
      if (value === null || value === '') next.delete(key);
      else next.set(key, value);
    }
    if (!('offset' in patch)) next.delete('offset');
    setParams(next, { replace: true });
  };

  const seedCount = useSeedCount(profileId);
  const data = rankings.data;
  const items = data?.items ?? [];
  // The server's own count is authoritative; the trust set covers the window
  // before the first ranking has arrived.
  const seeds = data?.cold_start.seeds ?? seedCount.count;
  const total = data?.total ?? 0;
  const positions = distinguishablePositions(items);

  return (
    <div className="space-y-8">
      <header className="space-y-3">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="font-serif text-2xl tracking-tight text-ink">Rankings</h1>
            <p className="mt-1.5 max-w-measure text-sm leading-relaxed text-ink-muted">
              Papers ordered by how much weight your trust set places on them. This is a ranking of{' '}
              <em>proximity to you</em>, and it will disagree with any ranking built for anyone
              else.
            </p>
          </div>
          {data ? (
            <p className="text-2xs tnum text-ink-faint">
              {total.toLocaleString('en-GB')} scored · computed in {formatMillis(data.timing_ms)}
            </p>
          ) : null}
        </div>

        {seeds > 0 && seeds < 5 ? (
          <Notice title={`${seeds} seed${seeds === 1 ? '' : 's'}: this ranking is not reliable`}>
            <p>
              {data?.cold_start.message ??
                'Below five seeds the ranking is dominated by whichever papers you happened to add first. Small additions will reorder it completely.'}
            </p>
            <p className="mt-1.5">
              <Link to="/trust" className="link">
                Add more seeds
              </Link>{' '}
              — the trust set screen shows how much the ranking moves with each one.
            </p>
          </Notice>
        ) : null}

        {data && !data.cold_start.reliable && seeds >= 5 && data.cold_start.message ? (
          <Notice title="The server flags this ranking as unreliable">
            <p>{data.cold_start.message}</p>
          </Notice>
        ) : null}
      </header>

      <section aria-label="Filters" className="rounded-sm border border-rule bg-surface px-5 py-4">
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-5">
          <Field label="Context" hint={CONTEXT_GLOSS[context]}>
            {(id) => (
              <Select
                id={id}
                value={context}
                onChange={(e) => update({ context: e.target.value })}
              >
                {CONTEXTS.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </Select>
            )}
          </Field>

          <Field label="Published from">
            {(id) => (
              <Input
                id={id}
                type="number"
                inputMode="numeric"
                placeholder="any"
                value={yearFrom ?? ''}
                onChange={(e) => update({ year_from: e.target.value })}
              />
            )}
          </Field>

          <Field label="Published to">
            {(id) => (
              <Input
                id={id}
                type="number"
                inputMode="numeric"
                placeholder="any"
                value={yearTo ?? ''}
                onChange={(e) => update({ year_to: e.target.value })}
              />
            )}
          </Field>

          <Field label="Rows per page">
            {(id) => (
              <Select id={id} value={String(limit)} onChange={(e) => update({ limit: e.target.value })}>
                {PAGE_SIZES.map((size) => (
                  <option key={size} value={size}>
                    {size}
                  </option>
                ))}
              </Select>
            )}
          </Field>

          <div className="flex items-end">
            <label className="flex cursor-pointer items-center gap-2 pb-2 text-sm text-ink">
              <input
                type="checkbox"
                checked={excludeTrusted}
                onChange={(e) => update({ exclude_trusted: e.target.checked ? null : 'false' })}
                className="h-4 w-4 rounded-[2px] border-rule-strong accent-[hsl(var(--accent))]"
              />
              Hide my own seeds
            </label>
          </div>
        </div>
      </section>

      {rankings.isError ? (
        <ErrorState error={rankings.error} onRetry={() => void rankings.refetch()} />
      ) : rankings.isLoading ? (
        <TableRowSkeleton rows={10} />
      ) : seeds === 0 ? (
        <NoTrustSetYet what="This ranking" />
      ) : (
        <>
          <div aria-busy={rankings.isFetching}>
            <RankingTable items={items} onExplain={setExplaining} />
          </div>

          {items.length > 0 ? (
            <div className="flex flex-wrap items-center justify-between gap-4 border-t border-rule pt-4">
              <p className="max-w-measure text-xs leading-relaxed text-ink-muted">
                These {items.length} rows resolve to{' '}
                <strong className="font-semibold text-ink">
                  {positions} statistically distinguishable position
                  {positions === 1 ? '' : 's'}
                </strong>
                . The rest of the ordering is noise, and the brackets mark where.
                {hasNonContiguousGroups(items)
                  ? ' A tie group is interrupted in this page, which should not happen — brackets are drawn per contiguous run only.'
                  : ''}
              </p>
              <div className="flex items-center gap-2">
                <Button
                  size="sm"
                  disabled={offset === 0}
                  onClick={() => update({ offset: String(Math.max(0, offset - limit)) })}
                >
                  Previous
                </Button>
                <span className="font-mono text-2xs tnum text-ink-muted">
                  {offset + 1}–{offset + items.length}
                </span>
                <Button
                  size="sm"
                  disabled={offset + items.length >= total}
                  onClick={() => update({ offset: String(offset + limit) })}
                >
                  Next
                </Button>
              </div>
            </div>
          ) : null}

          <div className="space-y-3 border-t border-rule pt-5">
            <Disclaimer text={data?.disclaimer} />
            <CoverageNote />
          </div>
        </>
      )}

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
                <PaperTitle
                  as="p"
                  title={explaining.title}
                  className="mt-0.5 truncate text-sm text-ink"
                />
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
