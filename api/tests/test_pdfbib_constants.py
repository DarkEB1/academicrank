"""One named test per named threshold in pdfbib.constants (spec requirement).

Each test demonstrates the constant's behavioural edge with synthetic input --
these are calibration pins: if a constant moves, its test moves with it, and the
diff shows both.
"""
from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from provenance.pdfbib import constants as C
from provenance.pdfbib.guards import ExtractionRefused, check_pdf
from provenance.pdfbib.headings import bibliography_lines
from provenance.pdfbib.layout import _box_spans, tail_page_numbers
from provenance.pdfbib.split import (
    _features, _gate_failures, split_bibliography,
)
from provenance.pdfbib.types import LayoutLine


def mk(text: str, y: float, x0: float = 50.0, page: int = 0,
       fs: float = 10.0, col: int | None = None, width: float = 250.0) -> LayoutLine:
    return LayoutLine(text=text, x0=x0, y0=y, x1=x0 + width, y1=y + fs,
                      font_size=fs, page=page, column=col)


def keyed_entries(n: int, year: bool = True, start: int = 1) -> list[LayoutLine]:
    """n well-formed bracketed-numeric entries, one line each."""
    out = []
    for i in range(n):
        k = start + i
        y = 700 - 14 * (i % 45) - 700 * (i // 45)
        yr = f"({1990 + (k % 30)}) " if year else ""
        out.append(mk(
            f"[{k}] A. Author and B. Author, Title number {k} of the series, "
            f"Journal of Examples {k} {yr}12-34.", y=y, page=i // 45))
    return out


def box(x0: float, x1: float) -> SimpleNamespace:
    return SimpleNamespace(x0=x0, x1=x1)


# --- guards -------------------------------------------------------------------

def test_MAX_PDF_BYTES():
    blob = b"%PDF" + b"0" * C.MAX_PDF_BYTES
    with pytest.raises(ExtractionRefused, match="MB"):
        check_pdf(blob)


def _minimal_pdf(n_pages: int) -> bytes:
    """A syntactically valid PDF with n empty pages, built by hand."""
    objs = ["<< /Type /Catalog /Pages 2 0 R >>"]
    kids = " ".join(f"{3 + i} 0 R" for i in range(n_pages))
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>")
    for _ in range(n_pages):
        objs.append("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>")
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{body}\nendobj\n".encode()
    xref_at = len(out)
    out += f"xref\n0 {len(objs) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        out += f"{off:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_at}\n%%EOF\n").encode()
    return bytes(out)


def test_MAX_PDF_PAGES():
    assert check_pdf(_minimal_pdf(3)) == 3
    with pytest.raises(ExtractionRefused, match="pages"):
        check_pdf(_minimal_pdf(C.MAX_PDF_PAGES + 1))


def test_TAIL_FRACTION():
    pages = tail_page_numbers(80)
    assert pages[0] == int(80 * (1 - C.TAIL_FRACTION))
    assert pages[-1] == 79
    assert tail_page_numbers(1) == [0]          # tiny docs: at least the end
    assert tail_page_numbers(3) == [1, 2]


def test_EXTRACTION_TIMEOUT_SECONDS_is_finite_and_sane():
    # The behavioural cut is exercised in test_pdfbib_pdf.py (spawned worker);
    # here: the constant must be bounded, or a hostile PDF blocks a worker.
    assert 5.0 <= C.EXTRACTION_TIMEOUT_SECONDS <= 600.0


# --- column detection -----------------------------------------------------------

def _two_col_boxes(n_per_side: int = 6) -> list[SimpleNamespace]:
    left = [box(50, 290) for _ in range(n_per_side)]
    right = [box(322, 562) for _ in range(n_per_side)]
    return left + right


def test_COLUMN_GUTTER_MIN_FRACTION():
    two, split = _box_spans(_two_col_boxes(), 612.0)
    assert two and 290 < split < 322
    # Columns whose midpoints sit closer than the gutter minimum: one column.
    narrow = [box(50, 290) for _ in range(6)] + [box(300, 540) for _ in range(6)]
    # midpoints 170 vs 420 -> gap 250 ok; force failure with truly close mids
    close = [box(50, 290) for _ in range(6)] + [box(60, 300) for _ in range(6)]
    two, _ = _box_spans(close, 612.0)
    assert not two


def test_COLUMN_MIN_SIDE_FRACTION():
    boxes = [box(50, 290) for _ in range(14)] + [box(322, 562) for _ in range(2)]
    two, _ = _box_spans(boxes, 612.0)  # 2/16 = 12.5% on the right: not columns
    assert not two


def test_FULL_WIDTH_BOX_FRACTION():
    # Full-width boxes must not vote: 8 voters + 6 full-width still two-column.
    boxes = _two_col_boxes(4) + [box(50, 562) for _ in range(6)]
    two, _ = _box_spans(boxes, 612.0)
    assert two


def test_COLUMN_VOTE_MIN_WIDTH_FRACTION():
    # Narrow fragments (equation shards) must not drown the balance check:
    # 5-per-side genuine columns plus 30 narrow left-side fragments.
    boxes = _two_col_boxes(5) + [box(60 + i, 60 + i + 40) for i in range(30)]
    two, _ = _box_spans(boxes, 612.0)
    assert two


def test_COLUMN_MIN_VOTING_BOXES():
    two, _ = _box_spans(_two_col_boxes(3), 612.0)  # 6 voters < minimum 8
    assert not two


def test_COLUMN_SPLIT_MIN_MAX_FRACTION():
    # A "gutter" at ~35% of the width is a margin column, not two columns.
    boxes = [box(20, 100) for _ in range(6)] + [box(170, 560) for _ in range(6)]
    two, _ = _box_spans(boxes, 612.0)  # split at 212.5 < 0.35 * 612 = 214.2
    assert not two


# --- headings -------------------------------------------------------------------

def _region_of(lines):
    region, _heading = bibliography_lines(lines)
    return region


def test_BIBLIOGRAPHY_HEADINGS():
    for head in ("References", "REFERENCES", "R E F E R E N C E S",
                 "7. Bibliography", "Literaturverzeichnis"):
        lines = [mk(head, y=700, fs=14.0)] + keyed_entries(8)
        assert _region_of(lines), f"heading {head!r} not recognised"


def test_POST_BIB_HEADINGS():
    lines = ([mk("References", y=720, fs=14.0)] + keyed_entries(8)
             + [mk("Acknowledgements", y=80, fs=14.0),
                mk("We thank the anonymous reviewers.", y=66)])
    region = _region_of(lines)
    assert all("reviewers" not in ln.text for ln in region)


def test_HEADING_FONT_MARGIN():
    # Heading displayed larger than body: a same-size line ends the region.
    big = ([mk("References", y=720, fs=14.0)] + keyed_entries(8)
           + [mk("B. Extra material", y=80, fs=14.0),
              mk("Content after the sibling heading.", y=66)])
    region = _region_of(big)
    assert all("sibling" not in ln.text for ln in region)
    # Heading at body size (AMS small caps): the rule must stay OFF, or the
    # first same-size line would cut the region.
    small = [mk("References", y=720, fs=10.0)] + keyed_entries(8)
    assert len(_region_of(small)) == 8


def test_DENSE_RUN_MIN_LINES_and_MIN_REFLIKE_FRACTION():
    # Unheaded, unkeyed author-year block: dense run fires...
    refs = [mk(f"Author{i} A, Other B ({1990 + i}). Title {i}. Journal, 1-10.",
               y=700 - 14 * i) for i in range(C.DENSE_RUN_MIN_LINES)]
    assert _region_of(refs)
    # ...but not on plain prose of the same length.
    prose = [mk("This sentence discusses methods without citing anything.",
                y=700 - 14 * i) for i in range(C.DENSE_RUN_MIN_LINES)]
    assert not _region_of(prose)


def test_KEYED_REGION_MIN_KEYS_and_DENSITY():
    # Heading-less keyed bibliography (REVTeX): fallback fires from [1].
    assert len(_region_of(keyed_entries(C.KEYED_REGION_MIN_KEYS))) \
        == C.KEYED_REGION_MIN_KEYS
    # Too few keys: no region.
    assert not _region_of(keyed_entries(C.KEYED_REGION_MIN_KEYS - 2))


# --- structural check -----------------------------------------------------------

def test_ALPHA_KEY_PATTERN():
    pat = re.compile(C.ALPHA_KEY_PATTERN + r"$")
    for good in ("Har77", "BCHM10", "Kol13a", "McK07", "GKP13"):
        assert pat.fullmatch(good), good
    for bad in ("KM", "harv77", "77", "H", "Kaw"):
        assert not pat.fullmatch(bad), bad


def test_STRUCTURAL_MIN_ENTRIES():
    below = split_bibliography(keyed_entries(C.STRUCTURAL_MIN_ENTRIES - 1))
    assert not below.structural  # gap-free but too few to be decisive
    at = split_bibliography(keyed_entries(C.STRUCTURAL_MIN_ENTRIES))
    assert at.structural and at.confidence == 1.0


# --- discriminative features and gates -------------------------------------------

def _cand_of(entries: list[str]):
    return SimpleNamespace(method="indent", entries=entries,
                           keys=[""] * len(entries))


def test_ENTRY_LEN_MIN_and_MAX():
    short = _features(_cand_of(["A. B, T." for _ in range(10)]), None)
    assert not short["len_ok"] and any("length" in f for f in _gate_failures(short))
    blob = _features(_cand_of(["X" * (C.ENTRY_LEN_MAX + 50) for _ in range(10)]), None)
    assert not blob["len_ok"]
    ok = _features(_cand_of(["A. Author, A title of reasonable length, "
                             "Journal of Examples 12 (1999) 345-367." ] * 10), None)
    assert ok["len_ok"]


def test_YEAR_MIN_and_MAX():
    f = _features(_cand_of([f"A. Author, Old title, Gazette ({C.YEAR_MIN - 1}) 1-2, "
                            "with more text to reach a plausible length ok."] * 8), None)
    assert f["year_frac"] == 0.0
    f = _features(_cand_of([f"A. Author, New title, Gazette ({C.YEAR_MAX}) 1-2, "
                            "with more text to reach a plausible length ok."] * 8), None)
    assert f["year_frac"] == 1.0


def test_MIN_YEAR_FRACTION():
    f = _features(_cand_of(["A. Author, Undated book title, Publisher House, "
                            "City of Print, first edition, hardcover."] * 8), None)
    assert any("year" in fail for fail in _gate_failures(f))


def test_MIN_AUTHOR_FRACTION():
    f = _features(_cand_of(["the quick brown fox jumps over the lazy dog near "
                            "the river bank in autumn (1999) 12-34."] * 8), None)
    assert any("author" in fail for fail in _gate_failures(f))


def test_MIN_KEY_ORDER_FRACTION():
    cand = SimpleNamespace(
        method="bracket_numeric",
        entries=[f"[{k}] A. Author, Title {k}, Journal {k} (199{k % 10}) 1-10, "
                 "with enough trailing text to pass length." for k in
                 (1, 4, 2, 5, 3, 6, 7, 10, 8, 9)],
        keys=[str(k) for k in (1, 4, 2, 5, 3, 6, 7, 10, 8, 9)])
    f = _features(cand, None)
    assert f["key_order_frac"] < C.MIN_KEY_ORDER_FRACTION
    assert any("order" in fail for fail in _gate_failures(f))


def test_ACCEPT_MARGIN_and_SOLE_CANDIDATE_MIN_SCORE():
    # Ordinal-numbered undated prose list: candidates exist but none is a clear
    # winner over the others -> ambiguous or floor refusal, never acceptance.
    lines = [mk(f"{i}. Chapter heading about methods and results, revised "
                "draft with extended commentary and notes.",
                y=700 - 14 * i) for i in range(1, 9)]
    result = split_bibliography(lines)
    assert result.refused
    assert C.ACCEPT_MARGIN > 0 and C.SOLE_CANDIDATE_MIN_SCORE > 0


def test_MIN_ENTRIES():
    result = split_bibliography(keyed_entries(C.MIN_ENTRIES - 1))
    assert result.refused


def test_MAX_ENTRIES():
    result = split_bibliography(keyed_entries(C.MAX_ENTRIES + 5))
    assert result.refused and str(C.MAX_ENTRIES) in result.refusal_reason
