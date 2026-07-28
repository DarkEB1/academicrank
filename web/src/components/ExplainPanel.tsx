import { Fragment } from 'react';
import { Link } from 'react-router-dom';
import { ArrowRight, Quote } from 'lucide-react';
import { useExplain } from '@/lib/queries';
import { formatContribution, KIND_LABEL, pathSentence, pathSteps } from '@/lib/paths';
import { formatInterval, formatScore, METHOD_COPY } from '@/lib/format';
import type { ContextContribution, ContributingPath } from '@/lib/types';
import { PaperTitle } from './Math';
import { ScoreReadout } from './ScoreBar';
import { Badge } from './ui/Badge';
import { CardSkeleton, LoadingRegion, Skeleton } from './ui/Skeleton';
import { ErrorState, EmptyState } from './States';
import { cn } from '@/lib/cn';

/**
 * The explanation. Every number here is a share of a total that the backend
 * computed; nothing is inferred client-side.
 */
export function ExplainContent({
  profileId,
  paperId,
}: {
  profileId: string;
  paperId: string;
}): JSX.Element {
  const explain = useExplain(profileId, paperId);

  if (explain.isLoading) {
    return (
      <LoadingRegion
        label="Reconstructing the contributing paths. This walks the trust graph and can take a few seconds."
        className="space-y-6 px-5 py-5"
      >
        <CardSkeleton lines={2} />
        <div className="space-y-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-16 w-full" />
          ))}
        </div>
      </LoadingRegion>
    );
  }

  if (explain.isError) {
    return <ErrorState error={explain.error} onRetry={() => void explain.refetch()} className="m-5" />;
  }

  const data = explain.data;
  if (!data) return <EmptyState title="No explanation available" />;

  const sortedPaths = [...data.paths].sort((a, b) => b.contribution - a.contribution);
  const accounted = sortedPaths.reduce((sum, p) => sum + p.contribution, 0);

  return (
    <div className="space-y-8 px-5 py-5">
      <section>
        <PaperTitle as="h3" title={data.target.title} className="text-base leading-snug text-ink" />
        <p className="mt-3 max-w-measure text-sm leading-relaxed text-ink">{data.summary}</p>
        <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-ink-muted">
          <span>
            Trust <ScoreReadout value={data.trust} uncertainty={data.uncertainty} className="text-ink" />
          </span>
          <span className="tnum">Interval {formatInterval(data.uncertainty)}</span>
          <span className="tnum">Tie group {data.uncertainty.tie_group}</span>
        </div>
        <p className="mt-2 max-w-measure text-xs leading-relaxed text-ink-faint">
          {METHOD_COPY[data.uncertainty.method]}
        </p>
      </section>

      <section aria-labelledby="explain-paths">
        <div className="flex items-baseline justify-between gap-3">
          <h4 id="explain-paths" className="text-sm font-semibold text-ink">
            How the trust arrives
          </h4>
          <span className="text-2xs uppercase tracking-[0.08em] text-ink-faint">
            {sortedPaths.length} path{sortedPaths.length === 1 ? '' : 's'} shown
          </span>
        </div>

        {sortedPaths.length === 0 ? (
          <p className="mt-3 max-w-measure text-sm leading-relaxed text-ink-muted">
            No contributing path was reconstructed. Either this paper is not reachable from your
            trust set within the walk depth, or its score comes from diffuse contributions too
            small to reconstruct individually.
          </p>
        ) : (
          <>
            <ol className="mt-4 space-y-4">
              {sortedPaths.map((path, index) => (
                <li key={`${path.seed.id}-${index}`}>
                  <PathCard path={path} />
                </li>
              ))}
            </ol>
            <p className="mt-4 max-w-measure text-xs leading-relaxed text-ink-faint">
              These paths account for {formatContribution(accounted)} of the score. The remainder
              arrives through paths too weak to list individually.
            </p>
          </>
        )}
      </section>

      {data.by_context.length > 0 ? (
        <section aria-labelledby="explain-contexts">
          <h4 id="explain-contexts" className="text-sm font-semibold text-ink">
            Contribution by relation family
          </h4>
          <p className="mt-1.5 max-w-measure text-xs leading-relaxed text-ink-muted">
            Each figure is a <em>marginal</em>: the score in that context minus the score in the
            pure citation context. Because trust and citation edges exist in every context, this is
            not "trust arriving purely through topic" — it is what adding that relation family
            changes.
          </p>
          <ContextBars items={data.by_context} className="mt-4" />
        </section>
      ) : null}

      <section className="border-t border-rule pt-5">
        <p className="flex max-w-measure gap-2.5 text-xs leading-relaxed text-ink-muted">
          <Quote aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0 text-ink-faint" />
          <span>{data.caveat}</span>
        </p>
      </section>
    </div>
  );
}

function PathCard({ path }: { path: ContributingPath }): JSX.Element {
  const steps = pathSteps(path);

  return (
    <div className="rounded-sm border border-rule bg-raised/40 px-4 py-3">
      <div className="flex items-baseline justify-between gap-3">
        <Link
          to={`/paper/${path.seed.id}`}
          className="min-w-0 text-2xs uppercase tracking-[0.08em] text-ink-faint hover:text-accent"
        >
          from seed
        </Link>
        <Badge tone="accent" title="Share of this paper's total score carried by this path">
          {formatContribution(path.contribution)}
        </Badge>
      </div>

      <PaperTitle
        as="p"
        title={path.seed.title}
        className="mt-1 truncate text-sm leading-snug text-ink"
      />

      <p className="mt-2.5 text-sm leading-relaxed text-ink">{pathSentence(path)}</p>

      {steps.length > 0 ? (
        <div className="mt-3 flex flex-wrap items-center gap-x-1.5 gap-y-1.5 text-2xs">
          {path.nodes.map((node, index) => (
            <Fragment key={`${node.id}-${index}`}>
              {index > 0 ? (
                <span className="inline-flex items-center gap-1 text-ink-faint">
                  <ArrowRight aria-hidden className="h-3 w-3" />
                  <span className="font-mono">{steps[index - 1]?.relation}</span>
                  <span className="tnum">×{steps[index - 1]?.weight.toFixed(2)}</span>
                  <ArrowRight aria-hidden className="h-3 w-3" />
                </span>
              ) : null}
              <span
                className={cn(
                  'rounded-[2px] border px-1.5 py-0.5',
                  node.kind === 'profile'
                    ? 'border-accent/40 bg-accent-wash/60 text-accent'
                    : 'border-rule-strong text-ink-muted',
                )}
                title={KIND_LABEL[node.kind]}
              >
                {node.kind === 'profile' ? 'your profile' : node.label}
              </span>
            </Fragment>
          ))}
        </div>
      ) : null}
    </div>
  );
}

export function ContextBars({
  items,
  className,
}: {
  items: ContextContribution[];
  className?: string;
}): JSX.Element {
  const max = Math.max(...items.map((i) => Math.abs(i.marginal)), Number.EPSILON);

  return (
    <ul className={cn('space-y-2.5', className)}>
      {items.map((item) => {
        const width = (Math.abs(item.marginal) / max) * 50;
        const negative = item.marginal < 0;
        return (
          <li key={item.context} className="grid grid-cols-[7rem_1fr_5.5rem] items-center gap-3">
            <span className="truncate text-xs text-ink">{item.context}</span>
            <span className="relative block h-3">
              <span className="absolute left-1/2 top-0 h-full w-px bg-rule-strong" aria-hidden />
              <span
                className={cn(
                  'absolute top-1/2 h-2 -translate-y-1/2 rounded-[1px]',
                  negative ? 'bg-critical/45' : 'bg-accent/55',
                )}
                style={
                  negative
                    ? { right: '50%', width: `${width}%` }
                    : { left: '50%', width: `${width}%` }
                }
              />
            </span>
            <span className="text-right font-mono text-2xs tnum text-ink-muted">
              {item.marginal >= 0 ? '+' : ''}
              {formatScore(item.marginal)}
            </span>
          </li>
        );
      })}
    </ul>
  );
}
