"""Assembles the part-aware routers (part-aware DESIGN.md section 6; mounted under ``/api/v1``).

Each ``api/v1`` module of the part-aware layer exposes ``ROUTERS``: its routers in registration order (JSON routers
with ``AuthGuard``, file routers with ``FileAuthGuard``; static routes before their ``{id}`` siblings). A slice adds
routes - or another router appended to its module's ``ROUTERS`` - without touching this file.
"""

from __future__ import annotations

from types import ModuleType
from typing import Final

from fastapi import APIRouter

from api.v1 import features, image_generations, model_registry, part_analyses, segmentations, workflows

PART_AWARE_MODULES: Final[tuple[ModuleType, ...]] = (
    features,
    model_registry,
    image_generations,
    segmentations,
    part_analyses,
    workflows,
)


def build_part_aware_router() -> APIRouter:
    router = APIRouter()
    for module in PART_AWARE_MODULES:
        for sub_router in module.ROUTERS:
            router.include_router(sub_router)
    return router
