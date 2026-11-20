from typing import Any

import pytest

from events.api.schemas import UserSettings
from events.config import UserLimits
from events.models import SourceConfig
from events.persistence.users_repository import (
    SourceLimitReached,
    UsersRepository,
)


class InMemoryUsersCollection:
    def __init__(self) -> None:
        self.documents: dict[str, dict[str, Any]] = {}

    async def find_one(
        self,
        query: dict[str, Any],
        projection: dict[str, int],
    ) -> dict[str, Any] | None:
        return self.documents.get(query["_id"])

    async def update_one(
        self,
        query: dict[str, Any],
        update: dict[str, Any],
        *,
        upsert: bool,
    ) -> None:
        document = self.documents.setdefault(query["_id"], {})
        document.update(update.get("$setOnInsert", {}))
        document.update(update.get("$set", {}))


def _settings(source_count: int) -> UserSettings:
    return UserSettings(
        timezone="UTC",
        sources=[
            SourceConfig(
                name=f"Venue {index}",
                platform="instagram",
                url=f"https://www.instagram.com/venue-{index}/",
                lookback_days=7,
            )
            for index in range(source_count)
        ],
    )


@pytest.mark.asyncio
async def test_user_source_limit_allows_the_configured_maximum():
    repository = UsersRepository(
        InMemoryUsersCollection(),
        UserLimits(max_sources_per_user=10),
    )
    settings = _settings(10)

    saved = await repository.put_settings("alice", settings)

    assert saved == settings


@pytest.mark.asyncio
async def test_user_source_limit_rejects_excess_without_persisting():
    collection = InMemoryUsersCollection()
    repository = UsersRepository(
        collection,
        UserLimits(max_sources_per_user=10),
    )

    with pytest.raises(SourceLimitReached, match="at most 10"):
        await repository.put_settings("alice", _settings(11))

    assert collection.documents == {}
