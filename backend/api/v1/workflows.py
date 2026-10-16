"""Workflows (``wf_``): catalog, validate, create, list, read, graph, cancel, delete (section 6.5).

Mounted empty in slice S1 (every path answers 404); slice S11 adds the routes on :data:`router`, registering the
static ``/workflows/catalog`` and ``/workflows/validate`` before ``/workflows/{wf_id}``.
"""

from __future__ import annotations

from typing import Final

from fastapi import APIRouter

from api.deps import AuthGuard
from schemas.common import ERROR_RESPONSES

PREFIX = "/workflows"
TAGS: list[str] = ["workflows"]

router = APIRouter(prefix=PREFIX, tags=TAGS, dependencies=[AuthGuard], responses=ERROR_RESPONSES)

ROUTERS: Final[tuple[APIRouter, ...]] = (router,)
"""Routers of this module in registration order (``api.part_routes`` mounts them)."""
