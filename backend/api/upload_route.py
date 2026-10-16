"""Route class enforcing the upload size limit while the request body streams in.

FastAPI parses multipart bodies before dependencies run, so this route class:

1. authenticates *before* reading the body (no anonymous 25 MB uploads),
2. rejects a declared ``Content-Length`` above the limit immediately (413),
3. counts the bytes actually received and aborts with 413 as soon as the limit is exceeded
   (covers chunked uploads and lying ``Content-Length`` headers).

The endpoint additionally enforces the exact per-file limit while reading the file.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Request, Response
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException
from starlette.types import Message, Receive

from api.deps import get_container, request_token
from core.exceptions import PayloadTooLargeError

MULTIPART_OVERHEAD_BYTES = 256 * 1024
"""Allowance for multipart boundaries, part headers and small form fields."""


def _too_large_message(max_upload_mb: int) -> str:
    return f"Upload exceeds the maximum size of {max_upload_mb} MB"


def limited_receive(receive: Receive, max_bytes: int, message: str) -> Receive:
    received = 0

    async def wrapper() -> Message:
        nonlocal received
        msg = await receive()
        if msg["type"] == "http.request":
            received += len(msg.get("body", b""))
            if received > max_bytes:
                # HTTPException is re-raised untouched by FastAPI's body parsing (other errors become 400).
                raise HTTPException(status_code=413, detail=message)
        return msg

    return wrapper


class LimitedUploadRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def limited_handler(request: Request) -> Response:
            container = get_container(request)
            container.auth_service.authorize(request_token(request, allow_query=False))

            settings = container.settings
            max_body = settings.max_upload_bytes + MULTIPART_OVERHEAD_BYTES
            message = _too_large_message(settings.max_upload_mb)
            declared = request.headers.get("content-length")
            if declared is not None and declared.isdigit() and int(declared) > max_body:
                raise PayloadTooLargeError(message, details={"max_upload_mb": settings.max_upload_mb})

            limited = Request(request.scope, receive=limited_receive(request.receive, max_body, message))
            return await handler(limited)

        return limited_handler
