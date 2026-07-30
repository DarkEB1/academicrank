"""Hermetic splitter/scorer tests over the committed layout-line artefacts, the
Phase-1 acceptance gate (numbers printed), and the four adversarial synthetic
cases, each of which must be REFUSED.

Nothing in this file touches pdfminer or a PDF: the artefacts are JSON.
"""
from __future__ import annotations

import gzip
import json
import re
from pathlib import Path

import pytest

from provenance.pdfbib.headings import bibliography_lines
from provenance.pdfbib.split import split_bibliography
from provenance.pdfbib.types import LayoutLine

FIXDIR = Path(__file__).parent / "fixtures" / "pdfbib"
EXPECTED = json.loads((FIXDIR / "expected.json").read_text(encoding="utf-8"))
FIXTURES = [k for k in EXPECTED if not k.startswith("_")]


def load_lines(name: str) -> list[LayoutLine]:
    with gzip.open(FIXDIR / f"{name}.layout.json.gz", "rt", encoding="utf-8") as fh:
        payload = json.load(fh)
    return [LayoutLine.from_json(d) for d in payload["lines"]]


def run_fixture(name: str):
    lines = load_lines(name)
    bib, heading = bibliography_lines(lines)
    assert bib, f"{name}: no bibliography region found"
    result = split_bibliography(bib)
    return result, heading


def _expected_keys(spec: dict) -> list[str] | None:
    keys = spec.get("keys")
    if isinstance(keys, str) and ".." in keys:
        lo, hi = keys.split("..")
        return [str(i) for i in range(int(lo), int(hi) + 1)]
    return keys


@pytest.mark.parametrize("name", FIXTURES)
def test_fixture_split(name: str):
    spec = EXPECTED[name]
    result, heading = run_fixture(name)
    assert not result.refused, f"{name} refused: {result.refusal_reason}"
    assert result.method == spec["method"]
    assert result.structural == spec["structural"]
    assert len(result.entries) == spec["n_entries"]
    assert heading == spec["heading"]
    assert result.entries[0].raw.startswith(spec["first_prefix"])
    assert result.entries[-1].raw.startswith(spec["last_prefix"])
    keys = _expected_keys(spec)
    if keys is not None:
        assert [e.key for e in result.entries] == keys


def _delimited_fraction(name: str) -> tuple[int, int]:
    """(correct, total_true): an entry counts as correctly delimited when it
    carries the expected key in the expected position (keyed styles) or opens
    with an author-year head (unkeyed), against the independent ground truth."""
    spec = EXPECTED[name]
    result, _ = run_fixture(name)
    if result.refused:
        return 0, spec["n_entries"]
    keys = _expected_keys(spec)
    if keys is not None:
        got = [e.key for e in result.entries]
        correct = sum(1 for i, k in enumerate(keys)
                      if i < len(got) and got[i] == k
                      and result.entries[i].raw.startswith(f"[{k}]"))
    else:
        head = re.compile(spec["entry_head_pattern"])
        correct = sum(1 for e in result.entries if head.match(e.raw))
        correct = min(correct, spec["n_entries"])
    return correct, spec["n_entries"]


def test_phase1_acceptance_gate(capsys):
    """Spec gate: >= 90% of entries correctly delimited on at least 3 of the 4
    fixtures. Numbers are printed, not just asserted."""
    passing = 0
    report = []
    for name in FIXTURES:
        correct, total = _delimited_fraction(name)
        frac = correct / total
        report.append(f"  {name}: {correct}/{total} = {frac:.1%}")
        if frac >= 0.90:
            passing += 1
    with capsys.disabled():
        print("\nPhase 1 gate - correctly delimited entries per fixture:")
        for line in report:
            print(line)
        print(f"  fixtures at >=90%: {passing}/4 (gate needs >=3)")
    assert passing >= 3, "\n".join(report)


# ---------------------------------------------------------------------------
# Adversarial synthetic cases -- each must be REFUSED, never mis-accepted.
# A rejecter cannot be tested on well-formed input (spec, Testing).
# ---------------------------------------------------------------------------

def mk(text: str, y: float, x0: float = 50.0, page: int = 0,
       fs: float = 10.0, col: int | None = None, width: float = 250.0) -> LayoutLine:
    return LayoutLine(text=text, x0=x0, y0=y, x1=x0 + width, y1=y + fs,
                      font_size=fs, page=page, column=col)


def test_adversarial_oversplit_trap_refused():
    """Body prose whose lines happen to start with gap-free [1]..[5] markers --
    the trap that defeats a splitter that trusts key shape alone. No years, no
    author-shaped openings: must go to review."""
    prose = [
        "[1] shows that the operator norm is bounded above by the spectral",
        "radius whenever the underlying space is complete and the map",
        "[2] gives the converse direction under the additional hypothesis",
        "of compactness, which cannot be dropped as the counterexample in",
        "[3] demonstrates for weighted shifts on separable Hilbert spaces",
        "with unbounded weight sequences of subexponential growth rates",
        "[4] extends the argument to the non-separable setting using nets",
        "instead of sequences and a transfinite exhaustion of the domain",
        "[5] concludes with applications to ergodic averages along cubes",
        "and polynomial orbits in nilpotent group actions on tori",
    ]
    lines = [mk(t, y=700 - 14 * i) for i, t in enumerate(prose)]
    result = split_bibliography(lines)
    assert result.refused, (
        f"over-split trap was accepted: method={result.method} "
        f"entries={len(result.entries)}")


def test_adversarial_column_break_interleave_refused():
    """Column detection failed on a genuinely two-column page (all lines came
    back column=None), so text order interleaves the two columns row by row and
    every entry is sheared mid-sentence. Keys arrive out of order; the correct
    outcome is refusal, not a confidently wrong split."""
    left = [
        "[1] A. Author, B. Coauthor, On the first topic of interest,",
        "Journal of Examples 12 (1999) 345-367.",
        "[2] C. Writer, D. Scribe, A second contribution to the field,",
        "Annals of Cases 3 (2001) 1-20.",
        "[3] E. Scholar, Third considerations revisited once more,",
        "Bulletin of Instances 7 (2003) 88-104.",
    ]
    right = [
        "[4] F. Theorist, G. Prover, Fourth remarks on convergence,",
        "Proceedings of Samples 4 (2005) 210-230.",
        "[5] H. Analyst, Fifth and final observations in the series,",
        "Reviews of Illustrations 9 (2007) 55-73.",
        "[6] I. Geometer, Sixth structures on moduli of examples,",
        "Transactions of Demonstrations 2 (2009) 140-159.",
    ]
    lines: list[LayoutLine] = []
    for i in range(6):  # interleave row-wise, as a sheared reading order would
        lines.append(mk(left[i], y=700 - 14 * i, x0=50, col=None))
        lines.append(mk(right[i], y=700 - 14 * i, x0=320, col=None))
    result = split_bibliography(lines)
    assert result.refused, (
        f"interleaved column shear was accepted: method={result.method} "
        f"entries={[e.raw[:40] for e in result.entries]}")


def test_adversarial_fullwidth_footnote_refused():
    """A full-width footnote on a two-column page whose own numeric markers
    ([1], [2]) collide with the bibliography's keys after the column metadata
    was lost. The key sequence is irreparable; refuse."""
    entries = [
        "[1] A. Author, First title of record, J. Ex. 1 (1991) 1-10.",
        "[2] B. Author, Second title of record, J. Ex. 2 (1992) 11-20.",
        "[3] C. Author, Third title of record, J. Ex. 3 (1993) 21-30.",
        "[4] D. Author, Fourth title of record, J. Ex. 4 (1994) 31-40.",
    ]
    footnote = [
        "[1] Supported by grant 12345 of the Example Foundation and by",
        "[2] the Hypothetical Institute visiting programme.",
    ]
    lines = [mk(t, y=700 - 14 * i, col=None) for i, t in enumerate(entries)]
    # Footnote sits at the page bottom, full width, smaller font, column lost.
    lines += [mk(t, y=100 - 12 * i, fs=8.0, width=500.0, col=None)
              for i, t in enumerate(footnote)]
    result = split_bibliography(lines)
    assert result.refused, (
        f"footnote-key collision was accepted: method={result.method} "
        f"entries={[e.raw[:40] for e in result.entries]}")


def test_adversarial_undated_books_refused():
    """A bibliography of undated books: well-formed hanging-indent entries,
    author-shaped, but not one carries a year. Recall is known to degrade worst
    here (KNOWN_ISSUES); the failure mode must be review, never silent trust."""
    books = [
        "Hartshorne R. Algebraic Geometry. Springer Graduate Texts,",
        "New York. Foundational reference for the working geometer.",
        "Bourbaki N. Elements de Mathematique, Algebre Commutative.",
        "Hermann, Paris. The canonical source text of the school.",
        "Grothendieck A. Elements de Geometrie Algebrique, volume II.",
        "Publications Mathematiques, Bures-sur-Yvette, France.",
        "Serre JP. Faisceaux Algebriques Coherents. Annals reprint,",
        "Princeton University Press, Princeton, New Jersey.",
        "Weil A. Foundations of Algebraic Geometry. AMS Colloquium",
        "Publications, Providence, Rhode Island, revised edition.",
        "Zariski O. Commutative Algebra, with Samuel P, volume one.",
        "Van Nostrand, Princeton. Standard graduate reference work.",
        "Atiyah M, Macdonald I. Introduction to Commutative Algebra.",
        "Addison-Wesley, Reading, Massachusetts. Slim classic text.",
        "Matsumura H. Commutative Ring Theory. Cambridge University",
        "Press, Cambridge. Translated from the Japanese original.",
    ]
    lines = []
    for i, t in enumerate(books):
        indent = 50.0 if i % 2 == 0 else 62.0  # hanging indent pairs
        lines.append(mk(t, y=700 - 14 * i, x0=indent))
    result = split_bibliography(lines)
    assert result.refused, (
        f"undated books were accepted: method={result.method} "
        f"entries={len(result.entries)}")
    assert "year" in (result.refusal_reason or "").lower() or "ambiguous" in (
        result.refusal_reason or "").lower(), result.refusal_reason
