"""PDF -> LayoutLine list, using pdfminer's LAParams layout analysis.

Deliberately no hand-rolled character clustering (spec N4): LAParams already
groups characters into lines and boxes, and a bespoke x-means re-derivation of
columns invents structure in justified single-column text. Columns are decided
per page from LTTextBox midpoints only.
"""
from __future__ import annotations

import io

from pdfminer.high_level import extract_pages
from pdfminer.layout import LAParams, LTTextBox, LTTextLine

from .constants import (
    COLUMN_GUTTER_MIN_FRACTION, COLUMN_MIN_SIDE_FRACTION, COLUMN_MIN_VOTING_BOXES,
    COLUMN_SPLIT_MAX_FRACTION, COLUMN_SPLIT_MIN_FRACTION,
    COLUMN_VOTE_MIN_WIDTH_FRACTION, FULL_WIDTH_BOX_FRACTION, TAIL_FRACTION,
)
from .types import LayoutLine


def tail_page_numbers(n_pages: int) -> list[int]:
    """The final ~TAIL_FRACTION of pages, where bibliographies live. Always at
    least the last two pages so tiny documents still work."""
    start = min(int(n_pages * (1.0 - TAIL_FRACTION)), max(n_pages - 2, 0))
    return list(range(start, n_pages))


def _box_spans(boxes: list[LTTextBox], page_width: float) -> tuple[bool, float]:
    """Two-column decision for one page (spec: LTTextBox midpoints; gutter >
    COLUMN_GUTTER_MIN_FRACTION of page width; >= COLUMN_MIN_SIDE_FRACTION of
    boxes each side). Returns (is_two_column, split_x).

    Only column-width boxes vote: full-width content cannot, and neither can
    narrow fragments (equation shards, lone symbols) -- on maths-heavy pages
    dozens of those pile up in one column and drown the side-balance check
    (observed on the quant-ph fixture: 45 boxes, most of them sub-12%-width
    fragments, defeated the 25% rule on a visibly two-column page)."""
    mids = []
    for b in boxes:
        w = b.x1 - b.x0
        if w > FULL_WIDTH_BOX_FRACTION * page_width:
            continue
        if w < COLUMN_VOTE_MIN_WIDTH_FRACTION * page_width:
            continue
        mids.append((b.x0 + b.x1) / 2.0)
    if len(mids) < COLUMN_MIN_VOTING_BOXES:
        # A sparse page (last page with one entry and an address block) offers
        # too few boxes to distinguish columns from coincidence.
        return False, 0.0
    mids.sort()
    # Largest midpoint gap anywhere near the middle of the page is the candidate
    # gutter. (Not k-means: one sorted pass, one gap.)
    best_gap, split_x = 0.0, 0.0
    for a, b in zip(mids, mids[1:]):
        gap = b - a
        if gap > best_gap:
            best_gap, split_x = gap, (a + b) / 2.0
    if best_gap < COLUMN_GUTTER_MIN_FRACTION * page_width:
        return False, 0.0
    # A real gutter sits near the middle of the page; a "gutter" at 20% width
    # is a margin note or a figure column.
    if not (COLUMN_SPLIT_MIN_FRACTION * page_width
            <= split_x <= COLUMN_SPLIT_MAX_FRACTION * page_width):
        return False, 0.0
    left = sum(1 for m in mids if m < split_x)
    right = len(mids) - left
    if min(left, right) < COLUMN_MIN_SIDE_FRACTION * len(mids):
        return False, 0.0
    return True, split_x


def _line_font_size(line: LTTextLine) -> float:
    sizes = [c.size for c in line if hasattr(c, "size")]
    return max(sizes) if sizes else 0.0


def layout_lines(pdf_bytes: bytes, page_numbers: list[int]) -> list[LayoutLine]:
    """Extract LayoutLines for the given pages, in reading order: for two-column
    pages the whole left column precedes the right column (top-to-bottom within
    each), which is what lets an entry spanning a column break re-join."""
    out: list[LayoutLine] = []
    for doc_page, page in zip(sorted(page_numbers), extract_pages(
        io.BytesIO(pdf_bytes), page_numbers=page_numbers, laparams=LAParams()
    )):
        boxes = [el for el in page if isinstance(el, LTTextBox)]
        two_col, split_x = _box_spans(boxes, page.width)

        page_lines: list[tuple[tuple, LayoutLine]] = []
        for box in boxes:
            box_mid = (box.x0 + box.x1) / 2.0
            full_width = (box.x1 - box.x0) > FULL_WIDTH_BOX_FRACTION * page.width
            if not two_col:
                col = None
            elif full_width:
                col = None
            else:
                col = 0 if box_mid < split_x else 1
            for line in box:
                if not isinstance(line, LTTextLine):
                    continue
                text = line.get_text().rstrip("\n")
                if not text.strip():
                    continue
                ll = LayoutLine(
                    text=text, x0=line.x0, y0=line.y0, x1=line.x1, y1=line.y1,
                    font_size=_line_font_size(line), page=doc_page, column=col,
                )
                # Sort key: column bucket first (None/full-width sorts with the
                # column its x-position falls in, so a full-width footnote does
                # not shear the reading order), then top-to-bottom, then x.
                bucket = col if col is not None else (0 if box_mid < (split_x or page.width) else 1)
                page_lines.append(((bucket, -line.y1, line.x0), ll))

        page_lines.sort(key=lambda t: t[0])
        out.extend(ll for _key, ll in page_lines)
    return out
