import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from events import executor
from events.api.schemas import RunProgress, StoredRun, UserSettings
from events.config import (
    AISettings,
    ActorSettings,
    ApplicationSettings,
    CollectionSettings,
    RunExecutionSettings,
    SettingsConfig,
)
from events.pipeline import _report_template
from events.models import SocialPost, SourceConfig
from events.persistence.runs_repository import RunLease, RunLeaseLost


def _settings(*, lease_duration_seconds=60) -> SettingsConfig:
    return SettingsConfig(
        application=ApplicationSettings(),
        collection=CollectionSettings(
            actors={"instagram": ActorSettings()}
        ),
        ai=AISettings(model="test-model"),
        run_execution=RunExecutionSettings(
            max_concurrent_runs=5,
            max_concurrent_runs_per_user=1,
            max_outstanding_runs_per_user=1,
            lease_duration_seconds=lease_duration_seconds,
            dispatch_poll_interval_seconds=1,
        ),
    )


def _run() -> StoredRun:
    now = datetime.now(UTC)
    return StoredRun(
        run_id="run-1",
        user_id="alice",
        status="running",
        current_stage="collect",
        progress=RunProgress(),
        settings_snapshot=UserSettings(
            timezone="Europe/Rome",
            sources=[
                SourceConfig(
                    name="Example Venue",
                    platform="instagram",
                    url="https://www.instagram.com/example_venue/",
                    lookback_days=30,
                )
            ],
        ),
        attempt=1,
        created_at=now,
        updated_at=now,
        started_at=now,
    )


class InMemoryRunsRepository:
    def __init__(self) -> None:
        self.run = _run()
        self.reports = []
        self.progress = []
        self.failure = None
        self.heartbeat_count = 0
        self.cancellation_requested = False

    async def finish_stopped(self, run_id, lease_token):
        if self.cancellation_requested:
            self.run = self.run.model_copy(
                update={
                    "status": "stopped",
                    "cancellation_requested": True,
                    "finished_at": datetime.now(UTC),
                }
            )
            return self.run
        return None

    async def claim_next_run(self, worker_id, *, run_id):
        assert run_id == self.run.run_id
        return RunLease(self.run, "lease-token")

    async def heartbeat(self, run_id, lease_token):
        assert (run_id, lease_token) == (self.run.run_id, "lease-token")
        self.heartbeat_count += 1
        return True

    async def update_progress(self, run_id, lease_token, stage, progress):
        self.progress.append((stage, progress))
        return self.run

    async def update_report(self, run_id, lease_token, report):
        if self.cancellation_requested:
            raise RunLeaseLost("Cancellation was requested.")
        self.reports.append(report)
        self.run = self.run.model_copy(update={"report": report})
        return self.run

    async def advance_stage(self, run_id, lease_token, expected_stage):
        if self.cancellation_requested:
            raise RunLeaseLost("Cancellation was requested.")
        next_stage = {
            "collect": "analyze",
            "analyze": "publish",
            "publish": "publish",
        }[expected_stage]
        status = "succeeded" if expected_stage == "publish" else "running"
        self.run = self.run.model_copy(
            update={
                "status": status,
                "current_stage": next_stage,
                "checkpoints": {
                    **self.run.checkpoints,
                    expected_stage: datetime.now(UTC),
                },
            }
        )
        return self.run

    async def fail_run(
        self,
        run_id,
        lease_token,
        *,
        message,
        error_type,
    ):
        self.failure = (message, error_type)
        self.run = self.run.model_copy(
            update={
                "status": "failed",
                "error": {
                    "stage": self.run.current_stage,
                    "message": message,
                    "error_type": error_type,
                    "occurred_at": datetime.now(UTC),
                },
            }
        )
        return self.run


class InMemoryRunPostsRepository:
    def __init__(self, posts):
        self.posts = posts
        self.events = []
        self.run_user_id = "alice"

    async def replace_posts(self, run_id, user_id, posts):
        assert user_id == self.run_user_id
        self.posts = posts

    async def list_posts(self, run_id, user_id):
        assert user_id == self.run_user_id
        return self.posts

    async def replace_events(self, run_id, user_id, events):
        assert user_id == self.run_user_id
        self.events = events

    async def list_events(self, run_id, user_id):
        assert user_id == self.run_user_id
        return self.events


def _post() -> SocialPost:
    return SocialPost(
        source_name="Example Venue",
        platform="instagram",
        external_id="post-1",
        post_url="https://www.instagram.com/p/post-1/",
        caption="Event announcement",
    )


@pytest.mark.asyncio
async def test_executor_runs_and_checkpoints_each_stage_without_provider_calls(
    monkeypatch,
):
    post = _post()
    events = []
    runs = InMemoryRunsRepository()
    run_posts = InMemoryRunPostsRepository([])
    saved_events = []
    report = _report_template(1)

    async def collect(sources, settings, secrets, **kwargs):
        assert kwargs["user_id"] == "alice"
        await asyncio.sleep(0.4)
        return [post], report

    async def analyze(posts, current_report, settings, secrets, **kwargs):
        assert posts == [post]
        return events, current_report

    monkeypatch.setattr(executor, "_collect_posts", collect)
    monkeypatch.setattr(executor, "_analyze_posts", analyze)
    run_executor = executor.RunExecutor(
        runs_repository=runs,
        run_posts_repository=run_posts,
        posts_repository=SimpleNamespace(),
        events_repository=SimpleNamespace(
            save_events=lambda user_id, saved: _save_events(
                saved_events, user_id, saved
            )
        ),
        settings=_settings(lease_duration_seconds=1),
        secrets=SimpleNamespace(),
        worker_id="test-worker",
    )

    assert await run_executor.execute_claimed(
        RunLease(runs.run, "lease-token")
    )

    assert runs.run.status == "succeeded"
    assert runs.heartbeat_count >= 1
    assert set(runs.run.checkpoints) == {"collect", "analyze", "publish"}
    assert run_posts.posts == [post]
    assert run_posts.events == []
    assert saved_events == [("alice", [])]
    assert runs.progress[-1][1].message == (
        "Prepared 0 events for API retrieval."
    )


@pytest.mark.asyncio
async def test_executor_saves_provider_errors_in_report_and_failed_run(
    monkeypatch,
):
    runs = InMemoryRunsRepository()
    run_posts = InMemoryRunPostsRepository([])
    report = _report_template(1)
    report["source_errors"].append("Apify request failed.")

    async def collect(*args, **kwargs):
        return [], report

    monkeypatch.setattr(executor, "_collect_posts", collect)
    run_executor = executor.RunExecutor(
        runs_repository=runs,
        run_posts_repository=run_posts,
        posts_repository=SimpleNamespace(),
        events_repository=SimpleNamespace(),
        settings=_settings(),
        secrets=SimpleNamespace(),
        worker_id="test-worker",
    )

    assert not await run_executor.execute_claimed(
        RunLease(runs.run, "lease-token")
    )

    assert runs.run.status == "failed"
    assert runs.run.report["source_errors"] == ["Apify request failed."]
    assert runs.run.error["message"] == "Collection failed for 1 source(s)."
    assert runs.run.current_stage == "collect"


@pytest.mark.asyncio
async def test_executor_finishes_stopped_work_after_lease_update_is_rejected(
    monkeypatch,
):
    runs = InMemoryRunsRepository()
    run_posts = InMemoryRunPostsRepository([])

    async def collect(*args, **kwargs):
        runs.cancellation_requested = True
        return [], _report_template(1)

    monkeypatch.setattr(executor, "_collect_posts", collect)
    run_executor = executor.RunExecutor(
        runs_repository=runs,
        run_posts_repository=run_posts,
        posts_repository=SimpleNamespace(),
        events_repository=SimpleNamespace(),
        settings=_settings(),
        secrets=SimpleNamespace(),
        worker_id="test-worker",
    )

    assert not await run_executor.execute_claimed(
        RunLease(runs.run, "lease-token")
    )
    assert runs.run.status == "stopped"


@pytest.mark.asyncio
async def test_executor_resumes_at_the_first_incomplete_stage(monkeypatch):
    post = _post()
    runs = InMemoryRunsRepository()
    runs.run = runs.run.model_copy(
        update={
            "current_stage": "analyze",
            "report": _report_template(1),
            "checkpoints": {"collect": datetime.now(UTC)},
        }
    )
    run_posts = InMemoryRunPostsRepository([post])

    async def collect(*args, **kwargs):
        raise AssertionError("A completed collect stage must not be repeated.")

    async def analyze(posts, report, settings, secrets, **kwargs):
        assert posts == [post]
        return [], report

    monkeypatch.setattr(executor, "_collect_posts", collect)
    monkeypatch.setattr(executor, "_analyze_posts", analyze)
    run_executor = executor.RunExecutor(
        runs_repository=runs,
        run_posts_repository=run_posts,
        posts_repository=SimpleNamespace(),
        events_repository=SimpleNamespace(
            save_events=lambda user_id, saved: _save_events([], user_id, saved)
        ),
        settings=_settings(),
        secrets=SimpleNamespace(),
        worker_id="test-worker",
    )

    assert await run_executor.execute_claimed(
        RunLease(runs.run, "lease-token")
    )

    assert runs.run.status == "succeeded"
    assert set(runs.run.checkpoints) == {"collect", "analyze", "publish"}


async def _save_events(saved_events, user_id, events):
    saved_events.append((user_id, events))
