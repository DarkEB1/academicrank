import { cn } from '@/lib/cn';

export function Skeleton({ className }: { className?: string }): JSX.Element {
  return (
    <div
      aria-hidden
      className={cn('animate-shimmer rounded-sm bg-raised', className)}
    />
  );
}

/**
 * Loading regions announce themselves once, rather than firing an update for
 * every shimmering block.
 */
export function LoadingRegion({
  label,
  children,
  className,
}: {
  label: string;
  children: React.ReactNode;
  className?: string;
}): JSX.Element {
  return (
    <div role="status" aria-live="polite" aria-busy className={className}>
      <span className="sr-only">{label}</span>
      {children}
    </div>
  );
}

export function TableRowSkeleton({ rows = 8 }: { rows?: number }): JSX.Element {
  return (
    <LoadingRegion label="Loading rankings. The first query for a new profile warms the trust walks and can take several seconds.">
      <div className="divide-y divide-rule">
        {Array.from({ length: rows }, (_, i) => (
          <div key={i} className="grid grid-cols-[3rem_1fr_9rem_11rem] items-center gap-4 py-3.5">
            <Skeleton className="h-3 w-6" />
            <div className="space-y-2">
              <Skeleton className="h-4 w-full" />
              <Skeleton className="h-3 w-1/3" />
            </div>
            <Skeleton className="h-3 w-20" />
            <Skeleton className="h-6 w-full" />
          </div>
        ))}
      </div>
    </LoadingRegion>
  );
}

export function CardSkeleton({ lines = 3 }: { lines?: number }): JSX.Element {
  return (
    <div className="space-y-3">
      <Skeleton className="h-5 w-2/3" />
      {Array.from({ length: lines }, (_, i) => (
        <Skeleton key={i} className={cn('h-3', i === lines - 1 ? 'w-1/2' : 'w-full')} />
      ))}
    </div>
  );
}
