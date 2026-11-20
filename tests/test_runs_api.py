from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from events.api import app as app_module
from events.api.app import app
from events.api.dependencies import (
    get_runs_repository,
    get_users_repository,
)
from events.api.schemas import StoredRun, UserSettings
from events.core.settings import BackendSettings
from events.models import SourceConfig
from events.persistence.runs_repository import (
    RunLimitReached,
    RunStateConflict,
)


class InMemoryUsersRepository:
    async def get_settings(self, user_id: str) -> UserSettings:
        return UserSettings(
            timezone="Europe/Rome",
            sources=[
                SourceConfig(
                    name="Example Venue",
                    platform="instagram",
                    url="https://www.instagram.com/example_venue/",
                    lookback_days=30,
                )
            ],
        )


class InMemoryRunsRepository:
    def __init__(self) -> None:
        self.runs: list[StoredRun] = []
        self.max_outstanding_per_user = 1

    async def create_run(
        self,
        user_id: str,
        settings_snapshot: UserSettings,
    ) -> StoredRun:
        outstanding = sum(
            run.user_id == user_id and run.status in ("queued", "running")
            for run in self.runs
        )
        if outstanding >= self.max_outstanding_per_user:
            raise RunLimitReached
        now = datetime.now(UTC)
        run = StoredRun(
            run_id=f"run-{len(self.runs) + 1}",
            user_id=user_id,
            status="queued",
            current_stage="collect",
            progress={"completed": 0},
            checkpoints={},
            settings_snapshot=settings_snapshot,
            attempt=0,
            created_at=now,
            updated_at=now,
        )
        self.runs.append(run)
        return run

    async def list_runs(
        self,
        user_id: str,
        *,
        limit: int,
        offset: int = 0,
    ) -> list[StoredRun]:
        items = [run for run in self.runs if run.user_id == user_id]
        return list(reversed(items))[offset : offset + limit]

    async def get_run(self, user_id: str, run_id: str) -> StoredRun | None:
        return next(
            (
                run
                for run in self.runs
                if run.user_id == user_id and run.run_id == run_id
            ),
            None,
        )

    async def resume_run(self, user_id: str, run_id: str) -> StoredRun | None:
        run = await self.get_run(user_id, run_id)
        if run is None:
            return None
        if run.status == "queued":
            return run
        if run.status != "failed":
            raise RunStateConflict("Run can only be resumed after failure.")
        outstanding = sum(
            item.user_id == user_id
            and item.status in ("queued", "running")
            for item in self.runs
        )
        if outstanding >= self.max_outstanding_per_user:
            raise RunLimitReached
        run.status = "queued"
        run.error = None
        run.finished_at = None
        return run


@pytest.fixture(autouse=True)
def disable_database_connection(monkeypatch):
    monkeypatch.setattr(
        app_module,
        "BackendSettings",
        lambda: BackendSettings(_env_file=None),
    )


def test_runs_api_creates_lists_scopes_and_resumes_runs():
    runs_repository = InMemoryRunsRepository()
    app.dependency_overrides[get_runs_repository] = lambda: runs_repository
    app.dependency_overrides[get_users_repository] = (
        lambda: InMemoryUsersRepository()
    )
    try:
        with TestClient(app) as client:
            created = client.post("/api/users/alice/runs")
            rejected = client.post("/api/users/alice/runs")
            listed = client.get("/api/users/alice/runs")
            fetched = client.get(
                f"/api/users/alice/runs/{created.json()['run_id']}"
            )
            wrong_user = client.get(
                f"/api/users/bob/runs/{created.json()['run_id']}"
            )
            runs_repository.runs[0].status = "failed"
            resumed = client.post(
                f"/api/users/alice/runs/{created.json()['run_id']}/resume"
            )
    finally:
        app.dependency_overrides.clear()

    assert created.status_code == 202
    assert created.json()["status"] == "queued"
    assert rejected.status_code == 409
    assert len(listed.json()) == 1
    assert fetched.status_code == 200
    assert wrong_user.status_code == 404
    assert resumed.status_code == 202
    assert resumed.json()["status"] == "queued"


def test_run_creation_requires_saved_sources():
    class EmptyUsersRepository:
        async def get_settings(self, user_id: str) -> UserSettings:
            return UserSettings()

    app.dependency_overrides[get_runs_repository] = (
        lambda: InMemoryRunsRepository()
    )
    app.dependency_overrides[get_users_repository] = (
        lambda: EmptyUsersRepository()
    )
    try:
        with TestClient(app) as client:
            response = client.post("/api/users/alice/runs")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422


def test_configured_outstanding_limit_allows_queued_runs_up_to_the_limit():
    runs_repository = InMemoryRunsRepository()
    runs_repository.max_outstanding_per_user = 3
    app.dependency_overrides[get_runs_repository] = lambda: runs_repository
    app.dependency_overrides[get_users_repository] = (
        lambda: InMemoryUsersRepository()
    )
    try:
        with TestClient(app) as client:
            responses = [
                client.post("/api/users/alice/runs")
                for _ in range(4)
            ]
    finally:
        app.dependency_overrides.clear()

    assert [response.status_code for response in responses] == [
        202,
        202,
        202,
        409,
    ]
