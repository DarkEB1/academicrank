import { useState } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';
import { Command, Loader2 } from 'lucide-react';
import { cn } from '@/lib/cn';
import { useCommandKey } from '@/lib/hooks';
import { useSession } from '@/lib/session';
import { useHealth, useSeedCount } from '@/lib/queries';
import { CommandPalette } from './CommandPalette';
import { ThemeToggle } from './ThemeToggle';
import { ProximityStatement } from './Honesty';
import { ErrorBoundary } from './ErrorBoundary';
import { formatCount } from '@/lib/format';
import { Button } from './ui/Button';

const NAV = [
  { to: '/', label: 'Rankings', end: true },
  { to: '/trust', label: 'Trust set', end: false },
  { to: '/uploads', label: 'Uploads', end: false },
  { to: '/recommendations', label: 'Recommendations', end: false },
  { to: '/graph', label: 'Graph', end: false },
  { to: '/params', label: 'Parameters', end: false },
];

function HealthDot(): JSX.Element | null {
  const health = useHealth();
  if (health.isLoading) return null;

  const ok = health.data?.ok === true && health.data?.graph_loaded === true;
  const label = health.isError
    ? 'API unreachable'
    : ok
      ? `Graph loaded: ${formatCount(health.data?.nodes ?? 0)} nodes, ${formatCount(
          health.data?.edges ?? 0,
        )} edges`
      : 'Graph not fully loaded — scores may be incomplete';

  return (
    <span className="hidden items-center gap-1.5 text-2xs text-ink-muted lg:inline-flex" title={label}>
      <span
        aria-hidden
        className={cn(
          'h-1.5 w-1.5 rounded-full',
          health.isError ? 'bg-critical' : ok ? 'bg-positive' : 'bg-caution',
        )}
      />
      <span className="tnum">
        {health.isError
          ? 'API down'
          : `${formatCount(health.data?.nodes ?? 0)} nodes · ${formatCount(health.data?.edges ?? 0)} edges`}
      </span>
    </span>
  );
}

function SeedCount({ profileId }: { profileId: string }): JSX.Element | null {
  const seeds = useSeedCount(profileId);
  if (seeds.isLoading) return null;
  return (
    <span className="tnum">
      {seeds.count} seed{seeds.count === 1 ? '' : 's'}
    </span>
  );
}

export function AppShell(): JSX.Element {
  const [paletteOpen, setPaletteOpen] = useState(false);
  const { profile, status } = useSession();
  const location = useLocation();
  useCommandKey(() => setPaletteOpen((v) => !v));

  return (
    <div className="min-h-screen bg-canvas">
      {/* Under HashRouter the location *is* the hash, so a plain href="#main"
          is parsed as the route "main" and bounces the user to "/" via the
          catch-all. Move focus directly instead and leave the route alone. */}
      <a
        href="#main"
        onClick={(event) => {
          event.preventDefault();
          document.getElementById('main')?.focus();
        }}
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-sm focus:bg-surface focus:px-3 focus:py-2 focus:text-sm focus:text-ink"
      >
        Skip to content
      </a>

      <header className="sticky top-0 z-30 border-b border-rule bg-canvas/95 backdrop-blur-sm">
        <div className="mx-auto flex h-14 max-w-[110rem] items-center gap-6 px-6">
          <NavLink to="/" className="flex shrink-0 items-baseline gap-2">
            <span className="font-serif text-lg tracking-tight text-ink">Provenance</span>
            <span className="hidden text-2xs uppercase tracking-[0.14em] text-ink-faint sm:inline">
              trust graph
            </span>
          </NavLink>

          <nav aria-label="Primary" className="flex min-w-0 items-center gap-1 overflow-x-auto">
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) =>
                  cn(
                    'whitespace-nowrap rounded-sm px-2.5 py-1.5 text-sm transition-colors',
                    isActive
                      ? 'bg-raised font-medium text-ink'
                      : 'text-ink-muted hover:bg-raised/60 hover:text-ink',
                  )
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>

          <div className="ml-auto flex shrink-0 items-center gap-3">
            <HealthDot />
            <span className="hidden items-center gap-1.5 text-2xs text-ink-muted xl:inline-flex">
              {status === 'loading' ? (
                <Loader2 aria-hidden className="h-3 w-3 animate-spin" />
              ) : profile ? (
                <SeedCount profileId={profile.id} />
              ) : null}
            </span>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setPaletteOpen(true)}
              aria-label="Open command palette"
              className="gap-1.5 border border-rule"
            >
              <Command aria-hidden className="h-3.5 w-3.5" />
              <span className="hidden font-mono text-2xs md:inline">K</span>
            </Button>
            <ThemeToggle />
          </div>
        </div>
      </header>

      <ProximityStatement />

      {/* tabIndex -1 so the skip link can put focus here without adding it to
          the tab order. */}
      <main id="main" tabIndex={-1} className="mx-auto max-w-[110rem] px-6 py-8 focus:outline-none">
        <ErrorBoundary section="This screen" key={location.pathname}>
          <Outlet />
        </ErrorBoundary>
      </main>

      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
    </div>
  );
}
