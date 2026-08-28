import asyncio

import pytest

from civitas.worker.cli import WorkerRunner, _load_factory


class FakeWorker:
    def __init__(self, outcomes: list[bool]) -> None:
        self.outcomes = outcomes
        self.recoveries = 0
        self.processed = 0
        self.closed = 0
        self.heartbeats = 0

    async def heartbeat(self) -> None:
        self.heartbeats += 1

    async def recover_abandoned(self) -> int:
        self.recoveries += 1
        return 0

    async def process_next(self) -> bool:
        self.processed += 1
        return self.outcomes.pop(0) if self.outcomes else False

    async def close(self) -> None:
        self.closed += 1


@pytest.mark.asyncio
async def test_runner_recovers_before_one_shot_processing() -> None:
    worker = FakeWorker([True])
    runner = WorkerRunner(worker, poll_interval=0.01, recovery_interval=0.01)  # type: ignore[arg-type]

    assert await runner.run_once() is True
    assert worker.recoveries == 1
    assert worker.processed == 1
    assert worker.heartbeats == 1


@pytest.mark.asyncio
async def test_runner_stops_cleanly_while_queue_is_idle() -> None:
    worker = FakeWorker([False])
    runner = WorkerRunner(worker, poll_interval=0.01, recovery_interval=60)  # type: ignore[arg-type]
    stop = asyncio.Event()
    task = asyncio.create_task(runner.run(stop))
    await asyncio.sleep(0)
    stop.set()
    await task

    assert worker.recoveries == 1
    assert worker.processed == 1
    assert worker.heartbeats >= 1


def test_factory_path_must_be_explicit_and_callable() -> None:
    with pytest.raises(ValueError, match="module:callable"):
        _load_factory("invalid")
    with pytest.raises(TypeError, match="not callable"):
        _load_factory("civitas.worker.cli:signal")
