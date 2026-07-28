import { useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Check,
  FileUp,
  Loader2,
  Plus,
  Search as SearchIcon,
  ThumbsDown,
  Trash2,
} from 'lucide-react';
import {
  useImportBibtex,
  usePaperSearch,
  useSetTrust,
  useSimulate,
  useTrustSet,
} from '@/lib/queries';
import { useSession } from '@/lib/session';
import { useDebounced } from '@/lib/hooks';
import type { PaperBrief, SimulateResponse, TrustEntry } from '@/lib/types';
import { formatAuthors, formatCount, formatYear } from '@/lib/format';
import { PaperTitle } from '@/components/Math';
import { StrengthPicker, STRENGTH_LABELS } from '@/components/StrengthPicker';
import { SimulationPreview } from '@/components/SimulationPreview';
import { Notice } from '@/components/Honesty';
import { EmptyState, ErrorState } from '@/components/States';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Badge } from '@/components/ui/Badge';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import { CardSkeleton, LoadingRegion, Skeleton } from '@/components/ui/Skeleton';
import { Dialog, DialogHeader } from '@/components/ui/Dialog';
import { cn } from '@/lib/cn';

const DEFAULT_STRENGTH = 3;

export function TrustSetScreen(): JSX.Element {
  const { profile } = useSession();
  const profileId = profile?.id ?? '';

  const trust = useTrustSet(profileId);
  const setTrust = useSetTrust(profileId);
  const simulate = useSimulate(profileId);

  const [query, setQuery] = useState('');
  const debounced = useDebounced(query, 250);
  const search = usePaperSearch(debounced);

  const [pendingStrength, setPendingStrength] = useState<Record<string, number>>({});
  const [preview, setPreview] = useState<{
    paper: PaperBrief;
    mode: 'add' | 'remove';
    result: SimulateResponse | null;
  } | null>(null);

  const entries = trust.data?.items ?? [];
  const trusted = entries.filter((e) => !e.is_distrust);
  const distrusted = entries.filter((e) => e.is_distrust);
  const seeds = trusted.length;
  const inSet = new Set(entries.map((e) => e.work.id));

  const add = (paper: PaperBrief, strength: number, isDistrust = false) => {
    setTrust.mutate({ work_id: paper.id, strength, is_distrust: isDistrust });
  };

  const runPreview = (paper: PaperBrief, mode: 'add' | 'remove') => {
    setPreview({ paper, mode, result: null });
    simulate.mutate(
      mode === 'add'
        ? {
            add: [{ work_id: paper.id, strength: pendingStrength[paper.id] ?? DEFAULT_STRENGTH }],
            limit: 25,
          }
        : { remove: [paper.id], limit: 25 },
      {
        onSuccess: (result) => setPreview((prev) => (prev ? { ...prev, result } : null)),
      },
    );
  };

  return (
    <div className="space-y-8">
      <header className="space-y-3">
        <h1 className="font-serif text-2xl tracking-tight text-ink">Trust set</h1>
        <p className="max-w-measure text-sm leading-relaxed text-ink-muted">
          Name the papers you consider sound. Everything else in Provenance is computed by walking
          outwards from these, so the trust set is not a preference — it is the entire input. There
          is no correct trust set; there is only yours, and its consequences.
        </p>
      </header>

      {seeds < 5 ? (
        <Notice
          title={
            seeds === 0
              ? 'No seeds yet — nothing can be ranked'
              : `${seeds} seed${seeds === 1 ? '' : 's'}: rankings are unreliable`
          }
        >
          <p>
            Below five seeds, a personalised ranking is largely an artefact of which paper you
            happened to add first. One more seed can rewrite the entire top ten — use the{' '}
            <strong className="font-medium text-ink">Preview impact</strong> control on any search
            result to see exactly how much.
          </p>
          <p className="mt-1.5">
            This notice stays until you have five. It is not dismissable, because the unreliability
            is not dismissable.
          </p>
        </Notice>
      ) : null}

      <div className="grid gap-8 xl:grid-cols-[minmax(0,1fr)_minmax(0,26rem)]">
        {/* ---------------- search + add ---------------- */}
        <div className="space-y-5">
          <Card>
            <CardHeader
              title="Find papers to seed"
              description="Full-text search over the corpus. Pick work you know well enough to vouch for; a seed you are unsure about propagates that uncertainty everywhere."
            />
            <CardBody className="space-y-4">
              <Input
                type="search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search titles and abstracts…"
                aria-label="Search papers"
                leading={<SearchIcon aria-hidden className="h-4 w-4" />}
              />

              {debounced.trim().length > 0 && debounced.trim().length < 2 ? (
                <p className="text-xs text-ink-muted">Type at least two characters.</p>
              ) : null}

              {search.isError ? (
                <ErrorState error={search.error} onRetry={() => void search.refetch()} />
              ) : search.isLoading && debounced.trim().length >= 2 ? (
                <LoadingRegion label="Searching the corpus" className="space-y-4 py-2">
                  {[0, 1, 2].map((i) => (
                    <CardSkeleton key={i} lines={2} />
                  ))}
                </LoadingRegion>
              ) : search.data ? (
                search.data.items.length === 0 ? (
                  <EmptyState title="Nothing matched" className="py-10">
                    <p>
                      The corpus is a slice of OpenAlex, not all of it. Try the exact title, an
                      author surname, or a DOI-bearing paper you know is indexed. Non-English and
                      pre-digital work is frequently absent altogether.
                    </p>
                  </EmptyState>
                ) : (
                  <>
                    <p className="text-2xs uppercase tracking-[0.08em] text-ink-faint">
                      {formatCount(search.data.total)} match
                      {search.data.total === 1 ? '' : 'es'} · showing {search.data.items.length}
                    </p>
                    <ul className="divide-y divide-rule">
                      {search.data.items.map((paper) => (
                        <SearchResultRow
                          key={paper.id}
                          paper={paper}
                          alreadyIn={inSet.has(paper.id)}
                          strength={pendingStrength[paper.id] ?? DEFAULT_STRENGTH}
                          onStrength={(value) =>
                            setPendingStrength((prev) => ({ ...prev, [paper.id]: value }))
                          }
                          onAdd={() => add(paper, pendingStrength[paper.id] ?? DEFAULT_STRENGTH)}
                          onDistrust={() => add(paper, pendingStrength[paper.id] ?? DEFAULT_STRENGTH, true)}
                          onPreview={() => runPreview(paper, 'add')}
                          busy={setTrust.isPending}
                        />
                      ))}
                    </ul>
                  </>
                )
              ) : (
                <p className="py-6 text-sm text-ink-muted">
                  Search above, or import a BibTeX file to seed from a bibliography you already
                  keep.
                </p>
              )}
            </CardBody>
          </Card>

          <BibtexImport profileId={profileId} />
        </div>

        {/* ---------------- current set ---------------- */}
        <div className="space-y-5">
          <Card>
            <CardHeader
              title={`Your trust set (${seeds})`}
              description="Strength 1–5 scales the weight of the edge from your profile to the paper. It is a ratio, not a rating out of five."
            />
            <CardBody className="p-0">
              {trust.isError ? (
                <ErrorState error={trust.error} onRetry={() => void trust.refetch()} className="m-4" />
              ) : trust.isLoading ? (
                <LoadingRegion label="Loading your trust set" className="space-y-4 p-5">
                  {[0, 1, 2].map((i) => (
                    <Skeleton key={i} className="h-14 w-full" />
                  ))}
                </LoadingRegion>
              ) : trusted.length === 0 ? (
                <EmptyState title="Empty" className="py-10">
                  <p>
                    Add three to ten papers you know well. Breadth matters more than count: ten
                    papers from one subfield produce a narrower graph than five spread across the
                    areas you actually read.
                  </p>
                </EmptyState>
              ) : (
                <ul className="divide-y divide-rule">
                  {trusted.map((entry) => (
                    <TrustRow
                      key={entry.work.id}
                      entry={entry}
                      busy={setTrust.isPending}
                      onStrength={(value) =>
                        setTrust.mutate({ work_id: entry.work.id, strength: value })
                      }
                      onRemove={() => setTrust.mutate({ work_id: entry.work.id, strength: 0 })}
                      onDistrust={() =>
                        setTrust.mutate({
                          work_id: entry.work.id,
                          strength: entry.strength,
                          is_distrust: true,
                        })
                      }
                      onPreview={() => runPreview(entry.work, 'remove')}
                    />
                  ))}
                </ul>
              )}
            </CardBody>
          </Card>

          <Card>
            <CardHeader
              title={`Distrusted (${distrusted.length})`}
              description="A negative edge. Use it for work you believe is unsound, not merely work you dislike — it suppresses everything downstream of it, including papers that only cite it in passing."
            />
            <CardBody className="p-0">
              {distrusted.length === 0 ? (
                <p className="px-5 py-6 text-sm text-ink-muted">
                  Nothing marked as distrusted. This is usually the right state.
                </p>
              ) : (
                <ul className="divide-y divide-rule">
                  {distrusted.map((entry) => (
                    <TrustRow
                      key={entry.work.id}
                      entry={entry}
                      busy={setTrust.isPending}
                      onStrength={(value) =>
                        setTrust.mutate({
                          work_id: entry.work.id,
                          strength: value,
                          is_distrust: true,
                        })
                      }
                      onRemove={() => setTrust.mutate({ work_id: entry.work.id, strength: 0 })}
                      onDistrust={() =>
                        setTrust.mutate({
                          work_id: entry.work.id,
                          strength: entry.strength,
                          is_distrust: false,
                        })
                      }
                      onPreview={() => runPreview(entry.work, 'remove')}
                    />
                  ))}
                </ul>
              )}
            </CardBody>
          </Card>

          {seeds >= 5 ? (
            <p className="text-xs leading-relaxed text-ink-muted">
              With {seeds} seeds the ranking is usable.{' '}
              <Link to="/" className="link">
                Go to rankings
              </Link>
              , or check the{' '}
              <Link to="/params" className="link">
                parameter playground
              </Link>{' '}
              to see how much of the order is the weights rather than the data.
            </p>
          ) : null}
        </div>
      </div>

      {setTrust.isError ? <ErrorState error={setTrust.error} /> : null}

      <Dialog
        open={Boolean(preview)}
        onClose={() => setPreview(null)}
        title="Impact preview"
        className="max-w-3xl"
      >
        {preview ? (
          <>
            <DialogHeader
              title={preview.mode === 'add' ? 'If you add this seed' : 'If you remove this seed'}
              description="Simulated against a scratch copy of your profile. Nothing is saved."
              onClose={() => setPreview(null)}
            />
            <div className="max-h-[60vh] overflow-y-auto px-5 py-4">
              <PaperTitle
                as="p"
                title={preview.paper.title}
                className="mb-4 text-sm leading-snug text-ink"
              />
              {simulate.isPending || !preview.result ? (
                simulate.isError ? (
                  <ErrorState error={simulate.error} />
                ) : (
                  <LoadingRegion label="Simulating" className="space-y-3">
                    <Skeleton className="h-16 w-full" />
                    <Skeleton className="h-40 w-full" />
                  </LoadingRegion>
                )
              ) : (
                <SimulationPreview result={preview.result} />
              )}
            </div>
            <div className="flex justify-end gap-2 border-t border-rule px-5 py-3">
              <Button onClick={() => setPreview(null)}>Close</Button>
              {preview.mode === 'add' ? (
                <Button
                  variant="primary"
                  onClick={() => {
                    add(preview.paper, pendingStrength[preview.paper.id] ?? DEFAULT_STRENGTH);
                    setPreview(null);
                  }}
                >
                  Add this seed
                </Button>
              ) : null}
            </div>
          </>
        ) : null}
      </Dialog>
    </div>
  );
}

function SearchResultRow({
  paper,
  alreadyIn,
  strength,
  onStrength,
  onAdd,
  onDistrust,
  onPreview,
  busy,
}: {
  paper: PaperBrief;
  alreadyIn: boolean;
  strength: number;
  onStrength: (value: number) => void;
  onAdd: () => void;
  onDistrust: () => void;
  onPreview: () => void;
  busy: boolean;
}): JSX.Element {
  return (
    <li className="py-3.5">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <Link to={`/paper/${paper.id}`} className="block">
            <PaperTitle
              as="span"
              title={paper.title}
              className="block text-[0.95rem] leading-snug text-ink hover:text-accent"
            />
          </Link>
          <p className="mt-1 text-xs text-ink-muted">
            {formatAuthors(paper.authors)} · {formatYear(paper.year)}
            {paper.venue ? ` · ${paper.venue.name}` : ''}
          </p>
          <p className="mt-1 font-mono text-2xs tnum text-ink-faint">
            {formatCount(paper.cited_by_count)} citations · {formatCount(paper.in_corpus_cited_by)}{' '}
            inside this corpus
            {paper.is_stub ? ' · stub record' : ''}
          </p>
        </div>
        {alreadyIn ? (
          <Badge tone="positive" className="mt-1 shrink-0">
            <Check aria-hidden className="h-3 w-3" />
            in set
          </Badge>
        ) : null}
      </div>

      {!alreadyIn ? (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <StrengthPicker value={strength} onChange={onStrength} size="sm" disabled={busy} />
          <span className="mr-auto text-2xs text-ink-faint">{STRENGTH_LABELS[strength]}</span>
          <Button size="sm" onClick={onPreview}>
            Preview impact
          </Button>
          <Button size="sm" variant="ghost" onClick={onDistrust} title="Mark as distrusted">
            <ThumbsDown aria-hidden className="h-3.5 w-3.5" />
          </Button>
          <Button size="sm" variant="primary" onClick={onAdd} disabled={busy}>
            {busy ? (
              <Loader2 aria-hidden className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Plus aria-hidden className="h-3.5 w-3.5" />
            )}
            Add
          </Button>
        </div>
      ) : null}
    </li>
  );
}

function TrustRow({
  entry,
  busy,
  onStrength,
  onRemove,
  onDistrust,
  onPreview,
}: {
  entry: TrustEntry;
  busy: boolean;
  onStrength: (value: number) => void;
  onRemove: () => void;
  onDistrust: () => void;
  onPreview: () => void;
}): JSX.Element {
  return (
    <li className={cn('px-5 py-3.5', entry.is_distrust && 'bg-critical/5')}>
      <Link to={`/paper/${entry.work.id}`} className="block">
        <PaperTitle
          as="span"
          title={entry.work.title}
          className="block text-sm leading-snug text-ink hover:text-accent"
        />
      </Link>
      <p className="mt-1 truncate text-xs text-ink-muted">
        {formatAuthors(entry.work.authors, 2)} · {formatYear(entry.work.year)}
      </p>
      <div className="mt-2.5 flex flex-wrap items-center gap-2">
        <StrengthPicker
          value={entry.strength}
          onChange={onStrength}
          size="sm"
          disabled={busy}
          label={`Strength for ${entry.work.title ?? 'this paper'}`}
        />
        <span className="mr-auto text-2xs text-ink-faint">{STRENGTH_LABELS[entry.strength]}</span>
        <Button size="sm" variant="ghost" onClick={onPreview} title="Preview removing this seed">
          Impact
        </Button>
        <Button
          size="sm"
          variant="ghost"
          onClick={onDistrust}
          title={entry.is_distrust ? 'Convert back to trust' : 'Convert to distrust'}
        >
          <ThumbsDown
            aria-hidden
            className={cn('h-3.5 w-3.5', entry.is_distrust && 'text-critical')}
          />
        </Button>
        <Button size="sm" variant="danger" onClick={onRemove} disabled={busy} title="Remove">
          <Trash2 aria-hidden className="h-3.5 w-3.5" />
        </Button>
      </div>
    </li>
  );
}

function BibtexImport({ profileId }: { profileId: string }): JSX.Element {
  const importer = useImportBibtex(profileId);
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  const handle = (file: File | undefined) => {
    if (!file) return;
    importer.mutate(file);
  };

  return (
    <Card>
      <CardHeader
        title="Import BibTeX"
        description="DOIs and titles are resolved against the corpus. Anything that cannot be resolved is listed rather than silently dropped."
      />
      <CardBody className="space-y-4">
        <div
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            handle(e.dataTransfer.files?.[0]);
          }}
          className={cn(
            'rounded-sm border border-dashed px-5 py-8 text-center transition-colors',
            dragging ? 'border-accent bg-accent-wash/50' : 'border-rule-strong',
          )}
        >
          <FileUp aria-hidden className="mx-auto h-5 w-5 text-ink-faint" />
          <p className="mt-2 text-sm text-ink">Drop a .bib file here</p>
          <p className="mt-1 text-xs text-ink-muted">or</p>
          <Button
            className="mt-2"
            size="sm"
            onClick={() => inputRef.current?.click()}
            disabled={importer.isPending}
          >
            {importer.isPending ? (
              <Loader2 aria-hidden className="h-3.5 w-3.5 animate-spin" />
            ) : null}
            Choose a file
          </Button>
          <input
            ref={inputRef}
            type="file"
            accept=".bib,.bibtex,text/plain,application/x-bibtex"
            className="sr-only"
            onChange={(e) => {
              handle(e.target.files?.[0]);
              e.target.value = '';
            }}
          />
        </div>

        {importer.isError ? <ErrorState error={importer.error} /> : null}

        {importer.data ? (
          <div className="space-y-3" role="status">
            <p className="text-sm text-ink">
              <strong className="font-semibold">{importer.data.added}</strong> added to your trust
              set from <strong className="font-semibold">{importer.data.matched.length}</strong>{' '}
              matched record{importer.data.matched.length === 1 ? '' : 's'}.
            </p>
            {importer.data.unmatched.length > 0 ? (
              <details className="rounded-sm border border-rule bg-raised/40 px-3 py-2">
                <summary className="cursor-pointer text-xs text-ink">
                  {importer.data.unmatched.length} entr
                  {importer.data.unmatched.length === 1 ? 'y' : 'ies'} could not be resolved
                </summary>
                <ul className="mt-2 space-y-1 text-xs text-ink-muted">
                  {importer.data.unmatched.map((raw, i) => (
                    <li key={`${raw}-${i}`} className="font-mono text-2xs">
                      {raw}
                    </li>
                  ))}
                </ul>
                <p className="mt-2 text-2xs leading-relaxed text-ink-faint">
                  Unresolved entries are usually absent from the corpus rather than mis-parsed —
                  books, theses, non-English venues and pre-1990 work especially.
                </p>
              </details>
            ) : null}
          </div>
        ) : null}
      </CardBody>
    </Card>
  );
}
