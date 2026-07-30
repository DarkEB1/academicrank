"""Slow, non-hermetic pdfbib tests: the real PDFs through the real pdfminer
path (one per fixture, spec requirement), plus the guards that need genuine PDF
bytes -- the timeout worker and the text-less refusal.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from provenance.pdfbib import extract_bibliography, extract_with_timeout
from provenance.pdfbib.guards import ExtractionRefused
from test_pdfbib_constants import _minimal_pdf

FIXDIR = Path(__file__).parent / "fixtures" / "pdfbib"
EXPECTED = json.loads((FIXDIR / "expected.json").read_text(encoding="utf-8"))
FIXTURES = [k for k in EXPECTED if not k.startswith("_")]


@pytest.mark.parametrize("name", FIXTURES)
def test_pdf_to_entries_end_to_end(name: str):
    """PDF -> layout -> region -> split, one slow pass per fixture. Guards the
    hermetic artefacts against silent pdfminer drift."""
    spec = EXPECTED[name]
    result = extract_bibliography((FIXDIR / f"{name}.pdf").read_bytes())
    assert not result.refused, result.refusal_reason
    assert result.method == spec["method"]
    assert len(result.entries) == spec["n_entries"]
    assert result.entries[0].raw.startswith(spec["first_prefix"])
    assert result.entries[-1].raw.startswith(spec["last_prefix"])

    doi_count = sum(1 for e in result.entries if e.doi)
    arxiv_count = sum(1 for e in result.entries if e.arxiv_id)
    if "min_doi_count" in spec:
        assert doi_count >= spec["min_doi_count"], f"only {doi_count} DOIs parsed"
    if "max_doi_count" in spec:
        assert doi_count <= spec["max_doi_count"]
    if "min_arxiv_count" in spec:
        assert arxiv_count >= spec["min_arxiv_count"], (
            f"only {arxiv_count} arXiv ids parsed")


def test_textless_pdf_refused_with_specific_reason():
    """A structurally valid PDF with no text layer (a scan, in effect) must be
    refused as such -- never as a generic parse error."""
    with pytest.raises(ExtractionRefused, match="scan|text"):
        extract_bibliography(_minimal_pdf(5))


def test_timeout_worker_kills_slow_extraction():
    """The wall-clock timeout runs extraction in a separate process and refuses
    when it overruns. An absurdly small budget guarantees the overrun without
    needing a hostile PDF."""
    pdf = (FIXDIR / "two_column_doi_rich.pdf").read_bytes()
    result = extract_with_timeout(pdf, timeout=0.05)
    assert result.refused
    assert "time limit" in (result.refusal_reason or "")


def test_timeout_worker_happy_path():
    """Same worker, sane budget: the result must round-trip the process
    boundary intact."""
    pdf = (FIXDIR / "pre2000_no_doi.pdf").read_bytes()
    result = extract_with_timeout(pdf, timeout=120.0)
    assert not result.refused, result.refusal_reason
    assert len(result.entries) == EXPECTED["pre2000_no_doi"]["n_entries"]
