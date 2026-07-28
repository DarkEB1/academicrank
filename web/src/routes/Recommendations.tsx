import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useRecommendations } from '@/lib/queries';
import { useSession } from '@/lib/session';
import { useDebounced } from '@/lib/hooks';
import {
  describeDiversity,
  diversityValueText,
  DIVERSITY_STEP,
  noveltyLabel,
  quantizeDiversity,
  tradeoffSplit,
} from '@/lib/diversity';
import { domainFor, formatAuthors, formatCount, formatYear } from '@/lib/format';
import { PaperTitle } from '@/components/Math';
import { ScoreBar } from '@/components/ScoreBar';
import { CoverageNote, Disclaimer } from '@/components/Honesty';
import { ErrorState, NoTrustSetYet } from '@/components/States';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import { Slider } from '@/components/ui/Slider';
import { Badge } from '@/components/ui/Badge';
import { LoadingRegion, Skeleton } from '@/components/ui/Skeleton';
import { groupTies } from '@/lib/ties';
import { cn } from '@/lib/cn';

export function RecommendationsScreen(): JSX.Element {
  const { profile } = useSession();
  const profileId = profile?.id ?? '';
  const [params, setParams] = useSearchParams();

  const initial = quantizeDiversity(Number(params.get('diversity') ?? 0.35));
  const [diversity, setDiversity] = useState(initial);
  const settled = useDebounced(diversity, 260);

  useEffect(() => {
    const next = new URLSearchParams(params);
    next.set('diversity', String(settled));
    setParams(next, { replace: true });
    // `params` intentionally omitted: writing it back would loop.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settled]);

  const recs = useRecommendations(profileId, settled, 30);
  const description = describeDiversity(diversity);
  const split = tradeoffSplit(diversity);
  const items = recs.data?.items ?? [];
  const domain = domainFor(items);
  const runs = groupTies(items);
  const seeds = profile?.trust_count ?? 0;

  return (
    <div className="space-y-8">
      <header className="space-y-3">
        <h1 className="font-serif text-2xl tracking-tight text-ink">Recommendations</h1>
        <p className="max-w-measure text-sm leading-relaxed text-ink-muted">
          One control, one trade-off. Near the left you get work adjacent to what you already
          trust — reliable and largely redundant. Near the right you get well-supported work your
          trust set cannot reach — surprising and frequently irrelevant. There is no setting that
          gives you both.
        </p>
      </header>

      <Card>
        <CardHeader
          title="Diversity dial"
          description="Sent to the API as the `diversity` parameter, 0 to 1. Nothing is reweighted client-side."
        />
        <CardBody className="space-y-6">
          <div>
            <div className="flex items-baseline justify-between gap-4 pb-2">
              <span className="text-2xs uppercase tracking-[0.1em] text-ink-muted">
                Exploitation
              </span>
              <span className="font-mono text-lg tnum text-ink">{diversity.toFixed(2)}</span>
              <span className="text-2xs uppercase tracking-[0.1em] text-ink-muted">
                Exploration
              </span>
            </div>

            <Slider
              min={0}
              max={1}
              step={DIVERSITY_STEP}
              value={diversity}
              valueText={diversityValueText(diversity)}
              aria-label="Diversity: exploitation to exploration"
              onChange={(e) => setDiversity(quantizeDiversity(Number(e.target.value)))}
            />

            <div className="mt-1 flex justify-between font-mono text-2xs tnum text-ink-faint">
              <span>0.00</span>
              <span>0.50</span>
              <span>1.00</span>
            </div>
          </div>

          <div className="grid gap-4 md:grid-cols-[8rem_1fr]">
            <div>
              <Badge tone="accent">{description.label}</Badge>
              <p className="mt-2 font-mono text-2xs tnum text-ink-muted">
                {split.exploitation}% / {split.exploration}%
              </p>
            </div>
            <dl className="grid gap-3 sm:grid-cols-2">
              <div>
                <dt className="text-2xs uppercase tracking-[0.08em] text-ink-faint">
                  What you get
                </dt>
                <dd className="mt-1 max-w-measure text-xs leading-relaxed text-ink">
                  {description.gain}
                </dd>
              </div>
              <div>
                <dt className="text-2xs uppercase tracking-[0.08em] text-ink-faint">
                  What you give up
                </dt>
                <dd className="mt-1 max-w-measure text-xs leading-relaxed text-caution">
                  {description.cost}
                </dd>
              </div>
            </dl>
          </div>

          {diversity !== settled ? (
            <p className="font-mono text-2xs text-ink-faint">Refetching at {settled.toFixed(2)}…</p>
          ) : null}
        </CardBody>
      </Card>

      {recs.isError ? (
        <ErrorState error={recs.error} onRetry={() => void recs.refetch()} />
      ) : seeds === 0 && !recs.isLoading ? (
        <NoTrustSetYet what="Recommendation" />
      ) : recs.isLoading ? (
        <LoadingRegion label="Fetching recommendations" className="space-y-4">
          {[0, 1, 2, 3, 4].map((i) => (
            <Skeleton key={i} className="h-28 w-full" />
          ))}
        </LoadingRegion>
      ) : items.length === 0 ? (
        <p className="py-10 text-center text-sm text-ink-muted">
          No recommendations came back at this setting. Try moving the dial, or add more seeds.
        </p>
      ) : (
        <div aria-busy={recs.isFetching} className="space-y-4">
          <ol className="space-y-3">
            {runs.map((run) =>
              run.items.map((item, indexInRun) => (
                <li key={item.id}>
                  {run.tied && indexInRun === 0 ? (
                    <p className="mb-1.5 pl-3 text-2xs uppercase tracking-[0.08em] text-accent">
                      next {run.items.length} statistically tied — order arbitrary
                    </p>
                  ) : null}
                  <article
                    className={cn(
                      'rounded-sm border bg-surface px-5 py-4',
                      run.tied ? 'border-l-2 border-l-accent/50 border-rule' : 'border-rule',
                    )}
                  >
                    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_13rem]">
                      <div className="min-w-0">
                        <Link to={`/paper/${item.id}`}>
                          <PaperTitle
                            as="span"
                            title={item.title}
                            className="block text-base leading-snug text-ink hover:text-accent"
                          />
                        </Link>
                        <p className="mt-1 text-xs text-ink-muted">
                          {formatAuthors(item.authors)} · {formatYear(item.year)}
                          {item.venue ? ` · ${item.venue.name}` : ''}
                        </p>

                        <p className="mt-3 max-w-measure border-l-2 border-accent/30 pl-3 text-sm leading-relaxed text-ink">
                          {item.reason}
                        </p>

                        <div className="mt-3 flex flex-wrap items-center gap-2">
                          <Badge title={`novelty ${item.novelty.toFixed(2)}`}>
                            {noveltyLabel(item.novelty)}
                          </Badge>
                          <Badge>{formatCount(item.cited_by_count)} citations</Badge>
                          {item.disagreement >= 0.45 ? (
                            <Badge tone="caution" title="Trust, global merit and citations disagree">
                              contested
                            </Badge>
                          ) : null}
                        </div>
                      </div>

                      <div className="lg:pl-4">
                        <p className="text-2xs uppercase tracking-[0.08em] text-ink-faint">
                          Trust (95% interval)
                        </p>
                        <ScoreBar
                          value={item.trust}
                          uncertainty={item.uncertainty}
                          domain={domain}
                          className="mt-1.5"
                        />
                      </div>
                    </div>
                  </article>
                </li>
              )),
            )}
          </ol>

          <div className="space-y-3 border-t border-rule pt-5">
            <Disclaimer text={recs.data?.disclaimer} />
            <p className="max-w-measure text-xs leading-relaxed text-ink-muted">
              The reason text above is generated from the graph path, not from the content of the
              paper. It tells you why the walk arrived here. It does not tell you the paper is
              good, and it has not read the abstract.
            </p>
            <CoverageNote />
          </div>
        </div>
      )}
    </div>
  );
}
