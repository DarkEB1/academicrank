"""OpenAlex client for the api: httpx, disk-cached, key never logged.

Follows the pattern of scripts/openalex.py (cache keyed by a hash of the URL
WITHOUT the api key, so the key never lands on disk), adapted for service use:
exceptions instead of SystemExit, short timeouts, and an explicit
`OpenAlexUnavailable` so callers can mark entries "couldn't check" -- our
failure -- rather than "not found" -- a claim about the paper.

The cache directory defaults to the system temp dir, NOT data/cache: the api
container mounts ./data read-only.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import tempfile
import threading
import time
from pathlib import Path

import httpx

log = logging.getLogger("provenance.openalex")

API = "https://api.openalex.org"
TIMEOUT_SECONDS = 8.0
MIN_INTERVAL = 0.12
_RETRIES = 2
# Circuit breaker: after a hard failure, raise immediately for this long
# instead of paying the full retry ladder on all remaining entries of a draft.
_BREAKER_SECONDS = 120.0


class OpenAlexUnavailable(Exception):
    """Network/HTTP-5xx/timeout failure. NOT raised for a clean 404."""


def _cache_dir() -> Path:
    override = os.environ.get("OPENALEX_CACHE_DIR")
    if override:
        return Path(override)
    return Path(tempfile.gettempdir()) / "provenance_openalex_cache"


def _api_key() -> str | None:
    key = os.environ.get("OPENALEX_API_KEY")
    if key:
        return key
    # Host-side fallback: the repo .env (never logged, never cached).
    env = Path(__file__).resolve().parents[2] / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("OPENALEX_API_KEY="):
                return line.split("=", 1)[1].strip()
    return None


class OpenAlex:
    def __init__(self) -> None:
        self.key = _api_key()
        self.cache = _cache_dir()
        self.cache.mkdir(parents=True, exist_ok=True)
        self._last = 0.0
        self._down_until = 0.0
        self._lock = threading.Lock()
        self._client = httpx.Client(
            timeout=TIMEOUT_SECONDS,
            headers={"User-Agent": "Provenance/1.0 (research prototype)"},
        )

    def _cache_path(self, url: str) -> Path:
        h = hashlib.sha256(url.encode()).hexdigest()
        return self.cache / h[:2] / f"{h}.json.gz"

    def get(self, path: str, **params) -> dict | None:
        """GET {API}/{path}. Returns None on a clean 404 (the thing does not
        exist); raises OpenAlexUnavailable on any transport/server failure."""
        qs = httpx.QueryParams(
            {k: v for k, v in params.items() if v is not None})
        url = f"{API}/{path}" + (f"?{qs}" if qs else "")
        cp = self._cache_path(url)
        if cp.exists():
            with gzip.open(cp, "rt", encoding="utf-8") as fh:
                cached = json.load(fh)
            return None if cached == {"__404__": True} else cached

        if time.monotonic() < self._down_until:
            raise OpenAlexUnavailable("circuit breaker open (recent failure)")

        req_params = dict(qs)
        if self.key:
            req_params["api_key"] = self.key

        last: Exception | None = None
        for attempt in range(_RETRIES + 1):
            with self._lock:
                delta = time.monotonic() - self._last
                if delta < MIN_INTERVAL:
                    time.sleep(MIN_INTERVAL - delta)
                self._last = time.monotonic()
            try:
                r = self._client.get(f"{API}/{path}", params=req_params)
            except httpx.HTTPError as e:
                last = e
                time.sleep(min(2 ** attempt, 5))
                continue
            if r.status_code == 404:
                self._write_cache(cp, {"__404__": True})
                return None
            if r.status_code in (429, 500, 502, 503, 504):
                last = RuntimeError(f"HTTP {r.status_code}")
                time.sleep(min(2 ** attempt, 5))
                continue
            r.raise_for_status()
            data = r.json()
            self._write_cache(cp, data)
            return data
        self._down_until = time.monotonic() + _BREAKER_SECONDS
        raise OpenAlexUnavailable(str(last))

    def _write_cache(self, cp: Path, data: dict) -> None:
        cp.parent.mkdir(parents=True, exist_ok=True)
        tmp = cp.with_suffix(f".tmp{os.getpid()}")
        with gzip.open(tmp, "wt", encoding="utf-8") as fh:
            json.dump(data, fh)
        tmp.replace(cp)

    # -- typed lookups ---------------------------------------------------------

    def work_by_doi(self, doi: str) -> dict | None:
        return self.get(f"works/https://doi.org/{doi}")

    def work_by_arxiv(self, arxiv_id: str) -> dict | None:
        """arXiv deposits carry DataCite DOIs 10.48550/arxiv.<id>; old-style ids
        (hep-th/9711200) use the same scheme."""
        return self.work_by_doi(f"10.48550/arxiv.{arxiv_id.lower()}")

    def search_title(self, title: str) -> dict | None:
        """Best OpenAlex hit for a title, or None."""
        page = self.get("works", filter=f"title.search:{_sanitise(title)}",
                        per_page=1)
        results = (page or {}).get("results") or []
        return results[0] if results else None


def _sanitise(title: str) -> str:
    # Commas separate OpenAlex filter clauses; colons separate key from value.
    return title.replace(",", " ").replace(":", " ").strip()


def short_id(openalex_id: str | None) -> str | None:
    """https://openalex.org/W123 -> W123"""
    if not openalex_id:
        return None
    return openalex_id.rsplit("/", 1)[-1]
