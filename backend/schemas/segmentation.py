"""Segmentation schemas: ``/segmentations`` (part-aware DESIGN.md sections 4.5 and 6.3). Frozen in slice S1.

:class:`SegmentationOptions` is shared by ``POST /segmentations``, ``PartAnalysisOptions.segmentation`` (the internal
pass of a part analysis) and ``WorkflowRequest.segmentation``. Every nested options object accepts partial objects:
omitted fields take their defaults, unknown keys are 422 ``extra_forbidden``.
"""

from __future__ import annotations

from typing import Annotated, Any, Final, Literal, get_args

from pydantic import Field, StringConstraints, field_validator
from pydantic_core import PydanticCustomError

from models.segmentation import (
    SegmentationFrame,
    SegmentationLabel,
    SegmentationResult,
    SegmentationRun,
    SegmentationSource,
    SegmentationSourceKind,
    SegmentationSpace,
)
from parts.taxonomy import TAXONOMY
from parts.types import ConditioningMode, FrameRecoveryMethod, SegmentationProfile, SegmentationSubject, Side
from schemas.common import API_V1_PREFIX, ApiModel, RequestModel
from schemas.image_generation import clean_prompt_text
from schemas.runs import RunOutBase, run_out_fields

SegmentationProfileOption = Literal["auto", "baseline", "anime", "realistic"]
"""``auto`` maps the (detected) subject to a profile."""
SegmentationSubjectOption = Literal["auto", "anime_character", "realistic_human", "object"]
"""``auto`` uses the Florence-2 caption with deterministic keyword rules (``object`` without Florence-2)."""

SEGMENTATION_PROFILES: Final[tuple[str, ...]] = get_args(SegmentationProfile)
SEGMENTATION_SUBJECT_OPTIONS: Final[tuple[str, ...]] = get_args(SegmentationSubjectOption)
MAX_EXTRA_TERMS: Final = 16
EXTRA_TERM_MAX_CHARS: Final = 40
DEFAULT_MIN_CONFIDENCE: Final = 0.35

ExtraTerm = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=EXTRA_TERM_MAX_CHARS)]


# --------------------------------------------------------------------------------------------------
# Requests
# --------------------------------------------------------------------------------------------------


class SegmentationOptions(RequestModel):
    profile: SegmentationProfileOption = "auto"
    subject: SegmentationSubjectOption = "auto"
    classes: list[str] | None = Field(default=None, min_length=1)
    """``None`` = the whole taxonomy; else a subset of taxonomy class ids (unknown ids are 422 ``unknown_class``)."""
    extra_terms: list[ExtraTerm] = Field(default_factory=list, max_length=MAX_EXTRA_TERMS)
    """Extra nouns to look for: an alias maps to its class, anything else to ``object.held``."""
    use_prompt_terms: bool = True
    """Add the ``expected_terms`` of the ``ig_`` run that produced the image (via ``ReferenceImage.source``)."""
    head_pass: bool = True
    """Second pass on a full-resolution head crop for the small face classes (section 4.5.3)."""
    min_confidence: float = Field(default=DEFAULT_MIN_CONFIDENCE, ge=0.05, le=0.95)

    @field_validator("classes")
    @classmethod
    def _known_classes(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        unknown = sorted({class_id for class_id in value if class_id not in TAXONOMY})
        if unknown:
            raise PydanticCustomError(
                "unknown_class",
                "Unknown taxonomy class id(s): {classes}",
                {"classes": ", ".join(unknown)},
            )
        return list(dict.fromkeys(value))

    @field_validator("extra_terms")
    @classmethod
    def _clean_terms(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(clean_prompt_text(term, field="extra_terms") for term in value))


class ReferenceImageSegmentationSource(RequestModel):
    """Segment a reference image before reconstruction (snapshotted with ``normalize_to_png`` at creation)."""

    kind: Literal["reference_image"]
    reference_set_id: str
    image_id: str


class JobSegmentationSource(RequestModel):
    """Segment a completed job's conditioning image (``preview.png``)."""

    kind: Literal["job"]
    job_id: str


SegmentationSourceIn = Annotated[
    ReferenceImageSegmentationSource | JobSegmentationSource, Field(discriminator="kind")
]


class SegmentationRequest(RequestModel):
    """``POST /segmentations``."""

    source: SegmentationSourceIn
    options: SegmentationOptions = Field(default_factory=SegmentationOptions)


# --------------------------------------------------------------------------------------------------
# Responses
# --------------------------------------------------------------------------------------------------


def segmentation_url(sg_id: str) -> str:
    return f"{API_V1_PREFIX}/segmentations/{sg_id}"


class SegmentationSourceOut(ApiModel):
    kind: SegmentationSourceKind
    reference_set_id: str | None
    image_id: str | None
    job_id: str | None

    @classmethod
    def from_domain(cls, source: SegmentationSource) -> SegmentationSourceOut:
        return cls(
            kind=source.kind,
            reference_set_id=source.reference_set_id,
            image_id=source.image_id,
            job_id=source.job_id,
        )


class SegmentationFrameOut(ApiModel):
    mode: ConditioningMode
    method: FrameRecoveryMethod
    scale: float
    crop_box: list[int] | None
    size: list[int]
    verified: bool
    residual: float

    @classmethod
    def from_domain(cls, frame: SegmentationFrame) -> SegmentationFrameOut:
        return cls.model_validate(frame.model_dump())


class SegmentationLabelOut(ApiModel):
    value: int
    category: str
    group: str
    area_px: int
    confidence: float
    bbox: list[int]
    side: Side | None
    sources: list[str]
    mask_url: str

    @classmethod
    def from_domain(cls, sg_id: str, label: SegmentationLabel) -> SegmentationLabelOut:
        return cls(**label.model_dump(), mask_url=f"{segmentation_url(sg_id)}/masks/{label.value}")


class SegmentationResultOut(ApiModel):
    space: SegmentationSpace
    frame: SegmentationFrameOut | None
    subject: SegmentationSubject
    profile: SegmentationProfile
    labels: list[SegmentationLabelOut]
    annotations: dict[str, dict[str, Any]]
    expected_terms_missing: list[str]
    overlay_url: str
    labels_url: str
    timings: dict[str, float]

    @classmethod
    def from_domain(cls, sg_id: str, result: SegmentationResult) -> SegmentationResultOut:
        base = segmentation_url(sg_id)
        return cls(
            space=result.space,
            frame=SegmentationFrameOut.from_domain(result.frame) if result.frame is not None else None,
            subject=result.subject,
            profile=result.profile,
            labels=[SegmentationLabelOut.from_domain(sg_id, label) for label in result.labels],
            annotations={key: dict(value) for key, value in result.annotations.items()},
            expected_terms_missing=list(result.expected_terms_missing),
            overlay_url=f"{base}/overlay",
            labels_url=f"{base}/labels",
            timings=dict(result.timings),
        )


class SegmentationOut(RunOutBase):
    kind: Literal["segmentation"]
    source: SegmentationSourceOut
    job_id: str | None
    """The job of a job source (``None`` for reference images)."""
    source_url: str
    """The snapshot that was segmented (``GET /segmentations/{id}/source``)."""
    result: SegmentationResultOut | None

    @classmethod
    def from_run(cls, run: SegmentationRun) -> SegmentationOut:
        return cls(
            **run_out_fields(run),
            source=SegmentationSourceOut.from_domain(run.source),
            job_id=run.job_id,
            source_url=f"{segmentation_url(run.id)}/source",
            result=SegmentationResultOut.from_domain(run.id, run.result) if run.result is not None else None,
        )


class SegmentationList(ApiModel):
    items: list[SegmentationOut]
