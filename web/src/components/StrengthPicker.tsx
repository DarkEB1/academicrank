import { cn } from '@/lib/cn';

const LEVELS = [1, 2, 3, 4, 5] as const;

export const STRENGTH_LABELS: Record<number, string> = {
  1: 'Worth knowing about',
  2: 'Sound',
  3: 'I rely on this',
  4: 'Foundational to my work',
  5: 'I would stake an argument on it',
};

/**
 * Five discrete levels, rendered as a radiogroup. Arrow keys move between them
 * because that is what a radiogroup does natively.
 */
export function StrengthPicker({
  value,
  onChange,
  disabled,
  size = 'md',
  label = 'Trust strength',
}: {
  value: number;
  onChange: (value: number) => void;
  disabled?: boolean;
  size?: 'sm' | 'md';
  label?: string;
}): JSX.Element {
  return (
    <div
      role="radiogroup"
      aria-label={label}
      className={cn('inline-flex overflow-hidden rounded-sm border border-rule-strong')}
    >
      {LEVELS.map((level) => {
        const active = value === level;
        return (
          <button
            key={level}
            type="button"
            role="radio"
            aria-checked={active}
            disabled={disabled}
            title={STRENGTH_LABELS[level]}
            onClick={() => onChange(level)}
            className={cn(
              'border-r border-rule-strong font-mono tnum transition-colors last:border-r-0',
              size === 'sm' ? 'h-7 w-7 text-2xs' : 'h-8 w-8 text-xs',
              active
                ? 'bg-accent text-canvas'
                : 'bg-surface text-ink-muted hover:bg-raised hover:text-ink',
              disabled && 'cursor-not-allowed opacity-50',
            )}
          >
            {level}
          </button>
        );
      })}
    </div>
  );
}
