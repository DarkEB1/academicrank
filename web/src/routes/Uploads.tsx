import { useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Check, FileUp, Loader2, Pencil, Search as SearchIcon } from 'lucide-react';
import {
  useConfirmUpload,
  useCreateUpload,
  usePaperSearch,
  usePatchUpload,
  usePatchUploadReference,
  useUpload,
  useUploads,
} from '@/lib/queries';
import { useSession } from '@/lib/session';
import { useDebounced } from '@/lib/hooks';
import type { Upload, UploadListItem, UploadReference, UploadStatus } from '@/lib/types';
import {
  acceptedCount,
  describeMatch,
  DEFAULT_SEED_STRENGTH,
  POST_IMPORT_DIVERSITY,
  WILL_BE_ADDED_COPY,
} from '@/lib/uploads';
import { formatAuthors, formatCount, formatYear } from '@/lib/format';
import { PaperTitle } from '@/components/Math';
import { StrengthPicker } from '@/components/StrengthPicker';
import { Notice } from '@/components/Honesty';
import { EmptyState, ErrorState } from '@/components/States';
import { UploadUndoDialog, type UndoTarget } from '@/components/UploadUndoDialog';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Badge } from '@/components/ui/Badge';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import { LoadingRegion, Skeleton } from '@/components/ui/Skeleton';
import { Dialog, DialogHeader } from '@/components/ui/Dialog';
import { cn } from '@/lib/cn';

const STATUS_TONE: Record<UploadStatus, 'neutral' | 'accent' | 'caution' | 'positive'> = {
  draft: 'neutral',
  applying: 'accent',
  engine_pending: 'caution',
  confirmed: 'positive',
};

const STATUS_LABEL: Record<UploadStatus, string> = {
  draft: 'draft',
  applying: 'applying',
  engine_pending: 'engine catching up',
  confirmed: 'imported',
};

export function UploadsScreen(): JSX.Element {
  const { profile } = useSession();
  const profileId = profile?.id ?? '';

  const uploads = useUploads(profileId);
  const creator = useCreateUpload(profileId);
  const [activeId, setActiveId] = useState<string | null>(null);
  const active = useUpload(activeId);
  const [undoTarget, setUndoTarget] = useState<UndoTarget | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);

  const handleFile = (file: File | undefined) => {
    if (!file || creator.isPending) return;
    const isPdf = file.type === 'application/pdf' || file.name.toLowerCase().endsWith('.pdf');
    if (!isPdf) {
      setFileError('Only PDF files are accepted here. For a .bib file, use the trust set screen.');
      return;
    }
    setFileError(null);
    creator.mutate(file, { onSuccess: (upload) => setActiveId(upload.id) });
  };

  return (
    <div className="space-y-8">
      <header className="space-y-3">
        <h1 className="font-serif text-2xl tracking-tight text-ink">Uploads</h1>
        <p className="max-w-measure text-sm leading-relaxed text-ink-muted">
          Upload a PDF of a paper you wrote. Its bibliography is parsed and matched against the
          corpus, you review every reference, and the ones you accept become trust seeds at 3/5 —
          the encoding of &ldquo;I cited this&rdquo;, not an endorsement. Everything is reviewable
          before import and undoable afterwards.
        </p>
      </header>

      <Card>
        <CardHeader
          title="Your paper"
          description="One PDF, up to 25 MB and 80 pages. Encrypted or image-only files are refused with the reason; a bibliography that cannot be split unambiguously goes to review rather than being guessed at."
        />
        <CardBody>
          {creator.isPending ? (
            <LoadingRegion
              label="Parsing and matching the bibliography"
              className="flex flex-col items-center gap-3 py-10 text-center"
            >
              <Loader2 aria-hidden className="h-6 w-6 animate-spin text-accent" />
              <p className="text-sm text-ink">Parsing and matching the bibliography…</p>
              <p className="max-w-measure text-xs leading-relaxed text-ink-muted">
                This runs on the server and typically takes 10–60 seconds: every reference is
                matched by DOI, arXiv id, then title. There is no progress to report honestly, so
                none is invented.
              </p>
            </LoadingRegion>
          ) : (
            <DropZone onFile={handleFile} />
          )}

          {fileError ? (
            <p role="alert" className="mt-3 text-sm text-critical">
              {fileError}
            </p>
          ) : null}
          {creator.isError ? (
            <ErrorState error={creator.error} className="mt-4" />
          ) : null}
        </CardBody>
      </Card>

      {activeId ? (
        active.isError ? (
          <ErrorState error={active.error} onRetry={() => void active.refetch()} />
        ) : active.isLoading ? (
          <LoadingRegion label="Loading the draft" className="space-y-3">
            {[0, 1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-14 w-full" />
            ))}
          </LoadingRegion>
        ) : active.data ? (
          <UploadReview upload={active.data} profileId={profileId} />
        ) : null
      ) : null}

      <UploadsList
        uploads={uploads.data?.items ?? []}
        isLoading={uploads.isLoading}
        error={uploads.isError ? uploads.error : null}
        onRetry={() => void uploads.refetch()}
        activeId={activeId}
        onOpen={setActiveId}
        onUndo={setUndoTarget}
      />

      <UploadUndoDialog
        target={undoTarget}
        profileId={profileId}
        onClose={() => setUndoTarget(null)}
        onDone={() => {
          if (undoTarget && undoTarget.id === activeId) setActiveId(null);
        }}
      />
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Drop zone                                                           */
/* ------------------------------------------------------------------ */

function DropZone({ onFile }: { onFile: (file: File | undefined) => void }): JSX.Element {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  return (
    <div
      data-testid="uploads-dropzone"
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragging(false);
        onFile(e.dataTransfer.files?.[0]);
      }}
      className={cn(
        'rounded-sm border border-dashed px-5 py-10 text-center transition-colors',
        dragging ? 'border-accent bg-accent-wash/50' : 'border-rule-strong',
      )}
    >
      <FileUp aria-hidden className="mx-auto h-5 w-5 text-ink-faint" />
      <p className="mt-2 text-sm text-ink">Drop a PDF of your paper here</p>
      <p className="mt-1 text-xs text-ink-muted">or</p>
      <Button className="mt-2" size="sm" onClick={() => inputRef.current?.click()}>
        Choose a PDF
      </Button>
      <input
        ref={inputRef}
        type="file"
        accept=".pdf,application/pdf"
        className="sr-only"
        aria-label="Upload a PDF of your paper"
        onChange={(e) => {
          onFile(e.target.files?.[0]);
          e.target.value = '';
        }}
      />
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Review                                                              */
/* ------------------------------------------------------------------ */

function UploadReview({
  upload,
  profileId,
}: {
  upload: Upload;
  profileId: string;
}): JSX.Element {
  const navigate = useNavigate();
  const patchRef = usePatchUploadReference(upload.id);
  const confirm = useConfirmUpload(profileId);
  const [findFor, setFindFor] = useState<UploadReference | null>(null);

  const editable = upload.status === 'draft';
  const accepted = acceptedCount(upload.references);
  const pendingIdx = patchRef.isPending ? patchRef.variables?.idx : undefined;

  const patch = (idx: number, body: Parameters<typeof patchRef.mutate>[0]['body']) =>
    patchRef.mutate({ idx, body });

  const runImport = () => {
    confirm.mutate(upload.id, {
      onSuccess: (result) => {
        navigate(`/recommendations?diversity=${POST_IMPORT_DIVERSITY}`, {
          state: {
            upload: {
              n_trust: result.n_trust,
              n_added: result.n_added,
              status: result.status,
              detail: result.detail,
            },
          },
        });
      },
    });
  };

  return (
    <Card>
      <CardHeader
        title="Review the bibliography"
        description="Nothing below has touched the graph yet. DOI and arXiv matches are exact and arrive ticked; title-similarity candidates arrive unticked because the tick is your judgement, not ours."
      />
      <CardBody className="space-y-5">
        <OwnPaperTitle upload={upload} editable={editable} />

        {!editable ? (
          <Notice tone="neutral" title={`This upload is ${STATUS_LABEL[upload.status]}`} icon={false}>
            <p>
              The review below is read-only. To take the whole batch back out, use undo in the
              uploads list.
            </p>
          </Notice>
        ) : null}

        <p className="font-mono text-2xs tnum text-ink-faint">
          {formatCount(upload.n_parsed)} parsed · {formatCount(upload.n_matched)} matched ·{' '}
          {formatCount(upload.n_unresolved)} unresolved
        </p>

        {upload.references.length === 0 ? (
          <EmptyState title="No references in this draft" className="py-8">
            <p>The parser found a bibliography heading but no entries survived review.</p>
          </EmptyState>
        ) : (
          <ul data-testid="uploads-review-table" className="divide-y divide-rule border-t border-rule">
            {upload.references.map((ref) => (
              <ReferenceRow
                key={ref.idx}
                reference={ref}
                editable={editable}
                busy={pendingIdx === ref.idx}
                onDecision={(accept) => patch(ref.idx, { decision: accept ? 'accept' : 'pending' })}
                onStrength={(strength) => patch(ref.idx, { strength })}
                onFindMatch={() => setFindFor(ref)}
              />
            ))}
          </ul>
        )}

        {patchRef.isError ? <ErrorState error={patchRef.error} /> : null}
        {confirm.isError ? <ErrorState error={confirm.error} /> : null}

        {editable ? (
          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-rule pt-4">
            <p className="max-w-measure text-xs leading-relaxed text-ink-muted">
              Each accepted reference becomes one seed at the strength shown on its row —
              3/5 unless you raised it. The whole upload counts as one decision in the
              uncertainty arithmetic, not {upload.references.length}.
            </p>
            <Button
              data-testid="uploads-import-button"
              variant="primary"
              disabled={accepted === 0 || confirm.isPending}
              onClick={runImport}
            >
              {confirm.isPending ? (
                <Loader2 aria-hidden className="h-3.5 w-3.5 animate-spin" />
              ) : null}
              Import {accepted} seed{accepted === 1 ? '' : 's'} at {DEFAULT_SEED_STRENGTH}/5
            </Button>
          </div>
        ) : null}
      </CardBody>

      <FindMatchDialog
        reference={findFor}
        onClose={() => setFindFor(null)}
        onPick={(workId) => {
          if (findFor) patch(findFor.idx, { work_id: workId });
          setFindFor(null);
        }}
      />
    </Card>
  );
}

function OwnPaperTitle({ upload, editable }: { upload: Upload; editable: boolean }): JSX.Element {
  const patchUpload = usePatchUpload(upload.id);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(upload.title ?? '');

  const save = () => {
    setEditing(false);
    if ((upload.title ?? '') !== draft.trim()) {
      patchUpload.mutate({ title: draft.trim() });
    }
  };

  return (
    <div className="space-y-1">
      <p className="text-2xs uppercase tracking-[0.08em] text-ink-faint">
        Your paper{upload.filename ? ` · ${upload.filename}` : ''}
      </p>
      {editing ? (
        <form
          className="flex items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            save();
          }}
        >
          <Input
            autoFocus
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            aria-label="Title of your paper"
            onKeyDown={(e) => {
              if (e.key === 'Escape') {
                setDraft(upload.title ?? '');
                setEditing(false);
              }
            }}
          />
          <Button size="sm" variant="primary" type="submit">
            Save
          </Button>
        </form>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <PaperTitle as="p" title={upload.title} className="text-base leading-snug text-ink" />
          {editable ? (
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                setDraft(upload.title ?? '');
                setEditing(true);
              }}
              aria-label="Edit the title of your paper"
            >
              <Pencil aria-hidden className="h-3.5 w-3.5" />
              Edit
            </Button>
          ) : null}
          {patchUpload.isPending ? (
            <Loader2 aria-hidden className="h-3.5 w-3.5 animate-spin text-ink-faint" />
          ) : null}
        </div>
      )}
      <p className="max-w-measure text-xs leading-relaxed text-ink-muted">
        The title identifies your paper in the corpus. Correcting it re-runs the lookup for the
        paper itself; it does not change the references below.
      </p>
      {patchUpload.isError ? <ErrorState error={patchUpload.error} /> : null}
    </div>
  );
}

function ReferenceRow({
  reference,
  editable,
  busy,
  onDecision,
  onStrength,
  onFindMatch,
}: {
  reference: UploadReference;
  editable: boolean;
  busy: boolean;
  onDecision: (accept: boolean) => void;
  onStrength: (strength: number) => void;
  onFindMatch: () => void;
}): JSX.Element {
  const [expanded, setExpanded] = useState(false);
  const match = describeMatch(reference);
  const accepted = reference.decision === 'accept';
  const disabled = !editable || busy;

  return (
    <li
      data-testid={`uploads-row-${reference.idx}`}
      className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-2 py-3.5 sm:grid-cols-[auto_minmax(0,1fr)_auto]"
    >
      <div className="pt-0.5">
        <input
          type="checkbox"
          checked={accepted}
          disabled={disabled}
          onChange={(e) => onDecision(e.target.checked)}
          aria-label={`Trust reference ${reference.idx + 1}`}
          className="h-4 w-4 accent-accent disabled:cursor-not-allowed"
        />
      </div>

      <div className="min-w-0 space-y-1.5">
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
          title={expanded ? 'Collapse the raw entry' : 'Show the full raw entry'}
          className={cn(
            'block w-full text-left font-mono text-2xs leading-relaxed text-ink-muted hover:text-ink',
            !expanded && 'truncate',
          )}
        >
          {reference.raw}
        </button>

        <div className="flex flex-wrap items-center gap-1.5">
          <Badge tone={match.tone}>{match.label}</Badge>
          {match.confidence !== null ? (
            <span className="font-mono text-2xs tnum text-ink-faint">
              similarity {match.confidence.toFixed(2)}
            </span>
          ) : null}
          {reference.is_self_citation ? <Badge tone="accent">self-citation</Badge> : null}
        </div>

        {reference.work ? (
          <p className="text-xs text-ink">
            <PaperTitle as="span" title={reference.work.title} className="text-xs" />{' '}
            <span className="text-ink-muted">({formatYear(reference.work.year)})</span>
          </p>
        ) : null}

        {match.willBeAdded ? (
          <p className="text-xs text-ink-muted">
            Known to OpenAlex
            {reference.resolved_openalex_id ? (
              <span className="font-mono text-2xs"> ({reference.resolved_openalex_id})</span>
            ) : null}{' '}
            but not yet in the corpus — {WILL_BE_ADDED_COPY}.
          </p>
        ) : null}

        {reference.couldnt_check ? (
          <p className="text-xs leading-relaxed text-ink-muted">
            OpenAlex was unreachable when this entry was checked. That is our failure, not
            evidence about the paper.
          </p>
        ) : null}
      </div>

      <div className="col-start-2 flex flex-wrap items-center gap-2 sm:col-start-3 sm:flex-col sm:items-end">
        <StrengthPicker
          value={reference.strength}
          onChange={onStrength}
          size="sm"
          disabled={disabled}
          label={`Seed strength for reference ${reference.idx + 1}`}
        />
        {editable && reference.match_method !== 'doi' && reference.match_method !== 'arxiv' ? (
          <Button size="sm" variant="ghost" onClick={onFindMatch} disabled={busy}>
            <SearchIcon aria-hidden className="h-3.5 w-3.5" />
            {match.unmatched ? 'Find match' : 'Change match'}
          </Button>
        ) : null}
        {busy ? <Loader2 aria-hidden className="h-3.5 w-3.5 animate-spin text-ink-faint" /> : null}
      </div>
    </li>
  );
}

/* ------------------------------------------------------------------ */
/* Manual match                                                        */
/* ------------------------------------------------------------------ */

function FindMatchDialog({
  reference,
  onClose,
  onPick,
}: {
  reference: UploadReference | null;
  onClose: () => void;
  onPick: (workId: string) => void;
}): JSX.Element {
  return (
    <Dialog open={Boolean(reference)} onClose={onClose} title="Find a match" className="max-w-2xl">
      {reference ? (
        <FindMatchBody key={reference.idx} reference={reference} onClose={onClose} onPick={onPick} />
      ) : null}
    </Dialog>
  );
}

function FindMatchBody({
  reference,
  onClose,
  onPick,
}: {
  reference: UploadReference;
  onClose: () => void;
  onPick: (workId: string) => void;
}): JSX.Element {
  const [query, setQuery] = useState(reference.parsed_title ?? '');
  const debounced = useDebounced(query, 250);
  const search = usePaperSearch({ q: debounced }, debounced.trim().length >= 2);

  return (
    <>
      <DialogHeader
        title="Match this reference to a corpus work"
        description="Choosing a work here is the tick: the reference is accepted with your chosen match."
        onClose={onClose}
      />
      <div className="max-h-[60vh] space-y-4 overflow-y-auto px-5 py-4">
        <p className="rounded-sm border border-rule bg-raised/40 px-3 py-2 font-mono text-2xs leading-relaxed text-ink-muted">
          {reference.raw}
        </p>
        <Input
          type="search"
          autoFocus
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search titles and abstracts…"
          aria-label="Search the corpus for a match"
          leading={<SearchIcon aria-hidden className="h-4 w-4" />}
        />
        {search.isError ? (
          <ErrorState error={search.error} onRetry={() => void search.refetch()} />
        ) : search.isLoading && debounced.trim().length >= 2 ? (
          <LoadingRegion label="Searching the corpus" className="space-y-3">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-12 w-full" />
            ))}
          </LoadingRegion>
        ) : search.data && debounced.trim().length >= 2 ? (
          search.data.items.length === 0 ? (
            <EmptyState title="Nothing matched" className="py-8">
              <p>
                The corpus is a slice of OpenAlex, not all of it. If the paper is not here, leave
                the reference unmatched rather than forcing a wrong match.
              </p>
            </EmptyState>
          ) : (
            <ul className="divide-y divide-rule">
              {search.data.items.map((paper) => (
                <li key={paper.id} className="flex items-start justify-between gap-3 py-3">
                  <div className="min-w-0">
                    <PaperTitle
                      as="p"
                      title={paper.title}
                      className="text-sm leading-snug text-ink"
                    />
                    <p className="mt-1 text-xs text-ink-muted">
                      {formatAuthors(paper.authors, 2)} · {formatYear(paper.year)}
                      {paper.venue ? ` · ${paper.venue.name}` : ''}
                    </p>
                  </div>
                  <Button size="sm" variant="primary" onClick={() => onPick(paper.id)}>
                    <Check aria-hidden className="h-3.5 w-3.5" />
                    Use this
                  </Button>
                </li>
              ))}
            </ul>
          )
        ) : (
          <p className="text-xs text-ink-muted">Type at least two characters to search.</p>
        )}
      </div>
    </>
  );
}

/* ------------------------------------------------------------------ */
/* List of past uploads                                                */
/* ------------------------------------------------------------------ */

function UploadsList({
  uploads,
  isLoading,
  error,
  onRetry,
  activeId,
  onOpen,
  onUndo,
}: {
  uploads: UploadListItem[];
  isLoading: boolean;
  error: Error | null;
  onRetry: () => void;
  activeId: string | null;
  onOpen: (id: string) => void;
  onUndo: (target: UndoTarget) => void;
}): JSX.Element {
  return (
    <Card>
      <CardHeader
        title={`Your uploads (${uploads.length})`}
        description="Every batch stays undoable: undo removes the seeds it created (hand-added seeds survive) and the paper itself if this upload was its only source."
      />
      <CardBody className="p-0">
        {error ? (
          <ErrorState error={error} onRetry={onRetry} className="m-4" />
        ) : isLoading ? (
          <LoadingRegion label="Loading your uploads" className="space-y-3 p-5">
            {[0, 1].map((i) => (
              <Skeleton key={i} className="h-12 w-full" />
            ))}
          </LoadingRegion>
        ) : uploads.length === 0 ? (
          <p className="px-5 py-6 text-sm text-ink-muted">
            Nothing uploaded yet. Drafts and imported batches will be listed here.
          </p>
        ) : (
          <ul className="divide-y divide-rule">
            {uploads.map((u) => (
              <li key={u.id} className="flex flex-wrap items-center gap-3 px-5 py-3.5">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <PaperTitle
                      as="span"
                      title={u.title ?? u.filename}
                      className="text-sm leading-snug text-ink"
                    />
                    <Badge tone={STATUS_TONE[u.status]}>{STATUS_LABEL[u.status]}</Badge>
                  </div>
                  <p className="mt-1 font-mono text-2xs tnum text-ink-faint">
                    {formatCount(u.n_parsed)} parsed · {formatCount(u.n_matched)} matched ·{' '}
                    {formatCount(u.n_added)} added · {formatCount(u.n_unresolved)} unresolved ·{' '}
                    {new Date(u.created_at).toLocaleDateString('en-GB')}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <Button size="sm" onClick={() => onOpen(u.id)} disabled={u.id === activeId}>
                    {u.status === 'draft' ? 'Open draft' : 'View'}
                  </Button>
                  <Button
                    size="sm"
                    variant="danger"
                    data-testid={`uploads-undo-${u.id}`}
                    onClick={() =>
                      onUndo({ id: u.id, title: u.title, filename: u.filename, status: u.status })
                    }
                  >
                    {u.status === 'draft' ? 'Discard' : 'Undo'}
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </CardBody>
    </Card>
  );
}
