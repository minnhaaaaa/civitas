"""Transport-level authentication checks for Streamable HTTP."""

import httpx
import pytest
from tests.contract.mcp_server.test_inbound_server import FakeService, _context

from civitas.mcp_server import InboundMCPServer, StaticIdentityProvider


async def _resolve(token: str):
    return _context() if token == "valid-token" else None


@pytest.mark.asyncio
async def test_streamable_http_rejects_unauthenticated_requests() -> None:
    server = InboundMCPServer(FakeService(), StaticIdentityProvider(_context()))
    # Authentication is outermost, so this check does not need the SDK lifespan.
    transport = httpx.ASGITransport(app=server.streamable_http_app(_resolve))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/mcp", content=b"{}")
    assert response.status_code == 401
    assert response.json() == {"error": "unauthenticated"}
