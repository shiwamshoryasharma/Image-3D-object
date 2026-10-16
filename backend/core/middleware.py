"""Pure-ASGI middleware: request id, access log, security headers and last-resort error handling.

Implemented without ``BaseHTTPMiddleware`` so streaming responses (GLB downloads) and WebSockets are untouched.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Final

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from core.error_handlers import INTERNAL_ERROR_MESSAGE, error_response
from core.logging import request_id_var
from utils.ids import new_request_id

access_logger = logging.getLogger("img3d.access")
error_logger = logging.getLogger("img3d.errors")

REQUEST_ID_HEADER: Final = "X-Request-ID"
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_QUIET_PATHS: Final = frozenset({"/api/v1/health"})

SECURITY_HEADERS: Final[dict[str, str]] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


def _incoming_request_id(scope: Scope) -> str | None:
    for name, value in scope.get("headers") or ():
        if name == b"x-request-id":
            try:
                candidate = value.decode("latin-1").strip()
            except UnicodeDecodeError:  # pragma: no cover - latin-1 never fails
                return None
            return candidate if _VALID_REQUEST_ID.match(candidate) else None
    return None


class RequestContextMiddleware:
    """Assigns a request id (``X-Request-ID`` in/out), writes the access log (path only - never the query
    string, which may carry a token), adds security headers and converts unhandled exceptions into the
    standard 500 error body."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        request_id = _incoming_request_id(scope) or new_request_id()
        scope.setdefault("state", {})["request_id"] = request_id
        token = request_id_var.set(request_id)

        if scope["type"] == "websocket":
            try:
                await self.app(scope, receive, send)
            finally:
                request_id_var.reset(token)
            return

        started = time.perf_counter()
        status_code = 500
        response_started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status_code = int(message["status"])
                headers = MutableHeaders(scope=message)
                headers[REQUEST_ID_HEADER] = request_id
                for name, value in SECURITY_HEADERS.items():
                    if name not in headers:
                        headers[name] = value
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:  # noqa: BLE001 - last-resort handler
            error_logger.error(
                "Unhandled exception on %s %s", scope.get("method"), scope.get("path"), exc_info=exc
            )
            status_code = 500
            if not response_started:
                response = error_response(500, "internal_error", INTERNAL_ERROR_MESSAGE, request_id=request_id)
                await response(scope, receive, send_wrapper)
        finally:
            duration_ms = (time.perf_counter() - started) * 1000.0
            path = scope.get("path", "")
            level = logging.DEBUG if path in _QUIET_PATHS or not path.startswith("/api/") else logging.INFO
            if status_code >= 500:
                level = logging.WARNING
            client = scope.get("client")
            access_logger.log(
                level,
                "%s %s -> %d (%.1f ms)",
                scope.get("method"),
                path,
                status_code,
                duration_ms,
                extra={"client": f"{client[0]}:{client[1]}" if client else None},
            )
            request_id_var.reset(token)
