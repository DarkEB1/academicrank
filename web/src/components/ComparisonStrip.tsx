import { Scale } from 'lucide-react';
import type { Percentiles } from '@/lib/types';
import {
  DISAGREEMENT_COPY,
  disagreementBand,
  formatPercentile,
  normalisePercentile,
} from '@/lib/format';
import { cn } from '@/lib/cn';
import { Badge } from './ui/Badge';

const ROWS = [
  {
    key: 'trust' as const,
    label: 'Your trust graph',
    gloss: 'Proximity to the papers you seeded. Changes when your seeds change.',
    tone: 'bg-accent',
  },
  {
    key: 'global' as const,
    label: 'Unpersonalised merit',
    gloss: 'The same walk with no ego node: what the corpus says without you in it.',
    tone: 'bg-ink-muted',
  },
  {
    key: 'citations' as const,
    label: 'Raw citation count',
    gloss: 'Counted, not weighted. Rewards age, field size and self-citation alike.',
    tone: 'bg-ink-faint',
  },
];

/**
 * Three percentiles side by side. The point is the comparison: when they
 * disagree, at least one of them is telling you something the others cannot.
 */
export function ComparisonStrip({
  percentiles,
  disagreement,
  className,
}: {
  percentiles: Percentiles;
  disagreement: number;
  className?: string;
}): JSX.Element {
  const band = disagreementBand(disagreement);
  const loud = band === 'notable' || band === 'stark';

  return (
    <div className={cn('space-y-5', className)}>
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h3 className="text-sm font-semibold text-ink">Three measures, compared</h3>
        <Badge tone={loud ? 'caution' : 'neutral'} title="Normalised spread across the three percentile ranks">
          disagreement {normalisePercentile(disagreement).toFixed(2)}
        </Badge>
      </div>

      <ul className="space-y-4">
        {ROWS.map((row) => {
          const value = normalisePercentile(percentiles[row.key]);
          return (
            <li key={row.key} className="grid grid-cols-[11rem_1fr_3.5rem] items-center gap-4">
              <div className="min-w-0">
                <p className="truncate text-xs font-medium text-ink" title={row.label}>
                  {row.label}
                </p>
              </div>
              <div
                className="relative h-2.5 rounded-[1px] bg-raised"
                role="img"
                aria-label={`${row.label}: ${formatPercentile(percentiles[row.key])} percentile`}
              >
                <span
                  className={cn('absolute inset-y-0 left-0 rounded-[1px]', row.tone)}
                  style={{ width: `${value * 100}%` }}
                />
              </div>
              <span className="text-right font-mono text-2xs tnum text-ink-muted">
                {formatPercentile(percentiles[row.key])}
              </span>
            </li>
          );
        })}
      </ul>

      <dl className="grid gap-2 border-t border-rule pt-4 text-xs leading-relaxed text-ink-muted sm:grid-cols-3">
        {ROWS.map((row) => (
          <div key={row.key}>
            <dt className="font-medium text-ink">{row.label}</dt>
            <dd className="mt-0.5">{row.gloss}</dd>
          </div>
        ))}
      </dl>

      <div
        className={cn(
          'flex gap-3 rounded-sm border px-4 py-3',
          loud ? 'border-caution/40 bg-caution-wash/60' : 'border-rule bg-raised/40',
        )}
      >
        <Scale
          aria-hidden
          className={cn('mt-0.5 h-4 w-4 shrink-0', loud ? 'text-caution' : 'text-ink-faint')}
        />
        <p className="max-w-measure text-xs leading-relaxed text-ink">{DISAGREEMENT_COPY[band]}</p>
      </div>
    </div>
  );
}
