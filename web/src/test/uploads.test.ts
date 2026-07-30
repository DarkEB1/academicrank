import { describe, expect, it } from 'vitest';
import {
  acceptedCount,
  describeMatch,
  INCLUDE_UPLOADS_CAVEAT,
  POST_IMPORT_DIVERSITY,
} from '@/lib/uploads';
import type { UploadReference } from '@/lib/types';

const ref = (over: Partial<UploadReference> = {}): UploadReference => ({
  idx: 0,
  raw: 'A. Author. Some paper. Journal, 2001.',
  parsed_title: 'Some paper',
  parsed_doi: null,
  parsed_year: 2001,
  match_method: 'none',
  confidence: 0,
  decision: 'pending',
  strength: 3,
  is_self_citation: false,
  resolved_openalex_id: null,
  work: null,
  couldnt_check: false,
  ...over,
});

const work = { id: 'W1', title: 'Some paper', year: 2001, authors: [], venue: null,
  cited_by_count: 0, in_corpus_cited_by: 0, is_stub: false, doi: null };

describe('acceptedCount', () => {
  it('counts only accepted references — the live N in the import button', () => {
    const refs = [
      ref({ decision: 'accept' }),
      ref({ decision: 'pending' }),
      ref({ decision: 'reject' }),
      ref({ decision: 'accept' }),
    ];
    expect(acceptedCount(refs)).toBe(2);
    expect(acceptedCount([])).toBe(0);
  });
});

describe('describeMatch', () => {
  it('labels identifier matches as such, with no similarity number', () => {
    const doi = describeMatch(ref({ match_method: 'doi', confidence: 1, work }));
    expect(doi.label).toBe('matched by DOI');
    expect(doi.confidence).toBeNull();
    const arxiv = describeMatch(ref({ match_method: 'arxiv', confidence: 1, work }));
    expect(arxiv.label).toBe('matched by arXiv id');
  });

  it('surfaces the confidence on similarity candidates', () => {
    const m = describeMatch(ref({ match_method: 'trigram', confidence: 0.72, work }));
    expect(m.confidence).toBe(0.72);
    expect(m.tone).toBe('accent');
    expect(m.unmatched).toBe(false);
  });

  it('says "couldn\'t check OpenAlex" — our failure — and never "not found"', () => {
    const m = describeMatch(ref({ couldnt_check: true }));
    expect(m.label).toBe("couldn't check OpenAlex");
    expect(m.label).not.toMatch(/not found/i);
    expect(m.tone).toBe('caution');
    // Manual matching still offered: nothing was proven absent.
    expect(m.unmatched).toBe(true);
  });

  it('flags OpenAlex-resolved works that will be created at confirm', () => {
    const m = describeMatch(
      ref({ match_method: 'openalex', confidence: 0.8, resolved_openalex_id: 'W99' }),
    );
    expect(m.willBeAdded).toBe(true);
    expect(m.unmatched).toBe(false);
  });

  it('treats a bare unmatched row as searchable', () => {
    const m = describeMatch(ref());
    expect(m.label).toBe('unmatched');
    expect(m.unmatched).toBe(true);
    expect(m.willBeAdded).toBe(false);
  });

  it('marks manual matches as the user\'s own', () => {
    const m = describeMatch(ref({ match_method: 'manual', confidence: 1, work }));
    expect(m.tone).toBe('positive');
    expect(m.unmatched).toBe(false);
  });
});

describe('spec-bound constants', () => {
  it('keeps the display-level exclusion caveat verbatim', () => {
    expect(INCLUDE_UPLOADS_CAVEAT).toBe(
      'Exclusion is display-level. There is one shared graph; walks propagate through ' +
        'uploaded edges for everyone, so your scores are still perturbed by uploads existing. ' +
        'Excluding them hides them from your results; it does not isolate your ranking from them.',
    );
  });

  it('raises the diversity dial above its 0.35 default after an import', () => {
    expect(POST_IMPORT_DIVERSITY).toBeGreaterThan(0.35);
    expect(POST_IMPORT_DIVERSITY).toBeLessThanOrEqual(1);
  });
});
