"""Run framework domain records shared by every auxiliary run (part-aware DESIGN.md section 4.3).

Image generations (``ig_``), segmentations (``sg_``) and part analyses (``pa_``) are *runs*: separate resources that
never touch the frozen image-to-3D job path. They share one record shape (:class:`RunRecord`), one step vocabulary,
one percent formula and one queue. ``models/{image_generation,segmentation,part_analysis}.py`` subclass
:class:`RunRecord` and add ``result`` (plus ``job_id`` where it applies).

Statuses reuse :class:`~models.enums.JobStatus`. Progress units (UI BR8): :attr:`RunProgress.percent` is 0..100 and
:attr:`RunStep.progress` is 0..1 within its step.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final, Literal

from pydantic import Field

from models.enums import TERMINAL_STATUSES, JobStatus
from models.job import JobError
from models.types import DomainModel, UtcDateTime
from utils.ids import is_valid_id

RunStatus = JobStatus
"""Same values as job statuses: ``queued | running | completed | failed | cancelled``."""

StepStatus = Literal["pending", "running", "done", "warning", "skipped", "failed", "cancelled"]
RunKind = Literal["image_generation", "segmentation", "part_analysis"]
StepDevice = Literal["cpu", "gpu"]

RUN_KINDS: Final[tuple[RunKind, ...]] = ("image_generation", "segmentation", "part_analysis")

IMAGE_GENERATION_PREFIX: Final = "ig"
SEGMENTATION_PREFIX: Final = "sg"
PART_ANALYSIS_PREFIX: Final = "pa"
RUN_PREFIXES: Final[Mapping[RunKind, str]] = MappingProxyType(
    {
        "image_generation": IMAGE_GENERATION_PREFIX,
        "segmentation": SEGMENTATION_PREFIX,
        "part_analysis": PART_ANALYSIS_PREFIX,
    }
)
"""Id prefix of each run kind (``ig_<12 hex>``, ``sg_…``, ``pa_…``)."""

RUN_FILE: Final = "run.json"
"""Name of the persisted record inside a run directory."""

STEP_STATUSES: Final[frozenset[str]] = frozenset(
    {"pending", "running", "done", "warning", "skipped", "failed", "cancelled"}
)
COMPLETED_STEP_STATUSES: Final[frozenset[str]] = frozenset({"done", "warning", "skipped"})
"""Step statuses whose full weight counts towards the run percent."""
FINISHED_STEP_STATUSES: Final[frozenset[str]] = frozenset({"done", "warning", "skipped", "failed", "cancelled"})

# ---------------------------------------------------------------------------------------------------- error codes
# ``run.error.code`` values (DESIGN.md section 6.8). Executors raise AppErrors carrying the stage-specific codes.
INTERRUPTED_ERROR_CODE: Final = "interrupted"
OUT_OF_MEMORY_ERROR_CODE: Final = "out_of_memory"
INSUFFICIENT_VRAM_ERROR_CODE: Final = "insufficient_vram"
RUN_ERROR_CODE: Final = "run_error"
RUN_TIMEOUT_ERROR_CODE: Final = "run_timeout"
SOURCE_MISSING_ERROR_CODE: Final = "source_missing"
JOB_DELETED_ERROR_CODE: Final = "job_deleted"

RUN_ERROR_CODES: Final[frozenset[str]] = frozenset(
    {
        INTERRUPTED_ERROR_CODE,
        OUT_OF_MEMORY_ERROR_CODE,
        INSUFFICIENT_VRAM_ERROR_CODE,
        RUN_ERROR_CODE,
        RUN_TIMEOUT_ERROR_CODE,
        SOURCE_MISSING_ERROR_CODE,
        JOB_DELETED_ERROR_CODE,
        "unsupported_base_mesh",
        "generation_failed",
        "segmentation_failed",
        "camera_fit_failed",
        "lifting_failed",
        "partition_failed",
        "export_failed",
        "publish_conflict",
    }
)

# --------------------------------------------------------------------------------------------------- warning codes
GPU_DEFER_TIMEOUT_WARNING: Final = "gpu_defer_timeout"
"""The jobs-first gate gave up after ``run_max_defer_s`` and submitted the GPU phase anyway (section 4.3)."""

WARNING_CODES: Final[frozenset[str]] = frozenset(
    {
        "t2i_touches_border",
        "t2i_multiple_subjects",
        "t2i_coverage_low",
        "t2i_coverage_high",
        "t2i_aspect_mismatch",
        "profile_degraded",
        "frame_unverified",
        "head_pass_low_res",
        "segmentation_rerun_for_alignment",
        "camera_fit_low_iou",
        "too_small_for_geometry",
        "max_parts_exceeded",
        "expected_part_missing",
        "charts_cross_boundary",
        "low_confidence",
        "floater_candidates",
        GPU_DEFER_TIMEOUT_WARNING,
    }
)


@dataclass(frozen=True, slots=True)
class StepDef:
    """One step of an executor's plan.

    ``weight`` is the step's share of the run percent; ``device`` says which thread runs it. A step that does not apply
    to this run is still returned by ``plan()`` with a ``skip_reason``: it is pre-marked ``skipped`` with that message.
    """

    id: str
    label: str
    weight: float
    device: StepDevice
    skip_reason: str | None = None


class RunStep(DomainModel):
    id: str
    label: str
    status: StepStatus = "pending"
    message: str | None = None
    progress: float | None = None
    """0..1 within the step."""
    duration_s: float | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class RunProgress(DomainModel):
    percent: float = 0.0
    """0..100; never goes down; exactly 100 on ``completed``."""
    step_id: str | None = None
    step_index: int = 0
    """1-based index of the current step (0 before the first step starts)."""
    step_count: int = 0
    message: str | None = None
    updated_at: UtcDateTime | None = None


class RunWarning(DomainModel):
    code: str
    message: str
    step_id: str | None = None


class ModelUse(DomainModel):
    """A registry model actually used by a run (provenance)."""

    id: str
    revision: str | None = None
    license_id: str | None = None


class RunRecord(DomainModel):
    """Common persisted shape of every run (``run.json``)."""

    id: str
    kind: RunKind
    status: JobStatus = JobStatus.QUEUED
    created_at: UtcDateTime
    started_at: UtcDateTime | None = None
    finished_at: UtcDateTime | None = None
    options: dict[str, Any] = Field(default_factory=dict)
    """Effective (normalized) request."""
    progress: RunProgress = Field(default_factory=RunProgress)
    steps: list[RunStep] = Field(default_factory=list)
    warnings: list[RunWarning] = Field(default_factory=list)
    models: list[ModelUse] = Field(default_factory=list)
    error: JobError | None = None
    cancel_requested: bool = False
    workflow_id: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    @property
    def is_active(self) -> bool:
        return self.status in (JobStatus.QUEUED, JobStatus.RUNNING)

    def step(self, step_id: str) -> RunStep | None:
        return next((s for s in self.steps if s.id == step_id), None)


def steps_from_plan(plan: Iterable[StepDef]) -> list[RunStep]:
    """Initial step list of a plan: ``pending``, or ``skipped`` (progress 1, 0 s) with the step's skip reason."""
    steps: list[RunStep] = []
    for definition in plan:
        if definition.skip_reason is not None:
            steps.append(
                RunStep(
                    id=definition.id,
                    label=definition.label,
                    status="skipped",
                    message=definition.skip_reason,
                    progress=1.0,
                    duration_s=0.0,
                )
            )
        else:
            steps.append(RunStep(id=definition.id, label=definition.label))
    return steps


def run_kind_for_id(run_id: str) -> RunKind | None:
    """Run kind of a well-formed run id (``None`` for anything else, including job and workflow ids)."""
    for kind, prefix in RUN_PREFIXES.items():
        if is_valid_id(run_id, prefix):
            return kind
    return None
