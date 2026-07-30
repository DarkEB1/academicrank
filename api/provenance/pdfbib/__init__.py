"""pdfbib: extract a bibliography from a PDF of a paper.

Pipeline (spec: docs/superpowers/specs/2026-07-29-own-papers-bibliography-trust-design.md):

    PDF bytes -> guards (size/pages/encrypted/text-less)
             -> layout lines over the final ~40% of pages (pdfminer LAParams)
             -> per-page two-column detection on LTTextBox midpoints
             -> bibliography heading (multilingual set; dense-run fallback)
             -> four splitters; structural key-sequence check decisive;
                otherwise margin-based acceptance over discriminative features
             -> normalised entries + per-entry field guesses (doi/arxiv/year/title)

Every threshold is a named constant in constants.py with a named test. The library
refuses rather than guessing: a refusal carries the specific reason and the whole
document goes to manual review, which is the failure mode the spec demands.
"""
from .extract import extract_bibliography, layout_lines_for_pdf
from .guards import ExtractionRefused, extract_with_timeout
from .split import split_bibliography
from .types import BibEntry, BibResult, LayoutLine

__all__ = [
    "BibEntry",
    "BibResult",
    "ExtractionRefused",
    "LayoutLine",
    "extract_bibliography",
    "extract_with_timeout",
    "layout_lines_for_pdf",
    "split_bibliography",
]
