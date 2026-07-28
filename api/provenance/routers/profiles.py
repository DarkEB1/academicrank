"""Profiles, the trust set, and the parameter playground."""
from __future__ import annotations

import datetime as dt
import secrets
import uuid

from fastapi import APIRouter, BackgroundTasks, HTTPException, Response, status

from .. import config, ranking, schemas, services
from ..deps import COOKIE_NAME, CurrentProfile, DbSession, OwnedProfile
from ..models import Profile, Trust, Work

router = APIRouter(prefix="/api", tags=["profiles"])

# One year. There is nothing to recover if it is lost, which is the point: the cookie
# *is* the account.
COOKIE_MAX_AGE = 365 * 24 * 3600


def _default_params() -> dict:
    return {"context_weights": dict(config.DEFAULT_CONTEXT_WEIGHTS)}


def _stored(profile: Profile) -> schemas.StoredParams:
    return schemas.StoredParams(context_weights=services.stored_weights(profile))


@router.post("/profiles", response_model=schemas.ProfileCreated,
             status_code=status.HTTP_201_CREATED)
def create_profile(
    body: schemas.ProfileCreate, response: Response, db: DbSession
) -> schemas.ProfileCreated:
    """Mint an anonymous profile. No password, no email -- see deps.py."""
    profile = Profile(
        id=uuid.uuid4().hex,
        token=secrets.token_urlsafe(32),
        label=body.label,
        params=_default_params(),
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)

    response.set_cookie(
        COOKIE_NAME, profile.token,
        max_age=COOKIE_MAX_AGE, httponly=True, samesite="lax", path="/",
    )
    return schemas.ProfileCreated(
        id=profile.id,
        token=profile.token,
        label=profile.label,
        created_at=profile.created_at,
        params=_stored(profile),
    )


@router.get("/profiles/me", response_model=schemas.ProfileMe)
def me(profile: CurrentProfile, db: DbSession) -> schemas.ProfileMe:
    count = db.query(Trust).filter(Trust.profile_id == profile.id).count()
    return schemas.ProfileMe(
        id=profile.id,
        label=profile.label,
        params=_stored(profile),
        trust_count=count,
        warmed_at=profile.warmed_at,
    )


# ---------------------------------------------------------------------------
# Trust set
# ---------------------------------------------------------------------------


@router.get("/profiles/{profile_id}/trust", response_model=schemas.TrustListResponse)
def list_trust(profile: OwnedProfile, db: DbSession) -> schemas.TrustListResponse:
    return schemas.TrustListResponse(items=services.trust_entries(db, profile))


@router.post("/profiles/{profile_id}/trust", response_model=schemas.TrustMutateResponse)
def set_trust(
    body: schemas.TrustUpdate,
    profile: OwnedProfile,
    db: DbSession,
    background: BackgroundTasks,
) -> schemas.TrustMutateResponse:
    """`strength: 0` removes the entry; 1..5 upserts it.

    The engine edge is written by `ranking.ensure_seeded` before every read, so the
    only thing that must happen synchronously is the row. The walk warm is scheduled
    in the background because building walks for a fresh ego is not instant.
    """
    work = db.get(Work, body.work_id)
    if work is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown work id {body.work_id!r}. It is not in the corpus.",
        )

    existing = db.get(Trust, {"profile_id": profile.id, "work_id": body.work_id})
    if body.strength == 0:
        if existing is not None:
            # Drop the engine edge too, otherwise the removed seed keeps contributing
            # until the next graph rebuild.
            try:
                services.mr_of(db).delete_edge(
                    profile.node, f"U{body.work_id}", config.AGGREGATE
                )
            except Exception:  # noqa: BLE001 - the row is the source of truth
                pass
            db.delete(existing)
    elif existing is None:
        db.add(Trust(
            profile_id=profile.id, work_id=body.work_id,
            strength=body.strength, is_distrust=body.is_distrust,
        ))
    else:
        existing.strength = body.strength
        existing.is_distrust = body.is_distrust
    db.commit()

    background.add_task(services.warm_profile, profile.id)

    items = services.trust_entries(db, profile)
    return schemas.TrustMutateResponse(trust_count=len(items), items=items)


# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------

# Why each of these is refused rather than stored-and-ignored. The product rule is
# "we never expose a slider that does nothing" (config.py); the API rule is the same
# one, enforced.
_UNHONOURED = {
    "alpha": (
        "alpha is the MeritRank damping factor. It is read by the Rust service from "
        "the MERITRANK_ALPHA environment variable at process start and applies to the "
        "whole graph; there is no per-request or per-ego override in the mr_* surface. "
        "Set MERITRANK_ALPHA on the mr-service container and restart it."
    ),
    "num_walks": (
        "num_walks is the Monte-Carlo walk count, read by the Rust service from "
        "MERITRANK_NUM_WALKS at process start. mr_scores() takes no walk-count "
        "argument, so a per-profile value would be silently discarded."
    ),
    "epoch_half_life_years": (
        "epoch decay is applied by us at graph-construction time (scripts/build_graph.py "
        "bakes it into edge weights), not by the engine -- it exposes no epoch "
        "parameter. Changing it per profile would require rebuilding and re-loading "
        "the whole edge list, so it is a build-time flag, not a request parameter."
    ),
}


@router.post("/profiles/{profile_id}/params", response_model=schemas.ParamsResponse)
def set_params(
    body: schemas.ParamsUpdate, profile: OwnedProfile, db: DbSession
) -> schemas.ParamsResponse:
    """Store per-context weights. Anything the engine does not honour -> 422."""
    rejected: list[str] = []
    for name in _UNHONOURED:
        if getattr(body, name, None) is not None:
            rejected.append(name)
    if rejected:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=[
                {
                    "loc": ["body", name],
                    "msg": _UNHONOURED[name],
                    "type": "value_error.not_honoured_by_engine",
                }
                for name in rejected
            ],
        )

    extra = {k for k in (body.model_extra or {})}
    if extra:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=[
                {
                    "loc": ["body", k],
                    "msg": f"Unknown parameter {k!r}. The only tunable parameter is "
                           f"context_weights (keys: {', '.join(config.CONTEXTS)}).",
                    "type": "value_error.unknown_parameter",
                }
                for k in sorted(extra)
            ],
        )

    weights = services.stored_weights(profile)
    if body.context_weights is not None:
        bad = sorted(set(body.context_weights) - set(config.CONTEXTS))
        if bad:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=[
                    {
                        "loc": ["body", "context_weights", k],
                        "msg": f"Unknown context {k!r}. Valid contexts are "
                               f"{', '.join(config.CONTEXTS)}. 'coupling' and "
                               "'cocitation' are paper-to-paper (User->User) relations "
                               "which the engine replicates into every context, so "
                               "they are part of the 'citation' baseline and cannot be "
                               "weighted separately (DECISIONS.md D1.5).",
                        "type": "value_error.unknown_context",
                    }
                    for k in bad
                ],
            )
        for k, v in body.context_weights.items():
            fv = float(v)
            if not (0.0 <= fv <= 5.0):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=[{
                        "loc": ["body", "context_weights", k],
                        "msg": "Context weights must be between 0 and 5.",
                        "type": "value_error.out_of_range",
                    }],
                )
            weights[k] = fv

    profile.params = {**(profile.params or {}), "context_weights": weights}
    db.commit()

    # Re-rank now under the new weights via ranking.compose(): the response proves the
    # dial moved something rather than merely being stored.
    preview: list[schemas.ScoredPaper] = []
    if db.query(Trust).filter(Trust.profile_id == profile.id).count():
        pool = services.build_pool(db, profile, context="aggregate", exclude_trusted=True)
        preview = services.scored_page(db, pool.items[:5], pool, start_rank=1)

    return schemas.ParamsResponse(context_weights=weights, preview=preview)
