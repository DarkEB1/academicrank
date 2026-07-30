"""Every threshold in pdfbib, named, in one place, each with a named test in
api/tests/test_pdfbib_constants.py. Values are CALIBRATED against the four
committed fixtures (see tests/fixtures/pdfbib/MANIFEST.md), not derived.
"""
from __future__ import annotations

# --- guards -----------------------------------------------------------------
# Both caps apply (spec): a 300-page book scan under 25 MB is refused just the
# same as an 81 MB slide deck.
MAX_PDF_BYTES = 25 * 1024 * 1024
MAX_PDF_PAGES = 80
# Full layout analysis only over the final ~40% of pages -- bibliographies live
# at the end, and LAParams layout is the expensive part of pdfminer.
TAIL_FRACTION = 0.40
# Wall-clock timeout for the whole extraction, enforced by running it in a
# separate process (a thread cannot be killed).
EXTRACTION_TIMEOUT_SECONDS = 90.0

# --- column detection (spec: LTTextBox midpoints, no character clustering) ---
# Two-column hypothesis accepted only when the midpoint gutter exceeds this
# fraction of page width...
COLUMN_GUTTER_MIN_FRACTION = 0.04
# ...and each side holds at least this fraction of the page's text boxes.
COLUMN_MIN_SIDE_FRACTION = 0.25
# A box wider than this fraction of the usable page width is full-width content
# (title block, footnote rule, table) and takes no part in column assignment.
FULL_WIDTH_BOX_FRACTION = 0.72
# A box narrower than this cannot vote either: equation fragments and lone
# symbols cluster inside one column and would drown the side-balance check.
COLUMN_VOTE_MIN_WIDTH_FRACTION = 0.12
# Minimum voting boxes for the two-column hypothesis: sparse pages (a last page
# holding one entry and an address block) give false-positive gutters.
COLUMN_MIN_VOTING_BOXES = 8
# The gutter of a genuine two-column page sits near the centre.
COLUMN_SPLIT_MIN_FRACTION = 0.35
COLUMN_SPLIT_MAX_FRACTION = 0.65
# The font-size region terminator (a line as large as the References heading
# ends the bibliography) only works when the heading is actually displayed
# larger than the reference text; AMS small-caps headings are body-sized.
HEADING_FONT_MARGIN = 1.5

# --- heading detection ------------------------------------------------------
# Multilingual bibliography headings, lowercased, diacritics kept. All-caps and
# letter-spaced variants are handled by normalisation in headings.py, not by
# enumerating them here.
BIBLIOGRAPHY_HEADINGS = frozenset({
    "references", "reference", "bibliography", "works cited", "literature cited",
    "literature", "references cited",
    "bibliographie", "références", "referencias", "bibliografía",
    "literatur", "literaturverzeichnis", "bibliografia", "riferimenti",
    "referências", "литература", "список литературы", "参考文献",
})
# Headings that terminate the bibliography when they follow it.
POST_BIB_HEADINGS = frozenset({
    "appendix", "appendices", "acknowledgements", "acknowledgments",
    "supplementary material", "supplement", "annex",
})
# Dense-run fallback: a window of consecutive lines in the document tail where at
# least this fraction look reference-like (year + author-ish opening) is treated
# as an unheaded bibliography.
DENSE_RUN_MIN_LINES = 8
DENSE_RUN_MIN_REFLIKE_FRACTION = 0.6
# Keyed-region fallback (heading-less REVTeX-style bibliographies): needs a run
# beginning at key [1] with at least this many line-start keys, at at least this
# key-per-line density over the rest of the tail.
KEYED_REGION_MIN_KEYS = 8
KEYED_REGION_MIN_KEY_DENSITY = 0.12

# --- structural key-sequence check (decisive when it fires) ------------------
# Alpha keys must look like [Har77] / [BCHM10]: capital, letters (+ allowed for
# collaboration keys), exactly two trailing digits. Keys without digits ([KM])
# fall through to the scored strategies -- the structural claim is only decisive
# when the key shape is unambiguous.
ALPHA_KEY_PATTERN = r"[A-Z][a-zA-Z+\-]*\d{2}[a-z]?"
# A keyed split is structural only if at least this many entries were found --
# a handful of gap-free bracketed tokens occur in running prose (a numbered
# list, an over-split trap); no real paper cites fewer than this. Bibliographies
# smaller than this fall through to the scored strategies, whose failure mode is
# review, not mis-acceptance.
STRUCTURAL_MIN_ENTRIES = 6

# --- discriminative features (scored strategies only) -------------------------
# Median entry length must land in this window (chars). Shorter = over-split
# fragments; longer = unsplit blobs.
ENTRY_LEN_MIN = 60
ENTRY_LEN_MAX = 600
# Publication years considered plausible in a reference string.
YEAR_MIN = 1800
YEAR_MAX = 2030
# Hard gates: the winning candidate must clear ALL of these or the document is
# refused to review. These are what reject the adversarial cases (undated books
# fail YEAR; over-split prose fails AUTHOR and LEN).
MIN_YEAR_FRACTION = 0.5
MIN_AUTHOR_FRACTION = 0.4
# Margin-based acceptance: the best candidate must beat the runner-up by this
# much (on the [0,1] composite score) or the split is ambiguous -> review.
ACCEPT_MARGIN = 0.12
# Scored (non-structural) numeric-keyed splits must have nearly all adjacent
# key pairs increasing. A sheared reading order (failed column detection)
# interleaves entries and scrambles the sequence -- refuse rather than emit a
# confidently wrong split. One misread key in a real bibliography still leaves
# ~98% of adjacent pairs increasing.
MIN_KEY_ORDER_FRACTION = 0.8
# Composite-score floor for a *sole* candidate (no runner-up to margin against).
SOLE_CANDIDATE_MIN_SCORE = 0.55

# --- entry sanity -----------------------------------------------------------
# Bibliographies outside this size range are refused as implausible for a
# single paper (the spec caps references at 200 for the confirm path anyway).
MIN_ENTRIES = 3
MAX_ENTRIES = 400
