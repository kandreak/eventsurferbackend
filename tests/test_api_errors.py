import pytest
from fastapi.testclient import TestClient
from pymongo.errors import PyMongoError

from events.api import app as app_module
from events.api.app import app
from events.api.dependencies import (
    get_events_repository,
    get_users_repository,
)
from events.api.schemas import UserSettings
from events.core.settings import BackendSettings


@pytest.fixture(autouse=True)
def disable_database_connection(monkeypatch):
    monkeypatch.setattr(
        app_module,
        "BackendSettings",
        lambda: BackendSettings(_env_file=None),
    )


def test_request_validation_uses_a_stable_error_shape():
    app.dependency_overrides[get_users_repository] = (
        lambda: _MissingSettingsRepository()
    )
    try:
        with TestClient(app) as client:
            response = client.put(
                "/api/users/alice/settings",
                json={"timezone": "Not/A_Timezone", "sources": []},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert body["error"]["message"] == "Request validation failed."
    assert body["error"]["details"]
    assert set(body["error"]["details"][0]) == {
        "location",
        "message",
        "type",
    }


def test_missing_resource_uses_the_same_error_envelope():
    app.dependency_overrides[get_users_repository] = (
        lambda: _MissingSettingsRepository()
    )
    try:
        with TestClient(app) as client:
            response = client.get("/api/users/alice/settings")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "not_found",
            "message": "User settings were not found.",
        }
    }


def test_malformed_persisted_data_returns_a_sanitized_server_error():
    app.dependency_overrides[get_users_repository] = (
        lambda: _MalformedSettingsRepository()
    )
    try:
        with TestClient(app) as client:
            response = client.get("/api/users/alice/settings")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "invalid_persisted_data",
            "message": "Stored data is invalid.",
        }
    }


def test_unexpected_repository_errors_are_logged_and_not_success_shaped(
    caplog,
):
    app.dependency_overrides[get_users_repository] = (
        lambda: _UnexpectedUsersRepository()
    )
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/api/users/alice/settings")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "internal_error",
            "message": "An unexpected error occurred.",
        }
    }
    assert "Unhandled API error." in caplog.text


def test_database_outage_is_reported_as_service_unavailable():
    app.dependency_overrides[get_users_repository] = (
        lambda: _DatabaseErrorUsersRepository()
    )
    try:
        with TestClient(app) as client:
            response = client.get("/api/users/alice/settings")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "service_unavailable",
            "message": "Database service is unavailable.",
        }
    }


def test_calendar_export_requires_user_settings():
    app.dependency_overrides[get_events_repository] = (
        lambda: _EmptyEventsRepository()
    )
    app.dependency_overrides[get_users_repository] = (
        lambda: _MissingSettingsRepository()
    )
    try:
        with TestClient(app) as client:
            response = client.get("/api/users/alice/events.ics")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


class _MissingSettingsRepository:
    async def get_settings(self, user_id):
        return None


class _EmptyEventsRepository:
    async def list_events(self, user_id, **kwargs):
        return []


class _MalformedSettingsRepository:
    async def get_settings(self, user_id):
        return UserSettings.model_validate(
            {"timezone": "Invalid/Zone", "sources": []}
        )


class _UnexpectedUsersRepository:
    async def get_settings(self, user_id):
        raise RuntimeError("internal implementation detail")


class _DatabaseErrorUsersRepository:
    async def get_settings(self, user_id):
        raise PyMongoError("private connection detail")
