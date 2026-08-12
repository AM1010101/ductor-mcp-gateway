from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from ductor_mcp_gateway.app import GatewayApplication, create_application
from ductor_mcp_gateway.config import Settings

from .conftest import FakeDuctor

TOKEN = "test-token-with-at-least-thirty-two-characters"


def make_settings(fake: FakeDuctor, **overrides: Any) -> Settings:
    raw: dict[str, Any] = {
        "server": {"allowed_hosts": ["testserver"]},
        "ductor": {"url": fake.url, "timeout_seconds": 2},
        "policy": {
            "allowed_agents": ["main"],
            "allowed_tools": [
                "ductor_agents_list",
                "ductor_agent_message",
                "ductor_agent_message_async",
                "ductor_tasks_create",
                "ductor_tasks_list",
                "ductor_tasks_resume",
                "ductor_tasks_cancel",
            ],
        },
        "limits": {"rate_limit_per_minute": 100, "max_sessions": 4},
    }
    for section, values in overrides.items():
        raw.setdefault(section, {}).update(values)
    return Settings.model_validate(raw)


@asynccontextmanager
async def mcp_session(
    gateway: GatewayApplication, *, token: str = TOKEN
) -> AsyncIterator[ClientSession]:
    transport = httpx.ASGITransport(app=gateway)
    async with gateway.lifespan_context():
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers={"Authorization": f"Bearer {token}"},
        ) as http:
            async with streamable_http_client("http://testserver/mcp", http_client=http) as (
                read_stream,
                write_stream,
                _get_session_id,
            ):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    yield session


async def test_auth_protects_mcp_and_health(fake_ductor: FakeDuctor) -> None:
    gateway = create_application(make_settings(fake_ductor), bearer_token=TOKEN)
    transport = httpx.ASGITransport(app=gateway)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
        assert (await http.get("/health")).status_code == 401
        assert (
            await http.post(
                "/mcp",
                headers={"Authorization": "Bearer wrong"},
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
            )
        ).status_code == 401
    async with gateway.lifespan_context():
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers={"Authorization": f"Bearer {TOKEN}"},
        ) as http:
            response = await http.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "upstream": "ok"}


async def test_official_mcp_client_lists_tools_and_preserves_task_owner(
    fake_ductor: FakeDuctor, caplog: pytest.LogCaptureFixture
) -> None:
    gateway = create_application(make_settings(fake_ductor), bearer_token=TOKEN)
    secret_prompt = "highly sensitive prompt body"
    with caplog.at_level(logging.INFO, logger="ductor_mcp_gateway.audit"):
        async with mcp_session(gateway) as session:
            tools = await session.list_tools()
            assert {tool.name for tool in tools.tools} == {
                "ductor_agents_list",
                "ductor_agent_message",
                "ductor_agent_message_async",
                "ductor_tasks_create",
                "ductor_tasks_list",
                "ductor_tasks_resume",
                "ductor_tasks_cancel",
            }

            agents = await session.call_tool("ductor_agents_list", {})
            assert not agents.isError
            assert agents.structuredContent == {"agents": ["main"]}

            created = await session.call_tool(
                "ductor_tasks_create",
                {"from_agent": "main", "prompt": secret_prompt, "priority": "batch"},
            )
            assert not created.isError
            assert created.structuredContent["task_id"] == "deadbeef"

            listed = await session.call_tool("ductor_tasks_list", {"from_agent": "main"})
            assert not listed.isError
            tasks = listed.structuredContent["tasks"]
            assert len(tasks) == 1
            assert tasks[0]["parent_agent"] == "main"
            assert tasks[0]["future_field"] == "preserved"

    create_body = next(body for name, body in fake_ductor.state.requests if name == "task_create")
    assert create_body["from"] == "main"
    assert create_body["prompt"] == secret_prompt
    assert secret_prompt not in caplog.text


async def test_policy_rejects_disallowed_agent_before_upstream(fake_ductor: FakeDuctor) -> None:
    gateway = create_application(make_settings(fake_ductor), bearer_token=TOKEN)
    async with mcp_session(gateway) as session:
        result = await session.call_tool(
            "ductor_agent_message",
            {"from_agent": "main", "to_agent": "blocked", "message": "hello"},
        )
    assert result.isError
    assert not any(name == "send" for name, _body in fake_ductor.state.requests)


async def test_message_resume_and_cancel_payloads_preserve_ownership(
    fake_ductor: FakeDuctor,
) -> None:
    gateway = create_application(make_settings(fake_ductor), bearer_token=TOKEN)
    async with mcp_session(gateway) as session:
        sync_result = await session.call_tool(
            "ductor_agent_message",
            {"from_agent": "main", "to_agent": "main", "message": "fake only"},
        )
        async_result = await session.call_tool(
            "ductor_agent_message_async",
            {
                "from_agent": "main",
                "to_agent": "main",
                "message": "fake async only",
                "reply_to": "main",
            },
        )
        resumed = await session.call_tool(
            "ductor_tasks_resume",
            {"from_agent": "main", "task_id": "deadbeef", "prompt": "fake follow-up"},
        )
        cancelled = await session.call_tool(
            "ductor_tasks_cancel", {"from_agent": "main", "task_id": "deadbeef"}
        )

    assert all(not result.isError for result in (sync_result, async_result, resumed, cancelled))
    by_name = {name: body for name, body in fake_ductor.state.requests}
    assert by_name["send"]["from"] == "main"
    assert by_name["send_async"]["from"] == "main"
    assert by_name["send_async"]["reply_to"] == "main"
    assert by_name["task_resume"]["from"] == "main"
    assert by_name["task_cancel"]["from"] == "main"


async def test_disabled_tools_are_not_advertised(fake_ductor: FakeDuctor) -> None:
    settings = make_settings(fake_ductor, policy={"allowed_tools": ["ductor_agents_list"]})
    gateway = create_application(settings, bearer_token=TOKEN)
    async with mcp_session(gateway) as session:
        tools = await session.list_tools()
    assert [tool.name for tool in tools.tools] == ["ductor_agents_list"]


async def test_upstream_http_and_schema_errors_become_tool_errors(fake_ductor: FakeDuctor) -> None:
    gateway = create_application(make_settings(fake_ductor), bearer_token=TOKEN)
    fake_ductor.state.agents_status = 503
    async with mcp_session(gateway) as session:
        unavailable = await session.call_tool("ductor_agents_list", {})
        fake_ductor.state.agents_status = 200
        fake_ductor.state.malformed_agents = True
        malformed = await session.call_tool("ductor_agents_list", {})
    assert unavailable.isError
    assert malformed.isError
    assert "unavailable" not in str(unavailable.content).lower()


async def test_malformed_tool_input_never_reaches_ductor(fake_ductor: FakeDuctor) -> None:
    gateway = create_application(make_settings(fake_ductor), bearer_token=TOKEN)
    async with mcp_session(gateway) as session:
        result = await session.call_tool(
            "ductor_tasks_resume",
            {"from_agent": "main", "task_id": "bad id", "prompt": "continue"},
        )
    assert result.isError
    assert not any(name == "task_resume" for name, _body in fake_ductor.state.requests)


async def test_rate_limit_applies_after_authentication(fake_ductor: FakeDuctor) -> None:
    settings = make_settings(fake_ductor, limits={"rate_limit_per_minute": 1})
    gateway = create_application(settings, bearer_token=TOKEN)
    transport = httpx.ASGITransport(app=gateway)
    async with gateway.lifespan_context():
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers={"Authorization": f"Bearer {TOKEN}"},
        ) as http:
            assert (await http.get("/health")).status_code == 200
            assert (await http.get("/health")).status_code == 429


async def test_request_body_limit_is_enforced(fake_ductor: FakeDuctor) -> None:
    settings = make_settings(fake_ductor, limits={"max_body_bytes": 1024})
    gateway = create_application(settings, bearer_token=TOKEN)
    transport = httpx.ASGITransport(app=gateway)
    async with gateway.lifespan_context():
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers={
                "Authorization": f"Bearer {TOKEN}",
                "Accept": "application/json, text/event-stream",
            },
        ) as http:
            response = await http.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"padding": "x" * 2_000},
                },
            )
    assert response.status_code == 413


async def test_active_session_limit_rejects_second_initialization(fake_ductor: FakeDuctor) -> None:
    settings = make_settings(fake_ductor, limits={"max_sessions": 1})
    gateway = create_application(settings, bearer_token=TOKEN)
    transport = httpx.ASGITransport(app=gateway)
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "application/json, text/event-stream",
    }
    async with gateway.lifespan_context():
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver", headers=headers
        ) as http:
            first = await http.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "1"},
                    },
                },
            )
            second = await http.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "1"},
                    },
                },
            )
    assert first.status_code == 200
    assert first.headers.get("mcp-session-id")
    assert second.status_code == 503
