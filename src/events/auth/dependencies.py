"""Authenticated principal and FastAPI dependency for Firebase ID tokens."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from firebase_admin import auth
from firebase_admin.exceptions import FirebaseError

from events.auth.config import initialize_firebase_app

bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    """Principal derived from a verified Firebase ID token."""

    user_id: str
    email: str | None = None


async def get_current_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(bearer_scheme),
    ],
) -> AuthenticatedUser:
    """Verify the Firebase ID token from ``Authorization: Bearer <token>``.

    Returns the authenticated principal. Missing, malformed, invalid, expired,
    or otherwise unverifiable tokens result in HTTP 401.
    """
    if credentials is None or not credentials.credentials.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header.",
            headers={"WWW-Authenticate": 'Bearer realm="api"'},
        )

    try:
        initialize_firebase_app()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Firebase authentication is not configured.",
        ) from exc

    try:
        decoded = auth.verify_id_token(credentials.credentials)
    except (FirebaseError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired Firebase ID token.",
            headers={"WWW-Authenticate": 'Bearer realm="api"'},
        ) from None

    user_id = decoded.get("uid")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Firebase ID token does not contain a user ID.",
            headers={"WWW-Authenticate": 'Bearer realm="api"'},
        )

    return AuthenticatedUser(
        user_id=str(user_id),
        email=decoded.get("email"),
    )


CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]
