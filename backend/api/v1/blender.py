"""Blender runtime for the Auto Mesh Tuner: status, detection, selection, official release and download."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, status

from api.deps import AuthGuard, BlenderRuntimeDep
from schemas.common import ERROR_RESPONSES
from schemas.mesh_tuner import BlenderReleaseOut, BlenderRuntimeOut, DownloadRequestIn, RuntimeSelectionIn

router = APIRouter(prefix="/blender", tags=["blender"], dependencies=[AuthGuard], responses=ERROR_RESPONSES)


@router.get("/runtime", response_model=BlenderRuntimeOut)
async def get_runtime(runtime: BlenderRuntimeDep) -> dict[str, Any]:
    """Detected installations, the selected runtime and download state (detection starts lazily if needed)."""
    return await runtime.status()


@router.post("/runtime/detect", response_model=BlenderRuntimeOut)
async def detect_runtime(runtime: BlenderRuntimeDep) -> dict[str, Any]:
    """Force a re-scan (executes every candidate's ``--version``; runs in a worker thread)."""
    return await runtime.detect(force=True)


@router.put("/runtime/selection", response_model=BlenderRuntimeOut)
async def select_runtime(body: RuntimeSelectionIn, runtime: BlenderRuntimeDep) -> dict[str, Any]:
    """Use System / Embedded / Auto, optionally pinning one executable (404 not found, 422 incompatible)."""
    return await runtime.select(body.mode, body.executable)


@router.post("/runtime/release", response_model=BlenderReleaseOut)
async def resolve_release(runtime: BlenderRuntimeDep) -> dict[str, Any]:
    """Resolve the official latest portable release for this machine (502 if it cannot be resolved)."""
    return await runtime.resolve_release(force=True)


@router.post("/runtime/download", response_model=BlenderRuntimeOut, status_code=status.HTTP_202_ACCEPTED)
async def start_download(body: DownloadRequestIn, runtime: BlenderRuntimeDep) -> dict[str, Any]:
    """Start the confirmed download (409 in progress, 403 disabled, 422 without ``confirm: true``)."""
    return await runtime.start_download(confirm=body.confirm, version=body.version)


@router.delete("/runtime/download", response_model=BlenderRuntimeOut, status_code=status.HTTP_202_ACCEPTED)
async def cancel_download(runtime: BlenderRuntimeDep) -> dict[str, Any]:
    """Cancel a running download (the partial file is kept for resuming)."""
    return await runtime.cancel_download()
