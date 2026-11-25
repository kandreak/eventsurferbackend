from __future__ import annotations

import asyncio
import logging
from typing import Any, Coroutine

from events.api.schemas import RunProgress, RunStage, StoredRun
from events.config import SettingsConfig
from events.pipeline import (
    _analyze_posts,
    _collect_posts,
    _report_template,
)
from events.persistence.events_repository import EventsRepository
from events.persistence.posts_repository import PostsRepository
from events.persistence.run_posts_repository import RunPostsRepository
from events.persistence.runs_repository import (
    RunLease,
    RunLeaseLost,
    RunsRepository,
)
from events.secrets import Secrets

logger = logging.getLogger("events.executor")


class RunExecutor:
    def __init__(
        self,
        *,
        runs_repository: RunsRepository,
        run_posts_repository: RunPostsRepository,
        posts_repository: PostsRepository,
        events_repository: EventsRepository,
        settings: SettingsConfig,
        secrets: Secrets,
        worker_id: str,
    ) -> None:
        self._runs = runs_repository
        self._run_posts = run_posts_repository
        self._posts = posts_repository
        self._events = events_repository
        self._settings = settings
        self._secrets = secrets
        self._worker_id = worker_id

    async def claim_next_run(self) -> RunLease | None:
        return await self._runs.claim_next_run(self._worker_id)

    async def execute_claimed(self, lease: RunLease) -> bool:
        run_id = lease.run.run_id
        try:
            await self._with_heartbeat(
                lease,
                self._execute_stages(lease),
            )
        except RunLeaseLost:
            stopped = await self._runs.finish_stopped(
                run_id,
                lease.token,
            )
            if stopped is not None:
                return False
            raise
        except Exception as exc:
            logger.exception(
                "Run %s failed during stage %s",
                run_id,
                lease.run.current_stage,
            )
            stopped = await self._runs.finish_stopped(
                run_id,
                lease.token,
            )
            if stopped is not None:
                return False
            await self._runs.fail_run(
                run_id,
                lease.token,
                message=str(exc) or type(exc).__name__,
                error_type=type(exc).__name__,
            )
            return False
        return True

    async def _with_heartbeat(
        self,
        lease: RunLease,
        operation: Coroutine[Any, Any, None],
    ) -> None:
        stop = asyncio.Event()
        stage_task = asyncio.create_task(operation)
        heartbeat_task = asyncio.create_task(
            self._heartbeat(lease, stop)
        )
        try:
            done, _ = await asyncio.wait(
                (stage_task, heartbeat_task),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if heartbeat_task in done:
                error = heartbeat_task.exception()
                if error is not None:
                    stage_task.cancel()
                    await asyncio.gather(stage_task, return_exceptions=True)
                    raise error
                raise RuntimeError("Run heartbeat stopped unexpectedly.")
            await stage_task
        finally:
            stop.set()
            if not stage_task.done():
                stage_task.cancel()
                await asyncio.gather(stage_task, return_exceptions=True)
            if not heartbeat_task.done():
                await heartbeat_task

    async def _heartbeat(
        self,
        lease: RunLease,
        stop: asyncio.Event,
    ) -> None:
        interval = self._settings.run_execution.lease_duration_seconds / 3
        while True:
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
                return
            except TimeoutError:
                if not await self._runs.heartbeat(
                    lease.run.run_id,
                    lease.token,
                ):
                    raise RunLeaseLost(
                        f"Run {lease.run.run_id} lease renewal failed."
                    )

    def _settings_for_run(self, run: StoredRun) -> SettingsConfig:
        application = self._settings.application.model_copy(
            update={"timezone": run.settings_snapshot.timezone}
        )
        return self._settings.model_copy(update={"application": application})

    async def _execute_stages(self, lease: RunLease) -> None:
        run = lease.run
        while run.status == "running":
            if run.current_stage == "collect":
                await self._collect_stage(lease)
                run = await self._complete_stage(lease, "collect")
            elif run.current_stage == "analyze":
                await self._analyze_stage(lease, run.report)
                run = await self._complete_stage(lease, "analyze")
            elif run.current_stage == "publish":
                await self._publish_stage(lease, run.report)
                run = await self._complete_stage(lease, "publish")
            else:
                raise ValueError(f"Unknown run stage: {run.current_stage}")

    async def _collect_stage(self, lease: RunLease) -> dict[str, Any]:
        run = lease.run
        sources = run.settings_snapshot.sources
        await self._runs.update_progress(
            run.run_id,
            lease.token,
            "collect",
            RunProgress(
                completed=0,
                total=len(sources),
                message="Collecting posts.",
            ),
        )
        posts, report = await _collect_posts(
            sources,
            self._settings_for_run(run),
            self._secrets,
            posts_repository=self._posts,
            user_id=run.user_id,
        )
        await self._run_posts.replace_posts(
            run.run_id,
            run.user_id,
            posts,
        )
        await self._runs.update_report(run.run_id, lease.token, report)
        await self._runs.update_progress(
            run.run_id,
            lease.token,
            "collect",
            RunProgress(
                completed=len(sources),
                total=len(sources),
                message=f"Collected {len(posts)} posts.",
            ),
        )
        if report["source_errors"]:
            raise RuntimeError(
                f"Collection failed for {len(report['source_errors'])} source(s)."
            )
        return report

    async def _analyze_stage(
        self,
        lease: RunLease,
        previous_report: dict[str, Any] | None,
    ) -> dict[str, Any]:
        run = lease.run
        posts = await self._run_posts.list_posts(run.run_id, run.user_id)
        report = previous_report or _report_template(
            len(run.settings_snapshot.sources)
        )
        await self._runs.update_progress(
            run.run_id,
            lease.token,
            "analyze",
            RunProgress(
                completed=0,
                total=len(posts),
                message="Analyzing posts.",
            ),
        )
        events, report = await _analyze_posts(
            posts,
            report,
            self._settings_for_run(run),
            self._secrets,
            posts_repository=self._posts,
            user_id=run.user_id,
        )
        await self._run_posts.replace_events(run.run_id, run.user_id, events)
        await self._events.save_events(run.user_id, events)
        await self._runs.update_report(run.run_id, lease.token, report)
        await self._runs.update_progress(
            run.run_id,
            lease.token,
            "analyze",
            RunProgress(
                completed=len(posts),
                total=len(posts),
                message=f"Analyzed {len(posts)} posts.",
            ),
        )
        if report["post_errors"]:
            raise RuntimeError(
                f"Analysis failed for {len(report['post_errors'])} post(s)."
            )
        return report

    async def _publish_stage(
        self,
        lease: RunLease,
        report: dict[str, Any] | None,
    ) -> None:
        run = lease.run
        if report is None:
            raise RuntimeError("Run report is missing before publication.")
        events = await self._run_posts.list_events(run.run_id, run.user_id)
        await self._runs.update_progress(
            run.run_id,
            lease.token,
            "publish",
            RunProgress(
                completed=1,
                total=1,
                message=f"Prepared {len(events)} events for API retrieval.",
            ),
        )

    async def _complete_stage(
        self,
        lease: RunLease,
        stage: RunStage,
    ) -> StoredRun:
        return await self._runs.advance_stage(
            lease.run.run_id,
            lease.token,
            stage,
        )
