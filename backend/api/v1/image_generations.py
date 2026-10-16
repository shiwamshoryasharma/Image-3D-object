"""Image generation runs (``ig_``): create, list, read, cancel, delete, candidates, use (section 6.2).

JSON routes live on :data:`router` (Bearer only); the candidate image routes on :data:`files_router` (header or
``?token=``, so ``<img>`` tags work). Mutating routes answer 503 ``feature_disabled`` while
``IMG3D_IMAGE_GENERATION_ENABLED`` (or the part-aware master switch) is off; reads always work.
"""

from __future__ import annotations

import re
from typing import Annotated, Final

from fastapi import APIRouter, Query, Response, status
from fastapi.responses import FileResponse

from api.deps import AuthGuard, FileAuthGuard
from api.part_deps import ImageGenerationServiceDep
from core.exceptions import NotFoundError
from models.enums import JobStatus
from schemas.common import ERROR_RESPONSES
from schemas.image_generation import (
    ImageGenerationList,
    ImageGenerationOut,
    ImageGenerationRequest,
    UseCandidateRequest,
    UseCandidateResponse,
)

PREFIX = "/image-generations"
TAGS: list[str] = ["image-generations"]

router = APIRouter(prefix=PREFIX, tags=TAGS, dependencies=[AuthGuard], responses=ERROR_RESPONSES)
files_router = APIRouter(prefix=PREFIX, tags=TAGS, dependencies=[FileAuthGuard], responses=ERROR_RESPONSES)

ROUTERS: Final[tuple[APIRouter, ...]] = (router, files_router)
"""Routers of this module in registration order (``api.part_routes`` mounts them)."""

_CANDIDATE_INDEX = re.compile(r"^[0-3]$")
_CANDIDATE_CACHE = "private, max-age=3600"


def _candidate_index(ig_id: str, index: str) -> int:
    """The ``{index}`` path segment (``^[0-3]$``); anything else is an unknown candidate (404)."""
    if not _CANDIDATE_INDEX.fullmatch(index):
        raise NotFoundError("Candidate not found", details={"image_generation_id": ig_id, "index": index})
    return int(index)


@router.post("", response_model=ImageGenerationOut, status_code=status.HTTP_202_ACCEPTED)
async def create_image_generation(
    body: ImageGenerationRequest, service: ImageGenerationServiceDep
) -> ImageGenerationOut:
    return service.to_out(await service.create(body))


@router.get("", response_model=ImageGenerationList)
async def list_image_generations(
    service: ImageGenerationServiceDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    status_filter: Annotated[JobStatus | None, Query(alias="status")] = None,
) -> ImageGenerationList:
    runs = service.list_runs(limit=limit, status=status_filter)
    return ImageGenerationList(items=[service.to_out(run) for run in runs])


@router.get("/{ig_id}", response_model=ImageGenerationOut)
async def get_image_generation(ig_id: str, service: ImageGenerationServiceDep) -> ImageGenerationOut:
    return service.to_out(service.get(ig_id))


@router.post("/{ig_id}/cancel", response_model=ImageGenerationOut, status_code=status.HTTP_202_ACCEPTED)
async def cancel_image_generation(ig_id: str, service: ImageGenerationServiceDep) -> ImageGenerationOut:
    return service.to_out(await service.cancel(ig_id))


@router.delete("/{ig_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_image_generation(ig_id: str, service: ImageGenerationServiceDep) -> Response:
    await service.delete(ig_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{ig_id}/candidates/{index}/use", response_model=UseCandidateResponse, status_code=status.HTTP_201_CREATED
)
async def use_candidate(
    ig_id: str, index: str, body: UseCandidateRequest, service: ImageGenerationServiceDep
) -> UseCandidateResponse:
    return await service.use_candidate(ig_id, _candidate_index(ig_id, index), body)


@files_router.get("/{ig_id}/candidates/{index}", response_class=FileResponse)
async def get_candidate_image(ig_id: str, index: str, service: ImageGenerationServiceDep) -> FileResponse:
    path = service.candidate_file(ig_id, _candidate_index(ig_id, index))
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": _CANDIDATE_CACHE})


@files_router.get("/{ig_id}/candidates/{index}/thumbnail", response_class=FileResponse)
async def get_candidate_thumbnail(ig_id: str, index: str, service: ImageGenerationServiceDep) -> FileResponse:
    path = service.candidate_file(ig_id, _candidate_index(ig_id, index), thumbnail=True)
    return FileResponse(path, media_type="image/webp", headers={"Cache-Control": _CANDIDATE_CACHE})
