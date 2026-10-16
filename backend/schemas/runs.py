"""Wire models shared by the run resources (part-aware DESIGN.md sections 4.3 and 6).

``ImageGenerationOut``, ``SegmentationOut`` and ``PartAnalysisOut`` (``schemas/{image_generation,segmentation,
parts}.py``) extend :class:`RunOutBase` with their ``result`` (and ``job_id`` where it applies). Field order follows
the section 4.4.6 example: identity and timestamps, options, models, progress, steps, warnings, then the kind's own
fields, then ``error``.
"""

from __future__ import annotations

from typing import Any, Self

from models.enums import JobStatus
from models.runs import ModelUse, RunKind, RunProgress, RunRecord, RunStep, RunWarning, StepStatus
from models.types import UtcDateTime
from schemas.common import ApiModel
from schemas.jobs import JobErrorOut


class RunStepOut(ApiModel):
    id: str
    label: str
    status: StepStatus
    message: str | None
    progress: float | None
    """0..1 within the step."""
    duration_s: float | None
    details: dict[str, Any]

    @classmethod
    def from_domain(cls, step: RunStep) -> RunStepOut:
        return cls.model_validate(step.model_dump())


class RunProgressOut(ApiModel):
    percent: float
    """0..100."""
    step_id: str | None
    step_index: int
    step_count: int
    message: str | None
    updated_at: UtcDateTime | None

    @classmethod
    def from_domain(cls, progress: RunProgress) -> RunProgressOut:
        return cls.model_validate(progress.model_dump())


class RunWarningOut(ApiModel):
    code: str
    message: str
    step_id: str | None


class ModelUseOut(ApiModel):
    id: str
    revision: str | None
    license_id: str | None


class RunOutBase(ApiModel):
    """Fields every run response carries (subclasses add ``result`` and kind-specific fields)."""

    id: str
    kind: RunKind
    status: JobStatus
    created_at: UtcDateTime
    started_at: UtcDateTime | None
    finished_at: UtcDateTime | None
    workflow_id: str | None
    options: dict[str, Any]
    models: list[ModelUseOut]
    progress: RunProgressOut
    steps: list[RunStepOut]
    warnings: list[RunWarningOut]
    error: JobErrorOut | None

    @classmethod
    def from_domain(cls, run: RunRecord, **extra: Any) -> Self:
        """Build the response from a run record; ``extra`` supplies the subclass fields (e.g. ``result``)."""
        return cls(**run_out_fields(run), **extra)


def run_out_fields(run: RunRecord) -> dict[str, Any]:
    """Keyword arguments of :class:`RunOutBase` for ``run`` (shared by every run response model)."""
    return {
        "id": run.id,
        "kind": run.kind,
        "status": run.status,
        "created_at": run.created_at,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "workflow_id": run.workflow_id,
        "options": dict(run.options),
        "models": [_model_use_out(m) for m in run.models],
        "progress": RunProgressOut.from_domain(run.progress),
        "steps": [RunStepOut.from_domain(s) for s in run.steps],
        "warnings": [_warning_out(w) for w in run.warnings],
        "error": JobErrorOut.model_validate(run.error.model_dump()) if run.error else None,
    }


def _model_use_out(use: ModelUse) -> ModelUseOut:
    return ModelUseOut(id=use.id, revision=use.revision, license_id=use.license_id)


def _warning_out(warning: RunWarning) -> RunWarningOut:
    return RunWarningOut(code=warning.code, message=warning.message, step_id=warning.step_id)


class RunQueueOut(ApiModel):
    """State of the shared run queue (``FeaturesOut.queues.runs``)."""

    pending: int
    running_id: str | None
    running_kind: RunKind | None
    max_size: int
