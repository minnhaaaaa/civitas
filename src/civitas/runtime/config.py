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
