"""Per-entry field guesses: DOI, arXiv id, year, title. These feed the Phase-2
matching precedence (DOI -> arXiv -> corpus trigram -> OpenAlex -> review); a
wrong guess costs a review tick, never a silent wrong match."""
from __future__ import annotations

import re

from .constants import YEAR_MAX, YEAR_MIN
from .types import BibEntry

_DOI = re.compile(r"\b(10\.\d{4,9}/[^\s,;]+)", re.I)
_ARXIV_NEW = re.compile(r"\barXiv:\s*(\d{4}\.\d{4,5})(?:v\d+)?", re.I)
# Old-style ids appear both as 'arXiv:hep-th/9711200' and bare 'hep-th/9711200'.
_ARXIV_OLD = re.compile(
    r"\b(?:arXiv:\s*)?((?:[a-z]+(?:-[a-z]+)?)(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?\b")
_YEAR = re.compile(r"\b(1[89]\d{2}|20[0-2]\d|2030)\b")
# Authors usually end at the first period that terminates an initial or a name
# run; the title is the next sentence-ish chunk. Heuristic, and allowed to be:
# the trigram matcher tolerates noise and everything else goes to review.
_SEGMENT = re.compile(r"(?<!\b[A-Z])\.\s+")


def parse_fields(entry: BibEntry) -> BibEntry:
    text = entry.raw

    m = _DOI.search(text)
    if m:
        entry.doi = m.group(1).rstrip(".,;)]").lower()

    m = _ARXIV_NEW.search(text)
    if m:
        entry.arxiv_id = m.group(1)
    else:
        m = _ARXIV_OLD.search(text)
        if m:
            entry.arxiv_id = m.group(1)

    years = [int(y) for y in _YEAR.findall(text) if YEAR_MIN <= int(y) <= YEAR_MAX]
    if years:
        # The publication year is almost always the LAST plausible year -- early
        # years are usually part of a title ("the 1918 pandemic") or a volume.
        entry.year = years[-1]

    entry.title_guess = _title_guess(text)
    return entry


def _title_guess(text: str) -> str | None:
    # Strip a leading [key] if the splitter left one in.
    text = re.sub(r"^\s*\[[^\]]{1,16}\]\s*", "", text)
    segments = [s.strip() for s in _SEGMENT.split(text) if s.strip()]
    if len(segments) >= 2:
        # Segment 0 is the author run; the title is the first following segment
        # of plausible length.
        for seg in segments[1:]:
            if 15 <= len(seg) <= 300:
                return seg.rstrip(".,;")
    # Author-year styles: 'Bates D, Maechler M (2014). Title. Journal...'
    m = re.search(r"\((?:1[89]|20)\d{2}[a-z]?\)\.?\s*(.+)", text)
    if m:
        rest = m.group(1)
        first = re.split(r"[.?!]\s", rest, maxsplit=1)[0].strip()
        if 15 <= len(first) <= 300:
            return first.rstrip(".,;")
    return None
