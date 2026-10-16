"""Auth schemas (contract 7.1)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from models.types import UtcDateTime
from schemas.common import ApiModel, RequestModel


class AuthStatus(ApiModel):
    auth_required: bool
    authenticated: bool
    username: str | None


class LoginRequest(RequestModel):
    username: str = Field(min_length=1, max_length=256)
    password: str = Field(min_length=1, max_length=1024)


class TokenResponse(ApiModel):
    token: str
    token_type: Literal["bearer"] = "bearer"
    expires_at: UtcDateTime
