"""Regenerate the hermetic layout-line artefacts from the committed fixture PDFs.

Run from api/:  python tests/fixtures/pdfbib/generate.py

Writes <fixture>.layout.json.gz next to each PDF. expected.json is maintained BY
HAND -- it is the ground truth the tests assert against, so a generator must
never overwrite it.
"""
from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))  # api/

from provenance.pdfbib.extract import layout_lines_for_pdf  # noqa: E402

FIXTURES = [
    "two_column_doi_rich",
    "ams_alpha_math_ag",
    "apa_unnumbered_stats",
    "pre2000_no_doi",
]


def main() -> None:
    for name in FIXTURES:
        pdf = HERE / f"{name}.pdf"
        lines, n_pages = layout_lines_for_pdf(pdf.read_bytes())
        payload = {
            "fixture": name,
            "n_pages": n_pages,
            "lines": [ln.to_json() for ln in lines],
        }
        out = HERE / f"{name}.layout.json.gz"
        with gzip.open(out, "wt", encoding="utf-8") as fh:
            json.dump(payload, fh)
        print(f"{name}: {len(lines)} lines, {n_pages} pages -> {out.name}")


if __name__ == "__main__":
    main()
