"""Runtime liveness, readiness, metrics routes, and process heartbeats."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import timedelta
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

from civitas.persistence.health import PostgreSQLServiceHealthStore
from civitas.ports.clock import Clock
from civitas.runtime.config import RuntimeSettings
from civitas.runtime.observability import MetricsRegistry, OperationalTelemetryMiddleware

EXPECTED_DATABASE_REVISION = "c72e4a8b901d"


class RuntimeHealth:
    def __init__(
        self,
        *,
        store: PostgreSQLServiceHealthStore,
        clock: Clock,
        settings: RuntimeSettings,
        service_id: str,
    ) -> None:
        self._store = store
        self._clock = clock
        self._settings = settings
        self.service_id = service_id
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        self._started = False

    async def ready(self) -> tuple[bool, tuple[str, ...]]:
        reasons: list[str] = []
        if not await self._store.database_alive():
            reasons.append("database_unavailable")
            return False, tuple(reasons)
        try:
            revision = await self._store.database_revision()
        except Exception:
            revision = None
        if revision != EXPECTED_DATABASE_REVISION:
            reasons.append("migration_revision_mismatch")
        if self._settings.require_worker_ready:
            worker_ready = await self._store.is_ready(
                service_kind="worker",
                now=self._clock.now(),
                max_age=timedelta(seconds=self._settings.worker_readiness_seconds),
            )
            if not worker_ready:
                reasons.append("worker_unavailable")
        return not reasons, tuple(reasons)

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._stopping.clear()
        try:
            await self._store.heartbeat(
                service_id=self.service_id,
                service_kind="mcp-server",
                now=self._clock.now(),
                state="starting",
            )
        except Exception:
            self._started = False
            raise
        self._task = asyncio.create_task(self._heartbeat_loop())

    async def stop(self) -> None:
        if not self._started:
            return
        self._started = False
        self._stopping.set()
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        with suppress(Exception):
            await self._store.heartbeat(
                service_id=self.service_id,
                service_kind="mcp-server",
                now=self._clock.now(),
                state="stopping",
            )

    async def _heartbeat_loop(self) -> None:
        while not self._stopping.is_set():
            await self._store.heartbeat(
                service_id=self.service_id,
                service_kind="mcp-server",
                now=self._clock.now(),
            )
            await asyncio.sleep(self._settings.heartbeat_interval_seconds)


def install_operational_surface(
    app: Any,
    *,
    health: RuntimeHealth,
    metrics: MetricsRegistry,
    settings: RuntimeSettings,
) -> None:
    async def live(request: Request) -> Response:
        del request
        return JSONResponse({"status": "alive"})

    async def ready(request: Request) -> Response:
        del request
        is_ready, reasons = await health.ready()
        metrics.set_readiness(is_ready)
        return JSONResponse(
            {"status": "ready" if is_ready else "not_ready", "reason_codes": reasons},
            status_code=200 if is_ready else 503,
        )

    async def prometheus(request: Request) -> Response:
        del request
        if not settings.metrics_enabled:
            return PlainTextResponse("metrics disabled\n", status_code=404)
        return PlainTextResponse(
            metrics.render(), media_type="text/plain; version=0.0.4; charset=utf-8"
        )

    app.routes.extend(
        (
            Route("/health/live", live, methods=["GET"]),
            Route("/health/ready", ready, methods=["GET"]),
            Route("/metrics", prometheus, methods=["GET"]),
        )
    )
    app.add_middleware(
        OperationalTelemetryMiddleware,
        metrics=metrics,
        service=settings.service_name,
        environment=settings.environment,
    )
