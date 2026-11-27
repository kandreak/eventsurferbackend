from fastapi import FastAPI
from fastapi.testclient import TestClient

from events.auth import dependencies as auth_dependencies
from events.auth.dependencies import CurrentUser
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


def test_health_remains_public():
    with TestClient(api_app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_openapi_declares_http_bearer_security():
    scheme = auth_test_app.openapi()["components"]["securitySchemes"]

    assert scheme["HTTPBearer"]["scheme"] == "bearer"


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
