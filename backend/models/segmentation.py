"""Segmentation run (``sg_``) domain record (part-aware DESIGN.md section 4.5). Frozen in slice S1.

Persisted as ``runtime/segmentations/<sg>/run.json``. Next to it: ``source.png`` (the snapshot taken at creation),
``original.png`` (job sources, for the head pass), ``frame.json``, ``labels.png`` (16-bit, value = taxonomy value),
``overlay.png``, ``masks/<value>.png`` (8-bit soft masks) and ``segmentation.json`` (the result payload).

``options`` holds the effective request: ``{"source": {...}, "options": SegmentationOptions}`` plus the resolved
``expected_terms``. The source is snapshotted at creation, so replacing the set image later never affects the run.
"""

from __future__ import annotations

from typing import Any, Final, Literal

from pydantic import Field, model_validator

from models.runs import RunRecord
from models.types import DomainModel
from parts.types import ConditioningMode, FrameRecoveryMethod, SegmentationProfile, SegmentationSubject, Side

SOURCE_FILE: Final = "source.png"
ORIGINAL_FILE: Final = "original.png"
FRAME_FILE: Final = "frame.json"
LABELS_FILE: Final = "labels.png"
OVERLAY_FILE: Final = "overlay.png"
RESULT_FILE: Final = "segmentation.json"

SegmentationSourceKind = Literal["reference_image", "job"]
SegmentationSpace = Literal["conditioning", "source"]
"""``conditioning``: job sources, and reference images whose frame was predicted exactly (real alpha); otherwise
``source``."""


class SegmentationSource(DomainModel):
    """What was segmented: a reference image (before reconstruction) or a job's conditioning image (``preview.png``)."""

    kind: SegmentationSourceKind
    reference_set_id: str | None = None
    image_id: str | None = None
    job_id: str | None = None

    @model_validator(mode="after")
    def _ids_for_kind(self) -> SegmentationSource:
        if self.kind == "reference_image" and (not self.reference_set_id or not self.image_id or self.job_id):
            raise ValueError("a reference_image source names reference_set_id and image_id only")
        if self.kind == "job" and (not self.job_id or self.reference_set_id or self.image_id):
            raise ValueError("a job source names job_id only")
        return self


class SegmentationFrame(DomainModel):
    """Summary of the conditioning frame the masks were mapped through (section 4.5.2)."""

    mode: ConditioningMode
    method: FrameRecoveryMethod
    scale: float
    crop_box: list[int] | None = None
    size: list[int] = Field(default_factory=list)
    """``[width, height]`` of the conditioning image."""
    verified: bool
    residual: float


class SegmentationLabel(DomainModel):
    """One taxonomy class found in the image (its mask is ``masks/<value>.png``)."""

    value: int
    category: str
    group: str
    area_px: int
    confidence: float
    bbox: list[int]
    """``[x0, y0, x1, y1]`` in the result space."""
    side: Side | None = None
    sources: list[str] = Field(default_factory=list)
    """Model ids that contributed."""


class SegmentationResult(DomainModel):
    space: SegmentationSpace
    frame: SegmentationFrame | None = None
    subject: SegmentationSubject
    profile: SegmentationProfile
    labels: list[SegmentationLabel] = Field(default_factory=list)
    annotations: dict[str, dict[str, Any]] = Field(default_factory=dict)
    """2D guides keyed by class id, e.g. ``{"eyelash.left": {"lid_curve": [[x, y], …], "method": "heuristic",
    "confidence": 0.6}}``."""
    expected_terms_missing: list[str] = Field(default_factory=list)
    timings: dict[str, float] = Field(default_factory=dict)

    def label(self, value: int) -> SegmentationLabel | None:
        return next((label for label in self.labels if label.value == value), None)


class SegmentationRun(RunRecord):
    kind: Literal["segmentation"] = "segmentation"
    source: SegmentationSource
    job_id: str | None = None
    """``source.job_id`` for job sources (drives the store's ``job_id`` filter and the event scoping); else ``None``."""
    source_pixels_sha256: str | None = None
    """sha256 of the decoded RGBA pixels of ``source.png`` (compared by part analyses before reusing the masks)."""
    result: SegmentationResult | None = None

    @model_validator(mode="after")
    def _job_id_matches_source(self) -> SegmentationRun:
        if self.job_id is None and self.source.kind == "job":
            self.job_id = self.source.job_id
        if self.job_id != self.source.job_id:
            raise ValueError("job_id must equal source.job_id")
        return self
