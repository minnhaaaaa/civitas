"""Application-owned ports for outbound procurement providers."""

from __future__ import annotations

from typing import Protocol

from pydantic import SecretStr

from civitas.contracts.providers import (
    ProviderAccessContext,
    ProviderCapabilityManifest,
    ProviderRegistration,
)
from civitas.contracts.tools import MCPToolCall, MCPToolResult


class ProviderCredential(Protocol):
    """Opaque credential material visible only to a transport factory."""

    @property
    def secret(self) -> SecretStr: ...

    @property
    def context(self) -> ProviderAccessContext: ...


class ProviderCredentialResolver(Protocol):
    async def resolve(
        self,
        *,
        provider_id: str,
        credential_ref: str,
        context: ProviderAccessContext,
    ) -> ProviderCredential: ...


class OperationalProviderTransport(Protocol):
    async def discover_capabilities(self) -> ProviderCapabilityManifest: ...

    async def invoke(self, call: MCPToolCall) -> MCPToolResult: ...


class ProviderTransportFactory(Protocol):
    async def connect(
        self,
        *,
        registration: ProviderRegistration,
        credential: ProviderCredential,
        context: ProviderAccessContext,
    ) -> OperationalProviderTransport: ...
