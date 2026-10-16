"""Model registry: ``GET /models``, ``GET /models/{id}``, ``POST /models/{id}/load|unload`` (section 6.1).

Bearer auth. ``/models`` never returns absolute paths; ``?include_hidden=true`` adds the hidden (unsupported future)
entries. Load/unload answer 202: the work continues in the background and every state change is announced with
``features.updated``.
"""

from __future__ import annotations

from typing import Annotated, Final

from fastapi import APIRouter, Query, status

from api.deps import AuthGuard
from api.part_deps import ModelRegistryServiceDep
from inference.providers.base import ModelKind
from schemas.common import ERROR_RESPONSES
from schemas.model_registry import ModelList, ModelOut

PREFIX = "/models"
TAGS: list[str] = ["models"]

router = APIRouter(prefix=PREFIX, tags=TAGS, dependencies=[AuthGuard], responses=ERROR_RESPONSES)


@router.get("", response_model=ModelList)
async def list_models(
    models: ModelRegistryServiceDep,
    kind: Annotated[ModelKind | None, Query()] = None,
    include_hidden: Annotated[bool, Query()] = False,
) -> ModelList:
    return models.list_models(kind, include_hidden=include_hidden)


@router.get("/{model_id}", response_model=ModelOut)
async def get_model(model_id: str, models: ModelRegistryServiceDep) -> ModelOut:
    return models.get_model(model_id)


@router.post("/{model_id}/load", response_model=ModelOut, status_code=status.HTTP_202_ACCEPTED)
async def load_model(model_id: str, models: ModelRegistryServiceDep) -> ModelOut:
    return await models.load_model(model_id)


@router.post("/{model_id}/unload", response_model=ModelOut, status_code=status.HTTP_202_ACCEPTED)
async def unload_model(model_id: str, models: ModelRegistryServiceDep) -> ModelOut:
    return await models.unload_model(model_id)


ROUTERS: Final[tuple[APIRouter, ...]] = (router,)
"""Routers of this module in registration order (``api.part_routes`` mounts them)."""
