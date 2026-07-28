"""GET /api/health."""
from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from .. import config, schemas, services
from ..deps import DbSession

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health", response_model=schemas.HealthResponse)
def health(db: DbSession) -> schemas.HealthResponse:
    """Liveness plus a *real* MeritRank round trip.

    `mr_service()` and `mr_connector()` return a compile-time constant and never touch
    the network (DECISIONS.md D1.1), so they cannot be a health check.
    `MeritRank.health()` calls `mr_create_context`, which is the cheapest call that
    actually reaches the service.
    """
    db_ok = False
    detail: str | None = None
    try:
        db.execute(text("SELECT 1"))
        db_ok = True
    except Exception as e:  # noqa: BLE001
        detail = f"db: {e}"

    mr_ok = False
    nodes = 0
    edges = 0
    if db_ok:
        mr = services.mr_of(db)
        mr_ok, info = mr.health()
        if not mr_ok:
            detail = f"meritrank: {info}"
        else:
            try:
                nodes = len(mr.nodelist(config.AGGREGATE))
                edges = mr.edge_count(config.AGGREGATE)
            except Exception as e:  # noqa: BLE001
                detail = f"meritrank graph read: {e}"
        db.commit()

    graph_loaded = False
    if db_ok:
        persisted = db.execute(text("SELECT count(*) FROM graph_edges")).scalar_one()
        graph_loaded = bool(persisted) and edges > 0

    return schemas.HealthResponse(
        ok=db_ok and mr_ok,
        db=db_ok,
        meritrank=mr_ok,
        graph_loaded=graph_loaded,
        nodes=nodes,
        edges=edges,
        detail=detail,
    )
