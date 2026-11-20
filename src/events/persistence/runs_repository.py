from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError
from pymongo.asynchronous.collection import AsyncCollection

from events.api.schemas import (
    RunProgress,
    RunStage,
    StoredRun,
    UserSettings,
)
from events.config import RunExecutionSettings

_STAGES: tuple[RunStage, ...] = ("collect", "analyze", "publish")
_PRIVATE_FIELDS = {
    "_id",
    "lease_token",
    "lease_expires_at",
    "worker_id",
    "global_slot_id",
    "user_slot_id",
    "last_lease_token",
}


class RunLimitReached(Exception):
    pass


class RunStateConflict(Exception):
    pass


class RunLeaseLost(Exception):
    pass


@dataclass(frozen=True)
class RunLease:
    run: StoredRun
    token: str


class RunsRepository:
    def __init__(
        self,
        collection: AsyncCollection[dict[str, Any]],
        settings: RunExecutionSettings,
    ) -> None:
        self._collection = collection
        self._database = collection.database
        self._client = self._database.client
        self._counters: AsyncCollection[dict[str, Any]] = self._database[
            "run_user_counters"
        ]
        self._slots: AsyncCollection[dict[str, Any]] = self._database[
            "run_execution_slots"
        ]
        self._settings = settings

    @staticmethod
    def _to_model(document: dict[str, Any]) -> StoredRun:
        public_document = {
            key: value
            for key, value in document.items()
            if key not in _PRIVATE_FIELDS
        }
        return StoredRun.model_validate(public_document)

    async def _increment_outstanding(
        self,
        user_id: str,
        *,
        session: Any,
    ) -> None:
        counter = await self._counters.find_one_and_update(
            {"_id": user_id},
            [
                {
                    "$set": {
                        "outstanding_count": {
                            "$add": [
                                {"$ifNull": ["$outstanding_count", 0]},
                                1,
                            ]
                        }
                    }
                }
            ],
            upsert=True,
            return_document=ReturnDocument.AFTER,
            session=session,
        )
        if counter["outstanding_count"] > (
            self._settings.max_outstanding_runs_per_user
        ):
            raise RunLimitReached

    async def _decrement_outstanding(
        self,
        user_id: str,
        *,
        session: Any,
    ) -> None:
        result = await self._counters.update_one(
            {"_id": user_id, "outstanding_count": {"$gt": 0}},
            {"$inc": {"outstanding_count": -1}},
            session=session,
        )
        if result.matched_count != 1:
            raise RuntimeError(
                f"Outstanding run counter is missing for user {user_id}."
            )

    async def create_run(
        self,
        user_id: str,
        settings_snapshot: UserSettings,
    ) -> StoredRun:
        now = datetime.now(UTC)
        document = {
            "run_id": uuid4().hex,
            "user_id": user_id,
            "status": "queued",
            "current_stage": "collect",
            "progress": RunProgress().model_dump(mode="json"),
            "checkpoints": {},
            "settings_snapshot": settings_snapshot.model_dump(mode="json"),
            "attempt": 0,
            "error": None,
            "errors": [],
            "created_at": now,
            "updated_at": now,
            "started_at": None,
            "finished_at": None,
        }

        async def transaction(session: Any) -> StoredRun:
            await self._increment_outstanding(user_id, session=session)
            await self._collection.insert_one(document, session=session)
            return self._to_model(document)

        async with self._client.start_session() as session:
            return await session.with_transaction(transaction)

    async def get_run(self, user_id: str, run_id: str) -> StoredRun | None:
        document = await self._collection.find_one(
            {"user_id": user_id, "run_id": run_id},
            {"_id": 0},
        )
        return self._to_model(document) if document is not None else None

    async def list_runs(
        self,
        user_id: str,
        *,
        limit: int,
        offset: int = 0,
    ) -> list[StoredRun]:
        cursor = (
            self._collection.find({"user_id": user_id}, {"_id": 0})
            .sort("created_at", DESCENDING)
            .skip(offset)
            .limit(limit)
        )
        return [
            self._to_model(document)
            for document in await cursor.to_list(length=limit)
        ]

    async def resume_run(self, user_id: str, run_id: str) -> StoredRun | None:
        async def transaction(session: Any) -> StoredRun | None:
            current = await self._collection.find_one(
                {"user_id": user_id, "run_id": run_id},
                session=session,
            )
            if current is None:
                return None
            if current["status"] == "queued":
                return self._to_model(current)
            if current["status"] != "failed":
                raise RunStateConflict(
                    f"Run {run_id} can only be resumed after failure."
                )

            await self._increment_outstanding(user_id, session=session)
            now = datetime.now(UTC)
            resumed = await self._collection.find_one_and_update(
                {
                    "user_id": user_id,
                    "run_id": run_id,
                    "status": "failed",
                },
                {
                    "$set": {
                        "status": "queued",
                        "error": None,
                        "finished_at": None,
                        "updated_at": now,
                    },
                    "$unset": {
                        "lease_token": "",
                        "lease_expires_at": "",
                        "worker_id": "",
                        "global_slot_id": "",
                        "user_slot_id": "",
                        "last_lease_token": "",
                    },
                },
                return_document=ReturnDocument.AFTER,
                session=session,
            )
            if resumed is None:
                raise RunStateConflict(
                    f"Run {run_id} changed while it was being resumed."
                )
            return self._to_model(resumed)

        async with self._client.start_session() as session:
            return await session.with_transaction(transaction)

    async def _claim_slot(
        self,
        slot_id: str,
        *,
        run_id: str,
        worker_id: str,
        lease_token: str,
        now: datetime,
        expires_at: datetime,
    ) -> bool:
        try:
            slot = await self._slots.find_one_and_update(
                {
                    "_id": slot_id,
                    "$or": [
                        {"lease_expires_at": {"$lte": now}},
                        {"lease_token": {"$exists": False}},
                    ],
                },
                {
                    "$set": {
                        "run_id": run_id,
                        "worker_id": worker_id,
                        "lease_token": lease_token,
                        "lease_expires_at": expires_at,
                    }
                },
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError:
            return False
        return slot is not None

    async def _release_slot(
        self,
        slot_id: str | None,
        lease_token: str,
    ) -> None:
        if slot_id is None:
            return
        await self._slots.update_one(
            {"_id": slot_id, "lease_token": lease_token},
            {
                "$unset": {
                    "run_id": "",
                    "worker_id": "",
                    "lease_token": "",
                    "lease_expires_at": "",
                }
            },
        )

    async def claim_next_run(self, worker_id: str) -> RunLease | None:
        now = datetime.now(UTC)
        candidate_query = {
            "$or": [
                {"status": "queued"},
                {
                    "status": "running",
                    "lease_expires_at": {"$lte": now},
                },
            ]
        }
        candidates = await self._collection.find(
            candidate_query,
            {"_id": 0},
        ).sort("created_at", ASCENDING).to_list(length=None)

        for candidate in candidates:
            run_id = candidate["run_id"]
            user_id = candidate["user_id"]
            lease_token = uuid4().hex
            expires_at = now + timedelta(
                seconds=self._settings.lease_duration_seconds
            )
            global_slot_id = None
            for slot in range(self._settings.max_concurrent_runs):
                slot_id = f"global:{slot}"
                if await self._claim_slot(
                    slot_id,
                    run_id=run_id,
                    worker_id=worker_id,
                    lease_token=lease_token,
                    now=now,
                    expires_at=expires_at,
                ):
                    global_slot_id = slot_id
                    break
            if global_slot_id is None:
                return None
            user_slot_id = None
            for slot in range(self._settings.max_concurrent_runs_per_user):
                slot_id = f"user:{user_id}:{slot}"
                if await self._claim_slot(
                    slot_id,
                    run_id=run_id,
                    worker_id=worker_id,
                    lease_token=lease_token,
                    now=now,
                    expires_at=expires_at,
                ):
                    user_slot_id = slot_id
                    break
            if user_slot_id is None:
                await self._release_slot(global_slot_id, lease_token)
                continue

            if candidate["status"] == "queued":
                eligible = {"status": "queued"}
            else:
                eligible = {
                    "status": "running",
                    "lease_token": candidate["lease_token"],
                    "lease_expires_at": {"$lte": now},
                }
            claimed = await self._collection.find_one_and_update(
                {"run_id": run_id, **eligible},
                {
                    "$set": {
                        "status": "running",
                        "worker_id": worker_id,
                        "lease_token": lease_token,
                        "lease_expires_at": expires_at,
                        "global_slot_id": global_slot_id,
                        "user_slot_id": user_slot_id,
                        "started_at": now,
                        "finished_at": None,
                        "updated_at": now,
                    },
                    "$inc": {"attempt": 1},
                },
                return_document=ReturnDocument.AFTER,
            )
            if claimed is not None:
                return RunLease(self._to_model(claimed), lease_token)
            await self._release_slot(global_slot_id, lease_token)
            await self._release_slot(user_slot_id, lease_token)
        return None

    async def update_report(
        self,
        run_id: str,
        lease_token: str,
        report: dict[str, Any],
    ) -> StoredRun:
        now = datetime.now(UTC)
        result = await self._collection.find_one_and_update(
            {
                "run_id": run_id,
                "status": "running",
                "lease_token": lease_token,
                "lease_expires_at": {"$gt": now},
            },
            {"$set": {"report": report, "updated_at": now}},
            return_document=ReturnDocument.AFTER,
        )
        if result is None:
            raise RunLeaseLost(f"Run {run_id} is no longer owned.")
        return self._to_model(result)

    async def heartbeat(self, run_id: str, lease_token: str) -> bool:
        now = datetime.now(UTC)
        expires_at = now + timedelta(
            seconds=self._settings.lease_duration_seconds
        )

        async def transaction(session: Any) -> bool:
            run = await self._collection.find_one(
                {
                    "run_id": run_id,
                    "status": "running",
                    "lease_token": lease_token,
                    "lease_expires_at": {"$gt": now},
                },
                {"global_slot_id": 1, "user_slot_id": 1},
                session=session,
            )
            if run is None:
                return False
            for slot_id in (
                run["global_slot_id"],
                run["user_slot_id"],
            ):
                result = await self._slots.update_one(
                    {
                        "_id": slot_id,
                        "lease_token": lease_token,
                        "lease_expires_at": {"$gt": now},
                    },
                    {"$set": {"lease_expires_at": expires_at}},
                    session=session,
                )
                if result.matched_count != 1:
                    raise RunLeaseLost(
                        f"Run {run_id} no longer owns all execution slots."
                    )
            result = await self._collection.update_one(
                {
                    "run_id": run_id,
                    "status": "running",
                    "lease_token": lease_token,
                    "lease_expires_at": {"$gt": now},
                },
                {
                    "$set": {
                        "lease_expires_at": expires_at,
                        "updated_at": now,
                    }
                },
                session=session,
            )
            if result.matched_count != 1:
                raise RunLeaseLost(f"Run {run_id} no longer owns its lease.")
            return True

        try:
            async with self._client.start_session() as session:
                return await session.with_transaction(transaction)
        except RunLeaseLost:
            return False

    async def update_progress(
        self,
        run_id: str,
        lease_token: str,
        stage: RunStage,
        progress: RunProgress,
    ) -> StoredRun:
        now = datetime.now(UTC)
        result = await self._collection.find_one_and_update(
            {
                "run_id": run_id,
                "status": "running",
                "current_stage": stage,
                "lease_token": lease_token,
                "lease_expires_at": {"$gt": datetime.now(UTC)},
            },
            {
                "$set": {
                    "progress": progress.model_dump(mode="json"),
                    "updated_at": now,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if result is None:
            raise RunLeaseLost(
                f"Run {run_id} is no longer owned at stage {stage}."
            )
        return self._to_model(result)

    async def advance_stage(
        self,
        run_id: str,
        lease_token: str,
        expected_stage: RunStage,
    ) -> StoredRun:
        stage_index = _STAGES.index(expected_stage)
        next_stage = (
            _STAGES[stage_index + 1]
            if stage_index + 1 < len(_STAGES)
            else None
        )
        current = await self._collection.find_one(
            {"run_id": run_id},
            {"_id": 0},
        )
        now = datetime.now(UTC)
        if current is not None:
            checkpointed = expected_stage in current.get("checkpoints", {})
            lease_expires_at = current.get("lease_expires_at")
            if (
                lease_expires_at is not None
                and lease_expires_at.tzinfo is None
            ):
                lease_expires_at = lease_expires_at.replace(tzinfo=UTC)
            if (
                current.get("current_stage") == next_stage
                and current.get("lease_token") == lease_token
                and lease_expires_at is not None
                and lease_expires_at > now
                and checkpointed
            ):
                return self._to_model(current)
            if (
                next_stage is None
                and current.get("status") == "succeeded"
                and current.get("last_lease_token") == lease_token
                and checkpointed
            ):
                return self._to_model(current)

        if next_stage is None:
            return await self._finish_run(
                run_id,
                lease_token,
                status="succeeded",
                expected_stage=expected_stage,
                error=None,
                finished_at=now,
            )

        result = await self._collection.find_one_and_update(
            {
                "run_id": run_id,
                "status": "running",
                "current_stage": expected_stage,
                "lease_token": lease_token,
                "lease_expires_at": {"$gt": now},
            },
            {
                "$set": {
                    f"checkpoints.{expected_stage}": now,
                    "current_stage": next_stage,
                    "progress": RunProgress().model_dump(mode="json"),
                    "updated_at": now,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if result is None:
            raise RunStateConflict(
                f"Run {run_id} cannot advance from {expected_stage}."
            )
        return self._to_model(result)

    async def fail_run(
        self,
        run_id: str,
        lease_token: str,
        *,
        message: str,
        error_type: str,
    ) -> StoredRun:
        current = await self._collection.find_one(
            {"run_id": run_id},
            {"_id": 0},
        )
        if current is not None and (
            current.get("status") == "failed"
            and current.get("last_lease_token") == lease_token
        ):
            return self._to_model(current)
        if current is None or current.get("current_stage") not in _STAGES:
            raise RunLeaseLost(f"Run {run_id} is no longer available.")
        return await self._finish_run(
            run_id,
            lease_token,
            status="failed",
            expected_stage=current["current_stage"],
            error={
                "stage": current["current_stage"],
                "message": message,
                "error_type": error_type,
                "occurred_at": datetime.now(UTC),
            },
            finished_at=datetime.now(UTC),
        )

    async def _finish_run(
        self,
        run_id: str,
        lease_token: str,
        *,
        status: Literal["succeeded", "failed"],
        expected_stage: RunStage,
        error: dict[str, Any] | None,
        finished_at: datetime,
    ) -> StoredRun:
        async def transaction(
            session: Any,
        ) -> tuple[dict[str, Any], str | None, str | None]:
            current = await self._collection.find_one(
                {"run_id": run_id},
                session=session,
            )
            if current is None:
                raise RunLeaseLost(f"Run {run_id} does not exist.")
            if (
                current["status"] == status
                and current.get("last_lease_token") == lease_token
            ):
                return (
                    current,
                    current.get("global_slot_id"),
                    current.get("user_slot_id"),
                )
            lease_expires_at = current.get("lease_expires_at")
            if (
                lease_expires_at is not None
                and lease_expires_at.tzinfo is None
            ):
                lease_expires_at = lease_expires_at.replace(tzinfo=UTC)
            if (
                current.get("lease_token") != lease_token
                or lease_expires_at is None
                or lease_expires_at <= datetime.now(UTC)
            ):
                raise RunLeaseLost(f"Run {run_id} is no longer owned.")
            if (
                current["status"] != "running"
                or current["current_stage"] != expected_stage
            ):
                raise RunStateConflict(
                    f"Run {run_id} cannot finish from {expected_stage}."
                )
            update: dict[str, Any] = {
                "$set": {
                    "status": status,
                    "error": error,
                    "finished_at": finished_at,
                    "updated_at": finished_at,
                    "last_lease_token": lease_token,
                },
                "$unset": {
                    "lease_token": "",
                    "lease_expires_at": "",
                    "worker_id": "",
                    "global_slot_id": "",
                    "user_slot_id": "",
                },
            }
            if status == "failed" and error is not None:
                update["$push"] = {"errors": error}
            if status == "succeeded":
                update["$set"][f"checkpoints.{expected_stage}"] = finished_at
            result = await self._collection.find_one_and_update(
                {
                    "run_id": run_id,
                    "status": "running",
                    "current_stage": expected_stage,
                    "lease_token": lease_token,
                    "lease_expires_at": {"$gt": finished_at},
                },
                update,
                return_document=ReturnDocument.AFTER,
                session=session,
            )
            if result is None:
                raise RunLeaseLost(f"Run {run_id} is no longer owned.")
            await self._decrement_outstanding(
                result["user_id"],
                session=session,
            )
            return (
                result,
                current.get("global_slot_id"),
                current.get("user_slot_id"),
            )

        async with self._client.start_session() as session:
            completed, global_slot_id, user_slot_id = (
                await session.with_transaction(transaction)
            )
        await self._release_slot(
            global_slot_id,
            lease_token,
        )
        await self._release_slot(
            user_slot_id,
            lease_token,
        )
        return self._to_model(completed)
