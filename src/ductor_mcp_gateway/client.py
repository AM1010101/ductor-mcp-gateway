"""Portable, typed async client for Ductor's internal HTTP API.

This module deliberately performs no environment or configuration-file reads.
Callers inject all transport settings and every request value explicitly.
"""

from __future__ import annotations

import json
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from .errors import (
    DuctorProtocolError,
    DuctorUnavailableError,
    DuctorUpstreamError,
)
from .models import (
    AgentMessageAsyncRequest,
    AgentMessageRequest,
    AgentMessageResponse,
    AgentsResponse,
    AsyncMessageResponse,
    HealthResponse,
    TaskCancelRequest,
    TaskCancelResponse,
    TaskCreateRequest,
    TaskListRequest,
    TaskMutationResponse,
    TaskResumeRequest,
    TasksResponse,
)

ResponseT = TypeVar("ResponseT", bound=BaseModel)


class DuctorClient:
    """A small typed adapter over Ductor's internal aiohttp API."""

    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float,
        max_response_bytes: int,
        bearer_token: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._max_response_bytes = max_response_bytes
        self._owns_client = http_client is None
        self._headers = {"Accept": "application/json"}
        if bearer_token:
            self._headers["Authorization"] = f"Bearer {bearer_token}"
        self._http = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
        )

    async def __aenter__(self) -> DuctorClient:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if self._owns_client:
            await self._http.aclose()

    async def list_agents(self) -> AgentsResponse:
        return await self._request("GET", "/interagent/agents", AgentsResponse)

    async def health(self) -> HealthResponse:
        return await self._request("GET", "/interagent/health", HealthResponse)

    async def send_message(self, request: AgentMessageRequest) -> AgentMessageResponse:
        return await self._request(
            "POST",
            "/interagent/send",
            AgentMessageResponse,
            json_body=request.model_dump(by_alias=True, exclude_none=True),
        )

    async def send_message_async(self, request: AgentMessageAsyncRequest) -> AsyncMessageResponse:
        return await self._request(
            "POST",
            "/interagent/send_async",
            AsyncMessageResponse,
            json_body=request.model_dump(by_alias=True, exclude_none=True),
        )

    async def create_task(self, request: TaskCreateRequest) -> TaskMutationResponse:
        return await self._request(
            "POST",
            "/tasks/create",
            TaskMutationResponse,
            json_body=request.model_dump(by_alias=True, exclude_none=True),
        )

    async def list_tasks(self, request: TaskListRequest) -> TasksResponse:
        return await self._request(
            "GET",
            "/tasks/list",
            TasksResponse,
            query={"from": request.from_agent},
        )

    async def resume_task(self, request: TaskResumeRequest) -> TaskMutationResponse:
        return await self._request(
            "POST",
            "/tasks/resume",
            TaskMutationResponse,
            json_body=request.model_dump(by_alias=True, exclude_none=True),
        )

    async def cancel_task(self, request: TaskCancelRequest) -> TaskCancelResponse:
        return await self._request(
            "POST",
            "/tasks/cancel",
            TaskCancelResponse,
            json_body=request.model_dump(by_alias=True, exclude_none=True),
        )

    async def _request(
        self,
        method: str,
        path: str,
        response_type: type[ResponseT],
        *,
        json_body: dict[str, object] | None = None,
        query: dict[str, str] | None = None,
    ) -> ResponseT:
        try:
            async with self._http.stream(
                method,
                f"{self._base_url}{path}",
                json=json_body,
                params=query,
                headers=self._headers,
            ) as response:
                content_length = response.headers.get("content-length")
                if content_length is not None and int(content_length) > self._max_response_bytes:
                    raise DuctorProtocolError(
                        "Ductor upstream response exceeded the configured limit"
                    )
                raw = await response.aread()
        except DuctorProtocolError:
            raise
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise DuctorUnavailableError("Ductor upstream is unavailable") from exc
        except (ValueError, OverflowError) as exc:
            raise DuctorProtocolError("Ductor upstream sent an invalid Content-Length") from exc

        if len(raw) > self._max_response_bytes:
            raise DuctorProtocolError("Ductor upstream response exceeded the configured limit")
        if not 200 <= response.status_code < 300:
            raise DuctorUpstreamError(response.status_code)
        try:
            payload = json.loads(raw)
            return response_type.model_validate(payload)
        except (json.JSONDecodeError, UnicodeDecodeError, ValidationError) as exc:
            raise DuctorProtocolError("Ductor upstream returned malformed JSON data") from exc
