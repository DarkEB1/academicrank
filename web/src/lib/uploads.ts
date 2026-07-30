import type { UploadReference } from './types';

/**
 * Display logic for the upload review table. Pure, so the wording rules —
 * "couldn't check" is our failure and never "not found", pre-ticks are
 * DOI/arXiv only — are testable without rendering anything.
 */

/** 3/5 — the encoding of "I cited this". See the design spec's trust semantics. */
export const DEFAULT_SEED_STRENGTH = 3;

/**
 * Where the diversity dial lands after an import. Post-upload /rankings
 * degenerates to "the references of my references", so the user is dropped on
 * /recommendations with the dial raised well above its 0.35 default.
 */
export const POST_IMPORT_DIVERSITY = 0.6;

/**
 * Shown verbatim next to the include_user_uploads toggle. The spec demands
 * display-level honesty in exactly these words; do not paraphrase.
 */
export const INCLUDE_UPLOADS_CAVEAT =
  'Exclusion is display-level. There is one shared graph; walks propagate through uploaded ' +
  'edges for everyone, so your scores are still perturbed by uploads existing. Excluding them ' +
  'hides them from your results; it does not isolate your ranking from them.';

/** References the confirm will import — the live N in "Import N seeds". */
export function acceptedCount(refs: Pick<UploadReference, 'decision'>[]): number {
  return refs.filter((r) => r.decision === 'accept').length;
}

export type MatchDescriptor = {
  /** Short state label rendered as a badge. */
  label: string;
  tone: 'positive' | 'accent' | 'caution' | 'neutral';
  /** Shown for similarity-based matches only; identifier matches are exact. */
  confidence: number | null;
  /** Resolved on OpenAlex but no corpus work yet: created at confirm. */
  willBeAdded: boolean;
  /** Nothing found anywhere — offer the manual search. */
  unmatched: boolean;
};

export function describeMatch(
  ref: Pick<
    UploadReference,
    'match_method' | 'confidence' | 'work' | 'resolved_openalex_id' | 'couldnt_check'
  >,
): MatchDescriptor {
  const willBeAdded = !ref.work && ref.resolved_openalex_id !== null;

  if (ref.couldnt_check) {
    // OUR failure, and it must read as ours: never "not found".
    return {
      label: "couldn't check OpenAlex",
      tone: 'caution',
      confidence: null,
      willBeAdded,
      unmatched: !ref.work && !willBeAdded,
    };
  }

  switch (ref.match_method) {
    case 'doi':
      return { label: 'matched by DOI', tone: 'positive', confidence: null, willBeAdded, unmatched: false };
    case 'arxiv':
      return { label: 'matched by arXiv id', tone: 'positive', confidence: null, willBeAdded, unmatched: false };
    case 'manual':
      return { label: 'matched by you', tone: 'positive', confidence: null, willBeAdded, unmatched: false };
    case 'trigram':
    case 'openalex':
      // A similarity candidate, not a certainty: the tick stays with the user.
      return {
        label: ref.match_method === 'trigram' ? 'candidate (title match)' : 'candidate (OpenAlex search)',
        tone: 'accent',
        confidence: ref.confidence,
        willBeAdded,
        unmatched: false,
      };
    case 'none':
      return { label: 'unmatched', tone: 'neutral', confidence: null, willBeAdded, unmatched: !willBeAdded };
  }
}

/** "will be added to the corpus on import" — the exact promise, one place. */
export const WILL_BE_ADDED_COPY = 'will be added to the corpus on import';
