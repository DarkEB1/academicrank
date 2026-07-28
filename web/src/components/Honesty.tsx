import { useState } from 'react';
import { AlertTriangle, ChevronDown, Info } from 'lucide-react';
import { cn } from '@/lib/cn';

/**
 * The honesty furniture. These components are not decoration and they are not
 * dismissable where the brief says they must not be.
 */

/** Renders a server-supplied `disclaimer` string verbatim. Never paraphrased. */
export function Disclaimer({
  text,
  className,
}: {
  text: string | null | undefined;
  className?: string;
}): JSX.Element | null {
  if (!text) return null;
  return (
    <p
      className={cn(
        'border-l-2 border-rule-strong pl-3 text-xs leading-relaxed text-ink-muted',
        className,
      )}
      data-testid="disclaimer"
    >
      {text}
    </p>
  );
}

export const COVERAGE_CAVEAT =
  'A low score often means "thinly represented in the data", not "unimportant". The underlying corpus is OpenAlex, which under-represents non-English scholarship, pre-digital literature, monographs, and work from several regions. Absence here is not evidence of absence in the field.';

export const PROXIMITY_STATEMENT =
  'Provenance measures proximity in a weighted trust graph. It does not measure quality, correctness, or importance.';

/** The coverage caveat, shown wherever scores are shown. */
export function CoverageNote({ className }: { className?: string }): JSX.Element {
  return (
    <p className={cn('max-w-measure text-xs leading-relaxed text-ink-muted', className)}>
      <Info aria-hidden className="mr-1.5 inline h-3.5 w-3.5 -translate-y-px text-ink-faint" />
      {COVERAGE_CAVEAT}
    </p>
  );
}

/** A standing notice. `dismissable={false}` means exactly that: no close button. */
export function Notice({
  tone = 'caution',
  title,
  children,
  className,
  icon = true,
}: {
  tone?: 'caution' | 'neutral' | 'critical';
  title: string;
  children?: React.ReactNode;
  className?: string;
  icon?: boolean;
}): JSX.Element {
  const tones = {
    caution: 'border-caution/40 bg-caution-wash/60',
    neutral: 'border-rule-strong bg-raised/60',
    critical: 'border-critical/40 bg-critical/5',
  } as const;
  const iconTones = {
    caution: 'text-caution',
    neutral: 'text-ink-faint',
    critical: 'text-critical',
  } as const;

  return (
    <div
      role="note"
      className={cn('flex gap-3 rounded-sm border px-4 py-3', tones[tone], className)}
    >
      {icon ? (
        <AlertTriangle aria-hidden className={cn('mt-0.5 h-4 w-4 shrink-0', iconTones[tone])} />
      ) : null}
      <div className="min-w-0 space-y-1">
        <p className="text-sm font-medium text-ink">{title}</p>
        {children ? (
          <div className="max-w-measure text-xs leading-relaxed text-ink-muted">{children}</div>
        ) : null}
      </div>
    </div>
  );
}

/**
 * The permanent statement of what the number is. Lives in the masthead on every
 * screen — not in a footer, not behind a tooltip.
 */
export function ProximityStatement({ className }: { className?: string }): JSX.Element {
  const [open, setOpen] = useState(false);
  return (
    <div className={cn('border-b border-rule bg-raised/50', className)}>
      <div className="mx-auto max-w-[110rem] px-6 py-2">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <p className="text-xs leading-relaxed text-ink">
            <strong className="font-semibold">{PROXIMITY_STATEMENT}</strong>
          </p>
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            className="inline-flex items-center gap-1 text-xs text-accent underline decoration-accent/30 underline-offset-2 hover:decoration-accent"
          >
            What that means in practice
            <ChevronDown
              aria-hidden
              className={cn('h-3 w-3 transition-transform', open && 'rotate-180')}
            />
          </button>
        </div>
        {open ? (
          <div className="mt-2 grid max-w-5xl gap-3 pb-2 text-xs leading-relaxed text-ink-muted md:grid-cols-3">
            <p>
              A score is the stationary weight your seeds place on a paper through citation,
              authorship, venue, topic and institution edges. Move your seeds and every score
              moves. It is a statement about your position in the literature, not about the paper.
            </p>
            <p>{COVERAGE_CAVEAT}</p>
            <p>
              Papers whose intervals overlap are reported as tied. Where the interval is as wide as
              the estimate, the ordering carries no information and the interface says so rather
              than printing a confident-looking number.
            </p>
          </div>
        ) : null}
      </div>
    </div>
  );
}
