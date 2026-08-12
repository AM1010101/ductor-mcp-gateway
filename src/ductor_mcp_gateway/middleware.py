"""ASGI bearer authentication, rate limiting, and session accounting."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import time
from collections import defaultdict, deque
from collections.abc import Callable
from typing import Any, cast

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .audit import AuditLogger


class GatewayEdgeMiddleware:
    """Protect every HTTP route and bound requests before they enter MCP."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        bearer_token: str,
        rate_limit_per_minute: int,
        max_sessions: int,
        session_idle_seconds: float,
        audit: AuditLogger,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._app = app
        self._expected_digest = hashlib.sha256(bearer_token.encode()).digest()
        self._rate_limit = rate_limit_per_minute
        self._max_sessions = max_sessions
        self._session_idle_seconds = session_idle_seconds
        self._audit = audit
        self._clock = clock
        self._requests: dict[str, deque[float]] = defaultdict(deque)
        self._sessions: dict[str, float] = {}
        self._pending_sessions = 0
        self._lock = asyncio.Lock()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        peer = _peer(scope)
        if not self._authorized(scope):
            self._audit.event("authentication", outcome="rejected", peer=peer)
            await JSONResponse(
                {"error": "unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )(scope, receive, send)
            return

        self._audit.event("authentication", outcome="accepted", peer=peer)
        if not await self._within_rate_limit(peer):
            self._audit.event("rate_limit", outcome="rejected", peer=peer)
            await JSONResponse(
                {"error": "rate limit exceeded"},
                status_code=429,
                headers={"Retry-After": "60"},
            )(scope, receive, send)
            return

        reserved = False
        session_id = _header(scope, b"mcp-session-id")
        is_mcp = scope.get("path") == "/mcp"
        if is_mcp:
            reserved = await self._reserve_or_touch_session(scope, session_id)
            if reserved is False and session_id is None and scope["method"] == "POST":
                self._audit.event("session_limit", outcome="rejected", peer=peer)
                await JSONResponse({"error": "MCP session limit reached"}, status_code=503)(
                    scope, receive, send
                )
                return

        created_session: str | None = None

        async def capture_start(message: Message) -> None:
            nonlocal created_session
            if message["type"] == "http.response.start":
                for key, value in message.get("headers", []):
                    if key.lower() == b"mcp-session-id":
                        created_session = value.decode("ascii", errors="ignore")
                        break
            await send(message)

        try:
            await self._app(scope, receive, capture_start)
        finally:
            if is_mcp:
                await self._finish_session(
                    reserved=reserved,
                    created_session=created_session,
                    request_session=session_id,
                    method=str(scope["method"]),
                )

    def _authorized(self, scope: Scope) -> bool:
        authorization = _header(scope, b"authorization") or ""
        scheme, separator, value = authorization.partition(" ")
        candidate = value if separator and scheme.lower() == "bearer" else ""
        candidate_digest = hashlib.sha256(candidate.encode()).digest()
        return hmac.compare_digest(candidate_digest, self._expected_digest)

    async def _within_rate_limit(self, peer: str) -> bool:
        now = self._clock()
        async with self._lock:
            seen = self._requests[peer]
            while seen and seen[0] <= now - 60:
                seen.popleft()
            if len(seen) >= self._rate_limit:
                return False
            seen.append(now)
            return True

    async def _reserve_or_touch_session(self, scope: Scope, session_id: str | None) -> bool:
        now = self._clock()
        async with self._lock:
            self._prune_sessions(now)
            if session_id is not None:
                if session_id in self._sessions:
                    self._sessions[session_id] = now
                return True
            if scope["method"] != "POST":
                return True
            if len(self._sessions) + self._pending_sessions >= self._max_sessions:
                return False
            self._pending_sessions += 1
            return True

    async def _finish_session(
        self,
        *,
        reserved: bool,
        created_session: str | None,
        request_session: str | None,
        method: str,
    ) -> None:
        now = self._clock()
        async with self._lock:
            if reserved and request_session is None and method == "POST":
                self._pending_sessions = max(0, self._pending_sessions - 1)
            if created_session:
                self._sessions[created_session] = now
            if method == "DELETE" and request_session:
                self._sessions.pop(request_session, None)

    def _prune_sessions(self, now: float) -> None:
        cutoff = now - self._session_idle_seconds
        for session_id, last_seen in list(self._sessions.items()):
            if last_seen <= cutoff:
                self._sessions.pop(session_id, None)


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key.lower() == name:
            return cast(bytes, value).decode("latin-1")
    return None


def _peer(scope: Scope) -> str:
    client: Any = scope.get("client")
    if isinstance(client, tuple) and client:
        return str(client[0])
    return "unknown"
