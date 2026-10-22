"""Part analyses (``pa_``) of completed jobs: create/list/latest per job, read, cancel, delete, files (section 6.4).

``/jobs/{job_id}/part-analyses[/latest]`` live on :data:`jobs_router` (``latest`` is registered before any
``{id}`` sibling), the JSON routes on :data:`router` (Bearer only) and the manifest/GLB/ZIP/mask/thumbnail/preview/
buffer/log routes on :data:`files_router` (header or ``?token=``). Mutating routes answer 503 ``feature_disabled``
when the part-analysis flag is off; GET routes always work. Files of an analysis that has not completed are 409.
"""

from __future__ import annotations

from typing import Annotated, Final

from fastapi import APIRouter, Body, Path, Response, status
from fastapi.responses import FileResponse, PlainTextResponse

from api.deps import AuthGuard, FileAuthGuard
from api.part_deps import PartAnalysisServiceDep
from schemas.common import ERROR_RESPONSES
from schemas.parts import PartAnalysisList, PartAnalysisOptions, PartAnalysisOut

PREFIX = "/part-analyses"
TAGS: list[str] = ["part-analyses"]
_NO_CACHE: Final = "no-cache"
_RESULT_CACHE: Final = "private, max-age=3600"
GLB_MEDIA_TYPE: Final = "model/gltf-binary"
PART_ID_PATTERN: Final = r"^[a-z0-9_.]{1,80}$"

jobs_router = APIRouter(prefix="/jobs", tags=TAGS, dependencies=[AuthGuard], responses=ERROR_RESPONSES)
router = APIRouter(prefix=PREFIX, tags=TAGS, dependencies=[AuthGuard], responses=ERROR_RESPONSES)
files_router = APIRouter(prefix=PREFIX, tags=TAGS, dependencies=[FileAuthGuard], responses=ERROR_RESPONSES)

ROUTERS: Final[tuple[APIRouter, ...]] = (jobs_router, router, files_router)
"""Routers of this module in registration order (``api.part_routes`` mounts them)."""


# --------------------------------------------------------------------------------------------------
# Per job
# --------------------------------------------------------------------------------------------------


@jobs_router.post(
    "/{job_id}/part-analyses", response_model=PartAnalysisOut, status_code=status.HTTP_202_ACCEPTED
)
async def create_part_analysis(
    job_id: str,
    service: PartAnalysisServiceDep,
    body: Annotated[PartAnalysisOptions | None, Body()] = None,
) -> PartAnalysisOut:
    return service.to_out(await service.create(job_id, body))


@jobs_router.get("/{job_id}/part-analyses/latest", response_model=PartAnalysisOut)
async def latest_part_analysis(job_id: str, service: PartAnalysisServiceDep) -> PartAnalysisOut:
    return service.to_out(service.latest(job_id))


@jobs_router.get("/{job_id}/part-analyses", response_model=PartAnalysisList)
async def list_part_analyses(job_id: str, service: PartAnalysisServiceDep) -> PartAnalysisList:
    return PartAnalysisList(items=[service.to_out(run) for run in service.list_for_job(job_id)])


# --------------------------------------------------------------------------------------------------
# JSON routes
# --------------------------------------------------------------------------------------------------


@router.get("/{pa_id}", response_model=PartAnalysisOut)
async def get_part_analysis(pa_id: str, service: PartAnalysisServiceDep) -> PartAnalysisOut:
    return service.to_out(service.get(pa_id))


@router.post("/{pa_id}/cancel", response_model=PartAnalysisOut, status_code=status.HTTP_202_ACCEPTED)
async def cancel_part_analysis(pa_id: str, service: PartAnalysisServiceDep) -> PartAnalysisOut:
    return service.to_out(await service.cancel(pa_id))


@router.delete("/{pa_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_part_analysis(pa_id: str, service: PartAnalysisServiceDep) -> Response:
    await service.delete(pa_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------------------------------
# Files
# --------------------------------------------------------------------------------------------------


@files_router.get("/{pa_id}/manifest", response_class=FileResponse)
async def get_part_manifest(pa_id: str, service: PartAnalysisServiceDep) -> FileResponse:
    return FileResponse(
        service.asset_file(pa_id, "manifest.json"), media_type="application/json",
        headers={"Cache-Control": _NO_CACHE},
    )


@files_router.get("/{pa_id}/model.glb", response_class=FileResponse)
async def get_parts_model(pa_id: str, service: PartAnalysisServiceDep) -> FileResponse:
    return FileResponse(
        service.asset_file(pa_id, "model_parts.glb"), media_type=GLB_MEDIA_TYPE,
        headers={"Cache-Control": _RESULT_CACHE},
    )


@files_router.get("/{pa_id}/export/glb", response_class=FileResponse)
async def export_parts_glb(pa_id: str, service: PartAnalysisServiceDep) -> FileResponse:
    path = service.asset_file(pa_id, "model_parts.glb")
    run = service.get(pa_id)
    return FileResponse(path, media_type=GLB_MEDIA_TYPE, filename=f"img3d_{run.job_id}_parts_{pa_id}.glb")


@files_router.get("/{pa_id}/export/zip", response_class=FileResponse)
async def export_parts_zip(
    pa_id: str, service: PartAnalysisServiceDep, include_base: bool = False
) -> FileResponse:
    run, path = await service.bundle_zip(pa_id, include_base=include_base)
    return FileResponse(path, media_type="application/zip", filename=f"img3d_{run.job_id}_parts_{pa_id}.zip")


@files_router.get("/{pa_id}/parts/{part_id}/model.glb", response_class=FileResponse)
async def get_part_model(
    pa_id: str, part_id: Annotated[str, Path(pattern=PART_ID_PATTERN)], service: PartAnalysisServiceDep
) -> FileResponse:
    _run, path = await service.part_glb(pa_id, part_id)
    return FileResponse(path, media_type=GLB_MEDIA_TYPE, headers={"Cache-Control": _RESULT_CACHE})


@files_router.get("/{pa_id}/parts/{part_id}/export/glb", response_class=FileResponse)
async def export_part_model(
    pa_id: str, part_id: Annotated[str, Path(pattern=PART_ID_PATTERN)], service: PartAnalysisServiceDep
) -> FileResponse:
    run, path = await service.part_glb(pa_id, part_id)
    return FileResponse(path, media_type=GLB_MEDIA_TYPE, filename=f"img3d_{run.job_id}_{part_id}.glb")


@files_router.get("/{pa_id}/masks/{value}", response_class=FileResponse)
async def get_part_mask(
    pa_id: str, value: Annotated[str, Path(pattern=r"^\d{1,5}$")], service: PartAnalysisServiceDep
) -> FileResponse:
    return FileResponse(service.mask_file(pa_id, value), media_type="image/png", headers={"Cache-Control": _RESULT_CACHE})


@files_router.get("/{pa_id}/thumbs/{name}", response_class=FileResponse)
async def get_part_thumbnail(
    pa_id: str, name: Annotated[str, Path(pattern=r"^[a-z0-9_.]{1,90}\.webp$")], service: PartAnalysisServiceDep
) -> FileResponse:
    return FileResponse(
        service.thumbnail_file(pa_id, name), media_type="image/webp", headers={"Cache-Control": _RESULT_CACHE}
    )


@files_router.get("/{pa_id}/segmentation/{name}", response_class=FileResponse)
async def get_part_segmentation(pa_id: str, name: str, service: PartAnalysisServiceDep) -> FileResponse:
    return FileResponse(
        service.segmentation_file(pa_id, name), media_type="image/png", headers={"Cache-Control": _RESULT_CACHE}
    )


@files_router.get("/{pa_id}/previews/{name}", response_class=FileResponse)
async def get_part_preview(
    pa_id: str, name: Annotated[str, Path(pattern=r"^(front|back|left|right)\.png$")], service: PartAnalysisServiceDep
) -> FileResponse:
    return FileResponse(service.preview_file(pa_id, name), media_type="image/png", headers={"Cache-Control": _RESULT_CACHE})


@files_router.get("/{pa_id}/buffers/{buffer_id}", response_class=FileResponse)
async def get_part_buffer(pa_id: str, buffer_id: str, service: PartAnalysisServiceDep) -> FileResponse:
    path, record = service.buffer_file(pa_id, buffer_id)
    return FileResponse(
        path,
        media_type="application/octet-stream",
        headers={"X-Img3d-Dtype": record.dtype, "X-Img3d-Count": str(record.count), "Cache-Control": _RESULT_CACHE},
    )


@files_router.get("/{pa_id}/log", response_class=PlainTextResponse)
async def get_part_log(pa_id: str, service: PartAnalysisServiceDep) -> PlainTextResponse:
    return PlainTextResponse(service.log_tail(pa_id), headers={"Cache-Control": _NO_CACHE})
