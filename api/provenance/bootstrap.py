"""Cold-start bootstrap so `docker compose down -v && docker compose up` yields a
working app with real data and no network.

Two independent things can be empty on start, and both must be repaired:

1. **Postgres** -- wiped by `down -v`. Repaired from the committed corpus in
   `data/raw/*.jsonl.gz` by running `scripts/load_db.py`.
2. **mr-service** -- holds the whole graph *in memory*. It is empty after ANY restart of
   that container, even when Postgres is fully populated. Repaired by re-pushing
   `graph_edges` through `mr_bulk_load_edges`, which is fast (~25s) because the edge list
   is already materialised.

Runs on a background thread so uvicorn can serve `/api/health` (reporting
`graph_loaded: false`) while it works, instead of appearing hung.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
from pathlib import Path

from sqlalchemy import text

from . import config
from .db import engine
from .meritrank import Edge, MeritRank

log = logging.getLogger("provenance.bootstrap")

STATE = {"running": False, "stage": "idle", "error": None}
_LOCK = threading.Lock()

def _find_root() -> Path:
    """Locate the directory holding scripts/ and data/.

    In the container the package sits at /app/provenance, so the root is /app. On the
    host it is api/provenance, so the root is the repo root, one level higher. Probe
    rather than assume, and honour an explicit override.
    """
    override = os.environ.get("PROVENANCE_ROOT")
    if override:
        return Path(override)
    here = Path(__file__).resolve()
    for cand in (here.parent.parent, here.parent.parent.parent):
        if (cand / "scripts").is_dir():
            return cand
    return here.parent.parent


ROOT = _find_root()
SCRIPTS = ROOT / "scripts"
RAW = ROOT / "data" / "raw"


def _count(conn, table: str) -> int:
    try:
        return int(conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one())
    except Exception:
        return 0


def needs_corpus() -> bool:
    with engine.connect() as c:
        return _count(c, "works") == 0


def needs_graph_rows() -> bool:
    with engine.connect() as c:
        return _count(c, "graph_edges") == 0


def engine_is_empty() -> bool:
    """True when mr-service has no graph loaded (fresh container)."""
    with engine.connect() as c:
        try:
            n = int(c.execute(text("SELECT count(*) FROM mr_nodelist('')")).scalar_one())
            return n < 100
        except Exception:
            return True


def _run(script: str, *args: str) -> None:
    path = SCRIPTS / script
    if not path.exists():
        raise FileNotFoundError(f"{path} not found; mount ./scripts into the api image")
    # The scripts add `<root>/api` to sys.path themselves, which is right on the host
    # and wrong in the container (the package sits at /app/provenance). Put both on
    # PYTHONPATH so `import provenance` resolves either way.
    env = dict(
        os.environ,
        DATABASE_URL=config.DATABASE_URL,
        PYTHONPATH=os.pathsep.join([str(ROOT), str(ROOT / "api")]),
    )
    log.info("bootstrap: running %s %s", script, " ".join(args))
    p = subprocess.run([sys.executable, str(path), *args], env=env,
                       capture_output=True, text=True, cwd=str(ROOT))
    if p.returncode != 0:
        raise RuntimeError(f"{script} failed rc={p.returncode}: {p.stderr[-2000:]}")
    log.info("bootstrap: %s ok", script)


def push_graph_to_engine() -> int:
    """Re-push the materialised edge list into mr-service. Cheap and idempotent."""
    with engine.connect() as conn:
        mr = MeritRank(conn)
        for ctx in config.CONTEXTS:
            mr.create_context(ctx)
        rows = conn.execute(text(
            "SELECT src, dst, weight, context FROM graph_edges")).all()
        edges = [Edge(r[0], r[1], float(r[2]), r[3]) for r in rows]
        if edges:
            mr.bulk_load(edges)
        conn.commit()
        return len(edges)


def _work() -> None:
    try:
        if needs_corpus():
            if not (RAW / "works_full.jsonl.gz").exists():
                raise FileNotFoundError(
                    f"no corpus in the database and no committed data at {RAW}")
            STATE["stage"] = "loading corpus"
            _run("load_db.py")

        if needs_graph_rows():
            STATE["stage"] = "building graph"
            _run("build_graph.py", "--no-load")

        STATE["stage"] = "pushing graph to mr-service"
        n = push_graph_to_engine()
        STATE["stage"] = f"ready ({n} edges)"
        log.info("bootstrap complete: %s edges in mr-service", n)
    except Exception as e:  # noqa: BLE001 - surfaced on /health
        STATE["error"] = str(e)
        STATE["stage"] = "failed"
        log.exception("bootstrap failed")
    finally:
        STATE["running"] = False


def ensure_started() -> None:
    """Kick off bootstrap if anything is missing. Non-blocking, at most once."""
    with _LOCK:
        if STATE["running"]:
            return
        try:
            if not (needs_corpus() or needs_graph_rows() or engine_is_empty()):
                STATE["stage"] = "ready"
                return
        except Exception as e:  # DB not up yet; caller retries
            log.warning("bootstrap precheck failed: %s", e)
            return
        STATE["running"] = True
        STATE["error"] = None
    threading.Thread(target=_work, name="provenance-bootstrap", daemon=True).start()
