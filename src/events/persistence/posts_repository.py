import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from pymongo.asynchronous.collection import AsyncCollection

from events.models import Event, SocialPost


def analysis_content_hash(
    post: SocialPost,
    *,
    model: str,
    timezone: str,
    max_images: int,
) -> str:
    value = {
        "post": post.model_dump(mode="json"),
        "model": model,
        "timezone": timezone,
        "max_images": max_images,
    }
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()


class PostsRepository:
    def __init__(self, collection: AsyncCollection[dict[str, Any]]) -> None:
        self._collection = collection

    @staticmethod
    def _identity(user_id: str, post: SocialPost) -> dict[str, str]:
        return {
            "user_id": user_id,
            "platform": post.platform,
            "external_id": post.external_id,
        }

    async def upsert_post(
        self,
        user_id: str,
        post: SocialPost,
        *,
        content_hash: str,
    ) -> list[Event] | None:
        identity = self._identity(user_id, post)
        now = datetime.now(UTC)
        existing = await self._collection.find_one(
            identity,
            {"content_hash": 1, "analysis_status": 1, "extracted_events": 1},
        )
        normalized_post = post.model_dump(
            mode="json",
            exclude={"raw_provider_data"},
        )
        if existing is not None and existing.get("content_hash") == content_hash:
            updates = {
                "post": normalized_post,
                "updated_at": now,
            }
            if post.raw_provider_data:
                updates["raw_provider_data"] = post.raw_provider_data
            await self._collection.update_one(
                identity,
                {"$set": updates},
            )
            if existing.get("analysis_status") == "analyzed":
                return [
                    Event.model_validate(event)
                    for event in existing.get("extracted_events", [])
                ]
            return None

        await self._collection.update_one(
            identity,
            {
                "$set": {
                    "post": normalized_post,
                    "raw_provider_data": post.raw_provider_data,
                    "content_hash": content_hash,
                    "analysis_status": "pending",
                    "analysis_error": None,
                    "extracted_events": [],
                    "analyzed_at": None,
                    "updated_at": now,
                },
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )
        return None

    async def mark_analyzed(
        self,
        user_id: str,
        post: SocialPost,
        *,
        content_hash: str,
        events: list[Event],
    ) -> None:
        result = await self._collection.update_one(
            {
                **self._identity(user_id, post),
                "content_hash": content_hash,
            },
            {
                "$set": {
                    "analysis_status": "analyzed",
                    "analysis_error": None,
                    "extracted_events": [
                        event.model_dump(mode="json") for event in events
                    ],
                    "analyzed_at": datetime.now(UTC),
                    "updated_at": datetime.now(UTC),
                }
            },
        )
        if result.matched_count != 1:
            raise RuntimeError(
                f"Post {post.external_id} changed while analysis was running."
            )

    async def mark_analysis_failed(
        self,
        user_id: str,
        post: SocialPost,
        *,
        content_hash: str,
        error: str,
    ) -> None:
        result = await self._collection.update_one(
            {
                **self._identity(user_id, post),
                "content_hash": content_hash,
            },
            {
                "$set": {
                    "analysis_status": "failed",
                    "analysis_error": error,
                    "updated_at": datetime.now(UTC),
                }
            },
        )
        if result.matched_count != 1:
            raise RuntimeError(
                f"Post {post.external_id} changed while analysis was running."
            )

    async def mark_skipped(
        self,
        user_id: str,
        post: SocialPost,
        *,
        content_hash: str,
        reason: str,
    ) -> None:
        result = await self._collection.update_one(
            {
                **self._identity(user_id, post),
                "content_hash": content_hash,
            },
            {
                "$set": {
                    "analysis_status": "skipped",
                    "analysis_error": reason,
                    "updated_at": datetime.now(UTC),
                }
            },
        )
        if result.matched_count != 1:
            raise RuntimeError(
                f"Post {post.external_id} changed while analysis was running."
            )
