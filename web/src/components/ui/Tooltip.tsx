import { useId, useState } from 'react';
import type { ReactNode } from 'react';
import { cn } from '@/lib/cn';

/**
 * Hover *and* focus triggered, described by aria-describedby so the content is
 * available to screen readers rather than being decorative hover-only text.
 */
export function Tooltip({
  content,
  children,
  className,
  side = 'top',
}: {
  content: ReactNode;
  children: ReactNode;
  className?: string;
  side?: 'top' | 'bottom';
}): JSX.Element {
  const [open, setOpen] = useState(false);
  const id = useId();

  return (
    <span
      className={cn('relative inline-flex', className)}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onFocusCapture={() => setOpen(true)}
      onBlurCapture={() => setOpen(false)}
    >
      <span aria-describedby={id} className="inline-flex">
        {children}
      </span>
      <span
        role="tooltip"
        id={id}
        className={cn(
          'pointer-events-none absolute left-1/2 z-50 w-64 -translate-x-1/2 rounded-sm border border-rule-strong bg-raised px-2.5 py-2 text-xs leading-relaxed text-ink shadow-lg shadow-black/10 dark:shadow-black/50',
          side === 'top' ? 'bottom-full mb-1.5' : 'top-full mt-1.5',
          open ? 'opacity-100' : 'invisible opacity-0',
        )}
      >
        {content}
      </span>
    </span>
  );
}
