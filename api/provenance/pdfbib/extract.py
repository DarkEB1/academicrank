"""Orchestration: PDF bytes -> BibResult. The split/score core is hermetic
(split.py works on LayoutLines); this module is the only place pdfminer runs."""
from __future__ import annotations

from .fields import parse_fields
from .guards import ExtractionRefused, check_pdf
from .headings import bibliography_lines, is_bib_heading
from .layout import layout_lines, tail_page_numbers
from .split import split_bibliography
from .types import BibResult, LayoutLine

# Fewer characters than this across the document tail means the PDF is a scan
# (image-only) or has no text layer worth parsing.
MIN_TAIL_CHARS = 200


def layout_lines_for_pdf(pdf_bytes: bytes) -> tuple[list[LayoutLine], int]:
    """Guards + layout for the document tail. Used directly by the fixture
    pipeline that commits hermetic layout-line JSON artefacts."""
    n_pages = check_pdf(pdf_bytes)
    lines = layout_lines(pdf_bytes, tail_page_numbers(n_pages))
    return lines, n_pages


def extract_bibliography(pdf_bytes: bytes) -> BibResult:
    lines, n_pages = layout_lines_for_pdf(pdf_bytes)

    total_chars = sum(len(ln.text) for ln in lines)
    if total_chars < MIN_TAIL_CHARS:
        raise ExtractionRefused(
            "No extractable text in the final pages -- the PDF is likely a "
            "scan without OCR. Only text-layer PDFs can be parsed.")

    bib_lines, heading = bibliography_lines(lines)
    if not bib_lines:
        raise ExtractionRefused(
            "No bibliography found: no References/Bibliography heading in the "
            "final pages, and no dense run of reference-like lines either.")

    # Body lines = everything in the tail before the bibliography, for the
    # in-text citation-marker agreement feature.
    first_bib = bib_lines[0]
    body_lines = []
    for ln in lines:
        if ln is first_bib:
            break
        if not is_bib_heading(ln):
            body_lines.append(ln)

    result = split_bibliography(bib_lines, body_lines=body_lines)
    result.n_pages = n_pages
    result.heading_text = heading
    for e in result.entries:
        parse_fields(e)
    return result
