"""Command-line lifecycle for a composed durable Civitas workflow worker."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import inspect
import os
import signal
from collections.abc import Awaitable, Callable, Sequence
from contextlib import suppress
from types import ModuleType
from typing import cast

from civitas.worker.service import DurableWorkflowWorker

WorkerFactory = Callable[[], DurableWorkflowWorker | Awaitable[DurableWorkflowWorker]]


class WorkerRunner:
    """Poll a durable worker while periodically recovering expired leases."""

    def __init__(
        self,
        worker: DurableWorkflowWorker,
        *,
        poll_interval: float = 1.0,
        recovery_interval: float = 30.0,
    ) -> None:
        if poll_interval <= 0 or recovery_interval <= 0:
            raise ValueError("poll and recovery intervals must be positive")
        self._worker = worker
        self._poll_interval = poll_interval
        self._recovery_interval = recovery_interval

    async def run_once(self) -> bool:
        await self._worker.recover_abandoned()
        return await self._worker.process_next()

    async def run(self, stop: asyncio.Event) -> None:
        loop = asyncio.get_running_loop()
        next_recovery = 0.0
        while not stop.is_set():
            now = loop.time()
            if now >= next_recovery:
                await self._worker.recover_abandoned()
                next_recovery = now + self._recovery_interval
            processed = await self._worker.process_next()
            if processed:
                continue
            with suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=self._poll_interval)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the durable Civitas workflow worker")
    parser.add_argument(
        "--factory",
        default=os.getenv("CIVITAS_WORKER_FACTORY"),
        help="composition factory as module:callable (or CIVITAS_WORKER_FACTORY)",
    )
    parser.add_argument("--once", action="store_true", help="process at most one transition")
    parser.add_argument("--poll-interval", type=float, default=1.0)
    parser.add_argument("--recovery-interval", type=float, default=30.0)
    return parser


async def async_main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.factory:
        raise SystemExit("--factory or CIVITAS_WORKER_FACTORY is required")
    worker = await _create_worker(_load_factory(args.factory))
    runner = WorkerRunner(
        worker,
        poll_interval=args.poll_interval,
        recovery_interval=args.recovery_interval,
    )
    if args.once:
        await runner.run_once()
        return 0
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):  # pragma: no cover - Windows compatibility
            loop.add_signal_handler(signum, stop.set)
    await runner.run(stop)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(async_main(argv))


def _load_factory(path: str) -> WorkerFactory:
    module_name, separator, attribute = path.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError("worker factory must use module:callable syntax")
    module: ModuleType = importlib.import_module(module_name)
    factory = getattr(module, attribute, None)
    if not callable(factory):
        raise TypeError(f"worker factory {path!r} is not callable")
    return cast("WorkerFactory", factory)


async def _create_worker(factory: WorkerFactory) -> DurableWorkflowWorker:
    result = factory()
    worker = await result if inspect.isawaitable(result) else result
    if not isinstance(worker, DurableWorkflowWorker):
        raise TypeError("worker factory must return DurableWorkflowWorker")
    return worker


if __name__ == "__main__":
    raise SystemExit(main())
