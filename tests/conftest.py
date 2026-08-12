from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import pytest_asyncio
from aiohttp import web


@dataclass
class FakeDuctorState:
    requests: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    agents_status: int = 200
    malformed_agents: bool = False


@dataclass
class FakeDuctor:
    url: str
    state: FakeDuctorState


@pytest_asyncio.fixture
async def fake_ductor() -> AsyncIterator[FakeDuctor]:
    state = FakeDuctorState()
    app = web.Application()

    async def agents(_request: web.Request) -> web.Response:
        if state.agents_status != 200:
            return web.json_response({"error": "unavailable"}, status=state.agents_status)
        if state.malformed_agents:
            return web.json_response({"agents": "not-a-list"})
        return web.json_response({"agents": ["main", "blocked"]})

    async def health(_request: web.Request) -> web.Response:
        return web.json_response({"agents": {"main": {"status": "running"}}})

    async def send(request: web.Request) -> web.Response:
        body = await request.json()
        state.requests.append(("send", body))
        return web.json_response(
            {"sender": body["to"], "text": "fake reply", "success": True, "error": ""}
        )

    async def send_async(request: web.Request) -> web.Response:
        body = await request.json()
        state.requests.append(("send_async", body))
        return web.json_response({"success": True, "task_id": "abc12345"})

    async def task_create(request: web.Request) -> web.Response:
        body = await request.json()
        state.requests.append(("task_create", body))
        return web.json_response({"success": True, "task_id": "deadbeef"})

    async def task_list(request: web.Request) -> web.Response:
        owner = request.query.get("from", "")
        state.requests.append(("task_list", {"from": owner}))
        return web.json_response(
            {
                "tasks": [
                    {
                        "task_id": "deadbeef",
                        "parent_agent": owner,
                        "status": "done",
                        "future_field": "preserved",
                    },
                    {
                        "task_id": "badc0ffe",
                        "parent_agent": "blocked",
                        "status": "done",
                    },
                ]
            }
        )

    async def task_resume(request: web.Request) -> web.Response:
        body = await request.json()
        state.requests.append(("task_resume", body))
        return web.json_response({"success": True, "task_id": body["task_id"]})

    async def task_cancel(request: web.Request) -> web.Response:
        body = await request.json()
        state.requests.append(("task_cancel", body))
        return web.json_response({"success": True})

    app.router.add_get("/interagent/agents", agents)
    app.router.add_get("/interagent/health", health)
    app.router.add_post("/interagent/send", send)
    app.router.add_post("/interagent/send_async", send_async)
    app.router.add_post("/tasks/create", task_create)
    app.router.add_get("/tasks/list", task_list)
    app.router.add_post("/tasks/resume", task_resume)
    app.router.add_post("/tasks/cancel", task_cancel)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    sockets = site._server.sockets
    port = sockets[0].getsockname()[1]
    try:
        yield FakeDuctor(url=f"http://127.0.0.1:{port}", state=state)
    finally:
        await runner.cleanup()
