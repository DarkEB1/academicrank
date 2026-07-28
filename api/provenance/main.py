"""FastAPI application.

Startup is deliberately tolerant: under `PROVENANCE_AUTOLOAD=1` the API waits for
Postgres, creates the schema and registers the MeritRank contexts. It does *not* wait
for the corpus, because the loader runs independently and the API is useful (and
`/health` is answerable) long before the last work row lands.
"""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from . import config
from .db import engine
from .meritrank import MeritRank
from .models import Base
from .routers import health, imports, papers, profiles, rankings

log = logging.getLogger("provenance")

DB_WAIT_SECONDS = 300
DB_WAIT_INTERVAL = 2.0

DESCRIPTION = """
Personalised, uncertainty-annotated ranking of the mathematics literature over
MeritRank.

**Two rules govern every response.** No score is ever returned without an
`uncertainty` block and a `tie_group` -- items sharing a tie group are statistically
indistinguishable and must be presented as tied. And the score is *proximity in your
own trust graph*, not quality: every list response carries a `disclaimer` the client
renders verbatim.

Authentication is an anonymous profile token, sent as `Authorization: Bearer <token>`
or the `pv_token` cookie. `POST /api/profiles` mints one.
""".strip()


def _wait_for_db(timeout: float = DB_WAIT_SECONDS) -> bool:
    deadline = time.time() + timeout
    last: Exception | None = None
    while time.time() < deadline:
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except SQLAlchemyError as e:
            last = e
            time.sleep(DB_WAIT_INTERVAL)
    log.error("database never became available: %s", last)
    return False


def _ensure_contexts() -> None:
    """Register every MeritRank context. Idempotent, and cheap enough to redo on each
    boot -- which matters because `mr_bulk_load_edges` clears engine state."""
    with engine.connect() as conn:
        mr = MeritRank(conn)
        ok, info = mr.health()
        if not ok:
            log.warning("MeritRank unreachable at startup: %s", info)
            return
        for ctx in [config.AGGREGATE] + config.CONTEXTS:
            mr.create_context(ctx)
        conn.commit()
        log.info("MeritRank %s ready; contexts: %s", info, ", ".join(config.CONTEXTS))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    if config.AUTOLOAD:
        if _wait_for_db():
            Base.metadata.create_all(engine)
            log.info("schema ensured")
            _ensure_contexts()
    yield


app = FastAPI(
    title="Provenance API",
    version="1.0.0",
    description=DESCRIPTION,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,   # required: the pv_token cookie is the fallback auth
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(profiles.router)
app.include_router(papers.router)
app.include_router(rankings.router)
app.include_router(imports.router)
