"""Input guards and the wall-clock timeout worker.

Refusals are specific (spec, Error handling): encrypted / image-only / text-less
PDFs are rejected with the actual reason, never a generic parse error. The
timeout runs the whole extraction in a separate PROCESS -- a thread cannot be
killed, and a hostile PDF can make pdfminer spin for minutes.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

from pdfminer.pdfdocument import PDFDocument, PDFEncryptionError, PDFPasswordIncorrect
from pdfminer.pdfpage import PDFPage
from pdfminer.pdfparser import PDFParser, PDFSyntaxError

from .constants import EXTRACTION_TIMEOUT_SECONDS, MAX_PDF_BYTES, MAX_PDF_PAGES
from .types import BibResult


class ExtractionRefused(Exception):
    """Raised with a user-facing, specific reason."""


def check_pdf(pdf_bytes: bytes) -> int:
    """Structural guards. Returns the page count."""
    if not pdf_bytes:
        raise ExtractionRefused("The upload is empty.")
    if len(pdf_bytes) > MAX_PDF_BYTES:
        raise ExtractionRefused(
            f"The PDF is {len(pdf_bytes) / (1024 * 1024):.1f} MB; the limit is "
            f"{MAX_PDF_BYTES // (1024 * 1024)} MB.")
    if not pdf_bytes.startswith(b"%PDF"):
        raise ExtractionRefused("The file is not a PDF (missing %PDF header).")
    try:
        parser = PDFParser(io.BytesIO(pdf_bytes))
        doc = PDFDocument(parser)
    except (PDFEncryptionError, PDFPasswordIncorrect) as e:
        raise ExtractionRefused(
            "The PDF is encrypted; decrypt it before uploading.") from e
    except PDFSyntaxError as e:
        raise ExtractionRefused(f"The PDF is malformed: {e}") from e
    if not doc.is_extractable:
        raise ExtractionRefused(
            "The PDF forbids text extraction (its permissions flag copying off).")
    n_pages = sum(1 for _ in PDFPage.create_pages(doc))
    if n_pages == 0:
        raise ExtractionRefused("The PDF has no pages.")
    if n_pages > MAX_PDF_PAGES:
        raise ExtractionRefused(
            f"The PDF has {n_pages} pages; the limit is {MAX_PDF_PAGES}.")
    return n_pages


def extract_with_timeout(
    pdf_bytes: bytes, timeout: float = EXTRACTION_TIMEOUT_SECONDS
) -> BibResult:
    """Run the extraction in a worker subprocess with a wall-clock timeout.

    A subprocess with an explicit `-m provenance.pdfbib.worker` entrypoint, not
    multiprocessing: spawn re-imports the parent's MAIN module, which under
    pytest or uvicorn re-runs the host program inside the child (verified hang
    on Windows).
    """
    pkg_root = Path(__file__).resolve().parents[2]  # dir containing provenance/
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(pkg_root), env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "provenance.pdfbib.worker"],
            input=pdf_bytes, capture_output=True, timeout=timeout, env=env,
        )
    except subprocess.TimeoutExpired:
        return BibResult.refusal(
            f"Extraction exceeded the {timeout:.0f}s time limit. The PDF is "
            "too complex to parse; add references by search instead.")
    if proc.returncode != 0 or not proc.stdout:
        return BibResult.refusal(
            "Extraction crashed before producing a result "
            f"(worker exit code {proc.returncode}: "
            f"{proc.stderr.decode(errors='replace')[-300:]})")
    payload = json.loads(proc.stdout)
    if payload["kind"] == "ok":
        return BibResult.from_json(payload["result"])
    if payload["kind"] == "refused":
        return BibResult.refusal(payload["reason"])
    return BibResult.refusal(f"Extraction failed: {payload['reason']}")
