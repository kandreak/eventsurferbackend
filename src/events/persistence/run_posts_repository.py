from typing import Any

from pymongo.asynchronous.collection import AsyncCollection

from events.models import Event, SocialPost


class RunPostsRepository:
    def __init__(self, collection: AsyncCollection[dict[str, Any]]) -> None:
        self._collection = collection

    async def replace_posts(
        self,
        run_id: str,
        user_id: str,
        posts: list[SocialPost],
    ) -> None:
        await self._collection.delete_many({"run_id": run_id})
        for order, post in enumerate(posts):
            await self._collection.update_one(
                {
                    "run_id": run_id,
                    "platform": post.platform,
                    "external_id": post.external_id,
                },
                {
                    "$set": {
                        "user_id": user_id,
                        "post_order": order,
                        "post": post.model_dump(
                            mode="json",
                            exclude={"raw_provider_data"},
                        ),
                        "events": [],
                    }
                },
                upsert=True,
            )

    async def list_posts(self, run_id: str) -> list[SocialPost]:
        cursor = (
            self._collection.find({"run_id": run_id}, {"_id": 0, "post": 1})
            .sort("post_order", 1)
        )
        documents = await cursor.to_list(length=None)
        return [
            SocialPost.model_validate(document["post"])
            for document in documents
        ]

    async def replace_events(
        self,
        run_id: str,
        events: list[Event],
    ) -> None:
        await self._collection.update_many(
            {"run_id": run_id},
            {"$set": {"events": []}},
        )
        events_by_post: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for event in events:
            key = (str(event.source_post_url), event.source_name)
            events_by_post.setdefault(key, []).append(
                event.model_dump(mode="json")
            )

        cursor = self._collection.find(
            {"run_id": run_id},
            {"_id": 0, "platform": 1, "external_id": 1, "post": 1},
        )
        async for document in cursor:
            post = document["post"]
            post_key = (post["post_url"], post["source_name"])
            await self._collection.update_one(
                {
                    "run_id": run_id,
                    "platform": document["platform"],
                    "external_id": document["external_id"],
                },
                {"$set": {"events": events_by_post.get(post_key, [])}},
            )

    async def list_events(self, run_id: str) -> list[Event]:
        cursor = self._collection.find(
            {"run_id": run_id},
            {"_id": 0, "events": 1},
        ).sort("post_order", 1)
        documents = await cursor.to_list(length=None)
        return [
            Event.model_validate(event)
            for document in documents
            for event in document.get("events", [])
        ]
