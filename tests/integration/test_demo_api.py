from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from civitas.api.app import DemoIntegrationService, create_app


def test_create_app_exposes_health_route() -> None:
    app = create_app()

    assert app.title == "Civitas API"
    assert any(route.path == "/health" for route in app.routes)


@pytest.mark.asyncio
async def test_demo_service_runs_full_false_consensus_flow() -> None:
    service = DemoIntegrationService()

    run = await service.create_run("false-consensus-demo")
    snapshot = service.get_run(run.run_id)

    assert snapshot is not None
    assert snapshot.status == "completed"
    assert snapshot.final_state == "approve"

    event_types = [event.event_type.value for event in snapshot.events]

    assert "evidence.recorded" in event_types
    assert "jury.evaluated" in event_types
    assert "investigation.requested" in event_types
    assert event_types.count("execution.updated") == 2
    assert any(
        event.payload.get("task") == "clean_room_dissent"
        for event in snapshot.events
        if event.event_type.value == "task.started"
    )
    assert any(event.payload.get("state") == "duplicate" for event in snapshot.events)


@pytest.mark.asyncio
async def test_demo_http_api_runs_in_background_and_replays_sse() -> None:
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            "/api/demo-runs",
            json={"scenario_id": "false-consensus-demo"},
        )
        assert created.status_code == 200
        payload = created.json()
        assert payload["status"] == "running"

        snapshot = None
        for _ in range(100):
            response = await client.get(f"/api/demo-runs/{payload['run_id']}")
            snapshot = response.json()
            if snapshot["status"] == "completed":
                break
            await asyncio.sleep(0.01)

        assert snapshot is not None
        assert snapshot["final_state"] == "approve"
        stream = await client.get(payload["stream_url"])
        assert stream.status_code == 200
        assert "event: jury.evaluated" in stream.text
        assert "event: execution.updated" in stream.text
