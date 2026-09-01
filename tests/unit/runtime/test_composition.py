"""Composition-root tests without external PostgreSQL or provider calls."""

from datetime import UTC

import pytest

from civitas.runtime import RuntimeSettings, SettingsError, build_runtime


def _settings() -> RuntimeSettings:
    return RuntimeSettings(
        database_url="postgresql+psycopg://civitas:secret@localhost/civitas",
        approval_secret_pepper="p" * 32,
        bearer_token="t" * 32,
        organization_id="org-1",
        operator_id="operator-1",
        operator_subject="subject-1",
        operator_roles=("procurement-operator",),
    )


def test_environment_configuration_rejects_missing_secrets() -> None:
    with pytest.raises(SettingsError, match="DATABASE_URL"):
        RuntimeSettings.from_env({})


def test_environment_configuration_rejects_non_postgres_database() -> None:
    with pytest.raises(SettingsError, match="PostgreSQL"):
        RuntimeSettings(
            database_url="sqlite+aiosqlite:///:memory:",
            approval_secret_pepper="p" * 32,
            bearer_token="t" * 32,
            organization_id="org-1",
            operator_id="operator-1",
            operator_subject="subject-1",
            operator_roles=(),
        )


@pytest.mark.asyncio
async def test_composition_wires_authenticated_mcp_and_rejects_wrong_token() -> None:
    runtime = build_runtime(_settings())
    try:
        assert runtime.facade is not None
        assert runtime.workflow is not None
        assert runtime.mcp_server.mcp.name == "Civitas"
        assert await runtime.identity.resolve("wrong-token") is None
        context = await runtime.identity.resolve("t" * 32)
        assert context is not None
        assert context.organization_id == "org-1"
        assert context.operator_id == "operator-1"
        assert context.authenticated_at.tzinfo is UTC
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_unconfigured_provider_execution_fails_closed() -> None:
    runtime = build_runtime(_settings())
    try:
        result = await runtime.mcp_server.dispatch(
            "execute_approved_plan",
            {"receipt_id": "approval-1", "idempotency_key": "attempt-1"},
            context=runtime.identity.context(),
        )
        assert result["code"] == "rejected_execution"
        assert "not configured" in str(result["message"])
    finally:
        await runtime.close()
