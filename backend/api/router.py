"""Assembles the ``/api/v1`` router."""

from __future__ import annotations

from fastapi import APIRouter

from api.part_routes import build_part_aware_router
from api.v1 import auth, blender, health, jobs, references, results, system, tunings, ws
from schemas.common import API_V1_PREFIX


def build_api_router() -> APIRouter:
    api = APIRouter(prefix=API_V1_PREFIX)
    api.include_router(health.router)
    api.include_router(auth.router)
    api.include_router(system.router)
    api.include_router(references.router)
    api.include_router(references.upload_router)
    api.include_router(references.files_router)
    api.include_router(jobs.router)
    api.include_router(results.router)
    api.include_router(results.files_router)
    api.include_router(blender.router)
    api.include_router(tunings.jobs_router)
    api.include_router(tunings.router)
    api.include_router(tunings.files_router)
    api.include_router(build_part_aware_router())
    api.include_router(ws.router)
    return api
