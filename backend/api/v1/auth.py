"""Auth endpoints (public): status, login, logout."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status

from api.deps import AuthServiceDep, bearer_token
from schemas.auth import AuthStatus, LoginRequest, TokenResponse
from schemas.common import ERROR_RESPONSES

router = APIRouter(prefix="/auth", tags=["auth"], responses=ERROR_RESPONSES)


@router.get("/status", response_model=AuthStatus)
async def auth_status(request: Request, auth: AuthServiceDep) -> AuthStatus:
    return auth.status(bearer_token(request))


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, request: Request, auth: AuthServiceDep) -> TokenResponse:
    client = request.client.host if request.client else "unknown"
    issued = auth.login(body.username, body.password, client=client)
    return TokenResponse(token=issued.token, token_type="bearer", expires_at=issued.expires_at)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def logout(request: Request, auth: AuthServiceDep) -> Response:
    auth.logout(bearer_token(request))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
