import { forwardRef } from 'react';
import type { ButtonHTMLAttributes } from 'react';
import { cn } from '@/lib/cn';

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger';
type Size = 'sm' | 'md' | 'icon';

export type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant;
  size?: Size;
};

const VARIANTS: Record<Variant, string> = {
  primary:
    'bg-accent text-canvas hover:bg-accent-ink disabled:bg-accent/40 dark:text-canvas font-medium',
  secondary:
    'bg-surface text-ink border border-rule-strong hover:bg-raised disabled:opacity-50',
  ghost: 'text-ink-muted hover:text-ink hover:bg-raised disabled:opacity-50',
  danger:
    'bg-transparent text-critical border border-critical/40 hover:bg-critical/10 disabled:opacity-50',
};

const SIZES: Record<Size, string> = {
  sm: 'h-7 px-2.5 text-xs gap-1.5',
  md: 'h-9 px-3.5 text-sm gap-2',
  icon: 'h-8 w-8 justify-center',
};

/** Same visual treatment for `<Link>`/`<a>` elements. */
export function buttonClass(variant: Variant = 'secondary', size: Size = 'md', className?: string): string {
  return cn(
    'inline-flex items-center rounded-sm transition-colors duration-100',
    VARIANTS[variant],
    SIZES[size],
    className,
  );
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { className, variant = 'secondary', size = 'md', type = 'button', ...props },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      className={cn(
        'inline-flex items-center rounded-sm transition-colors duration-100',
        'disabled:cursor-not-allowed',
        VARIANTS[variant],
        SIZES[size],
        className,
      )}
      {...props}
    />
  );
});
