import { forwardRef, useId } from 'react';
import type { InputHTMLAttributes, ReactNode } from 'react';
import { cn } from '@/lib/cn';

export type InputProps = InputHTMLAttributes<HTMLInputElement> & {
  leading?: ReactNode;
};

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { className, leading, ...props },
  ref,
) {
  const input = (
    <input
      ref={ref}
      className={cn(
        'h-9 w-full rounded-sm border border-rule-strong bg-surface px-2.5 text-sm text-ink',
        'placeholder:text-ink-faint',
        'focus:border-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40',
        'disabled:opacity-50',
        leading && 'pl-8',
        className,
      )}
      {...props}
    />
  );
  if (!leading) return input;
  return (
    <div className="relative">
      <span className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-faint">
        {leading}
      </span>
      {input}
    </div>
  );
});

export function Field({
  label,
  hint,
  children,
  className,
}: {
  label: string;
  hint?: ReactNode;
  children: (id: string) => ReactNode;
  className?: string;
}): JSX.Element {
  const id = useId();
  return (
    <div className={cn('space-y-1.5', className)}>
      <label
        htmlFor={id}
        className="block text-2xs font-medium uppercase tracking-[0.08em] text-ink-muted"
      >
        {label}
      </label>
      {children(id)}
      {hint ? <p className="text-xs text-ink-faint">{hint}</p> : null}
    </div>
  );
}
