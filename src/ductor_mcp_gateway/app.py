"""Application composition: Ductor client + policy + authenticated MCP edge."""

from __future__ import annotations

import contextlib
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, TypeVar

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.fastmcp.server import Settings as FastMCPSettings
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.types import Receive, Scope, Send

from .audit import AuditLogger
from .client import DuctorClient
from .config import Settings
from .errors import DuctorError
from .middleware import GatewayEdgeMiddleware
from .models import (
    AgentMessageAsyncRequest,
    AgentMessageRequest,
    TaskCancelRequest,
    TaskCreateRequest,
    TaskListRequest,
    TaskResumeRequest,
)
from .policy import GatewayPolicy, PolicyError

T = TypeVar("T")

# MCP SDK 1.29 leaves this generic forward reference unresolved. Rebuilding it
# once avoids a pydantic-settings warning on every server construction.
FastMCPSettings[Any].model_rebuild()


@dataclass(slots=True)
class GatewayApplication:
    """ASGI callable with test-visible composition roots."""

    edge: GatewayEdgeMiddleware
    starlette: Starlette
    mcp: FastMCP[Any]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self.edge(scope, receive, send)

    def lifespan_context(self) -> contextlib.AbstractAsyncContextManager[Any]:
        return self.starlette.router.lifespan_context(self.starlette)


def create_application(
    settings: Settings,
    *,
    bearer_token: str,
    ductor_bearer_token: str | None = None,
    ductor_client: DuctorClient | None = None,
    audit: AuditLogger | None = None,
) -> GatewayApplication:
    """Compose the production ASGI application from explicit settings and credentials."""
    audit_log = audit or AuditLogger()
    client = ductor_client or DuctorClient(
        base_url=settings.ductor.url,
        timeout_seconds=settings.ductor.timeout_seconds,
        max_response_bytes=settings.ductor.max_response_bytes,
        bearer_token=ductor_bearer_token,
    )
    policy = GatewayPolicy(
        allowed_agents=settings.policy.allowed_agents,
        allowed_tools=settings.policy.allowed_tools,
    )
    hosts = settings.server.allowed_hosts or [
        f"127.0.0.1:{settings.server.port}",
        f"localhost:{settings.server.port}",
        f"[::1]:{settings.server.port}",
        "127.0.0.1:*",
        "localhost:*",
        "[::1]:*",
    ]
    mcp = FastMCP(
        "Ductor MCP Gateway",
        instructions="Policy-limited access to Ductor agents and delegated background tasks.",
        json_response=True,
        streamable_http_path="/mcp",
        max_request_body_size=settings.limits.max_body_bytes,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=hosts,
            allowed_origins=settings.server.allowed_origins,
        ),
    )

    async def run_tool(
        tool: str,
        operation: Callable[[], Awaitable[T]],
        *,
        from_agent: str | None = None,
        to_agent: str | None = None,
        task_id: str | None = None,
    ) -> T:
        request_id = uuid.uuid4().hex
        started = time.monotonic()
        audit_log.event(
            "tool_call",
            outcome="started",
            request_id=request_id,
            tool=tool,
            from_agent=from_agent,
            to_agent=to_agent,
            task_id=task_id,
        )
        try:
            policy.require_tool(tool)
            result = await operation()
        except (DuctorError, PolicyError, ValidationError) as exc:
            audit_log.event(
                "tool_call",
                outcome="rejected" if isinstance(exc, (PolicyError, ValidationError)) else "failed",
                request_id=request_id,
                tool=tool,
                error_type=type(exc).__name__,
                duration_ms=round((time.monotonic() - started) * 1_000, 2),
            )
            raise ToolError(str(exc)) from None
        audit_log.event(
            "tool_call",
            outcome="completed",
            request_id=request_id,
            tool=tool,
            duration_ms=round((time.monotonic() - started) * 1_000, 2),
        )
        return result

    if "ductor_agents_list" in policy.allowed_tools:

        @mcp.tool(name="ductor_agents_list", structured_output=True)
        async def ductor_agents_list() -> dict[str, Any]:
            """List running Ductor agents visible under this gateway's policy."""

            async def operation() -> dict[str, Any]:
                response = await client.list_agents()
                return {"agents": policy.filter_agents(response.agents)}

            return await run_tool("ductor_agents_list", operation)

    if "ductor_agent_message" in policy.allowed_tools:

        @mcp.tool(name="ductor_agent_message", structured_output=True)
        async def ductor_agent_message(
            from_agent: str,
            to_agent: str,
            message: str,
            new_session: bool = False,
            chat_id: int = 0,
            topic_id: int | None = None,
        ) -> dict[str, Any]:
            """Send a message to a Ductor agent and wait for the response."""

            async def operation() -> dict[str, Any]:
                policy.require_agent(from_agent)
                policy.require_agent(to_agent)
                request = AgentMessageRequest(
                    from_agent=from_agent,
                    to_agent=to_agent,
                    message=message,
                    new_session=new_session,
                    chat_id=chat_id,
                    topic_id=topic_id,
                )
                response = await client.send_message(request)
                return response.model_dump(mode="json")

            return await run_tool(
                "ductor_agent_message",
                operation,
                from_agent=from_agent,
                to_agent=to_agent,
            )

    if "ductor_agent_message_async" in policy.allowed_tools:

        @mcp.tool(name="ductor_agent_message_async", structured_output=True)
        async def ductor_agent_message_async(
            from_agent: str,
            to_agent: str,
            message: str,
            new_session: bool = False,
            summary: str = "",
            chat_id: int = 0,
            topic_id: int | None = None,
            transport: Literal["mcp", "telegram", "tg", "matrix", "mx"] = "mcp",
            reply_to: str | None = None,
            silent: bool = True,
        ) -> dict[str, Any]:
            """Send a Ductor agent message asynchronously and return its task identifier."""

            async def operation() -> dict[str, Any]:
                policy.require_agent(from_agent)
                policy.require_agent(to_agent)
                if reply_to is not None:
                    policy.require_agent(reply_to)
                request = AgentMessageAsyncRequest(
                    from_agent=from_agent,
                    to_agent=to_agent,
                    message=message,
                    new_session=new_session,
                    summary=summary,
                    chat_id=chat_id,
                    topic_id=topic_id,
                    transport=transport,
                    reply_to=reply_to,
                    silent=silent,
                )
                response = await client.send_message_async(request)
                return response.model_dump(mode="json")

            return await run_tool(
                "ductor_agent_message_async",
                operation,
                from_agent=from_agent,
                to_agent=to_agent,
            )

    if "ductor_tasks_create" in policy.allowed_tools:

        @mcp.tool(name="ductor_tasks_create", structured_output=True)
        async def ductor_tasks_create(
            from_agent: str,
            prompt: str,
            name: str = "",
            provider: str | None = None,
            model: str | None = None,
            thinking: str | None = None,
            priority: Literal["interactive", "background", "batch"] = "background",
            chat_id: int = 0,
            topic_id: int | None = None,
        ) -> dict[str, Any]:
            """Create a Ductor background task owned by the specified agent."""

            async def operation() -> dict[str, Any]:
                policy.require_agent(from_agent)
                request = TaskCreateRequest(
                    from_agent=from_agent,
                    prompt=prompt,
                    name=name,
                    provider=provider,
                    model=model,
                    thinking=thinking,
                    priority=priority,
                    chat_id=chat_id,
                    topic_id=topic_id,
                )
                response = await client.create_task(request)
                return response.model_dump(mode="json")

            return await run_tool("ductor_tasks_create", operation, from_agent=from_agent)

    if "ductor_tasks_list" in policy.allowed_tools:

        @mcp.tool(name="ductor_tasks_list", structured_output=True)
        async def ductor_tasks_list(from_agent: str) -> dict[str, Any]:
            """List tasks owned by one allowed Ductor agent."""

            async def operation() -> dict[str, Any]:
                policy.require_agent(from_agent)
                request = TaskListRequest(from_agent=from_agent)
                response = await client.list_tasks(request)
                owned = policy.filter_owned_tasks(response.tasks, from_agent)
                return {"tasks": [task.model_dump(mode="json") for task in owned]}

            return await run_tool("ductor_tasks_list", operation, from_agent=from_agent)

    if "ductor_tasks_resume" in policy.allowed_tools:

        @mcp.tool(name="ductor_tasks_resume", structured_output=True)
        async def ductor_tasks_resume(from_agent: str, task_id: str, prompt: str) -> dict[str, Any]:
            """Resume a completed Ductor task, retaining its owner and context."""

            async def operation() -> dict[str, Any]:
                policy.require_agent(from_agent)
                request = TaskResumeRequest(from_agent=from_agent, task_id=task_id, prompt=prompt)
                response = await client.resume_task(request)
                return response.model_dump(mode="json")

            return await run_tool(
                "ductor_tasks_resume",
                operation,
                from_agent=from_agent,
                task_id=task_id,
            )

    if "ductor_tasks_cancel" in policy.allowed_tools:

        @mcp.tool(name="ductor_tasks_cancel", structured_output=True)
        async def ductor_tasks_cancel(from_agent: str, task_id: str) -> dict[str, Any]:
            """Cancel a running Ductor task owned by the specified agent."""

            async def operation() -> dict[str, Any]:
                policy.require_agent(from_agent)
                request = TaskCancelRequest(from_agent=from_agent, task_id=task_id)
                response = await client.cancel_task(request)
                return response.model_dump(mode="json")

            return await run_tool(
                "ductor_tasks_cancel",
                operation,
                from_agent=from_agent,
                task_id=task_id,
            )

    mcp_app = mcp.streamable_http_app()
    mcp.session_manager.session_idle_timeout = settings.limits.session_idle_seconds

    async def health(_request: Request) -> Response:
        try:
            await client.health()
        except DuctorError as exc:
            audit_log.event("health", outcome="unavailable", error_type=type(exc).__name__)
            return JSONResponse(
                {"status": "unavailable", "upstream": "unavailable"}, status_code=503
            )
        return JSONResponse({"status": "ok", "upstream": "ok"})

    @contextlib.asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        async with client:
            async with mcp.session_manager.run():
                yield

    starlette = Starlette(
        routes=[Route("/health", health, methods=["GET"]), Mount("/", app=mcp_app)],
        lifespan=lifespan,
    )
    edge = GatewayEdgeMiddleware(
        starlette,
        bearer_token=bearer_token,
        rate_limit_per_minute=settings.limits.rate_limit_per_minute,
        max_sessions=settings.limits.max_sessions,
        session_idle_seconds=settings.limits.session_idle_seconds,
        audit=audit_log,
    )
    logging.getLogger(__name__).info("Gateway application configured")
    return GatewayApplication(edge=edge, starlette=starlette, mcp=mcp)
