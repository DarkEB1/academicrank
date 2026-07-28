"""Phase 1: build the mathematics corpus from OpenAlex.

  1. Resolve the Mathematics field id from /fields (never hardcoded).
  2. Pull ~3,000 works in that field, publication_year >= 1990, by citation count.
  3. Snowball one hop: anything referenced by >= 3 corpus papers becomes a full node.
  4. Everything else referenced becomes a lightweight stub so citation edges don't dangle.

Re-running with a warm cache is a no-op. Writes data/raw/*.jsonl.gz.
"""
from __future__ import annotations

import collections
import gzip
import json
import sys
from pathlib import Path

from openalex import OpenAlex, short_id

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"

TARGET_CORPUS = 3000
MIN_YEAR = 1990
SNOWBALL_MIN_REFERRERS = 3

WORK_FIELDS = ",".join([
    "id", "doi", "display_name", "publication_year", "publication_date",
    "cited_by_count", "authorships", "primary_location", "topics",
    "primary_topic", "referenced_works", "abstract_inverted_index", "type",
    "language", "open_access",
])
STUB_FIELDS = "id,display_name,publication_year,cited_by_count,type"


def write_jsonl(path: Path, records) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")
            n += 1
    return n


def read_jsonl(path: Path):
    if not path.exists():
        return
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def resolve_math_field(oa: OpenAlex) -> str:
    for f in oa.get("fields")["results"]:
        if f["display_name"].strip().lower() == "mathematics":
            return f["id"].rsplit("/", 1)[-1]
    raise SystemExit("Could not resolve the Mathematics field from /fields")


def fetch_by_ids(oa: OpenAlex, ids: list[str], select: str, chunk: int = 50):
    """Hydrate works by id, up to 50 pipe-separated ids per request."""
    out = []
    for i in range(0, len(ids), chunk):
        batch = ids[i:i + chunk]
        page = oa.get(
            "works",
            filter=f"openalex_id:{'|'.join(batch)}",
            per_page=chunk,
            select=select,
        )
        out.extend(page.get("results", []))
        done = min(i + chunk, len(ids))
        if done % 1000 == 0 or done == len(ids):
            print(f"    hydrated {done}/{len(ids)}", flush=True)
    return out


def main() -> int:
    oa = OpenAlex()
    print("Resolving Mathematics field...", flush=True)
    field = resolve_math_field(oa)
    print(f"  field = {field}", flush=True)

    filt = f"primary_topic.field.id:fields/{field},publication_year:>{MIN_YEAR - 1}"
    print(f"Pulling {TARGET_CORPUS} works ({filt})...", flush=True)
    corpus = []
    for w in oa.paginate(
        "works", per_page=200, max_records=TARGET_CORPUS,
        filter=filt, sort="cited_by_count:desc", select=WORK_FIELDS,
    ):
        corpus.append(w)
        if len(corpus) % 500 == 0:
            print(f"  {len(corpus)}/{TARGET_CORPUS}", flush=True)
    print(f"  corpus: {len(corpus)} works", flush=True)

    corpus_ids = {short_id(w["id"]) for w in corpus}

    # --- snowball one hop -------------------------------------------------
    referrer_count = collections.Counter()
    for w in corpus:
        for ref in w.get("referenced_works") or []:
            rid = short_id(ref)
            if rid and rid not in corpus_ids:
                referrer_count[rid] += 1

    promote = sorted([r for r, c in referrer_count.items() if c >= SNOWBALL_MIN_REFERRERS])
    stub_ids = sorted([r for r, c in referrer_count.items() if c < SNOWBALL_MIN_REFERRERS])
    print(f"External referenced works: {len(referrer_count)}", flush=True)
    print(f"  promote to full (>= {SNOWBALL_MIN_REFERRERS} referrers): {len(promote)}", flush=True)
    print(f"  keep as stubs: {len(stub_ids)}", flush=True)

    print("Hydrating promoted works...", flush=True)
    promoted = fetch_by_ids(oa, promote, WORK_FIELDS)
    print(f"  got {len(promoted)}", flush=True)

    # The promoted works are full nodes too, so THEIR references also need stubs --
    # otherwise every citation from a promoted paper to an unknown target dangles and
    # is dropped at load time. Missing this cost ~56k citation edges on the first run.
    full_ids = corpus_ids | {short_id(w["id"]) for w in promoted}
    extra: set[str] = set()
    for w in promoted:
        for ref in w.get("referenced_works") or []:
            rid = short_id(ref)
            if rid and rid not in full_ids:
                extra.add(rid)
    stub_set = set(stub_ids) | extra
    stub_set -= full_ids
    print(f"  additional stub targets from promoted works: "
          f"{len(stub_set) - len(stub_ids)}", flush=True)
    stub_ids = sorted(stub_set)

    print(f"Hydrating {len(stub_ids)} stubs...", flush=True)
    stubs = fetch_by_ids(oa, stub_ids, STUB_FIELDS)
    print(f"  got {len(stubs)}", flush=True)

    n_full = write_jsonl(RAW / "works_full.jsonl.gz", corpus + promoted)
    n_stub = write_jsonl(RAW / "works_stub.jsonl.gz", stubs)
    (RAW / "manifest.json").write_text(json.dumps({
        "field": field,
        "filter": filt,
        "target_corpus": TARGET_CORPUS,
        "seed_works": len(corpus),
        "promoted_works": len(promoted),
        "full_works": n_full,
        "stub_works": n_stub,
        "snowball_min_referrers": SNOWBALL_MIN_REFERRERS,
        "api_requests": oa.requests,
        "cache_hits": oa.cache_hits,
    }, indent=2), encoding="utf-8")

    print(f"\nWrote {n_full} full works, {n_stub} stubs", flush=True)
    print(f"API requests: {oa.requests}, cache hits: {oa.cache_hits}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
