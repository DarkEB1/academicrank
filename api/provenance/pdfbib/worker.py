"""Extraction worker process: PDF bytes on stdin, BibResult JSON on stdout.

Run as `python -m provenance.pdfbib.worker`. A plain subprocess, NOT
multiprocessing: spawn re-imports the parent's main module, which under
`python -m pytest` or uvicorn re-runs the host program inside the child (a
verified hang on Windows). A subprocess with an explicit entrypoint has no such
edge, and killing it on timeout is unambiguous.
"""
from __future__ import annotations

import json
import sys


def main() -> int:
    from .extract import extract_bibliography
    from .guards import ExtractionRefused

    pdf_bytes = sys.stdin.buffer.read()
    try:
        result = extract_bibliography(pdf_bytes)
        payload = {"kind": "ok", "result": result.to_json()}
    except ExtractionRefused as e:
        payload = {"kind": "refused", "reason": str(e)}
    except Exception as e:  # noqa: BLE001 - surfaced as a refusal, with the class
        payload = {"kind": "error", "reason": f"{type(e).__name__}: {e}"}
    sys.stdout.write(json.dumps(payload))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
