from datetime import UTC, datetime
from typing import Any

from pymongo.asynchronous.collection import AsyncCollection

from events.api.schemas import UserSettings
from events.config import UserLimits


class SourceLimitReached(Exception):
    pass


class UsersRepository:
    def __init__(
        self,
        collection: AsyncCollection[dict[str, Any]],
        limits: UserLimits,
    ) -> None:
        self._collection = collection
        self._limits = limits

    async def get_settings(self, user_id: str) -> UserSettings | None:
        document = await self._collection.find_one(
            {"_id": user_id},
            {"settings": 1},
        )
        if document is None:
            return None
        return UserSettings.model_validate(document["settings"])

    async def put_settings(
        self, user_id: str, settings: UserSettings
    ) -> UserSettings:
        if len(settings.sources) > self._limits.max_sources_per_user:
            raise SourceLimitReached(
                "A user can configure at most "
                f"{self._limits.max_sources_per_user} social sources."
            )
        now = datetime.now(UTC)
        await self._collection.update_one(
            {"_id": user_id},
            {
                "$set": {
                    "settings": settings.model_dump(mode="json"),
                    "updated_at": now,
                },
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )
        saved = await self.get_settings(user_id)
        if saved is None:
            raise RuntimeError("User settings were not persisted.")
        return saved
