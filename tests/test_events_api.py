from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient

from events.api import app as app_module
from events.api.app import app
from events.api.dependencies import (
    get_events_repository,
    get_users_repository,
)
from events.api.schemas import UserSettings
from events.core.settings import BackendSettings
from events.models import Event
from events.persistence.events_repository import EventsRepository


class InMemoryCursor:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self._documents = documents

    def sort(self, fields: list[tuple[str, int]]) -> InMemoryCursor:
        for field, direction in reversed(fields):
            self._documents.sort(
                key=lambda document: document.get(field) or "",
                reverse=direction < 0,
            )
        return self

    async def to_list(self, length: int | None = None) -> list[dict[str, Any]]:
        return self._documents if length is None else self._documents[:length]


class InMemoryEventCollection:
    def __init__(self) -> None:
        self.documents: list[dict[str, Any]] = []

    async def update_one(
        self,
        query: dict[str, Any],
        update: dict[str, Any],
        *,
        upsert: bool = False,
    ) -> None:
        document = next(
            (
                item
                for item in self.documents
                if all(item.get(key) == value for key, value in query.items())
            ),
            None,
        )
        if document is None:
            if not upsert:
                return
            document = query.copy()
            self.documents.append(document)
            document.update(update.get("$setOnInsert", {}))
        document.update(update.get("$set", {}))

    def find(
        self,
        query: dict[str, Any],
        projection: dict[str, int],
    ) -> InMemoryCursor:
        matching = [
            document.copy()
            for document in self.documents
            if self._matches(document, query)
        ]
        for document in matching:
            for key, enabled in projection.items():
                if enabled == 0:
                    document.pop(key, None)
        return InMemoryCursor(matching)

    @staticmethod
    def _matches(document: dict[str, Any], query: dict[str, Any]) -> bool:
        for field, value in query.items():
            saved = document.get(field)
            if isinstance(value, dict):
                if "$gte" in value and (
                    saved is None or saved < value["$gte"]
                ):
                    return False
                if "$lte" in value and (
                    saved is None or saved > value["$lte"]
                ):
                    return False
            elif saved != value:
                return False
        return True


class InMemoryUsersRepository:
    async def get_settings(self, user_id: str) -> UserSettings:
        return UserSettings(timezone="Europe/Rome")


@pytest.fixture(autouse=True)
def disable_database_connection(monkeypatch, use_test_auth_override):
    monkeypatch.setattr(
        app_module,
        "BackendSettings",
        lambda: BackendSettings(_env_file=None),
    )


def _event(
    title: str,
    *,
    event_date: date | None,
    source_name: str = "Example Venue",
    post_id: str | None = None,
) -> Event:
    post_id = post_id or title.lower().replace(" ", "-")
    return Event(
        title=title,
        date=event_date,
        venue="Town Hall",
        confidence=0.9,
        needs_review=event_date is None,
        date_is_explicit=event_date is not None,
        time_is_explicit=False,
        source_post_url=f"https://www.instagram.com/p/{post_id}/",
        source_name=source_name,
    )


@pytest.mark.asyncio
async def test_events_api_filters_by_user_date_source_and_status_and_exports():
    collection = InMemoryEventCollection()
    repository = EventsRepository(collection)
    await repository.save_events(
        "alice",
        [
            _event("Summer concert", event_date=date(2026, 8, 10)),
            _event("Winter concert", event_date=date(2026, 12, 10)),
            _event("Undated meetup", event_date=None),
            _event(
                "Other source event",
                event_date=date(2026, 8, 10),
                source_name="Other Venue",
            ),
        ],
    )
    next(
        item
        for item in collection.documents
        if item["title"] == "Winter concert"
    )["status"] = "archived"

    app.dependency_overrides[get_events_repository] = lambda: repository
    app.dependency_overrides[get_users_repository] = (
        lambda: InMemoryUsersRepository()
    )
    try:
        with TestClient(app) as client:
            all_events = client.get("/api/users/me/events.json")
            filtered = client.get(
                "/api/users/me/events",
                params={
                    "date_from": "2026-08-01",
                    "date_to": "2026-08-31",
                    "source": "Example Venue",
                    "status": "active",
                },
            )
            archived = client.get(
                "/api/users/me/events.json",
                params={"status": "archived"},
            )
            other_user = client.get(
                "/api/users/me/events",
                headers={"X-Test-User": "bob"},
            )
            invalid_range = client.get(
                "/api/users/me/events",
                params={
                    "date_from": "2026-12-01",
                    "date_to": "2026-08-01",
                },
            )
            calendar = client.get("/api/users/me/events.ics")
    finally:
        app.dependency_overrides.clear()

    assert all_events.status_code == 200
    assert {event["title"] for event in all_events.json()} == {
        "Summer concert",
        "Winter concert",
        "Undated meetup",
        "Other source event",
    }
    undated = next(
        event
        for event in all_events.json()
        if event["title"] == "Undated meetup"
    )
    assert undated["date"] is None
    assert undated["needs_review"] is True
    assert undated["confidence"] == 0.9
    assert undated["status"] == "active"
    assert undated["created_at"]
    assert undated["updated_at"]
    assert [event["title"] for event in filtered.json()] == ["Summer concert"]
    assert [event["title"] for event in archived.json()] == ["Winter concert"]
    assert other_user.json() == []
    assert invalid_range.status_code == 422
    assert calendar.headers["content-type"].startswith("text/calendar")
    assert "BEGIN:VEVENT" in calendar.text
    assert "BEGIN:VTODO" in calendar.text


@pytest.mark.asyncio
async def test_event_saving_deduplicates_and_is_idempotent():
    collection = InMemoryEventCollection()
    repository = EventsRepository(collection)
    event = _event("Community workshop", event_date=date(2026, 8, 10))
    duplicate = event.model_copy(
        update={
            "title": "Community workshops",
            "source_post_url": "https://www.instagram.com/p/duplicate/",
        }
    )

    assert await repository.save_events("alice", [event, duplicate]) == 1
    assert await repository.save_events("alice", [event]) == 1
    assert len(collection.documents) == 1
    assert collection.documents[0]["status"] == "active"
