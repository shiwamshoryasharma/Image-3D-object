"""Capability discovery: ``GET /features`` (part-aware DESIGN.md section 6.1).

Bearer auth. The same document is pushed as the ``features.updated`` WebSocket event (debounced). A legacy backend
answers 404 here (image-to-3D only).
"""

from __future__ import annotations

from typing import Final

from fastapi import APIRouter

from api.deps import AuthGuard
from api.part_deps import FeatureServiceDep
from schemas.common import ERROR_RESPONSES
from schemas.features import FeaturesOut

TAGS: list[str] = ["features"]

router = APIRouter(tags=TAGS, dependencies=[AuthGuard], responses=ERROR_RESPONSES)


@router.get("/features", response_model=FeaturesOut, response_model_by_alias=True)
async def get_features(features: FeatureServiceDep) -> FeaturesOut:
    return features.features()


ROUTERS: Final[tuple[APIRouter, ...]] = (router,)
"""Routers of this module in registration order (``api.part_routes`` mounts them)."""
