import { Loader2 } from 'lucide-react';
import { useUndoUpload } from '@/lib/queries';
import type { UploadStatus } from '@/lib/types';
import { Button } from '@/components/ui/Button';
import { Dialog, DialogHeader } from '@/components/ui/Dialog';
import { ErrorState } from '@/components/States';

export type UndoTarget = {
  id: string;
  title: string | null;
  filename: string | null;
  status: UploadStatus;
};

/**
 * Confirm-before-undo for an upload batch. Used from the Uploads screen and
 * from the trust set's per-upload group headers, so the wording about what
 * survives lives in exactly one place.
 */
export function UploadUndoDialog({
  target,
  profileId,
  onClose,
  onDone,
}: {
  target: UndoTarget | null;
  profileId: string;
  onClose: () => void;
  onDone?: () => void;
}): JSX.Element {
  const undo = useUndoUpload(profileId);
  const isDraft = target?.status === 'draft';
  const name = target?.title || target?.filename || 'this upload';

  const close = () => {
    undo.reset();
    onClose();
  };

  return (
    <Dialog
      open={Boolean(target)}
      onClose={close}
      title={isDraft ? 'Discard draft' : 'Undo upload'}
    >
      {target ? (
        <>
          <DialogHeader
            title={isDraft ? 'Discard this draft?' : `Undo everything from “${name}”?`}
            onClose={close}
          />
          <div className="space-y-3 px-5 py-4 text-sm leading-relaxed text-ink-muted">
            {isDraft ? (
              <p>
                Nothing from a draft has touched the graph or your trust set; discarding it only
                deletes the parsed bibliography. You can upload the PDF again later.
              </p>
            ) : (
              <>
                <p>
                  This removes the whole batch: every trust seed this upload created, and its
                  citation edges in the shared graph. Seeds you also added by hand survive — only
                  the upload&rsquo;s contribution to them is withdrawn.
                </p>
                <p>
                  If the uploaded paper exists in the corpus only because of this upload, it is
                  removed too. Rankings recompute on the next read.
                </p>
              </>
            )}
            {undo.isError ? <ErrorState error={undo.error} /> : null}
          </div>
          <div className="flex justify-end gap-2 border-t border-rule px-5 py-3">
            <Button onClick={close} disabled={undo.isPending}>
              Keep it
            </Button>
            <Button
              variant="danger"
              disabled={undo.isPending}
              onClick={() =>
                undo.mutate(target.id, {
                  onSuccess: () => {
                    close();
                    onDone?.();
                  },
                })
              }
            >
              {undo.isPending ? (
                <Loader2 aria-hidden className="h-3.5 w-3.5 animate-spin" />
              ) : null}
              {isDraft ? 'Discard draft' : 'Undo the batch'}
            </Button>
          </div>
        </>
      ) : null}
    </Dialog>
  );
}
