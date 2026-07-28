import { useMemo } from 'react';
import { ArrowDownRight, ArrowUpRight, Minus } from 'lucide-react';
import type { SimulateResponse } from '@/lib/types';
import { formatSignedRank } from '@/lib/format';
import { PaperTitle } from './Math';
import { cn } from '@/lib/cn';

/**
 * How far the ranking moves. This is the honest answer to "does adding one more
 * seed matter?", and for a small trust set the answer is usually "enormously".
 */
export function SimulationPreview({
  result,
  className,
}: {
  result: SimulateResponse;
  className?: string;
}): JSX.Element {
  const titles = useMemo(() => {
    const map = new Map<string, string | null>();
    for (const paper of [...result.before, ...result.after]) map.set(paper.id, paper.title);
    return map;
  }, [result]);

  const beforeTop = result.before.slice(0, 10).map((p) => p.id);
  const afterTop = result.after.slice(0, 10).map((p) => p.id);
  const entered = afterTop.filter((id) => !beforeTop.includes(id));
  const left = beforeTop.filter((id) => !afterTop.includes(id));

  const moved = [...result.moved]
    .filter((m) => m.delta_rank !== 0)
    .sort((a, b) => Math.abs(b.delta_rank) - Math.abs(a.delta_rank))
    .slice(0, 8);

  const churn = beforeTop.length === 0 ? 0 : entered.length / beforeTop.length;

  return (
    <div className={cn('space-y-4', className)}>
      <div className="grid gap-3 sm:grid-cols-3">
        <Stat
          value={`${Math.round(churn * 100)}%`}
          label="of the top ten replaced"
          loud={churn >= 0.3}
        />
        <Stat value={String(result.moved.length)} label="papers changed rank" />
        <Stat
          value={String(moved.length > 0 ? Math.abs(moved[0].delta_rank) : 0)}
          label="largest single rank move"
          loud={moved.length > 0 && Math.abs(moved[0].delta_rank) >= 10}
        />
      </div>

      {churn >= 0.3 ? (
        <p className="max-w-measure text-xs leading-relaxed text-caution">
          A single change rewrote {Math.round(churn * 100)}% of the top ten. At this trust-set size
          the ranking is a statement about your last few clicks, not about the literature.
        </p>
      ) : null}

      {moved.length > 0 ? (
        <ul className="divide-y divide-rule rounded-sm border border-rule">
          {moved.map((m) => {
            const up = m.delta_rank < 0; // smaller rank number = higher position
            return (
              <li key={m.work_id} className="flex items-center gap-3 px-3 py-2">
                <span
                  className={cn(
                    'inline-flex w-16 shrink-0 items-center gap-1 font-mono text-2xs tnum',
                    up ? 'text-positive' : 'text-critical',
                  )}
                >
                  {up ? (
                    <ArrowUpRight aria-hidden className="h-3 w-3" />
                  ) : (
                    <ArrowDownRight aria-hidden className="h-3 w-3" />
                  )}
                  {formatSignedRank(-m.delta_rank)}
                </span>
                <PaperTitle
                  as="span"
                  title={titles.get(m.work_id) ?? null}
                  className="min-w-0 flex-1 truncate text-xs text-ink"
                />
                <span className="shrink-0 font-mono text-2xs tnum text-ink-faint">
                  {m.delta_trust >= 0 ? '+' : ''}
                  {m.delta_trust.toExponential(1)}
                </span>
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="flex items-center gap-2 text-xs text-ink-muted">
          <Minus aria-hidden className="h-3.5 w-3.5" />
          Nothing changed rank. This change adds no information the graph did not already have.
        </p>
      )}

      {left.length > 0 ? (
        <p className="text-xs leading-relaxed text-ink-muted">
          {left.length} paper{left.length === 1 ? '' : 's'} dropped out of the top ten entirely.
        </p>
      ) : null}
    </div>
  );
}

function Stat({
  value,
  label,
  loud,
}: {
  value: string;
  label: string;
  loud?: boolean;
}): JSX.Element {
  return (
    <div className="rounded-sm border border-rule bg-raised/40 px-3 py-2.5">
      <p
        className={cn(
          'font-mono text-lg tnum leading-none',
          loud ? 'text-caution' : 'text-ink',
        )}
      >
        {value}
      </p>
      <p className="mt-1 text-2xs leading-snug text-ink-muted">{label}</p>
    </div>
  );
}
