import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from firebase_admin.auth import ExpiredIdTokenError

from events.auth import dependencies as auth_dependencies
from events.auth.dependencies import (
    AuthenticatedUser,
    CurrentUser,
    get_current_user,
)
from events.api.app import app as api_app


auth_test_app = FastAPI()


@auth_test_app.get("/protected")
async def protected(current_user: CurrentUser) -> dict[str, str | None]:
    return {
        "user_id": current_user.user_id,
        "email": current_user.email,
    }


def test_missing_bearer_token_is_rejected():
    with TestClient(auth_test_app) as client:
        response = client.get("/protected")

    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Bearer")


@pytest.mark.parametrize(
    "authorization",
    ["Basic token", "Bearer "],
)
def test_malformed_authorization_header_is_rejected(authorization):
    with TestClient(auth_test_app) as client:
        response = client.get(
            "/protected",
            headers={"Authorization": authorization},
        )

    assert response.status_code == 401


def test_invalid_bearer_token_is_rejected(monkeypatch):
    monkeypatch.setattr(
        auth_dependencies,
        "initialize_firebase_app",
        lambda: None,
    )

    def reject_token(token: str) -> dict[str, str]:
        raise ValueError("invalid token")

    monkeypatch.setattr(
        auth_dependencies.auth,
        "verify_id_token",
        reject_token,
    )

    with TestClient(auth_test_app) as client:
        response = client.get(
            "/protected",
            headers={"Authorization": "Bearer invalid"},
        )

    assert response.status_code == 401


def test_expired_or_unverifiable_token_is_rejected(monkeypatch):
    monkeypatch.setattr(
        auth_dependencies,
        "initialize_firebase_app",
        lambda: None,
    )

    def reject_token(token: str) -> dict[str, str]:
        raise ExpiredIdTokenError("token has expired", None)

    monkeypatch.setattr(
        auth_dependencies.auth,
        "verify_id_token",
        reject_token,
    )

    with TestClient(auth_test_app) as client:
        response = client.get(
            "/protected",
            headers={"Authorization": "Bearer unverifiable"},
        )

    assert response.status_code == 401


def test_valid_token_uses_verified_uid_and_email(monkeypatch):
    monkeypatch.setattr(
        auth_dependencies,
        "initialize_firebase_app",
        lambda: None,
    )
    monkeypatch.setattr(
        auth_dependencies.auth,
        "verify_id_token",
        lambda token: {"uid": "firebase-user-123", "email": "user@example.com"},
    )

    with TestClient(auth_test_app) as client:
        response = client.get(
            "/protected",
            headers={"Authorization": "Bearer valid"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "user_id": "firebase-user-123",
        "email": "user@example.com",
    }


def test_token_without_uid_is_rejected(monkeypatch):
    monkeypatch.setattr(
        auth_dependencies,
        "initialize_firebase_app",
        lambda: None,
    )
    monkeypatch.setattr(
        auth_dependencies.auth,
        "verify_id_token",
        lambda token: {"email": "user@example.com"},
    )

    with TestClient(auth_test_app) as client:
        response = client.get(
            "/protected",
            headers={"Authorization": "Bearer missing-uid"},
        )

    assert response.status_code == 401


def test_health_remains_public():
    with TestClient(api_app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize(
    ("method", "path", "json_body"),
    [
        ("GET", "/api/users/me/settings", None),
        (
            "PUT",
            "/api/users/me/settings",
            {"timezone": "UTC", "sources": []},
        ),
        ("GET", "/api/users/me/events", None),
        ("GET", "/api/users/me/events.json", None),
        ("GET", "/api/users/me/events.ics", None),
        ("POST", "/api/users/me/runs", None),
        ("POST", "/api/users/me/runs/stop", None),
        ("POST", "/api/users/me/runs/run-1/stop", None),
        ("GET", "/api/users/me/runs", None),
        ("GET", "/api/users/me/runs/run-1", None),
        ("POST", "/api/users/me/runs/run-1/resume", None),
        ("GET", "/api/users/me/runs/run-1/report", None),
        ("GET", "/api/users/me/runs/run-1/events.json", None),
        ("GET", "/api/users/me/runs/run-1/events.ics", None),
    ],
)
def test_every_user_specific_route_requires_authentication(
    method,
    path,
    json_body,
):
    with TestClient(api_app) as client:
        response = client.request(method, path, json=json_body)

    assert response.status_code == 401


def test_openapi_declares_http_bearer_security():
    openapi = auth_test_app.openapi()
    scheme = openapi["components"]["securitySchemes"]

    assert scheme["HTTPBearer"]["scheme"] == "bearer"
    assert openapi["paths"]["/protected"]["get"]["security"]


def test_current_user_dependency_can_be_overridden():
    auth_test_app.dependency_overrides[get_current_user] = lambda: (
        AuthenticatedUser(user_id="test-user")
    )
    try:
        with TestClient(auth_test_app) as client:
            response = client.get("/protected")
    finally:
        auth_test_app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 200
    assert response.json()["user_id"] == "test-user"


def test_missing_firebase_configuration_is_reported_as_service_unavailable(
    monkeypatch,
):
    def fail_initialization() -> None:
        raise ValueError("missing Firebase configuration")

    monkeypatch.setattr(
        auth_dependencies,
        "initialize_firebase_app",
        fail_initialization,
    )

    with TestClient(auth_test_app) as client:
        response = client.get(
            "/protected",
            headers={"Authorization": "Bearer valid"},
        )

    assert response.status_code == 503
    assert response.json()["detail"] == (
        "Firebase authentication is not configured."
    )
