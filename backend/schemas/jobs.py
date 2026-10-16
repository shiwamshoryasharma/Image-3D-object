"""Job schemas (contract 7.4)."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from models.enums import JobStage, JobStatus, ReferenceMode, ReferenceSlot
from models.job import Job, JobProgress, JobResultData
from models.types import UtcDateTime
from schemas.common import API_V1_PREFIX, ApiModel, RequestModel

MAX_SEED = 2_147_483_647


# --------------------------------------------------------------------------------------------------
# Requests
# --------------------------------------------------------------------------------------------------


class SamplerSettings(RequestModel):
    steps: int | None = Field(default=None, ge=1, le=50)
    guidance_strength: float | None = Field(default=None, ge=0, le=10)
    guidance_rescale: float | None = Field(default=None, ge=0, le=1)
    rescale_t: float | None = Field(default=None, ge=1, le=6)


class AdvancedSettings(RequestModel):
    sparse_structure: SamplerSettings | None = None
    shape: SamplerSettings | None = None
    texture: SamplerSettings | None = None


class GenerationSettings(RequestModel):
    """Generation parameters. Membership of ``resolution``/``texture_size``/``polygon_target``/``reference_mode``
    is validated against the engine capabilities when the job is created."""

    resolution: int = Field(default=1024, gt=0)
    seed: int = Field(default=0, ge=0, le=MAX_SEED)
    randomize_seed: bool = True
    polygon_target: int = Field(default=500_000, gt=0)
    texture_size: int = Field(default=2048, gt=0)
    remove_background: bool = True
    reference_mode: ReferenceMode | None = None
    """``None`` means the server default (``IMG3D_REFERENCE_MODE``); always concrete in a stored job."""
    advanced: AdvancedSettings | None = None


class CreateJobRequest(RequestModel):
    reference_set_id: str
    primary_image_id: str | None = None
    settings: GenerationSettings = Field(default_factory=GenerationSettings)


# --------------------------------------------------------------------------------------------------
# Responses
# --------------------------------------------------------------------------------------------------


class JobReferenceOut(ApiModel):
    image_id: str
    slot: ReferenceSlot
    is_primary: bool


class JobProgressOut(ApiModel):
    stage: JobStage
    stage_label: str
    stage_index: int
    stage_count: int
    step: int | None
    total_steps: int | None
    percent: float
    message: str | None
    elapsed_seconds: float | None
    eta_seconds: float | None
    updated_at: UtcDateTime | None

    @classmethod
    def from_domain(cls, progress: JobProgress) -> JobProgressOut:
        return cls.model_validate(progress.model_dump())


class JobErrorOut(ApiModel):
    code: str
    message: str
    details: Any | None = None


class MeshStatsOut(ApiModel):
    vertices: int
    faces: int
    materials: int
    textures: int
    texture_size: int | None
    has_uv: bool
    bbox_min: list[float]
    bbox_max: list[float]
    dimensions: list[float]
    file_size_bytes: int


class JobResultOut(ApiModel):
    model_url: str
    download_url: str
    preview_url: str | None
    stats: MeshStatsOut
    timings: dict[str, float]
    seed: int
    resolution: int
    effective_resolution: int
    texture_size: int
    polygon_target: int

    @classmethod
    def from_domain(cls, job_id: str, result: JobResultData) -> JobResultOut:
        base = f"{API_V1_PREFIX}/jobs/{job_id}"
        return cls(
            model_url=f"{base}/model.glb",
            download_url=f"{base}/export/glb",
            preview_url=f"{base}/preview" if result.has_preview else None,
            stats=MeshStatsOut.model_validate(result.stats.model_dump()),
            timings=dict(result.timings),
            seed=result.seed,
            resolution=result.resolution,
            effective_resolution=result.effective_resolution,
            texture_size=result.texture_size,
            polygon_target=result.polygon_target,
        )


class JobOut(ApiModel):
    id: str
    status: JobStatus
    engine: str
    created_at: UtcDateTime
    started_at: UtcDateTime | None
    finished_at: UtcDateTime | None
    reference_set_id: str
    primary_image_id: str
    references: list[JobReferenceOut]
    settings: dict[str, Any]
    seed: int
    queue_position: int | None
    progress: JobProgressOut
    error: JobErrorOut | None
    result: JobResultOut | None

    @classmethod
    def from_domain(cls, job: Job, *, queue_position: int | None = None) -> JobOut:
        return cls(
            id=job.id,
            status=job.status,
            engine=job.engine,
            created_at=job.created_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
            reference_set_id=job.reference_set_id,
            primary_image_id=job.primary_image_id,
            references=[
                JobReferenceOut(image_id=r.image_id, slot=r.slot, is_primary=r.is_primary) for r in job.references
            ],
            settings=dict(job.settings),
            seed=job.seed,
            queue_position=queue_position if job.status is JobStatus.QUEUED else None,
            progress=JobProgressOut.from_domain(job.progress),
            error=JobErrorOut.model_validate(job.error.model_dump()) if job.error else None,
            result=JobResultOut.from_domain(job.id, job.result)
            if job.result is not None and job.status is JobStatus.COMPLETED
            else None,
        )


class JobList(ApiModel):
    items: list[JobOut]
