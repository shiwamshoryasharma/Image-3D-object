"""Auto Mesh Tuner run domain model (persisted as ``runtime/jobs/<job_id>/tuning/<tn_id>/run.json``).

A tuning run post-processes a completed job's ``model.glb`` with Blender (DESIGN.md sections 3, 4 and 6). Statuses
reuse :class:`~models.enums.JobStatus` (``queued``/``running``/``completed``/``failed``/``cancelled``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final, Literal

from pydantic import Field

from models.enums import TERMINAL_STATUSES, JobStatus
from models.job import JobError, MeshStats
from models.types import DomainModel, UtcDateTime

TuningStatus = JobStatus
"""Same values as job statuses."""

StepStatus = Literal["pending", "running", "done", "warning", "skipped", "failed"]

TUNING_PREFIX: Final = "tn"
INTERRUPTED_ERROR_CODE: Final = "interrupted"

RUN_FILE: Final = "run.json"
MODEL_FILE: Final = "tuned.glb"
REPORT_FILE: Final = "report.json"
BLEND_FILE: Final = "tuned.blend"
LOG_FILE: Final = "blender.log"
CONFIG_FILE: Final = "config.json"
PREVIEWS_DIR: Final = "previews"


@dataclass(frozen=True, slots=True)
class StepInfo:
    id: str
    label: str
    weight: float


STEP_TABLE: Final[tuple[StepInfo, ...]] = (
    StepInfo("load", "Load mesh", 0.05),
    StepInfo("analyze", "Mesh analysis", 0.07),
    StepInfo("geometry_repair", "Geometry repair", 0.30),
    StepInfo("face_refinement", "Face refinement", 0.15),
    StepInfo("topology_tuning", "Topology tuning", 0.20),
    StepInfo("rig_optimization", "Rig optimization", 0.05),
    StepInfo("validation", "Validation", 0.13),
    StepInfo("export", "Save results", 0.05),
)
"""Fixed step ids/labels/overall-progress weights of the Blender-side protocol (DESIGN.md section 3)."""
STEP_IDS: Final[tuple[str, ...]] = tuple(step.id for step in STEP_TABLE)
STEP_STATUSES: Final[frozenset[str]] = frozenset({"pending", "running", "done", "warning", "skipped", "failed"})
FINISHED_STEP_STATUSES: Final[frozenset[str]] = frozenset({"done", "warning", "skipped", "failed"})


class TuningStep(DomainModel):
    id: str
    label: str
    status: StepStatus = "pending"
    message: str | None = None
    duration_s: float | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class TuningProgress(DomainModel):
    percent: float = 0.0
    step_id: str | None = None
    step_index: int = 0
    """1-based index of the current step (0 before the first step starts)."""
    step_count: int = len(STEP_TABLE)
    message: str | None = None
    updated_at: UtcDateTime | None = None


class TuningBlender(DomainModel):
    executable: str
    version: str | None = None
    kind: str | None = None


class TuningPreview(DomainModel):
    name: str
    view: str | None = None
    variant: str | None = None


class TuningResultData(DomainModel):
    """Files of a completed run (relative to its tuning directory) and the tuned model's statistics."""

    model_file: str = MODEL_FILE
    report_file: str | None = REPORT_FILE
    blend_file: str | None = None
    previews: list[TuningPreview] = Field(default_factory=list)
    stats: MeshStats | None = None
    duration_s: float | None = None


def default_steps() -> list[TuningStep]:
    return [TuningStep(id=step.id, label=step.label) for step in STEP_TABLE]


class TuningRun(DomainModel):
    id: str
    job_id: str
    status: JobStatus = JobStatus.QUEUED
    created_at: UtcDateTime
    started_at: UtcDateTime | None = None
    finished_at: UtcDateTime | None = None
    options: dict[str, Any] = Field(default_factory=dict)
    """Effective options (preset defaults + explicit fields)."""
    progress: TuningProgress = Field(default_factory=TuningProgress)
    steps: list[TuningStep] = Field(default_factory=default_steps)
    metrics: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    validation: dict[str, Any] | None = None
    blender: TuningBlender | None = None
    result: TuningResultData | None = None
    error: JobError | None = None
    cancel_requested: bool = False

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    @property
    def is_active(self) -> bool:
        return self.status in (JobStatus.QUEUED, JobStatus.RUNNING)

    def step(self, step_id: str) -> TuningStep | None:
        return next((s for s in self.steps if s.id == step_id), None)
