"""Exception handlers producing the uniform error body for every failure::

    {"error": {"code": str, "message": str, "details": Any | None, "request_id": str | None}}
"""

from __future__ import annotations

import logging
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import Scope

from core.exceptions import AppError
from core.logging import request_id_var

logger = logging.getLogger("img3d.errors")

_STATUS_CODES: dict[int, str] = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    406: "not_acceptable",
    409: "conflict",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "validation_error",
    429: "too_many_requests",
    500: "internal_error",
    503: "service_unavailable",
}

INTERNAL_ERROR_MESSAGE = "Internal server error"


def request_id_from_scope(scope: Scope) -> str | None:
    state = scope.get("state") or {}
    return state.get("request_id") or request_id_var.get()


def error_payload(code: str, message: str, details: Any | None, request_id: str | None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details, "request_id": request_id}}


def error_response(
    status_code: int,
    code: str,
    message: str,
    details: Any | None = None,
    *,
    request_id: str | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=error_payload(code, message, details, request_id),
        headers=headers,
    )


def code_for_status(status_code: int) -> str:
    return _STATUS_CODES.get(status_code, "internal_error" if status_code >= 500 else "error")


def _sanitize_validation_errors(errors: Any) -> list[dict[str, Any]]:
    """Strip ``input`` (may contain passwords) and make ``ctx`` JSON-safe."""
    sanitized: list[dict[str, Any]] = []
    for err in errors or []:
        item: dict[str, Any] = {
            "loc": [str(part) if not isinstance(part, int) else part for part in err.get("loc", ())],
            "msg": str(err.get("msg", "")),
            "type": str(err.get("type", "")),
        }
        ctx = err.get("ctx")
        if isinstance(ctx, dict) and ctx:
            item["ctx"] = {
                str(k): v if isinstance(v, (str, int, float, bool)) or v is None else str(v) for k, v in ctx.items()
            }
        sanitized.append(item)
    return sanitized


# --------------------------------------------------------------------------------------------------
# Handlers
# --------------------------------------------------------------------------------------------------


async def app_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    if exc.status_code >= 500:
        logger.error("%s: %s", exc.code, exc.message, exc_info=exc)
    else:
        logger.info("%s %s -> %d %s: %s", request.method, request.url.path, exc.status_code, exc.code, exc.message)
    headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
    return error_response(
        exc.status_code,
        exc.code,
        exc.message,
        exc.details,
        request_id=request_id_from_scope(request.scope),
        headers=headers,
    )


async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    details = _sanitize_validation_errors(exc.errors())
    logger.info("%s %s -> 422 validation_error (%d issue(s))", request.method, request.url.path, len(details))
    return error_response(
        422,
        "validation_error",
        "Request validation failed",
        details,
        request_id=request_id_from_scope(request.scope),
    )


async def http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    status_code = exc.status_code
    if isinstance(exc.detail, str) and exc.detail:
        message = exc.detail
    else:
        try:
            message = HTTPStatus(status_code).phrase
        except ValueError:
            message = "Error"
    headers = dict(exc.headers) if exc.headers else None
    if status_code == 401:
        headers = {**(headers or {}), "WWW-Authenticate": "Bearer"}
    return error_response(
        status_code,
        code_for_status(status_code),
        message,
        None,
        request_id=request_id_from_scope(request.scope),
        headers=headers,
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = request_id_from_scope(request.scope)
    logger.error("Unhandled exception on %s %s", request.method, request.url.path, exc_info=exc)
    headers = {"X-Request-ID": request_id} if request_id else None
    return error_response(500, "internal_error", INTERNAL_ERROR_MESSAGE, None, request_id=request_id, headers=headers)


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
