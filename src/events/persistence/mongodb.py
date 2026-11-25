from pymongo import ASCENDING, DESCENDING, AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase


class MongoDatabase:
    def __init__(self, client: AsyncMongoClient, database_name: str) -> None:
        self.client = client
        self.database: AsyncDatabase = client[database_name]

    async def initialize(self) -> None:
        await self.client.admin.command("ping")
        await self.database["posts"].create_index(
            [
                ("user_id", ASCENDING),
                ("platform", ASCENDING),
                ("external_id", ASCENDING),
            ],
            unique=True,
            name="user_platform_external_id",
        )
        await self.database["events"].create_index(
            [("user_id", ASCENDING), ("date", ASCENDING)],
            name="user_event_date",
        )
        await self.database["events"].create_index(
            [("user_id", ASCENDING), ("event_id", ASCENDING)],
            unique=True,
            name="user_event_id",
        )
        await self.database["runs"].create_index(
            [("user_id", ASCENDING), ("started_at", ASCENDING)],
            name="user_run_history",
        )
        await self.database["runs"].create_index(
            [("user_id", ASCENDING), ("created_at", DESCENDING)],
            name="user_run_created",
        )
        await self.database["runs"].create_index(
            [("run_id", ASCENDING)],
            unique=True,
            name="run_id_unique",
        )
        await self.database["run_posts"].create_index(
            [
                ("run_id", ASCENDING),
                ("platform", ASCENDING),
                ("external_id", ASCENDING),
            ],
            unique=True,
            name="run_post_identity",
        )
        await self.database["run_posts"].create_index(
            [("run_id", ASCENDING), ("post_order", ASCENDING)],
            name="run_post_order",
        )
        legacy_cancelled_runs = await self.database["runs"].find(
            {"status": "cancelled"},
            {"run_id": 1, "user_id": 1},
        ).to_list(length=None)
        for run in legacy_cancelled_runs:
            await self.database["run_posts"].delete_many(
                {
                    "run_id": run["run_id"],
                    "user_id": run["user_id"],
                }
            )
        await self.database["runs"].update_many(
            {"status": "cancelled"},
            {"$set": {"status": "stopped"}},
        )

    async def close(self) -> None:
        await self.client.close()
