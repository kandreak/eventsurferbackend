from __future__ import annotations

import asyncio
import logging

from events.executor import RunExecutor
from events.persistence.runs_repository import RunLeaseLost

logger = logging.getLogger("events.dispatcher")


class RunDispatcher:
    def __init__(
        self,
        executor: RunExecutor,
        *,
        max_concurrent_runs: int,
        poll_interval_seconds: int,
    ) -> None:
        self._executor = executor
        self._max_concurrent_runs = max_concurrent_runs
        self._poll_interval_seconds = poll_interval_seconds
        self._wake = asyncio.Event()
        self._tasks: set[asyncio.Task[bool]] = set()
        self._stopping = False
        self._dispatcher_task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._dispatcher_task is not None:
            raise RuntimeError("Run dispatcher is already started.")
        self._dispatcher_task = asyncio.create_task(self._run())

    def notify(self) -> None:
        self._wake.set()

    async def stop(self) -> None:
        if self._dispatcher_task is None:
            return
        self._stopping = True
        self._wake.set()
        await self._dispatcher_task
        self._dispatcher_task = None

    async def _run(self) -> None:
        while not self._stopping:
            self._wake.clear()
            try:
                await self._claim_available_runs()
            except Exception:
                logger.exception("Could not dispatch queued runs.")
            if self._stopping:
                break
            try:
                await asyncio.wait_for(
                    self._wake.wait(),
                    timeout=self._poll_interval_seconds,
                )
            except TimeoutError:
                pass

        running = list(self._tasks)
        for task in running:
            task.cancel()
        if running:
            await asyncio.gather(*running, return_exceptions=True)

    async def _claim_available_runs(self) -> None:
        while (
            not self._stopping
            and len(self._tasks) < self._max_concurrent_runs
        ):
            lease = await self._executor.claim_next_run()
            if lease is None:
                return
            task = asyncio.create_task(
                self._executor.execute_claimed(lease)
            )
            self._tasks.add(task)
            task.add_done_callback(self._task_finished)

    def _task_finished(self, task: asyncio.Task[bool]) -> None:
        self._tasks.discard(task)
        if not task.cancelled():
            error = task.exception()
            if isinstance(error, RunLeaseLost):
                logger.info("%s", error)
            elif error is not None:
                logger.error(
                    "Run executor task failed.",
                    exc_info=(type(error), error, error.__traceback__),
                )
        self._wake.set()
