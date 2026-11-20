import asyncio

import pytest

from events.dispatcher import RunDispatcher


class InMemoryExecutor:
    def __init__(self) -> None:
        self.pending = [object(), object(), object()]
        self.started_two = asyncio.Event()
        self.release = asyncio.Event()
        self.completed_all = asyncio.Event()
        self.active = 0
        self.max_active = 0
        self.completed = 0

    async def claim_next_run(self):
        return self.pending.pop(0) if self.pending else None

    async def execute_claimed(self, lease):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        if self.active == 2:
            self.started_two.set()
        await self.release.wait()
        self.active -= 1
        self.completed += 1
        if self.completed == 3:
            self.completed_all.set()
        return True


@pytest.mark.asyncio
async def test_dispatcher_respects_local_capacity_and_drains_queued_runs():
    executor = InMemoryExecutor()
    dispatcher = RunDispatcher(
        executor,
        max_concurrent_runs=2,
        poll_interval_seconds=10,
    )
    dispatcher.start()
    try:
        await asyncio.wait_for(executor.started_two.wait(), timeout=1)
        assert len(executor.pending) == 1

        executor.release.set()
        await asyncio.wait_for(executor.completed_all.wait(), timeout=1)
    finally:
        await dispatcher.stop()

    assert executor.max_active == 2
    assert executor.completed == 3
