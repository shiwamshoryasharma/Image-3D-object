"""Job outputs: result metadata, GLB (viewer + download) and preview image."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import FileResponse

from api.deps import AuthGuard, FileAuthGuard, ResultServiceDep
from schemas.common import ERROR_RESPONSES
from schemas.jobs import JobResultOut

router = APIRouter(prefix="/jobs", tags=["results"], dependencies=[AuthGuard], responses=ERROR_RESPONSES)
files_router = APIRouter(prefix="/jobs", tags=["results"], dependencies=[FileAuthGuard], responses=ERROR_RESPONSES)

_RESULT_CACHE = "private, max-age=3600"
_NO_CACHE = "no-cache"


@router.get("/{job_id}/result", response_model=JobResultOut)
async def get_job_result(job_id: str, results: ResultServiceDep) -> JobResultOut:
    return results.get_result(job_id)


@files_router.get("/{job_id}/preview", response_class=FileResponse)
async def get_job_preview(job_id: str, results: ResultServiceDep) -> FileResponse:
    stored = results.preview_file(job_id)
    return FileResponse(stored.path, media_type=stored.media_type, headers={"Cache-Control": _NO_CACHE})


@files_router.get("/{job_id}/model.glb", response_class=FileResponse)
async def get_job_model(job_id: str, results: ResultServiceDep) -> FileResponse:
    stored = results.model_file(job_id)
    return FileResponse(
        stored.path,
        media_type=stored.media_type,
        filename=stored.filename,
        content_disposition_type="inline",
        headers={"Cache-Control": _RESULT_CACHE},
    )


@files_router.get("/{job_id}/export/glb", response_class=FileResponse)
async def export_job_glb(job_id: str, results: ResultServiceDep) -> FileResponse:
    stored = results.model_file(job_id)
    return FileResponse(
        stored.path,
        media_type=stored.media_type,
        filename=stored.filename,
        content_disposition_type="attachment",
        headers={"Cache-Control": _RESULT_CACHE},
    )
