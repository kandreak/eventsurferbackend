import hashlib
import json
from datetime import UTC, date, datetime
from typing import Any

from pymongo import ASCENDING
from pymongo.asynchronous.collection import AsyncCollection

from events.api.schemas import StoredEvent
from events.models import Event, EventStatus
from events.processing import deduplicate_events


class EventsRepository:
    def __init__(self, collection: AsyncCollection[dict[str, Any]]) -> None:
        self._collection = collection

    async def save_events(self, user_id: str, events: list[Event]) -> int:
        unique_events, _ = deduplicate_events(events)
        now = datetime.now(UTC)
        for event in unique_events:
            event_data = event.model_dump(mode="json")
            event_id = hashlib.sha256(
                json.dumps(
                    event_data,
                    sort_keys=True,
                    ensure_ascii=False,
                ).encode("utf-8")
            ).hexdigest()
            await self._collection.update_one(
                {"user_id": user_id, "event_id": event_id},
                {
                    "$set": {
                        **event_data,
                        "event_id": event_id,
                        "updated_at": now,
                    },
                    "$setOnInsert": {
                        "user_id": user_id,
                        "status": "active",
                        "created_at": now,
                    },
                },
                upsert=True,
            )
        return len(unique_events)

    async def list_events(
        self,
        user_id: str,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
        source: str | None = None,
        status: EventStatus | None = None,
    ) -> list[StoredEvent]:
        query: dict[str, Any] = {"user_id": user_id}
        date_filter: dict[str, str] = {}
        if date_from is not None:
            date_filter["$gte"] = date_from.isoformat()
        if date_to is not None:
            date_filter["$lte"] = date_to.isoformat()
        if date_filter:
            query["date"] = date_filter
        if source is not None:
            query["source_name"] = source
        if status is not None:
            query["status"] = status

        cursor = self._collection.find(
            query,
            {"_id": 0, "user_id": 0},
        ).sort([("date", ASCENDING), ("event_id", ASCENDING)])
        documents = await cursor.to_list(length=None)
        return [StoredEvent.model_validate(document) for document in documents]
