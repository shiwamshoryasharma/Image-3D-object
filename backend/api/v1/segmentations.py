"""Segmentation runs (``sg_``): create, list, read, cancel, delete, overlay/labels/masks/source (section 6.3).

JSON routes live on :data:`router` (Bearer only); the image routes on :data:`files_router` (header or ``?token=``).
Mutating routes answer 503 ``feature_disabled`` when the segmentation flag is off; GET routes always work.
"""

from __future__ import annotations

from typing import Annotated, Final

from fastapi import APIRouter, Path, Query, Response, status
from fastapi.responses import FileResponse

from api.deps import AuthGuard, FileAuthGuard
from api.part_deps import SegmentationServiceDep
from core.exceptions import ValidationAppError
from models.enums import JobStatus
from schemas.common import ERROR_RESPONSES
from schemas.segmentation import SegmentationList, SegmentationOut, SegmentationRequest

PREFIX = "/segmentations"
TAGS: list[str] = ["segmentations"]
DEFAULT_LIST_LIMIT: Final = 20
MAX_LIST_LIMIT: Final = 100
_NO_CACHE: Final = "no-cache"
_RESULT_CACHE: Final = "private, max-age=3600"

router = APIRouter(prefix=PREFIX, tags=TAGS, dependencies=[AuthGuard], responses=ERROR_RESPONSES)
files_router = APIRouter(prefix=PREFIX, tags=TAGS, dependencies=[FileAuthGuard], responses=ERROR_RESPONSES)

ROUTERS: Final[tuple[APIRouter, ...]] = (router, files_router)
"""Routers of this module in registration order (``api.part_routes`` mounts them)."""


def _parse_status(value: str | None) -> JobStatus | None:
    if value is None or not value.strip():
        return None
    try:
        return JobStatus(value.strip())
    except ValueError as exc:
        raise ValidationAppError(
            f"Invalid status filter '{value}'",
            details=[
                {
                    "loc": ["query", "status"],
                    "msg": f"Input should be one of: {', '.join(s.value for s in JobStatus)}",
                    "type": "enum",
                }
            ],
        ) from exc


@router.post("", response_model=SegmentationOut, status_code=status.HTTP_202_ACCEPTED)
async def create_segmentation(body: SegmentationRequest, service: SegmentationServiceDep) -> SegmentationOut:
    run = await service.create(body)
    return service.to_out(run)


@router.get("", response_model=SegmentationList)
async def list_segmentations(
    service: SegmentationServiceDep,
    limit: Annotated[int, Query(ge=1, le=MAX_LIST_LIMIT)] = DEFAULT_LIST_LIMIT,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    reference_set_id: str | None = None,
    job_id: str | None = None,
) -> SegmentationList:
    items = service.list_runs(
        limit=limit, status=_parse_status(status_filter), reference_set_id=reference_set_id, job_id=job_id
    )
    return SegmentationList(items=[service.to_out(run) for run in items])


@router.get("/{sg_id}", response_model=SegmentationOut)
async def get_segmentation(sg_id: str, service: SegmentationServiceDep) -> SegmentationOut:
    return service.to_out(service.get(sg_id))


@router.post("/{sg_id}/cancel", response_model=SegmentationOut, status_code=status.HTTP_202_ACCEPTED)
async def cancel_segmentation(sg_id: str, service: SegmentationServiceDep) -> SegmentationOut:
    return service.to_out(await service.cancel(sg_id))


@router.delete("/{sg_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_segmentation(sg_id: str, service: SegmentationServiceDep) -> Response:
    await service.delete(sg_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@files_router.get("/{sg_id}/overlay", response_class=FileResponse)
async def get_segmentation_overlay(sg_id: str, service: SegmentationServiceDep) -> FileResponse:
    return FileResponse(service.file(sg_id, "overlay"), media_type="image/png", headers={"Cache-Control": _RESULT_CACHE})


@files_router.get("/{sg_id}/labels", response_class=FileResponse)
async def get_segmentation_labels(sg_id: str, service: SegmentationServiceDep) -> FileResponse:
    return FileResponse(service.file(sg_id, "labels"), media_type="image/png", headers={"Cache-Control": _RESULT_CACHE})


@files_router.get("/{sg_id}/masks/{value}", response_class=FileResponse)
async def get_segmentation_mask(
    sg_id: str,
    value: Annotated[str, Path(pattern=r"^\d{1,5}$")],
    service: SegmentationServiceDep,
) -> FileResponse:
    return FileResponse(
        service.file(sg_id, f"masks/{value}"), media_type="image/png", headers={"Cache-Control": _RESULT_CACHE}
    )


@files_router.get("/{sg_id}/source", response_class=FileResponse)
async def get_segmentation_source(sg_id: str, service: SegmentationServiceDep) -> FileResponse:
    return FileResponse(service.file(sg_id, "source"), media_type="image/png", headers={"Cache-Control": _NO_CACHE})
