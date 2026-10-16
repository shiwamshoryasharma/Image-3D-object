"""System status and engine control."""

from __future__ import annotations

from fastapi import APIRouter, status

from api.deps import AuthGuard, SystemServiceDep
from schemas.common import ERROR_RESPONSES
from schemas.system import SystemStatus

router = APIRouter(prefix="/system", tags=["system"], dependencies=[AuthGuard], responses=ERROR_RESPONSES)


@router.get("", response_model=SystemStatus)
async def get_system_status(system: SystemServiceDep) -> SystemStatus:
    return await system.status()


@router.post("/engine/load", response_model=SystemStatus, status_code=status.HTTP_202_ACCEPTED)
async def load_engine(system: SystemServiceDep) -> SystemStatus:
    """Start loading the model in the background (no-op if it is loaded or loading)."""
    return await system.load_engine()


@router.post("/engine/unload", response_model=SystemStatus)
async def unload_engine(system: SystemServiceDep) -> SystemStatus:
    """Unload the model and free GPU memory (409 while a job is running)."""
    return await system.unload_engine()
