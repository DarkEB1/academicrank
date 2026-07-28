import type { ReactNode } from 'react';
import { cn } from '@/lib/cn';

type Tone = 'neutral' | 'accent' | 'caution' | 'critical' | 'positive';

const TONES: Record<Tone, string> = {
  neutral: 'border-rule-strong text-ink-muted',
  accent: 'border-accent/40 text-accent bg-accent-wash/60',
  caution: 'border-caution/40 text-caution bg-caution-wash/70',
  critical: 'border-critical/40 text-critical',
  positive: 'border-positive/40 text-positive',
};

export function Badge({
  children,
  tone = 'neutral',
  className,
  title,
}: {
  children: ReactNode;
  tone?: Tone;
  className?: string;
  title?: string;
}): JSX.Element {
  return (
    <span
      title={title}
      className={cn(
        'inline-flex items-center gap-1 whitespace-nowrap rounded-[2px] border px-1.5 py-0.5 text-2xs font-medium uppercase tracking-[0.06em]',
        TONES[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}
