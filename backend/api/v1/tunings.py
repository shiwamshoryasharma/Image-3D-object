"""Auto Mesh Tuner runs: create per job, list, inspect, cancel, delete, report/log/model/preview files."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Response, status
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse

from api.deps import AuthGuard, FileAuthGuard, MeshTunerServiceDep
from schemas.common import ERROR_RESPONSES
from schemas.mesh_tuner import TuningOptionsIn, TuningRunList, TuningRunOut

jobs_router = APIRouter(prefix="/jobs", tags=["tunings"], dependencies=[AuthGuard], responses=ERROR_RESPONSES)
router = APIRouter(prefix="/tunings", tags=["tunings"], dependencies=[AuthGuard], responses=ERROR_RESPONSES)
files_router = APIRouter(prefix="/tunings", tags=["tunings"], dependencies=[FileAuthGuard], responses=ERROR_RESPONSES)

_RESULT_CACHE = "private, max-age=3600"  # per-run URLs; a completed run's files never change
_NO_CACHE = "no-cache"


@jobs_router.post("/{job_id}/tunings", response_model=TuningRunOut, status_code=status.HTTP_202_ACCEPTED)
async def create_tuning(
    job_id: str,
    tuner: MeshTunerServiceDep,
    options: Annotated[TuningOptionsIn | None, Body()] = None,
) -> TuningRunOut:
    """Queue an Auto Tune run (409 job not completed, 503 ``blender_unavailable``, 429 queue full)."""
    return tuner.to_out(await tuner.create_run(job_id, options))


@jobs_router.get("/{job_id}/tunings", response_model=TuningRunList)
async def list_tunings(job_id: str, tuner: MeshTunerServiceDep) -> TuningRunList:
    return TuningRunList(items=[tuner.to_out(run) for run in tuner.list_runs(job_id)])


@router.get("/{tuning_id}", response_model=TuningRunOut)
async def get_tuning(tuning_id: str, tuner: MeshTunerServiceDep) -> TuningRunOut:
    return tuner.to_out(tuner.get_run(tuning_id))


@router.post("/{tuning_id}/cancel", response_model=TuningRunOut, status_code=status.HTTP_202_ACCEPTED)
async def cancel_tuning(tuning_id: str, tuner: MeshTunerServiceDep) -> TuningRunOut:
    """Queued -> cancelled immediately; running -> Blender is killed and the run becomes ``cancelled``."""
    return tuner.to_out(await tuner.cancel_run(tuning_id))


@router.delete("/{tuning_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_tuning(tuning_id: str, tuner: MeshTunerServiceDep) -> Response:
    await tuner.delete_run(tuning_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@files_router.get("/{tuning_id}/report")
async def get_tuning_report(tuning_id: str, tuner: MeshTunerServiceDep) -> JSONResponse:
    report: Any = await tuner.report(tuning_id)
    return JSONResponse(report, headers={"Cache-Control": _NO_CACHE})


@files_router.get("/{tuning_id}/model.glb", response_class=FileResponse)
async def get_tuned_model(tuning_id: str, tuner: MeshTunerServiceDep) -> FileResponse:
    stored = tuner.model_file(tuning_id)
    return FileResponse(
        stored.path,
        media_type=stored.media_type,
        filename=stored.filename,
        content_disposition_type="inline",
        headers={"Cache-Control": _RESULT_CACHE},
    )


@files_router.get("/{tuning_id}/export/glb", response_class=FileResponse)
async def export_tuned_glb(tuning_id: str, tuner: MeshTunerServiceDep) -> FileResponse:
    stored = tuner.model_file(tuning_id, attachment=True)
    return FileResponse(
        stored.path,
        media_type=stored.media_type,
        filename=stored.filename,
        content_disposition_type="attachment",
        headers={"Cache-Control": _RESULT_CACHE},
    )


@files_router.get("/{tuning_id}/previews/{name}", response_class=FileResponse)
async def get_tuning_preview(tuning_id: str, name: str, tuner: MeshTunerServiceDep) -> FileResponse:
    """Preview render; ``name`` must match ``^[a-z0-9_]+\\.png$``."""
    stored = tuner.preview_file(tuning_id, name)
    return FileResponse(stored.path, media_type=stored.media_type, headers={"Cache-Control": _RESULT_CACHE})


@files_router.get("/{tuning_id}/log", response_class=PlainTextResponse)
async def get_tuning_log(tuning_id: str, tuner: MeshTunerServiceDep) -> PlainTextResponse:
    """Tail (<= 256 KB) of the raw Blender output (live while the run is in progress)."""
    text = await tuner.log_tail(tuning_id)
    return PlainTextResponse(text, headers={"Cache-Control": _NO_CACHE})
