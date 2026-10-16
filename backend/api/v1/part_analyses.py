"""Part analyses (``pa_``) of completed jobs: create/list/latest per job, read, cancel, delete, files (section 6.4).

Mounted empty in slice S1 (every path answers 404); slice S10 adds ``/jobs/{job_id}/part-analyses[/latest]`` on
:data:`jobs_router` (``latest`` registered before any ``{id}`` sibling), the JSON routes on :data:`router` (Bearer
only) and the manifest/GLB/ZIP/mask/thumbnail/preview/buffer/log routes on :data:`files_router` (header or
``?token=``).
"""

from __future__ import annotations

from typing import Final

from fastapi import APIRouter

from api.deps import AuthGuard, FileAuthGuard
from schemas.common import ERROR_RESPONSES

PREFIX = "/part-analyses"
TAGS: list[str] = ["part-analyses"]

jobs_router = APIRouter(prefix="/jobs", tags=TAGS, dependencies=[AuthGuard], responses=ERROR_RESPONSES)
router = APIRouter(prefix=PREFIX, tags=TAGS, dependencies=[AuthGuard], responses=ERROR_RESPONSES)
files_router = APIRouter(prefix=PREFIX, tags=TAGS, dependencies=[FileAuthGuard], responses=ERROR_RESPONSES)

ROUTERS: Final[tuple[APIRouter, ...]] = (jobs_router, router, files_router)
"""Routers of this module in registration order (``api.part_routes`` mounts them)."""
