"""Generation job domain model and the stage table used for progress reporting."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import Field

from models.enums import TERMINAL_STATUSES, JobStage, JobStatus, ReferenceSlot
from models.types import DomainModel, UtcDateTime


@dataclass(frozen=True, slots=True)
class StageInfo:
    index: int
    label: str
    weight: float


STAGE_TABLE: dict[JobStage, StageInfo] = {
    JobStage.QUEUED: StageInfo(0, "Queued", 0),
    JobStage.WAITING_FOR_MODEL: StageInfo(0, "Waiting for model", 0),
    JobStage.PREPROCESSING: StageInfo(1, "Preparing image", 5),
    JobStage.CONDITIONING: StageInfo(2, "Encoding image", 5),
    JobStage.SPARSE_STRUCTURE: StageInfo(3, "Building structure", 15),
    JobStage.SHAPE: StageInfo(4, "Generating geometry", 30),
    JobStage.TEXTURE: StageInfo(5, "Generating texture", 20),
    JobStage.DECODING: StageInfo(6, "Decoding mesh", 8),
    JobStage.EXPORTING: StageInfo(7, "Exporting GLB", 15),
    JobStage.FINALIZING: StageInfo(8, "Finalizing", 2),
    JobStage.DONE: StageInfo(9, "Done", 0),
}
STAGE_COUNT = 9

INTERRUPTED_ERROR_CODE = "interrupted"


class JobReference(DomainModel):
    """Snapshot of one reference image at job creation (file copied to ``inputs/<image_id>.png``)."""

    image_id: str
    slot: ReferenceSlot
    is_primary: bool = False

    @property
    def input_file_name(self) -> str:
        return f"{self.image_id}.png"


class JobProgress(DomainModel):
    stage: JobStage = JobStage.QUEUED
    stage_label: str = STAGE_TABLE[JobStage.QUEUED].label
    stage_index: int = 0
    stage_count: int = STAGE_COUNT
    step: int | None = None
    total_steps: int | None = None
    percent: float = 0.0
    message: str | None = None
    elapsed_seconds: float | None = None
    eta_seconds: float | None = None
    updated_at: UtcDateTime | None = None

    @classmethod
    def for_stage(cls, stage: JobStage, *, message: str | None = None, percent: float = 0.0) -> JobProgress:
        info = STAGE_TABLE[stage]
        return cls(stage=stage, stage_label=info.label, stage_index=info.index, message=message, percent=percent)


class JobError(DomainModel):
    code: str
    message: str
    details: Any | None = None


class MeshStats(DomainModel):
    vertices: int = 0
    faces: int = 0
    materials: int = 0
    textures: int = 0
    texture_size: int | None = None
    has_uv: bool = False
    bbox_min: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    bbox_max: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    dimensions: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    file_size_bytes: int = 0


class JobResultData(DomainModel):
    stats: MeshStats
    timings: dict[str, float] = Field(default_factory=dict)
    seed: int
    resolution: int
    effective_resolution: int
    texture_size: int
    polygon_target: int
    has_preview: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
    """Engine metadata (not exposed by the API)."""


class Job(DomainModel):
    id: str
    status: JobStatus = JobStatus.QUEUED
    engine: str
    created_at: UtcDateTime
    started_at: UtcDateTime | None = None
    finished_at: UtcDateTime | None = None
    reference_set_id: str
    primary_image_id: str
    references: list[JobReference] = Field(default_factory=list)
    settings: dict[str, Any] = Field(default_factory=dict)
    """Normalized ``GenerationSettings`` (as submitted, with defaults filled in)."""
    seed: int
    progress: JobProgress = Field(default_factory=JobProgress)
    error: JobError | None = None
    result: JobResultData | None = None
    cancel_requested: bool = False

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    @property
    def is_active(self) -> bool:
        return self.status in (JobStatus.QUEUED, JobStatus.RUNNING)
