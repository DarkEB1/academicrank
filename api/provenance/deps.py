"""Request-scoped dependencies: session + anonymous-token auth.

Auth is deliberately minimal -- no password, no email, no recovery. A profile is a
bearer token and nothing else, which is the weakest thing that still lets a trust set
belong to somebody. The token is accepted from `Authorization: Bearer` or the
`pv_token` cookie, in that order.
"""
from __future__ import annotations

from typing import Annotated, Optional

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from .db import get_session
from .models import Profile

COOKIE_NAME = "pv_token"

DbSession = Annotated[Session, Depends(get_session)]


def bearer_token(request: Request) -> Optional[str]:
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        tok = auth[7:].strip()
        if tok:
            return tok
    cookie = request.cookies.get(COOKIE_NAME)
    return cookie.strip() if cookie else None


def current_profile(request: Request, db: DbSession) -> Profile:
    token = bearer_token(request)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing profile token. Send Authorization: Bearer <token> or the "
                   "pv_token cookie. POST /api/profiles mints one.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    profile = db.query(Profile).filter(Profile.token == token).one_or_none()
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unknown profile token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return profile


CurrentProfile = Annotated[Profile, Depends(current_profile)]


def owned_profile(profile_id: str, profile: CurrentProfile) -> Profile:
    """The `{id}` in the path must be the authenticated profile. `me` is accepted as an
    alias so the client does not have to interpolate its own id everywhere."""
    if profile_id not in (profile.id, "me"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This token does not own that profile.",
        )
    return profile


OwnedProfile = Annotated[Profile, Depends(owned_profile)]
