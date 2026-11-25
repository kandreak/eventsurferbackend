import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from pymongo import AsyncMongoClient

from events.api.schemas import RunProgress, StoredRun, UserSettings
from events.config import RunExecutionSettings, UserLimits
from events.core.settings import BackendSettings
from events.persistence.mongodb import MongoDatabase
from events.persistence.runs_repository import (
    RunLeaseLost,
    RunLimitReached,
    RunWeeklyLimitReached,
    RunsRepository,
)
from events.persistence.users_repository import UsersRepository

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_ATLAS_INTEGRATION_TESTS") != "1",
    reason="Set RUN_ATLAS_INTEGRATION_TESTS=1 to run against MongoDB Atlas.",
)


@pytest_asyncio.fixture
async def atlas_database():
    backend_settings = BackendSettings()
    if backend_settings.mongodb_uri is None:
        pytest.fail(
            "MONGODB_URI must be configured to run Atlas integration tests."
        )

    client = AsyncMongoClient(
        backend_settings.mongodb_uri.get_secret_value(),
        serverSelectionTimeoutMS=10000,
        connectTimeoutMS=10000,
    )
    database_name = f"eventsurfer_it_{uuid4().hex[:20]}"
    mongo = MongoDatabase(client, database_name)
    try:
        await mongo.initialize()
        yield mongo.database
    finally:
        try:
            await client.drop_database(database_name)
        finally:
            await client.close()


def _run_settings(
    *,
    max_concurrent_runs: int = 2,
    max_concurrent_runs_per_user: int = 1,
    max_outstanding_runs_per_user: int = 2,
    lease_duration_seconds: int = 60,
) -> RunExecutionSettings:
    return RunExecutionSettings(
        max_concurrent_runs=max_concurrent_runs,
        max_concurrent_runs_per_user=max_concurrent_runs_per_user,
        max_outstanding_runs_per_user=max_outstanding_runs_per_user,
        lease_duration_seconds=lease_duration_seconds,
        dispatch_poll_interval_seconds=1,
    )


def _user_id() -> str:
    return f"atlas-test-{uuid4().hex}"


def _user_limits(
    *,
    max_sources_per_user: int = 10,
    max_runs_per_user_per_week: int = 10,
) -> UserLimits:
    return UserLimits(
        max_sources_per_user=max_sources_per_user,
        max_runs_per_user_per_week=max_runs_per_user_per_week,
    )


@pytest.mark.asyncio
async def test_user_settings_persist_and_remain_user_scoped(atlas_database):
    repository = UsersRepository(atlas_database["users"], _user_limits())
    user_id = _user_id()
    settings = UserSettings(timezone="UTC")

    saved = await repository.put_settings(user_id, settings)

    assert saved == settings
    assert await repository.get_settings(user_id) == settings
    assert await repository.get_settings(f"{user_id}-other") is None


@pytest.mark.asyncio
async def test_outstanding_run_limit_is_atomic_and_runs_are_user_scoped(
    atlas_database,
):
    user_id = _user_id()
    repository = RunsRepository(
        atlas_database["runs"],
        _run_settings(max_outstanding_runs_per_user=1),
        _user_limits(),
    )

    results = await asyncio.gather(
        repository.create_run(user_id, UserSettings()),
        repository.create_run(user_id, UserSettings()),
        return_exceptions=True,
    )

    created = [result for result in results if isinstance(result, StoredRun)]
    rejected = [
        result
        for result in results
        if isinstance(result, RunLimitReached)
    ]
    assert len(created) == 1
    assert len(rejected) == 1
    persisted = await repository.get_run(user_id, created[0].run_id)
    assert persisted is not None
    assert persisted.run_id == created[0].run_id
    assert persisted.user_id == user_id
    assert persisted.settings_snapshot == created[0].settings_snapshot
    assert await repository.get_run(f"{user_id}-other", created[0].run_id) is None
    assert await repository.list_runs(f"{user_id}-other", limit=10) == []


@pytest.mark.asyncio
async def test_rolling_weekly_run_limit_is_atomic_and_resume_is_exempt(
    atlas_database,
):
    user_id = _user_id()
    repository = RunsRepository(
        atlas_database["runs"],
        _run_settings(max_outstanding_runs_per_user=10),
        _user_limits(max_runs_per_user_per_week=1),
    )
    results = await asyncio.gather(
        repository.create_run(user_id, UserSettings()),
        repository.create_run(user_id, UserSettings()),
        return_exceptions=True,
    )

    created = [result for result in results if isinstance(result, StoredRun)]
    rejected = [
        result
        for result in results
        if isinstance(result, RunWeeklyLimitReached)
    ]
    assert len(created) == 1
    assert len(rejected) == 1

    lease = await repository.claim_next_run("atlas-weekly-worker")
    assert lease is not None
    assert lease.run.run_id == created[0].run_id
    failed = await repository.fail_run(
        lease.run.run_id,
        lease.token,
        message="test failure",
        error_type="TestFailure",
    )
    assert failed.status == "failed"
    with pytest.raises(RunWeeklyLimitReached):
        await repository.create_run(user_id, UserSettings())

    old_created_at = datetime.now(UTC) - timedelta(weeks=1, seconds=1)
    await atlas_database["runs"].update_one(
        {"run_id": created[0].run_id},
        {"$set": {"created_at": old_created_at}},
    )
    next_run = await repository.create_run(user_id, UserSettings())
    resumed = await repository.resume_run(user_id, created[0].run_id)

    assert next_run.run_id != created[0].run_id
    assert resumed is not None
    assert resumed.status == "queued"
    assert resumed.run_id == created[0].run_id


@pytest.mark.asyncio
async def test_global_execution_slots_limit_concurrent_claims(atlas_database):
    repository = RunsRepository(
        atlas_database["runs"],
        _run_settings(max_concurrent_runs=1),
        _user_limits(),
    )
    first_user, second_user = _user_id(), _user_id()
    first = await repository.create_run(first_user, UserSettings())
    second = await repository.create_run(second_user, UserSettings())

    claimed = await repository.claim_next_run("atlas-worker-one")
    blocked = await repository.claim_next_run("atlas-worker-two")

    assert claimed is not None
    assert claimed.run.run_id in {first.run_id, second.run_id}
    assert blocked is None
    running_count = await atlas_database["runs"].count_documents(
        {"status": "running"}
    )
    assert running_count == 1


@pytest.mark.asyncio
async def test_stopping_runs_clears_run_posts_and_preserves_other_users(
    atlas_database,
):
    user_id, other_user_id = _user_id(), _user_id()
    repository = RunsRepository(
        atlas_database["runs"],
        _run_settings(max_outstanding_runs_per_user=2),
        _user_limits(),
    )
    running = await repository.create_run(user_id, UserSettings())
    lease = await repository.claim_next_run("atlas-stop-worker")
    assert lease is not None
    queued = await repository.create_run(user_id, UserSettings())
    other_user_run = await repository.create_run(
        other_user_id,
        UserSettings(),
    )
    await atlas_database["run_posts"].insert_many(
        [
            {
                "run_id": run_id,
                "user_id": owner_id,
                "platform": "instagram",
                "external_id": "post-1",
                "post_order": 0,
                "post": {},
                "events": [],
            }
            for run_id, owner_id in (
                (running.run_id, user_id),
                (queued.run_id, user_id),
                (other_user_run.run_id, other_user_id),
            )
        ]
    )

    stopped, stop_requested = await repository.stop_user_runs(user_id)
    persisted_running = await repository.get_run(user_id, running.run_id)
    persisted_queued = await repository.get_run(user_id, queued.run_id)
    assert persisted_running is not None
    assert persisted_queued is not None
    assert persisted_running.cancellation_requested
    assert persisted_queued.status == "stopped"

    finished = await repository.finish_stopped(running.run_id, lease.token)
    remaining_run_ids = await atlas_database["run_posts"].distinct("run_id")

    assert (stopped, stop_requested) == (1, 1)
    assert finished is not None
    assert finished.status == "stopped"
    assert remaining_run_ids == [other_user_run.run_id]


@pytest.mark.asyncio
async def test_stopping_one_run_does_not_stop_others_or_delete_their_posts(
    atlas_database,
):
    user_id, other_user_id = _user_id(), _user_id()
    repository = RunsRepository(
        atlas_database["runs"],
        _run_settings(max_outstanding_runs_per_user=3),
        _user_limits(),
    )
    target = await repository.create_run(user_id, UserSettings())
    lease = await repository.claim_next_run("atlas-stop-one-worker")
    assert lease is not None
    same_user_queued = await repository.create_run(user_id, UserSettings())
    other_user_queued = await repository.create_run(
        other_user_id,
        UserSettings(),
    )
    await atlas_database["run_posts"].insert_many(
        [
            {
                "run_id": run.run_id,
                "user_id": owner_id,
                "platform": "instagram",
                "external_id": "post-1",
                "post_order": 0,
                "post": {},
                "events": [],
            }
            for run, owner_id in (
                (target, user_id),
                (same_user_queued, user_id),
                (other_user_queued, other_user_id),
            )
        ]
    )

    requested = await repository.stop_run(user_id, target.run_id)
    remaining = await repository.stop_run(user_id, "missing-run")
    finished = await repository.finish_stopped(target.run_id, lease.token)
    remaining_run_ids = await atlas_database["run_posts"].distinct("run_id")

    assert requested is not None
    assert requested.status == "running"
    assert requested.cancellation_requested
    assert remaining is None
    assert finished is not None
    assert finished.status == "stopped"
    assert set(remaining_run_ids) == {
        same_user_queued.run_id,
        other_user_queued.run_id,
    }


@pytest.mark.asyncio
async def test_heartbeat_renews_lease_and_expired_lease_can_be_reclaimed(
    atlas_database,
):
    repository = RunsRepository(
        atlas_database["runs"],
        _run_settings(
            max_concurrent_runs=1,
            lease_duration_seconds=60,
        ),
        _user_limits(),
    )
    user_id = _user_id()
    run = await repository.create_run(user_id, UserSettings())
    lease = await repository.claim_next_run("atlas-worker-one")
    assert lease is not None

    before = await atlas_database["runs"].find_one(
        {"run_id": run.run_id},
        {"lease_expires_at": 1},
    )
    assert await repository.heartbeat(run.run_id, lease.token)
    after = await atlas_database["runs"].find_one(
        {"run_id": run.run_id},
        {"lease_expires_at": 1},
    )
    assert after["lease_expires_at"] > before["lease_expires_at"]

    expired_at = datetime.now(UTC) - timedelta(seconds=1)
    await atlas_database["runs"].update_one(
        {"run_id": run.run_id, "lease_token": lease.token},
        {"$set": {"lease_expires_at": expired_at}},
    )
    await atlas_database["run_execution_slots"].update_many(
        {"run_id": run.run_id, "lease_token": lease.token},
        {"$set": {"lease_expires_at": expired_at}},
    )

    reclaimed = await repository.claim_next_run("atlas-worker-two")

    assert reclaimed is not None
    assert reclaimed.run.run_id == run.run_id
    assert reclaimed.token != lease.token
    assert not await repository.heartbeat(run.run_id, lease.token)
    with pytest.raises(RunLeaseLost):
        await repository.update_progress(
            run.run_id,
            lease.token,
            "collect",
            RunProgress(completed=1),
        )


@pytest.mark.asyncio
async def test_stage_checkpoints_persist_and_resume_at_incomplete_stage(
    atlas_database,
):
    user_id = _user_id()
    repository = RunsRepository(
        atlas_database["runs"],
        _run_settings(max_concurrent_runs=1),
        _user_limits(),
    )
    run = await repository.create_run(user_id, UserSettings())
    lease = await repository.claim_next_run("atlas-worker-one")
    assert lease is not None

    after_collect = await repository.advance_stage(
        run.run_id,
        lease.token,
        "collect",
    )
    after_analyze = await repository.advance_stage(
        run.run_id,
        lease.token,
        "analyze",
    )
    assert set(after_analyze.checkpoints) == {"collect", "analyze"}

    failed = await repository.fail_run(
        run.run_id,
        lease.token,
        message="test failure",
        error_type="TestFailure",
    )
    assert failed.status == "failed"
    assert failed.current_stage == "publish"
    assert "collect" in after_collect.checkpoints

    resumed = await repository.resume_run(user_id, run.run_id)
    assert resumed is not None
    assert resumed.status == "queued"
    assert resumed.current_stage == "publish"

    next_lease = await repository.claim_next_run("atlas-worker-two")
    assert next_lease is not None
    assert next_lease.run.current_stage == "publish"
    assert set(next_lease.run.checkpoints) == {"collect", "analyze"}
