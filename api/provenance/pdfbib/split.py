"""Entry splitting: four strategies, adjudicated structurally first.

1. The structural check is DECISIVE when it fires (spec): for keyed strategies,
   if the extracted keys form a gap-free increasing sequence from 1 (numeric,
   ordinal) or a consistent [Har77]-shaped alpha-key set, accept that split at
   confidence 1.0 with no scoring.
2. Otherwise candidates are scored on discriminative features and accepted on
   MARGIN over the runner-up, never on an absolute floor (the old formula's 0.5
   floor was dead code and its uniformity term rewarded over-splitting).
3. Hard gates on the winner refuse the adversarial failure modes outright:
   undated bibliographies fail MIN_YEAR_FRACTION; prose masquerading as entries
   fails MIN_AUTHOR_FRACTION and the entry-length window.
"""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass

from .constants import (
    ACCEPT_MARGIN, ALPHA_KEY_PATTERN, ENTRY_LEN_MAX, ENTRY_LEN_MIN, MAX_ENTRIES,
    MIN_AUTHOR_FRACTION, MIN_ENTRIES, MIN_KEY_ORDER_FRACTION, MIN_YEAR_FRACTION,
    SOLE_CANDIDATE_MIN_SCORE, STRUCTURAL_MIN_ENTRIES, YEAR_MAX, YEAR_MIN,
)
from .normalize import join_lines, normalise, strip_trailing_pages
from .types import BibEntry, BibResult, LayoutLine

_BRACKET_NUM = re.compile(r"^\s*\[(\d{1,3})\]")
_ALPHA_KEY = re.compile(r"^\s*\[(" + ALPHA_KEY_PATTERN + r")\]")
_ORDINAL = re.compile(r"^\s*(\d{1,3})\.\s+\S")
_YEAR = re.compile(r"\b(1[89]\d{2}|20[0-2]\d|2030)\b")
_AUTHOR_OPEN = re.compile(
    r"^(?:(?:[A-Z]\.[\s-]*)+[A-Z][\w'À-ɏ-]+"      # J. Kollár / J.-P. Serre
    r"|[A-Z][\w'À-ɏ-]+,?\s+(?:[A-Z]\.|[A-Z][a-z]+|and\b|et\b)"  # Kollár, J. / Jane Doe
    r"|[A-Z][\w'À-ɏ-]+\s+[A-Z]{1,3}\b"            # Bates D / Belenky G (JSS, no dots)
    r"|(?:van|von|de|del|di|da)\s+[A-Z]"          # van der Waerden
    r"|[A-Z][\w'À-ɏ-]+\s+et\s+al)")               # Kollár et al
# Strip a leading key before feature tests: '[17] J. Maldacena' must read as
# author-shaped, and the key would mask it.
_LEAD_KEY = re.compile(r"^\s*(?:\[[^\]]{1,16}\]|\d{1,3}\.)\s*")
_VOLPAGE = re.compile(r"(?:\d+\s*[:(]\s*\d+|\bpp?\.\s*\d|\bIn:|\bvol\.\s*\d)", re.I)
_BARE_PAGE_NO = re.compile(r"^\s*\d{1,4}\s*$")


@dataclass
class Candidate:
    method: str
    entries: list[str]           # normalised entry strings
    keys: list[str]              # raw key per entry ('' when unkeyed)
    structural: bool = False
    score: float = 0.0
    features: dict | None = None


def _clean_region(lines: list[LayoutLine]) -> list[LayoutLine]:
    """Drop page furniture: bare page numbers, and short lines repeating on
    multiple pages (running heads)."""
    seen_pages: dict[str, set[int]] = {}
    for ln in lines:
        t = ln.text.strip()
        if t and len(t) < 60:
            seen_pages.setdefault(t, set()).add(ln.page)
    repeated = {t for t, pages in seen_pages.items() if len(pages) >= 2}
    # On a two-column page every reference line sits in a column; a column-less
    # (full-width) line there is a footnote, rule or caption and would splice
    # itself into whichever entry spans the column break.
    two_col_pages = {ln.page for ln in lines if ln.column is not None}
    out = []
    for ln in lines:
        t = ln.text.strip()
        if _BARE_PAGE_NO.match(t):
            continue
        if t in repeated:
            continue
        if ln.column is None and ln.page in two_col_pages:
            continue
        out.append(ln)
    return _trim_trailing_junk(_merge_key_rows(out))


_BARE_KEY = re.compile(r"^\s*(\[[^\]]{1,16}\]|\d{1,3}\.)\s*$")
_REF_SIGNAL = re.compile(
    r"\b(1[89]\d{2}|20[0-2]\d|2030)\b|arXiv|\bdoi\b|10\.\d{4,9}/"
    r"|\d+\s*[:(]\s*\d+|\bpp?\.\s*\d", re.I)
_MAX_TRAILING_TRIM = 12


def _merge_key_rows(lines: list[LayoutLine]) -> list[LayoutLine]:
    """Re-attach detached keys. Some styles (amsalpha, plain with hanging keys)
    emit the key column as separate one-token lines ('[24]') whose y does not
    exactly match the entry's first line, so text order interleaves them badly.
    Each bare key is prepended to the same-page line with the greatest vertical
    overlap to its right; keys with no such line are kept as-is."""
    bare_idx = [i for i, ln in enumerate(lines)
                if _BARE_KEY.match(ln.text) and len(ln.text.strip()) <= 16]
    if not bare_idx:
        return lines
    merged: dict[int, str] = {}   # target line index -> key text
    drop: set[int] = set()
    for i in bare_idx:
        key = lines[i]
        best_j, best_overlap = None, 0.0
        for j, ln in enumerate(lines):
            if j == i or j in drop or ln.page != key.page:
                continue
            if ln.x0 < key.x1 - 1.0:
                continue  # must sit to the key's right
            overlap = min(key.y1, ln.y1) - max(key.y0, ln.y0)
            if overlap > best_overlap:
                best_overlap, best_j = overlap, j
        min_h = min(key.y1 - key.y0, 1.0)
        if best_j is not None and best_overlap > 0.5 * min_h and best_j not in merged:
            merged[best_j] = key.text.strip()
            drop.add(i)
    out: list[LayoutLine] = []
    for j, ln in enumerate(lines):
        if j in drop:
            continue
        if j in merged:
            out.append(LayoutLine(
                text=f"{merged[j]} {ln.text}", x0=ln.x0, y0=ln.y0, x1=ln.x1,
                y1=ln.y1, font_size=ln.font_size, page=ln.page, column=ln.column))
        else:
            out.append(ln)
    return out


def _trim_trailing_junk(lines: list[LayoutLine]) -> list[LayoutLine]:
    """Author address blocks and emails trail the last entry when nothing
    terminates the region. Walk back over lines with no reference signal (no
    year, no arXiv/DOI, no volume:page); give up past _MAX_TRAILING_TRIM lines
    (then the 'junk' is probably reference text after all)."""
    cut = len(lines)
    while cut > 0:
        t = lines[cut - 1].text
        if _REF_SIGNAL.search(t) and "@" not in t:
            break
        cut -= 1
        if len(lines) - cut > _MAX_TRAILING_TRIM:
            return lines
    return lines[:cut]


def _assemble(lines: list[LayoutLine], starts: list[int],
              keys: list[str], method: str) -> Candidate:
    entries: list[str] = []
    for i, s in enumerate(starts):
        e = starts[i + 1] if i + 1 < len(starts) else len(lines)
        raw = join_lines([ln.text for ln in lines[s:e]])
        raw = strip_trailing_pages(normalise(raw))
        if raw:
            entries.append(raw)
    return Candidate(method=method, entries=entries, keys=keys)


def _keyed_candidate(lines: list[LayoutLine], pattern: re.Pattern,
                     method: str) -> Candidate | None:
    starts, keys = [], []
    for i, ln in enumerate(lines):
        m = pattern.match(ln.text)
        if m:
            starts.append(i)
            keys.append(m.group(1))
    if len(starts) < MIN_ENTRIES:
        return None
    return _assemble(lines, starts, keys, method)


def _indent_candidate(lines: list[LayoutLine]) -> Candidate | None:
    """Hanging-indent segmentation for unkeyed (APA-ish) bibliographies: a new
    entry starts at a line back at the column's left margin whose text opens
    author-shaped; continuation lines are indented or non-author-shaped."""
    if not lines:
        return None
    # Left margin per (page, column) bucket: the minimum x0 actually observed.
    margins: dict[tuple, float] = {}
    for ln in lines:
        key = (ln.page, ln.column)
        margins[key] = min(margins.get(key, 1e9), ln.x0)
    starts = []
    for i, ln in enumerate(lines):
        at_margin = ln.x0 <= margins[(ln.page, ln.column)] + 2.0
        if at_margin and _AUTHOR_OPEN.match(ln.text.strip()):
            starts.append(i)
    if len(starts) < MIN_ENTRIES:
        return None
    return _assemble(lines, starts, [""] * len(starts), "indent")


def _structural(cand: Candidate) -> bool:
    """Gap-free increasing keys from 1, or a consistent alpha-key set. Decisive."""
    if len(cand.entries) < STRUCTURAL_MIN_ENTRIES:
        return False
    if any(not e for e in cand.entries):
        return False
    if cand.method in ("bracket_numeric", "ordinal"):
        try:
            nums = [int(k) for k in cand.keys]
        except ValueError:
            return False
        return nums == list(range(1, len(nums) + 1))
    if cand.method == "alpha_key":
        pat = re.compile(ALPHA_KEY_PATTERN + r"$")
        return (len(set(cand.keys)) == len(cand.keys)
                and all(pat.fullmatch(k) for k in cand.keys))
    return False


# --- discriminative features -------------------------------------------------

def _features(cand: Candidate, n_intext_markers: int | None) -> dict:
    entries = cand.entries
    lens = [len(e) for e in entries]
    med = statistics.median(lens) if lens else 0
    bodies = [_LEAD_KEY.sub("", e) for e in entries]
    author_frac = sum(1 for b in bodies if _AUTHOR_OPEN.match(b)) / len(entries)
    year_frac = sum(1 for e in entries if _YEAR.search(e)) / len(entries)
    volpage_frac = sum(1 for e in entries if _VOLPAGE.search(e)) / len(entries)
    key_order_frac = None
    if cand.method in ("bracket_numeric", "ordinal") and len(cand.keys) >= 2:
        try:
            nums = [int(k) for k in cand.keys]
            key_order_frac = (sum(1 for a, b in zip(nums, nums[1:]) if b > a)
                              / (len(nums) - 1))
        except ValueError:
            pass
    return {
        "key_order_frac": key_order_frac,
        "median_len": med,
        "len_ok": ENTRY_LEN_MIN <= med <= ENTRY_LEN_MAX,
        "author_frac": author_frac,
        "year_frac": year_frac,
        "volpage_frac": volpage_frac,
        "n_entries": len(entries),
        "marker_agreement": (
            min(len(entries), n_intext_markers) / max(len(entries), n_intext_markers)
            if n_intext_markers else None),
    }


def _composite(f: dict) -> float:
    parts = [
        1.0 if f["len_ok"] else 0.0,
        f["author_frac"],
        f["year_frac"],
        f["volpage_frac"],
    ]
    if f["marker_agreement"] is not None:
        parts.append(f["marker_agreement"])
    return sum(parts) / len(parts)


def count_intext_markers(body_lines: list[LayoutLine], method: str) -> int:
    """Distinct citation markers in the body text, per key style."""
    text = " ".join(ln.text for ln in body_lines)
    if method in ("bracket_numeric", "ordinal"):
        return len(set(re.findall(r"\[(\d{1,3})\]", text)))
    if method == "alpha_key":
        return len(set(re.findall(r"\[(" + ALPHA_KEY_PATTERN + r")\]", text)))
    # author-year: (Name, 2004) / (Name et al. 2004)
    return len(set(re.findall(
        r"\(([A-Z][\w'À-ɏ-]+(?:\s+et\s+al\.?)?,?\s+(?:1[89]|20)\d{2})", text)))


# --- adjudication ------------------------------------------------------------

def _gate_failures(f: dict) -> list[str]:
    fails = []
    if not f["len_ok"]:
        fails.append(
            f"median entry length {f['median_len']:.0f} outside "
            f"[{ENTRY_LEN_MIN}, {ENTRY_LEN_MAX}]")
    if f["author_frac"] < MIN_AUTHOR_FRACTION:
        fails.append(
            f"only {f['author_frac']:.0%} of entries open author-shaped "
            f"(minimum {MIN_AUTHOR_FRACTION:.0%})")
    if f["year_frac"] < MIN_YEAR_FRACTION:
        fails.append(
            f"only {f['year_frac']:.0%} of entries carry a plausible year "
            f"(minimum {MIN_YEAR_FRACTION:.0%})")
    if f["n_entries"] > MAX_ENTRIES:
        fails.append(f"{f['n_entries']} entries exceeds the {MAX_ENTRIES} cap")
    if (f["key_order_frac"] is not None
            and f["key_order_frac"] < MIN_KEY_ORDER_FRACTION):
        fails.append(
            f"numeric keys are out of order ({f['key_order_frac']:.0%} of "
            f"adjacent pairs increasing, minimum {MIN_KEY_ORDER_FRACTION:.0%}) "
            "-- the reading order could not be resolved")
    return fails


def split_bibliography(
    bib_lines: list[LayoutLine],
    body_lines: list[LayoutLine] | None = None,
) -> BibResult:
    """Hermetic core: LayoutLines in, BibResult out. No pdfminer, no I/O."""
    lines = _clean_region(bib_lines)
    if not lines:
        return BibResult.refusal("bibliography region is empty after cleaning")

    candidates: list[Candidate] = []
    for pattern, method in (
        (_BRACKET_NUM, "bracket_numeric"),
        (_ALPHA_KEY, "alpha_key"),
        (_ORDINAL, "ordinal"),
    ):
        c = _keyed_candidate(lines, pattern, method)
        if c:
            candidates.append(c)
    c = _indent_candidate(lines)
    if c:
        candidates.append(c)

    if not candidates:
        return BibResult.refusal(
            "no splitting strategy produced at least "
            f"{MIN_ENTRIES} entries")

    # 1. Structural check, decisive, in strategy order.
    for cand in candidates:
        if _structural(cand):
            if len(cand.entries) > MAX_ENTRIES:
                return BibResult.refusal(
                    f"{len(cand.entries)} entries exceeds the {MAX_ENTRIES} cap")
            return _result(cand, structural=True, confidence=1.0)

    # 2. Margin-based acceptance over discriminative features.
    for cand in candidates:
        n_markers = (count_intext_markers(body_lines, cand.method)
                     if body_lines else None)
        cand.features = _features(cand, n_markers)
        cand.score = _composite(cand.features)

    candidates.sort(key=lambda c: -c.score)
    best = candidates[0]

    # Hard gates first: a specific diagnosis ("no years", "keys out of order")
    # is worth more to the reviewer than a generic low-score refusal.
    fails = _gate_failures(best.features)
    if fails:
        return BibResult.refusal(
            f"winning strategy ({best.method}) failed hard gates: "
            + "; ".join(fails))

    if len(candidates) > 1:
        runner_up = candidates[1]
        if best.score - runner_up.score < ACCEPT_MARGIN:
            return BibResult.refusal(
                f"ambiguous split: best strategy ({best.method}, "
                f"{best.score:.2f}) does not beat the runner-up "
                f"({runner_up.method}, {runner_up.score:.2f}) by the "
                f"{ACCEPT_MARGIN} margin")
    elif best.score < SOLE_CANDIDATE_MIN_SCORE:
        return BibResult.refusal(
            f"sole candidate ({best.method}) scored {best.score:.2f}, below "
            f"the {SOLE_CANDIDATE_MIN_SCORE} single-candidate floor")

    return _result(best, structural=False, confidence=best.score)


def _result(cand: Candidate, structural: bool, confidence: float) -> BibResult:
    entries = [
        BibEntry(raw=e, key=k or None)
        for e, k in zip(cand.entries, cand.keys)
    ]
    return BibResult(
        entries=entries, method=cand.method, confidence=confidence,
        structural=structural,
    )
