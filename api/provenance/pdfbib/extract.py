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


def paper_title_guess(pdf_bytes: bytes) -> str | None:
    """The uploaded paper's own title: the largest-font text run near the top of
    page one. A guess -- the review UI makes it editable, which is the actual
    contract; this only has to be right often enough to save typing."""
    try:
        lines = layout_lines(pdf_bytes, [0])
    except Exception:  # noqa: BLE001 - a title guess must never sink an upload
        return None
    if not lines:
        return None
    top = [ln for ln in lines if ln.font_size > 0]
    if not top:
        return None
    max_fs = max(ln.font_size for ln in top)
    titled = [ln for ln in top if ln.font_size >= max_fs - 0.5]
    # Consecutive max-font lines from the first one form the (possibly wrapped)
    # title; a stray same-size character elsewhere on the page is ignored.
    picked: list[str] = []
    started = False
    for ln in top:
        if ln in titled:
            picked.append(ln.text.strip())
            started = True
        elif started:
            break
    title = " ".join(picked).strip()
    if not (10 <= len(title) <= 300):
        return None
    from .normalize import normalise
    return normalise(title)


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
    result.paper_title = paper_title_guess(pdf_bytes)
    for e in result.entries:
        parse_fields(e)
    return result
