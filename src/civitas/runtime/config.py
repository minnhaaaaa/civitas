"""Validated environment configuration for the deployed MCP process."""

from __future__ import annotations

import os
from dataclasses import dataclass


class SettingsError(ValueError):
    """Raised before startup when required production configuration is unsafe."""


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    database_url: str
    approval_secret_pepper: str
    bearer_token: str
    organization_id: str
    operator_id: str
    operator_subject: str
    operator_roles: tuple[str, ...]
    transport: str = "streamable-http"
    host: str = "127.0.0.1"
    port: int = 8001
    log_level: str = "info"
    policy_version: str = "decision-integrity-v1"
    worker_id: str | None = None
    worker_lease_seconds: int = 60
    worker_max_attempts: int = 5

    def __post_init__(self) -> None:
        if not self.database_url.startswith(
            ("postgresql+psycopg://", "postgresql+psycopg_async://")
        ):
            raise SettingsError("DATABASE_URL must use the PostgreSQL psycopg async driver")
        if len(self.approval_secret_pepper.encode("utf-8")) < 32:
            raise SettingsError("CIVITAS_APPROVAL_SECRET_PEPPER must contain at least 32 bytes")
        if len(self.bearer_token) < 32:
            raise SettingsError("CIVITAS_BEARER_TOKEN must contain at least 32 characters")
        if not self.organization_id.strip() or not self.operator_id.strip():
            raise SettingsError("organization and operator identity bindings are required")
        if self.transport not in {"stdio", "streamable-http"}:
            raise SettingsError("CIVITAS_MCP_TRANSPORT must be stdio or streamable-http")
        if not 1 <= self.port <= 65535:
            raise SettingsError("CIVITAS_MCP_PORT must be between 1 and 65535")
        if self.worker_id is not None and not 1 <= len(self.worker_id.strip()) <= 128:
            raise SettingsError("CIVITAS_WORKER_ID must contain between 1 and 128 characters")
        if not 5 <= self.worker_lease_seconds <= 3600:
            raise SettingsError("CIVITAS_WORKER_LEASE_SECONDS must be between 5 and 3600")
        if not 1 <= self.worker_max_attempts <= 100:
            raise SettingsError("CIVITAS_WORKER_MAX_ATTEMPTS must be between 1 and 100")

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> RuntimeSettings:
        values = os.environ if environ is None else environ
        return cls(
            database_url=_required(values, "DATABASE_URL"),
            approval_secret_pepper=_required(values, "CIVITAS_APPROVAL_SECRET_PEPPER"),
            bearer_token=_required(values, "CIVITAS_BEARER_TOKEN"),
            organization_id=_required(values, "CIVITAS_ORGANIZATION_ID"),
            operator_id=_required(values, "CIVITAS_OPERATOR_ID"),
            operator_subject=values.get("CIVITAS_OPERATOR_SUBJECT", "controlled-bearer-token"),
            operator_roles=tuple(
                role.strip()
                for role in values.get("CIVITAS_OPERATOR_ROLES", "procurement-operator").split(",")
                if role.strip()
            ),
            transport=values.get("CIVITAS_MCP_TRANSPORT", "streamable-http"),
            host=values.get("CIVITAS_MCP_HOST", "127.0.0.1"),
            port=_integer(values, "CIVITAS_MCP_PORT", 8001),
            log_level=values.get("CIVITAS_LOG_LEVEL", "info"),
            policy_version=values.get("CIVITAS_INTEGRITY_POLICY_VERSION", "decision-integrity-v1"),
            worker_id=values.get("CIVITAS_WORKER_ID"),
            worker_lease_seconds=_integer(values, "CIVITAS_WORKER_LEASE_SECONDS", 60),
            worker_max_attempts=_integer(values, "CIVITAS_WORKER_MAX_ATTEMPTS", 5),
        )


def _required(values: dict[str, str] | os._Environ[str], name: str) -> str:
    value = values.get(name, "").strip()
    if not value:
        raise SettingsError(f"{name} is required")
    return value


def _integer(values: dict[str, str] | os._Environ[str], name: str, default: int) -> int:
    raw = values.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as error:
        raise SettingsError(f"{name} must be an integer") from error
