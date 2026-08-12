from __future__ import annotations

import httpx
import pytest
from pydantic import ValidationError

from ductor_mcp_gateway.client import DuctorClient
from ductor_mcp_gateway.errors import (
    DuctorProtocolError,
    DuctorUnavailableError,
    DuctorUpstreamError,
)
from ductor_mcp_gateway.models import AgentMessageRequest, TaskCreateRequest


def test_models_reject_blank_or_unexpected_input() -> None:
    with pytest.raises(ValidationError):
        AgentMessageRequest(from_agent="main", to_agent="other", message="   ")
    with pytest.raises(ValidationError):
        TaskCreateRequest.model_validate({"from": "main", "prompt": "work", "unexpected": "value"})


@pytest.mark.parametrize(
    ("response", "error_type"),
    [
        (httpx.Response(503, json={"error": "details"}), DuctorUpstreamError),
        (httpx.Response(200, content=b"not json"), DuctorProtocolError),
        (httpx.Response(200, json={"agents": "wrong"}), DuctorProtocolError),
    ],
)
async def test_client_maps_upstream_failures_without_body_leakage(
    response: httpx.Response, error_type: type[Exception]
) -> None:
    transport = httpx.MockTransport(lambda _request: response)
    async with httpx.AsyncClient(transport=transport) as http:
        client = DuctorClient(
            base_url="http://ductor.test",
            timeout_seconds=1,
            max_response_bytes=1024,
            http_client=http,
        )
        with pytest.raises(error_type) as caught:
            await client.list_agents()
    assert "details" not in str(caught.value)


async def test_client_preserves_ownership_aliases() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(__import__("json").loads(request.content))
        return httpx.Response(200, json={"success": True, "task_id": "deadbeef"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = DuctorClient(
            base_url="http://ductor.test",
            timeout_seconds=1,
            max_response_bytes=1024,
            http_client=http,
        )
        await client.create_task(TaskCreateRequest(from_agent="main", prompt="work"))
    assert captured["from"] == "main"
    assert "from_agent" not in captured


async def test_client_maps_network_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection failed", request=request)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = DuctorClient(
            base_url="http://ductor.test",
            timeout_seconds=1,
            max_response_bytes=1024,
            http_client=http,
        )
        with pytest.raises(DuctorUnavailableError, match="unavailable"):
            await client.list_agents()


async def test_client_rejects_oversized_response() -> None:
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, content=b"x" * 1_025))
    async with httpx.AsyncClient(transport=transport) as http:
        client = DuctorClient(
            base_url="http://ductor.test",
            timeout_seconds=1,
            max_response_bytes=1024,
            http_client=http,
        )
        with pytest.raises(DuctorProtocolError, match="exceeded"):
            await client.list_agents()


async def test_client_injects_explicit_upstream_bearer() -> None:
    observed_authorization = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal observed_authorization
        observed_authorization = request.headers.get("authorization", "")
        return httpx.Response(200, json={"agents": []})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = DuctorClient(
            base_url="http://ductor.test",
            timeout_seconds=1,
            max_response_bytes=1024,
            bearer_token="u" * 40,
            http_client=http,
        )
        await client.list_agents()
    assert observed_authorization == f"Bearer {'u' * 40}"
