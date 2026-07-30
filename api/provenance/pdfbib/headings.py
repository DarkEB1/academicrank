"""Locate the bibliography region in the document tail.

Three detectors, in order of trust:
1. A bibliography heading (multilingual set, all-caps/letter-spaced tolerated),
   region terminated by a post-bib heading, an appendix heading, or any line
   set in the same display size as the heading itself.
2. Keyed-region fallback for heading-less styles (REVTeX prints references as a
   rule-separated block with no title): a dense run of lines starting with
   bracketed keys, beginning at key [1].
3. Dense-run fallback: the longest tail window dominated by reference-like
   lines, for unheaded, unkeyed bibliographies.
"""
from __future__ import annotations

import re

from .constants import (
    BIBLIOGRAPHY_HEADINGS, DENSE_RUN_MIN_LINES, DENSE_RUN_MIN_REFLIKE_FRACTION,
    HEADING_FONT_MARGIN, KEYED_REGION_MIN_KEYS, KEYED_REGION_MIN_KEY_DENSITY,
    POST_BIB_HEADINGS,
)
from .types import LayoutLine

_YEAR = re.compile(r"\b(1[89]\d{2}|20[0-2]\d|2030)\b")
_AUTHORISH = re.compile(
    r"^\s*(?:\[[^\]]{1,12}\]|\d{1,3}\.|(?:[A-Z]\.\s*)+[A-Z][a-z]|"
    r"[A-Z][\w'À-ɏ-]+,?\s+(?:[A-Z]\.|[A-Z][a-z]|and\b|et\b)|"
    r"[A-Z][\w'À-ɏ-]+\s+[A-Z]{1,3}\b)")  # 'Bates D' (JSS: initials, no dots)
_KEY_START = re.compile(r"^\s*\[(\d{1,3}|[A-Z][a-zA-Z+\-]*\d{0,2}[a-z]?)\]")
_APPENDIX = re.compile(r"^\s*(?:[A-Z]|[IVXLC]+)?\s*\.?\s*append(?:ix|ices)\b", re.I)


def _norm_heading(text: str) -> str:
    """Lowercase, strip numbering ('7 References'), collapse letter-spacing
    ('R E F E R E N C E S' -> 'references')."""
    t = text.strip().lower().rstrip(".:").strip()
    t = re.sub(r"^[\divxlc]+[.\s]+", "", t)  # section numbers, roman or arabic
    if re.fullmatch(r"(?:\w[\s ]+)+\w", t):  # letter-spaced
        t = t.replace(" ", "").replace(" ", "")
    return re.sub(r"\s+", " ", t)


def is_bib_heading(line: LayoutLine) -> bool:
    return _norm_heading(line.text) in BIBLIOGRAPHY_HEADINGS


def _terminates(line: LayoutLine, heading_font: float, body_font: float) -> bool:
    if _norm_heading(line.text) in POST_BIB_HEADINGS:
        return True
    if _APPENDIX.match(line.text.strip()):
        return True
    # A line as large as the References heading = a sibling section heading
    # (lme4's 'A. Appendix: modularization examples' is set at exactly the
    # heading's 14.3pt). Only usable when the heading really is displayed
    # larger than reference text: AMS small-caps headings are body-sized, and
    # there the rule would cut the region at the first same-size line.
    if (heading_font >= body_font + HEADING_FONT_MARGIN
            and line.font_size >= heading_font - 0.5
            and not _reflike(line.text)):
        return True
    return False


def _reflike(text: str) -> bool:
    return bool(_YEAR.search(text)) and bool(_AUTHORISH.match(text))


def _heading_region(lines: list[LayoutLine]) -> tuple[list[LayoutLine], str | None]:
    head_idx: int | None = None
    for i, ln in enumerate(lines):
        if is_bib_heading(ln):
            head_idx = i  # keep the last occurrence: ToC entries come earlier
    if head_idx is None:
        return [], None
    heading = lines[head_idx]
    after = lines[head_idx + 1:]
    body_sizes = sorted(ln.font_size for ln in after[:40] if ln.font_size > 0)
    body_font = body_sizes[len(body_sizes) // 2] if body_sizes else 0.0
    picked: list[LayoutLine] = []
    for ln in after:
        if _terminates(ln, heading.font_size, body_font):
            break
        picked.append(ln)
    return picked, heading.text.strip()


def _keyed_region(lines: list[LayoutLine]) -> list[LayoutLine]:
    """Heading-less keyed bibliography: from the first line starting with [1]
    such that the rest of the document is dense in line-start keys."""
    for i, ln in enumerate(lines):
        m = _KEY_START.match(ln.text)
        if not (m and m.group(1) == "1"):
            continue
        region = lines[i:]
        n_keys = sum(1 for r in region if _KEY_START.match(r.text))
        if (n_keys >= KEYED_REGION_MIN_KEYS
                and n_keys / len(region) >= KEYED_REGION_MIN_KEY_DENSITY):
            return region
    return []


def _dense_run(lines: list[LayoutLine]) -> list[LayoutLine]:
    best_start, best_end = -1, -1
    start = None
    misses = 0
    for i, ln in enumerate(lines):
        if _reflike(ln.text):
            if start is None:
                start = i
            misses = 0
        elif start is not None:
            misses += 1
            if misses > 3:
                if i - misses - start >= (best_end - best_start):
                    best_start, best_end = start, i - misses
                start, misses = None, 0
    if start is not None and len(lines) - start >= (best_end - best_start):
        best_start, best_end = start, len(lines)
    if best_start < 0:
        return []
    window = lines[best_start:min(best_end + 1, len(lines))]
    n_ref = sum(1 for ln in window if _reflike(ln.text))
    if (len(window) >= DENSE_RUN_MIN_LINES
            and n_ref / max(len(window), 1) >= DENSE_RUN_MIN_REFLIKE_FRACTION):
        return window
    return []


def bibliography_lines(lines: list[LayoutLine]) -> tuple[list[LayoutLine], str | None]:
    """Returns (region_lines, heading_text). heading_text is None for the two
    fallback detectors; ([], None) when all three fail."""
    region, heading = _heading_region(lines)
    if region:
        return region, heading
    region = _keyed_region(lines)
    if region:
        return region, None
    return _dense_run(lines), None
