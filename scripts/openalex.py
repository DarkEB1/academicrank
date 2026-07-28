"""Thin OpenAlex client: disk-cached, rate-limited, credit-aware.

Every response is cached to disk keyed by a hash of the request URL (minus the
api_key, so the cache is shareable and the key never lands on disk). A warm
cache makes re-running the scraper a no-op -- see operating rule 6.
"""
from __future__ import annotations

import hashlib
import json
import os
import gzip
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.openalex.org"
ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache"
MIN_INTERVAL = 0.15  # ~6.6 req/s, comfortably under the 10 req/s ceiling


def _api_key() -> str:
    key = os.environ.get("OPENALEX_API_KEY")
    if not key:
        env = ROOT / ".env"
        if env.exists():
            for line in env.read_text(encoding="utf-8").splitlines():
                if line.startswith("OPENALEX_API_KEY="):
                    key = line.split("=", 1)[1].strip()
    if not key:
        raise SystemExit("OPENALEX_API_KEY missing (.env or environment)")
    return key


class OpenAlex:
    def __init__(self) -> None:
        self.key = _api_key()
        CACHE.mkdir(parents=True, exist_ok=True)
        self._last = 0.0
        self.requests = 0
        self.cache_hits = 0

    def _cache_path(self, url: str) -> Path:
        h = hashlib.sha256(url.encode()).hexdigest()
        return CACHE / h[:2] / f"{h}.json.gz"

    def get(self, path: str, **params) -> dict:
        """GET {API}/{path}?{params}, cached to disk. api_key is never cached."""
        qs = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        url = f"{API}/{path}" + (f"?{qs}" if qs else "")
        cp = self._cache_path(url)
        if cp.exists():
            self.cache_hits += 1
            with gzip.open(cp, "rt", encoding="utf-8") as fh:
                return json.load(fh)

        sep = "&" if "?" in url else "?"
        full = f"{url}{sep}api_key={self.key}"
        data = self._fetch_with_retry(full, url)

        cp.parent.mkdir(parents=True, exist_ok=True)
        tmp = cp.with_suffix(".tmp")
        with gzip.open(tmp, "wt", encoding="utf-8") as fh:
            json.dump(data, fh)
        tmp.replace(cp)
        return data

    def _fetch_with_retry(self, full: str, url: str, attempts: int = 5) -> dict:
        last_err = None
        for i in range(attempts):
            delta = time.monotonic() - self._last
            if delta < MIN_INTERVAL:
                time.sleep(MIN_INTERVAL - delta)
            self._last = time.monotonic()
            try:
                req = urllib.request.Request(
                    full, headers={"User-Agent": "Provenance/0.1 (research prototype)"}
                )
                with urllib.request.urlopen(req, timeout=120) as r:
                    self.requests += 1
                    return json.load(r)
            except urllib.error.HTTPError as e:
                last_err = e
                if e.code in (429, 500, 502, 503, 504):
                    time.sleep(min(2**i, 30))
                    continue
                raise SystemExit(f"OpenAlex HTTP {e.code} for {url}: {e.read()[:400]!r}")
            except Exception as e:  # network hiccup
                last_err = e
                time.sleep(min(2**i, 30))
        raise SystemExit(f"OpenAlex failed after {attempts} attempts: {url} ({last_err})")

    def paginate(self, path: str, per_page: int = 200, max_records: int | None = None, **params):
        """Cursor-paginate a list endpoint, yielding individual records."""
        cursor, seen = "*", 0
        while cursor:
            page = self.get(path, per_page=per_page, cursor=cursor, **params)
            for rec in page.get("results", []):
                yield rec
                seen += 1
                if max_records and seen >= max_records:
                    return
            cursor = page.get("meta", {}).get("next_cursor")
            if not page.get("results"):
                return


def short_id(openalex_id: str | None) -> str | None:
    """https://openalex.org/W123 -> W123"""
    if not openalex_id:
        return None
    return openalex_id.rsplit("/", 1)[-1]


def reconstruct_abstract(inv: dict | None) -> str | None:
    """OpenAlex ships abstracts as an inverted index; rebuild the text."""
    if not inv:
        return None
    positions: list[tuple[int, str]] = []
    for word, idxs in inv.items():
        for i in idxs:
            positions.append((i, word))
    if not positions:
        return None
    positions.sort()
    text = " ".join(w for _, w in positions)
    return text[:8000]
