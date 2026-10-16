"""Shared schema pieces."""

from __future__ import annotations

from typing import Any, Final

from pydantic import BaseModel, ConfigDict

API_V1_PREFIX: Final = "/api/v1"


class ApiModel(BaseModel):
    """Base for response models."""

    model_config = ConfigDict(extra="ignore", from_attributes=True)


class RequestModel(BaseModel):
    """Base for request bodies: unknown fields are rejected (422)."""

    model_config = ConfigDict(extra="forbid")


class ErrorBody(ApiModel):
    code: str
    message: str
    details: Any | None = None
    request_id: str | None = None


class ErrorResponse(ApiModel):
    error: ErrorBody


ERROR_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    code: {"model": ErrorResponse} for code in (401, 404, 409, 413, 415, 422, 429, 500, 503)
}
"""OpenAPI documentation of the uniform error body."""
