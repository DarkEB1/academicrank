import {
  barGeometry,
  formatInterval,
  formatScore,
  METHOD_COPY,
  UNCERTAINTY_COPY,
  uncertaintyVerdict,
} from '@/lib/format';
import type { Uncertainty } from '@/lib/types';
import { cn } from '@/lib/cn';
import { Tooltip } from './ui/Tooltip';

export type ScoreBarProps = {
  value: number;
  uncertainty: Uncertainty;
  domain: { min: number; max: number };
  className?: string;
  /** Hide the numeric readout when the caller prints it elsewhere. */
  showReadout?: boolean;
  label?: string;
};

/**
 * A score is never rendered without its interval. The band is the confidence
 * interval; the tick is the point estimate. Where the band is wide, the tick is
 * deliberately not emphasised.
 */
export function ScoreBar({
  value,
  uncertainty,
  domain,
  className,
  showReadout = true,
  label = 'Trust',
}: ScoreBarProps): JSX.Element {
  const geo = barGeometry(value, uncertainty, domain);
  const verdict = uncertaintyVerdict(value, uncertainty);

  const description = `${label} ${formatScore(value, uncertainty.stderr)} plus or minus ${formatScore(
    uncertainty.stderr,
    uncertainty.stderr,
  )}, 95% interval ${formatInterval(uncertainty)} over ${uncertainty.n_samples} samples${
    geo.clipped ? ', interval extends beyond the visible range' : ''
  }. ${UNCERTAINTY_COPY[verdict]}`;

  return (
    <div className={cn('w-full', className)}>
      <Tooltip
        visualOnly
        className="w-full"
        content={
          <span className="block space-y-1">
            <span className="block font-mono tnum">
              {formatScore(value, uncertainty.stderr)} ± {formatScore(uncertainty.stderr, uncertainty.stderr)}
            </span>
            <span className="block text-ink-muted">
              Interval {formatInterval(uncertainty)} over {uncertainty.n_samples} samples.
            </span>
            <span className="block text-ink-muted">{METHOD_COPY[uncertainty.method]}</span>
            <span className="block text-ink-muted">{UNCERTAINTY_COPY[verdict]}</span>
          </span>
        }
      >
        <span
          className="block w-full py-1"
          role="img"
          aria-label={description}
        >
          <span className="relative block h-4 w-full">
            {/* baseline */}
            <span className="absolute inset-x-0 top-1/2 h-px -translate-y-1/2 bg-rule" />
            {/* interval band */}
            <span
              className={cn(
                'absolute top-1/2 h-[7px] -translate-y-1/2 rounded-[1px]',
                verdict === 'uninformative'
                  ? 'bg-caution/35'
                  : verdict === 'loose'
                    ? 'bg-accent/25'
                    : 'bg-accent/40',
              )}
              style={{ left: `${geo.low * 100}%`, width: `${geo.width * 100}%` }}
            />
            {/* interval caps */}
            <span
              className="absolute top-1/2 h-3 w-px -translate-y-1/2 bg-rule-strong"
              style={{ left: `${geo.low * 100}%` }}
            />
            <span
              className="absolute top-1/2 h-3 w-px -translate-y-1/2 bg-rule-strong"
              style={{ left: `calc(${geo.high * 100}% - 1px)` }}
            />
            {/* point estimate */}
            <span
              className={cn(
                'absolute top-1/2 h-[13px] w-[2px] -translate-x-1/2 -translate-y-1/2',
                verdict === 'uninformative' ? 'bg-caution' : 'bg-accent',
              )}
              style={{ left: `${geo.center * 100}%` }}
            />
          </span>
        </span>
      </Tooltip>

      {showReadout ? (
        <div className="mt-0.5 flex items-baseline justify-between gap-2 font-mono text-2xs tnum text-ink-muted">
          <span className="text-ink">{formatScore(value, uncertainty.stderr)}</span>
          <span aria-hidden>± {formatScore(uncertainty.stderr, uncertainty.stderr)}</span>
        </div>
      ) : null}
    </div>
  );
}

/** Text-only variant for dense places: "0.0143 ± 0.0040". */
export function ScoreReadout({
  value,
  uncertainty,
  className,
}: {
  value: number;
  uncertainty: Uncertainty;
  className?: string;
}): JSX.Element {
  return (
    <span className={cn('font-mono tnum', className)}>
      {formatScore(value, uncertainty.stderr)}
      <span className="text-ink-muted"> ± {formatScore(uncertainty.stderr, uncertainty.stderr)}</span>
    </span>
  );
}
