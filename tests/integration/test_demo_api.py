from __future__ import annotations

import pytest

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
