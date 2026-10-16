"""Generation jobs (contract 7.4)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from api.deps import AuthGuard, JobServiceDep
from core.exceptions import ValidationAppError
from models.enums import JobStatus
from schemas.common import ERROR_RESPONSES
from schemas.jobs import CreateJobRequest, JobList, JobOut
from services.job_service import DEFAULT_LIST_LIMIT, MAX_LIST_LIMIT

router = APIRouter(prefix="/jobs", tags=["jobs"], dependencies=[AuthGuard], responses=ERROR_RESPONSES)


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


@router.post("", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
async def create_job(body: CreateJobRequest, jobs: JobServiceDep) -> JobOut:
    job = await jobs.create_job(body)
    return jobs.to_out(job)


@router.get("", response_model=JobList)
async def list_jobs(
    jobs: JobServiceDep,
    limit: Annotated[int, Query(ge=1, le=MAX_LIST_LIMIT)] = DEFAULT_LIST_LIMIT,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
) -> JobList:
    items = jobs.list_jobs(limit=limit, status=_parse_status(status_filter))
    return JobList(items=[jobs.to_out(job) for job in items])


@router.get("/{job_id}", response_model=JobOut)
async def get_job(job_id: str, jobs: JobServiceDep) -> JobOut:
    return jobs.to_out(jobs.get_job(job_id))


@router.post("/{job_id}/cancel", response_model=JobOut)
async def cancel_job(job_id: str, jobs: JobServiceDep) -> JobOut:
    return jobs.to_out(await jobs.cancel_job(job_id))


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_job(job_id: str, jobs: JobServiceDep) -> Response:
    await jobs.delete_job(job_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
