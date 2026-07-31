import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Beaker,
  BookMarked,
  Compass,
  CornerDownLeft,
  FileUp,
  ListOrdered,
  Loader2,
  Network,
  Search,
  SunMoon,
} from 'lucide-react';
import { Dialog } from './ui/Dialog';
import { usePaperSearch } from '@/lib/queries';
import { useDebounced } from '@/lib/hooks';
import { PaperTitle } from './Math';
import { formatAuthors, formatYear } from '@/lib/format';
import { applyTheme, resolveInitialTheme } from '@/lib/theme';
import { cn } from '@/lib/cn';

type Command = {
  id: string;
  label: string;
  hint?: string;
  icon: JSX.Element;
  run: () => void;
};

export function CommandPalette({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}): JSX.Element | null {
  const navigate = useNavigate();
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const listRef = useRef<HTMLUListElement>(null);
  const debounced = useDebounced(query, 220);

  const search = usePaperSearch({ q: debounced }, open && debounced.trim().length >= 2);

  useEffect(() => {
    if (open) {
      setQuery('');
      setActive(0);
    }
  }, [open]);

  const navCommands = useMemo<Command[]>(() => {
    const go = (path: string) => () => {
      navigate(path);
      onClose();
    };
    return [
      {
        id: 'nav-rankings',
        label: 'Rankings',
        hint: 'Ranked by proximity to your trust set',
        icon: <ListOrdered aria-hidden className="h-4 w-4" />,
        run: go('/'),
      },
      {
        id: 'nav-trust',
        label: 'Trust set',
        hint: 'Add, weight and remove seed papers',
        icon: <BookMarked aria-hidden className="h-4 w-4" />,
        run: go('/trust'),
      },
      {
        id: 'nav-uploads',
        label: 'Uploads',
        hint: 'Seed your trust set from a paper you wrote',
        icon: <FileUp aria-hidden className="h-4 w-4" />,
        run: go('/uploads'),
      },
      {
        id: 'nav-recs',
        label: 'Recommendations',
        hint: 'Exploitation ←→ exploration',
        icon: <Compass aria-hidden className="h-4 w-4" />,
        run: go('/recommendations'),
      },
      {
        id: 'nav-graph',
        label: 'Graph explorer',
        hint: 'The trust neighbourhood, rendered',
        icon: <Network aria-hidden className="h-4 w-4" />,
        run: go('/graph'),
      },
      {
        id: 'nav-params',
        label: 'Parameter playground',
        hint: 'Change the weights and watch the ranking move',
        icon: <Beaker aria-hidden className="h-4 w-4" />,
        run: go('/params'),
      },
      {
        id: 'theme',
        label: 'Toggle light / dark theme',
        icon: <SunMoon aria-hidden className="h-4 w-4" />,
        run: () => {
          applyTheme(resolveInitialTheme() === 'dark' ? 'light' : 'dark');
          onClose();
        },
      },
    ];
  }, [navigate, onClose]);

  const q = query.trim().toLowerCase();
  const filteredNav = q
    ? navCommands.filter((c) => c.label.toLowerCase().includes(q) || c.hint?.toLowerCase().includes(q))
    : navCommands;

  const paperItems = (search.data?.items ?? []).slice(0, 8);

  const rows: Command[] = [
    ...filteredNav,
    ...paperItems.map<Command>((paper) => ({
      id: `paper-${paper.id}`,
      label: paper.title ?? 'Untitled record',
      hint: `${formatAuthors(paper.authors, 2)} · ${formatYear(paper.year)}`,
      icon: <Search aria-hidden className="h-4 w-4" />,
      run: () => {
        navigate(`/paper/${paper.id}`);
        onClose();
      },
    })),
  ];

  useEffect(() => {
    setActive((prev) => Math.min(prev, Math.max(0, rows.length - 1)));
  }, [rows.length]);

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      setActive((i) => (rows.length === 0 ? 0 : (i + 1) % rows.length));
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      setActive((i) => (rows.length === 0 ? 0 : (i - 1 + rows.length) % rows.length));
    } else if (event.key === 'Enter') {
      event.preventDefault();
      rows[active]?.run();
    }
  };

  useEffect(() => {
    const node = listRef.current?.querySelector<HTMLElement>('[data-active="true"]');
    node?.scrollIntoView({ block: 'nearest' });
  }, [active]);

  if (!open) return null;

  return (
    <Dialog open={open} onClose={onClose} title="Command palette" align="top" className="max-w-xl">
      <div className="flex items-center gap-2.5 border-b border-rule px-4">
        <Search aria-hidden className="h-4 w-4 shrink-0 text-ink-faint" />
        <input
          autoFocus
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={onKeyDown}
          role="combobox"
          aria-expanded
          aria-controls="command-list"
          aria-activedescendant={rows[active] ? `cmd-${rows[active].id}` : undefined}
          placeholder="Go to a screen, or search the corpus…"
          className="h-12 w-full border-0 bg-transparent text-sm text-ink placeholder:text-ink-faint focus:outline-none"
        />
        {search.isFetching ? (
          <Loader2 aria-hidden className="h-4 w-4 shrink-0 animate-spin text-ink-faint" />
        ) : null}
      </div>

      <ul id="command-list" role="listbox" ref={listRef} className="max-h-[52vh] overflow-y-auto py-1.5">
        {rows.length === 0 ? (
          <li className="px-4 py-6 text-center text-sm text-ink-muted">
            {debounced.trim().length >= 2
              ? 'No screens or papers matched.'
              : 'Type at least two characters to search the corpus.'}
          </li>
        ) : null}
        {rows.map((row, index) => (
          <li key={row.id}>
            <button
              id={`cmd-${row.id}`}
              type="button"
              role="option"
              aria-selected={index === active}
              data-active={index === active}
              onMouseEnter={() => setActive(index)}
              onClick={row.run}
              className={cn(
                'flex w-full items-center gap-3 px-4 py-2 text-left',
                index === active ? 'bg-accent-wash' : 'bg-transparent',
              )}
            >
              <span className="shrink-0 text-ink-faint">{row.icon}</span>
              <span className="min-w-0 flex-1">
                {row.id.startsWith('paper-') ? (
                  <PaperTitle as="span" title={row.label} className="block truncate text-sm text-ink" />
                ) : (
                  <span className="block truncate text-sm text-ink">{row.label}</span>
                )}
                {row.hint ? (
                  <span className="block truncate text-xs text-ink-muted">{row.hint}</span>
                ) : null}
              </span>
              {index === active ? (
                <CornerDownLeft aria-hidden className="h-3.5 w-3.5 shrink-0 text-ink-faint" />
              ) : null}
            </button>
          </li>
        ))}
      </ul>

      <p className="border-t border-rule px-4 py-2 text-2xs text-ink-faint">
        ↑↓ to move · ⏎ to open · esc to dismiss
      </p>
    </Dialog>
  );
}
