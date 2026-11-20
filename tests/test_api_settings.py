import pytest
from fastapi.testclient import TestClient

from events.api import app as app_module
from events.api.app import app
from events.api.dependencies import get_users_repository
from events.api.schemas import UserSettings
from events.core.settings import BackendSettings


@pytest.fixture(autouse=True)
def disable_database_connection(monkeypatch):
    monkeypatch.setattr(
        app_module,
        "BackendSettings",
        lambda: BackendSettings(_env_file=None),
    )


class InMemoryUsersRepository:
    def __init__(self) -> None:
        self.settings: dict[str, UserSettings] = {}

    async def get_settings(self, user_id: str) -> UserSettings | None:
        return self.settings.get(user_id)

    async def put_settings(
        self, user_id: str, settings: UserSettings
    ) -> UserSettings:
        self.settings[user_id] = settings
        return settings


def test_settings_api_creates_updates_and_scopes_settings_by_user():
    repository = InMemoryUsersRepository()
    app.dependency_overrides[get_users_repository] = lambda: repository
    try:
        with TestClient(app) as client:
            first = client.put(
                "/api/users/alice/settings",
                json={
                    "timezone": "Europe/Rome",
                    "sources": [
                        {
                            "name": "Venue",
                            "platform": "instagram",
                            "url": "https://www.instagram.com/venue/",
                            "lookback_days": 14,
                        }
                    ],
                },
            )
            second = client.put(
                "/api/users/bob/settings",
                json={"timezone": "UTC", "sources": []},
            )
            fetched = client.get("/api/users/alice/settings")
            fetched_bob = client.get("/api/users/bob/settings")
            missing = client.get("/api/users/unknown/settings")
    finally:
        app.dependency_overrides.clear()

    assert first.status_code == 200
    assert second.status_code == 200
    assert fetched.json()["sources"][0]["lookback_days"] == 14
    assert fetched.json()["timezone"] == "Europe/Rome"
    assert fetched_bob.json() == {"timezone": "UTC", "sources": []}
    assert missing.status_code == 404


def test_settings_api_rejects_invalid_settings():
    repository = InMemoryUsersRepository()
    app.dependency_overrides[get_users_repository] = lambda: repository
    try:
        with TestClient(app) as client:
            response = client.put(
                "/api/users/alice/settings",
                json={"timezone": "Not/A_Timezone", "sources": []},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422


def test_database_routes_return_503_when_mongodb_is_not_configured(monkeypatch):
    monkeypatch.setattr(
        app_module,
        "BackendSettings",
        lambda: BackendSettings(mongodb_uri=None, _env_file=None),
    )
    with TestClient(app) as client:
        health = client.get("/health")
        settings = client.get("/api/users/alice/settings")

    assert health.status_code == 200
    assert settings.status_code == 503
    assert settings.json() == {
        "error": {
            "code": "service_unavailable",
            "message": "Database is not configured.",
        }
    }


def test_blank_mongodb_uri_is_treated_as_unconfigured():
    settings = BackendSettings(mongodb_uri="   ", _env_file=None)

    assert settings.mongodb_uri is None


def test_default_database_name_matches_the_application():
    settings = BackendSettings(_env_file=None)

    assert settings.mongodb_database == "eventsurfer"
