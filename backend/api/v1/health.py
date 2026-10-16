"""``GET /health`` (public)."""

from __future__ import annotations

from fastapi import APIRouter

from api.deps import SystemServiceDep
from schemas.system import HealthResponse

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthResponse, summary="Liveness probe (public)")
async def health(system: SystemServiceDep) -> HealthResponse:
    return system.health()
