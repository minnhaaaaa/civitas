"""Validated environment configuration for the deployed MCP process."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


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
    bearer_ttl_seconds: int = 3600
    rate_limit_requests: int = 120
    rate_limit_window_seconds: int = 60
    environment: str = "development"
    log_format: str = "console"
    service_name: str = "civitas-mcp"
    provider_factory: str | None = None
    live_provider_required: bool = False
    require_worker_ready: bool = False
    worker_readiness_seconds: int = 120
    heartbeat_interval_seconds: int = 10
    metrics_enabled: bool = True

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
        if not 60 <= self.bearer_ttl_seconds <= 86_400:
            raise SettingsError("CIVITAS_BEARER_TTL_SECONDS must be between 60 and 86400")
        if not 1 <= self.rate_limit_requests <= 100_000:
            raise SettingsError("CIVITAS_RATE_LIMIT_REQUESTS must be between 1 and 100000")
        if not 1 <= self.rate_limit_window_seconds <= 3600:
            raise SettingsError("CIVITAS_RATE_LIMIT_WINDOW_SECONDS must be between 1 and 3600")
        if self.environment not in {"development", "test", "production"}:
            raise SettingsError("CIVITAS_ENV must be development, test, or production")
        if self.log_format not in {"console", "json"}:
            raise SettingsError("CIVITAS_LOG_FORMAT must be console or json")
        if not self.service_name.strip() or len(self.service_name) > 64:
            raise SettingsError("CIVITAS_SERVICE_NAME must contain 1 to 64 characters")
        if not 15 <= self.worker_readiness_seconds <= 3600:
            raise SettingsError("CIVITAS_WORKER_READINESS_SECONDS must be between 15 and 3600")
        if not 2 <= self.heartbeat_interval_seconds < self.worker_readiness_seconds:
            raise SettingsError("heartbeat interval must be shorter than worker readiness TTL")
        if self.live_provider_required and not self.provider_factory:
            raise SettingsError(
                "CIVITAS_PROVIDER_FACTORY is required when live provider mode is required"
            )
        if self.provider_factory is not None and ":" not in self.provider_factory:
            raise SettingsError("CIVITAS_PROVIDER_FACTORY must use module:callable syntax")
        if self.environment == "production":
            self._validate_production()

    def _validate_production(self) -> None:
        if self.transport != "streamable-http":
            raise SettingsError("production requires Streamable HTTP transport")
        if self.log_format != "json":
            raise SettingsError("production requires CIVITAS_LOG_FORMAT=json")
        if not self.operator_roles:
            raise SettingsError("production requires at least one operator role")
        if self.bearer_token == self.approval_secret_pepper:
            raise SettingsError("bearer and approval secrets must be independent")
        weak_values = {"civitas_dev", "change-me", "changeme", "development"}
        development_secrets = {
            "local-approval-pepper-change-this-value",
            "local-bearer-token-change-this-value-now",
        }
        if self.bearer_token.casefold() in weak_values | development_secrets:
            raise SettingsError("production bearer token is a known development value")
        if self.approval_secret_pepper.casefold() in development_secrets:
            raise SettingsError("production approval secret is a known development value")
        parsed = urlsplit(self.database_url)
        if parsed.password is not None and parsed.password.casefold() in weak_values:
            raise SettingsError("production database URL contains a development password")

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> RuntimeSettings:
        values = os.environ if environ is None else environ
        return cls(
            database_url=_secret(values, "DATABASE_URL"),
            approval_secret_pepper=_secret(values, "CIVITAS_APPROVAL_SECRET_PEPPER"),
            bearer_token=_secret(values, "CIVITAS_BEARER_TOKEN"),
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
            bearer_ttl_seconds=_integer(values, "CIVITAS_BEARER_TTL_SECONDS", 3600),
            rate_limit_requests=_integer(values, "CIVITAS_RATE_LIMIT_REQUESTS", 120),
            rate_limit_window_seconds=_integer(values, "CIVITAS_RATE_LIMIT_WINDOW_SECONDS", 60),
            environment=values.get("CIVITAS_ENV", "development"),
            log_format=values.get("CIVITAS_LOG_FORMAT", "console"),
            service_name=values.get("CIVITAS_SERVICE_NAME", "civitas-mcp"),
            provider_factory=values.get("CIVITAS_PROVIDER_FACTORY") or None,
            live_provider_required=_boolean(values, "CIVITAS_LIVE_PROVIDER_REQUIRED", False),
            require_worker_ready=_boolean(values, "CIVITAS_REQUIRE_WORKER_READY", False),
            worker_readiness_seconds=_integer(values, "CIVITAS_WORKER_READINESS_SECONDS", 120),
            heartbeat_interval_seconds=_integer(values, "CIVITAS_HEARTBEAT_INTERVAL_SECONDS", 10),
            metrics_enabled=_boolean(values, "CIVITAS_METRICS_ENABLED", True),
        )


def _required(values: dict[str, str] | os._Environ[str], name: str) -> str:
    value = values.get(name, "").strip()
    if not value:
        raise SettingsError(f"{name} is required")
    return value


def _secret(values: dict[str, str] | os._Environ[str], name: str) -> str:
    direct = values.get(name, "").strip()
    file_name = values.get(f"{name}_FILE", "").strip()
    if direct and file_name:
        raise SettingsError(f"set only one of {name} or {name}_FILE")
    if direct:
        return direct
    if not file_name:
        raise SettingsError(f"{name} or {name}_FILE is required")
    path = Path(file_name)
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise SettingsError(f"{name}_FILE could not be read") from error
    if not value:
        raise SettingsError(f"{name}_FILE is empty")
    return value


def _integer(values: dict[str, str] | os._Environ[str], name: str, default: int) -> int:
    raw = values.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as error:
        raise SettingsError(f"{name} must be an integer") from error


def _boolean(values: dict[str, str] | os._Environ[str], name: str, default: bool) -> bool:
    raw = values.get(name)
    if raw is None:
        return default
    normalized = raw.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise SettingsError(f"{name} must be a boolean")
