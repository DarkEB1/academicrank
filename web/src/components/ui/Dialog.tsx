import { useCallback, useEffect, useRef } from 'react';
import type { ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';
import { cn } from '@/lib/cn';
import { Button } from './Button';

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** Trap Tab inside `ref`, restore focus to whatever was focused before. */
export function useFocusTrap(open: boolean, ref: React.RefObject<HTMLElement>): void {
  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    const node = ref.current;
    if (!node) return;

    const focusables = () =>
      Array.from(node.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
        (el) => el.offsetParent !== null || el === document.activeElement,
      );

    const first = focusables()[0] ?? node;
    first.focus();

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Tab') return;
      const items = focusables();
      if (items.length === 0) {
        event.preventDefault();
        return;
      }
      const firstEl = items[0];
      const lastEl = items[items.length - 1];
      if (event.shiftKey && document.activeElement === firstEl) {
        event.preventDefault();
        lastEl.focus();
      } else if (!event.shiftKey && document.activeElement === lastEl) {
        event.preventDefault();
        firstEl.focus();
      }
    };

    node.addEventListener('keydown', onKeyDown);
    return () => {
      node.removeEventListener('keydown', onKeyDown);
      previous?.focus?.();
    };
  }, [open, ref]);
}

export function useEscape(open: boolean, onClose: () => void): void {
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation();
        onClose();
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, onClose]);
}

export function Dialog({
  open,
  onClose,
  title,
  description,
  children,
  className,
  align = 'center',
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: string;
  children: ReactNode;
  className?: string;
  align?: 'center' | 'top';
}): JSX.Element | null {
  const ref = useRef<HTMLDivElement>(null);
  useFocusTrap(open, ref);
  useEscape(open, onClose);

  useEffect(() => {
    if (!open) return;
    const original = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = original;
    };
  }, [open]);

  const onBackdrop = useCallback(
    (event: React.MouseEvent<HTMLDivElement>) => {
      if (event.target === event.currentTarget) onClose();
    },
    [onClose],
  );

  if (!open) return null;

  return createPortal(
    <div
      className={cn(
        'fixed inset-0 z-50 flex justify-center bg-canvas/70 p-4 backdrop-blur-[1px]',
        align === 'center' ? 'items-center' : 'items-start pt-[12vh]',
      )}
      onMouseDown={onBackdrop}
    >
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        aria-description={description}
        className={cn(
          'animate-fade-in max-h-[86vh] w-full overflow-hidden rounded-sm border border-rule-strong bg-surface shadow-2xl shadow-black/10 dark:shadow-black/50',
          className ?? 'max-w-2xl',
        )}
      >
        {children}
      </div>
    </div>,
    document.body,
  );
}

export function DialogHeader({
  title,
  description,
  onClose,
}: {
  title: ReactNode;
  description?: ReactNode;
  onClose: () => void;
}): JSX.Element {
  return (
    <div className="flex items-start justify-between gap-4 border-b border-rule px-5 py-3.5">
      <div className="min-w-0">
        <h2 className="text-sm font-semibold text-ink">{title}</h2>
        {description ? (
          <p className="mt-1 max-w-measure text-xs leading-relaxed text-ink-muted">{description}</p>
        ) : null}
      </div>
      <Button variant="ghost" size="icon" onClick={onClose} aria-label="Close">
        <X className="h-4 w-4" aria-hidden />
      </Button>
    </div>
  );
}

/** Right-hand side panel. Used for the Explain drawer. */
export function SidePanel({
  open,
  onClose,
  title,
  children,
  width = 'max-w-xl',
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  width?: string;
}): JSX.Element | null {
  const ref = useRef<HTMLDivElement>(null);
  useFocusTrap(open, ref);
  useEscape(open, onClose);

  if (!open) return null;

  return createPortal(
    <div className="fixed inset-0 z-40 flex justify-end">
      <div
        className="absolute inset-0 bg-canvas/60"
        onMouseDown={onClose}
        aria-hidden
      />
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={cn(
          'animate-slide-in-right relative flex h-full w-full flex-col border-l border-rule-strong bg-surface shadow-2xl shadow-black/10 dark:shadow-black/60',
          width,
        )}
      >
        {children}
      </div>
    </div>,
    document.body,
  );
}
