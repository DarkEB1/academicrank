import type { ReactNode } from 'react';
import { useSession } from '@/lib/session';
import { ErrorState } from '@/components/States';
import { LoadingRegion, Skeleton } from '@/components/ui/Skeleton';

/**
 * Every screen needs a profile id. Rather than thread `profileId | null`
 * through every component, screens render only once the session exists.
 */
export function SessionGate({ children }: { children: ReactNode }): JSX.Element {
  const session = useSession();

  if (session.status === 'loading') {
    return (
      <LoadingRegion label="Starting an anonymous profile" className="space-y-6">
        <Skeleton className="h-7 w-72" />
        <Skeleton className="h-4 w-full max-w-measure" />
        <Skeleton className="h-64 w-full" />
      </LoadingRegion>
    );
  }

  if (session.status === 'error') {
    return (
      <div className="space-y-4">
        <ErrorState error={session.error} onRetry={session.retry} />
        <p className="mx-auto max-w-measure text-center text-xs text-ink-faint">
          If a previous session token has gone stale,{' '}
          <button type="button" onClick={session.reset} className="link">
            discard it and start a new anonymous profile
          </button>
          . Your trust set lives on the server against that token, so this is not reversible.
        </p>
      </div>
    );
  }

  return <>{children}</>;
}
