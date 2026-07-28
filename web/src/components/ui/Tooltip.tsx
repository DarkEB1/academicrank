import { useId, useState } from 'react';
import type { ReactNode } from 'react';
import { cn } from '@/lib/cn';

/**
 * Hover *and* focus triggered. By default the content is also exposed to
 * assistive technology via aria-describedby; pass `visualOnly` where the
 * trigger already carries an equivalent accessible name, so screen-reader users
 * do not hear the same figures twice on every row of a table.
 */
export function Tooltip({
  content,
  children,
  className,
  side = 'top',
  visualOnly = false,
}: {
  content: ReactNode;
  children: ReactNode;
  className?: string;
  side?: 'top' | 'bottom';
  visualOnly?: boolean;
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
      {/* flex-1/min-w-0 so a full-width trigger (an error bar) actually fills
          the wrapper instead of collapsing to its content width. */}
      <span
        aria-describedby={visualOnly ? undefined : id}
        className="inline-flex min-w-0 flex-1"
      >
        {children}
      </span>

      {/* `sr-only` rather than hidden-but-laid-out: an invisible absolutely
          positioned box still widens the document and forces horizontal scroll
          at narrow viewports. */}
      {visualOnly ? null : (
        <span role="tooltip" id={id} className="sr-only">
          {content}
        </span>
      )}

      {open ? (
        <span
          aria-hidden
          className={cn(
            'pointer-events-none absolute left-1/2 z-50 w-64 -translate-x-1/2 rounded-sm border border-rule-strong bg-raised px-2.5 py-2 text-xs leading-relaxed text-ink shadow-lg shadow-black/10 dark:shadow-black/50',
            side === 'top' ? 'bottom-full mb-1.5' : 'top-full mt-1.5',
          )}
        >
          {content}
        </span>
      ) : null}
    </span>
  );
}
