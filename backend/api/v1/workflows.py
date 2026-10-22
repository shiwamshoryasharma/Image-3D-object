"""Workflows (``wf_``): catalog, validate, create, list, read, graph, cancel, delete (section 6.5; slice S11).

The static ``/workflows/catalog`` and ``/workflows/validate`` are registered before ``/workflows/{wf_id}``.
``GET /workflows/catalog`` honours ``If-None-Match`` (304 with the same ``ETag``). ``POST /workflows/validate``
never creates anything and answers 422 only for a malformed body. Mutating routes answer 503 ``feature_disabled``
while ``IMG3D_WORKFLOWS_ENABLED`` (or the part-aware master switch) is off; reads always work.
"""

from __future__ import annotations

from typing import Annotated, Any, Final

from fastapi import APIRouter, Header, Query, Response, status

from api.deps import AuthGuard
from api.part_deps import WorkflowServiceDep
from models.enums import JobStatus
from schemas.common import ERROR_RESPONSES
from schemas.workflows import (
    CatalogOut,
    ValidationReport,
    WorkflowGraphOut,
    WorkflowList,
    WorkflowOut,
    WorkflowRequest,
)

PREFIX = "/workflows"
TAGS: list[str] = ["workflows"]

router = APIRouter(prefix=PREFIX, tags=TAGS, dependencies=[AuthGuard], responses=ERROR_RESPONSES)

ROUTERS: Final[tuple[APIRouter, ...]] = (router,)
"""Routers of this module in registration order (``api.part_routes`` mounts them)."""


@router.get("/catalog", response_model=CatalogOut, responses={304: {"description": "Not modified"}})
async def get_workflow_catalog(
    service: WorkflowServiceDep,
    response: Response,
    if_none_match: Annotated[str | None, Header()] = None,
) -> Any:
    catalog, etag = service.catalog()
    if if_none_match is not None and etag in {tag.strip() for tag in if_none_match.split(",")}:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": etag})
    response.headers["ETag"] = etag
    return catalog


@router.post("/validate", response_model=ValidationReport)
async def validate_workflow(body: WorkflowRequest, service: WorkflowServiceDep) -> ValidationReport:
    return service.validate(body)


@router.post("", response_model=WorkflowOut, status_code=status.HTTP_202_ACCEPTED)
async def create_workflow(body: WorkflowRequest, service: WorkflowServiceDep) -> WorkflowOut:
    return service.to_out(await service.create(body))


@router.get("", response_model=WorkflowList)
async def list_workflows(
    service: WorkflowServiceDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    status_filter: Annotated[JobStatus | None, Query(alias="status")] = None,
    job_id: Annotated[str | None, Query(max_length=64)] = None,
    image_generation_id: Annotated[str | None, Query(max_length=64)] = None,
) -> WorkflowList:
    records = service.list_runs(
        limit=limit, status=status_filter, job_id=job_id, image_generation_id=image_generation_id
    )
    return WorkflowList(items=[service.to_out(record) for record in records])


@router.get("/{wf_id}", response_model=WorkflowOut)
async def get_workflow(wf_id: str, service: WorkflowServiceDep) -> WorkflowOut:
    return service.to_out(service.get(wf_id))


@router.get("/{wf_id}/graph", response_model=WorkflowGraphOut)
async def get_workflow_graph(wf_id: str, service: WorkflowServiceDep) -> Any:
    return service.graph(wf_id)


@router.post("/{wf_id}/cancel", response_model=WorkflowOut, status_code=status.HTTP_202_ACCEPTED)
async def cancel_workflow(wf_id: str, service: WorkflowServiceDep) -> WorkflowOut:
    return service.to_out(await service.cancel(wf_id))


@router.delete("/{wf_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_workflow(wf_id: str, service: WorkflowServiceDep) -> Response:
    await service.delete(wf_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
