"""Composition-root tests without external PostgreSQL or provider calls."""

from dataclasses import replace
from datetime import UTC

import pytest

from civitas.application.live_execution import PersistedApprovedExecutionAdapter
from civitas.contracts.tools import MCPToolCall, MCPToolResult
from civitas.integrations import ExecutionProviderContext
from civitas.persistence.workflow_runs import PostgreSQLWorkflowRunStore
from civitas.runtime import (
    ProviderExecutionRuntime,
    RuntimeSettings,
    SettingsError,
    build_runtime,
    build_worker,
)


class UnusedProviderReads:
    async def invoke(self, call: MCPToolCall) -> MCPToolResult:
        del call
        raise AssertionError("composition must not call providers")


class UnusedExecutionConnections:
    async def connect(self, context: ExecutionProviderContext) -> UnusedProviderReads:
        del context
        raise AssertionError("composition must not connect providers")


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


def test_environment_configuration_rejects_unbounded_worker_attempts() -> None:
    with pytest.raises(SettingsError, match="WORKER_MAX_ATTEMPTS"):
        replace(_settings(), worker_max_attempts=0)


@pytest.mark.asyncio
async def test_composition_wires_authenticated_mcp_and_rejects_wrong_token() -> None:
    runtime = build_runtime(_settings())
    try:
        assert runtime.facade is not None
        assert runtime.workflow is not None
        assert isinstance(runtime.workflow_runs, PostgreSQLWorkflowRunStore)
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
async def test_worker_composition_uses_durable_run_limits_and_closes_database() -> None:
    worker = build_worker(_settings())
    assert worker is not None
    await worker.close()


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


@pytest.mark.asyncio
async def test_complete_provider_dependencies_enable_persisted_guarded_execution() -> None:
    runtime = build_runtime(
        _settings(),
        provider_execution=ProviderExecutionRuntime(
            reads=UnusedProviderReads(),
            connections=UnusedExecutionConnections(),
            server_name="provider-1",
        ),
    )
    try:
        assert isinstance(runtime.executions, PersistedApprovedExecutionAdapter)
    finally:
        await runtime.close()


def test_provider_runtime_rejects_empty_server_identity() -> None:
    with pytest.raises(ValueError, match="server name"):
        ProviderExecutionRuntime(
            reads=UnusedProviderReads(),
            connections=UnusedExecutionConnections(),
            server_name=" ",
        )
