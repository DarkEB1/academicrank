import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { RefreshCw, ServerCrash, Unplug } from 'lucide-react';
import { ApiError, NetworkError } from '@/lib/api';
import { Button, buttonClass } from './ui/Button';
import { cn } from '@/lib/cn';

/** Empty states teach the next action; they never just say "no data". */
export function EmptyState({
  title,
  children,
  action,
  className,
}: {
  title: string;
  children?: ReactNode;
  action?: ReactNode;
  className?: string;
}): JSX.Element {
  return (
    <div className={cn('mx-auto max-w-measure px-6 py-16 text-center', className)}>
      <h3 className="font-serif text-lg text-ink">{title}</h3>
      {children ? (
        <div className="mt-2 text-sm leading-relaxed text-ink-muted">{children}</div>
      ) : null}
      {action ? <div className="mt-5 flex justify-center gap-2">{action}</div> : null}
    </div>
  );
}

export function NoTrustSetYet({ what }: { what: string }): JSX.Element {
  return (
    <EmptyState
      title="Nothing to rank yet"
      action={
        <Link to="/trust" className={buttonClass('primary', 'md')}>
          Build your trust set
        </Link>
      }
    >
      <p>
        {what} is computed from your trust set: the papers you have told Provenance you consider
        sound. Until you name some, there is no ego node to walk from and nothing here would mean
        anything.
      </p>
      <p className="mt-2">
        Add at least five papers. Fewer than that and the rankings are dominated by whichever
        single seed you happened to pick first.
      </p>
    </EmptyState>
  );
}

export function ErrorState({
  error,
  onRetry,
  className,
}: {
  error: unknown;
  onRetry?: () => void;
  className?: string;
}): JSX.Element {
  const isNetwork = error instanceof NetworkError;
  const api = error instanceof ApiError ? error : null;
  const message =
    isNetwork || api
      ? (error as Error).message
      : error instanceof Error
        ? error.message
        : 'An unexpected error occurred.';

  return (
    <div
      role="alert"
      className={cn(
        'mx-auto max-w-measure rounded-sm border border-critical/30 bg-critical/5 px-5 py-6',
        className,
      )}
    >
      <div className="flex items-start gap-3">
        {isNetwork ? (
          <Unplug aria-hidden className="mt-0.5 h-5 w-5 shrink-0 text-critical" />
        ) : (
          <ServerCrash aria-hidden className="mt-0.5 h-5 w-5 shrink-0 text-critical" />
        )}
        <div className="min-w-0 space-y-2">
          <p className="text-sm font-medium text-ink">
            {isNetwork
              ? 'The API is not answering'
              : api?.status === 404
                ? 'Not found'
                : api?.status === 422
                  ? 'The server rejected that request'
                  : 'The request failed'}
          </p>
          <p className="text-sm leading-relaxed text-ink-muted">{message}</p>
          {isNetwork ? (
            <p className="text-xs leading-relaxed text-ink-faint">
              The development server proxies <code className="font-mono">/api</code> to{' '}
              <code className="font-mono">http://localhost:8000</code>. Start the backend, then
              retry.
            </p>
          ) : null}
          {api?.status === 422 ? (
            <p className="text-xs leading-relaxed text-ink-faint">
              422 on a parameter write means the engine does not actually honour that parameter.
              The API rejects it rather than pretending to apply it.
            </p>
          ) : null}
          {onRetry ? (
            <Button onClick={onRetry} size="sm" className="mt-1">
              <RefreshCw aria-hidden className="h-3.5 w-3.5" />
              Retry
            </Button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
