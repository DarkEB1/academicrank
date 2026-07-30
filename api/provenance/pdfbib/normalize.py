"""Per-entry text normalisation: de-hyphenation, ligatures, whitespace collapse,
trailing page-range strip. Unchanged from the spec's earlier draft on purpose --
these are boring and must stay boring."""
from __future__ import annotations

import re

_LIGATURES = {
    "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl",
    "ﬃ": "ffi", "ﬄ": "ffl", "­": "",  # soft hyphen
}
_WS = re.compile(r"\s+")
# 'exam-\nple' -> 'example', but 'well-\nknown' -> 'well-known' is unknowable
# without a dictionary; joining loses at most a hyphen, which trigram matching
# tolerates far better than a split word.
_HYPHEN_BREAK = re.compile(r"(\w)-\s+(\w)")
# Trailing ', pp. 1-24.' / ', 113-199.' page ranges add noise to title matching.
_TRAIL_PAGES = re.compile(
    r"[,;]?\s*(?:pp?\.\s*)?\d{1,5}\s*[-–—]\s*\d{1,5}\.?\s*$")


def join_lines(lines: list[str]) -> str:
    """Join hard-wrapped lines of one entry into a single string."""
    out = lines[0].rstrip() if lines else ""
    for ln in lines[1:]:
        nxt = ln.strip()
        if not nxt:
            continue
        if out.endswith("-") and nxt and nxt[0].islower():
            out = out[:-1] + nxt  # de-hyphenate across the line break
        else:
            out = f"{out} {nxt}"
    return out


def normalise(text: str) -> str:
    for lig, rep in _LIGATURES.items():
        text = text.replace(lig, rep)
    text = _HYPHEN_BREAK.sub(r"\1\2", text)
    text = _WS.sub(" ", text).strip()
    return text


def strip_trailing_pages(text: str) -> str:
    return _TRAIL_PAGES.sub("", text).strip()
