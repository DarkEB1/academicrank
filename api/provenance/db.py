"""Engine/session plumbing."""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from . import config

engine = create_engine(
    config.DATABASE_URL, future=True, pool_pre_ping=True, pool_size=20, max_overflow=10
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


def get_session() -> Iterator[Session]:
    with SessionLocal() as s:
        yield s
