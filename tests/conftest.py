from collections.abc import Iterator

import pytest
from fastapi import Request

from events.api.app import app
from events.auth.dependencies import AuthenticatedUser, get_current_user


@pytest.fixture
def use_test_auth_override() -> Iterator[None]:
    async def resolve_test_user(request: Request) -> AuthenticatedUser:
        return AuthenticatedUser(
            user_id=request.headers.get("X-Test-User", "alice")
        )

    app.dependency_overrides[get_current_user] = resolve_test_user
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_current_user, None)
