"""Run `alembic upgrade head` programmatically at api startup.

KNOWN_ISSUES §16: alembic was copied into the image but never executed, so column
additions silently failed to reach live databases (`create_all` adds tables, never
columns). This module is the fix -- called from the lifespan hook *before*
`create_all`, so from here on every schema change is a real migration.

Live databases created before this wiring have the full initial schema but no
`alembic_version` table (verified 2026-07-29 against the running stack -- the
implementation brief believed the DB was stamped at head; it was not). Running
`upgrade head` against one would try to re-create every table and fail, so those
are stamped at the initial-schema revision first, and only migrations after it run.

The Config is built without an ini file on purpose: alembic's env.py calls
`fileConfig()` when an ini is present, which would clobber uvicorn's logging setup
from inside a startup hook. env.py resolves the database URL itself (DATABASE_URL,
falling back to provenance.config), so the ini adds nothing we need here.
"""
from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text

from .db import engine

log = logging.getLogger("provenance.migrations")

# The revision whose schema equals what Base.metadata.create_all() produced before
# alembic was wired in. Legacy databases are stamped here, never migrated to here.
INITIAL_REVISION = "bec852712a4a"


def run_migrations() -> None:
    root = Path(__file__).resolve().parent.parent  # /app in the container, api/ on host
    cfg = Config()
    cfg.set_main_option("script_location", str(root / "alembic"))

    with engine.connect() as conn:
        has_version = conn.execute(
            text("SELECT to_regclass('alembic_version')")).scalar()
        has_schema = conn.execute(text("SELECT to_regclass('works')")).scalar()

    if has_version is None and has_schema is not None:
        log.info("legacy database without alembic_version; stamping %s",
                 INITIAL_REVISION)
        command.stamp(cfg, INITIAL_REVISION)

    command.upgrade(cfg, "head")
    log.info("alembic upgrade head complete")
