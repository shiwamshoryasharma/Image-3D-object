"""Part analysis schemas: ``/jobs/{job_id}/part-analyses`` and ``/part-analyses`` (part-aware DESIGN.md sections 4.6
and 6.4). Frozen in slice S1.

``PartAnalysisOptions`` is the body of ``POST /jobs/{job_id}/part-analyses`` (optional; ``{}`` = defaults) and the
``parts`` field of a workflow. There is no ``assembled_glb`` option: ``model_parts.glb`` is always written, so
``result.assembled_url`` and ``result.download_url`` are never null. ``views``/``max_parts``/``min_part_faces`` that the
body does not set fall back to the server settings (``parts_default_views``, ``parts_max_parts``,
``parts_min_part_faces``), whose defaults equal the defaults below; ``min_part_faces = null`` resolves to
``max(50, round(1e-4·F))`` for a base mesh of ``F`` faces.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final, Literal

from pydantic import Field, field_validator

from models.part_analysis import (
    PartAnalysisResult,
    PartAnalysisRun,
    PartAnalysisStatus,
    PartRefinementSummary,
    PartSummary,
)
from parts.taxonomy import DEFAULT_SEPARATE
from parts.types import REGION_ATTRIBUTE, Kind, RefinementNeed, RefinementStatus, SeparableGroup, Side, StrategyId
from schemas.common import API_V1_PREFIX, ApiModel, RequestModel
from schemas.jobs import MeshStatsOut
from schemas.runs import RunOutBase, run_out_fields
from schemas.segmentation import SegmentationOptions

MAX_VIEWS: Final = 24
DEFAULT_VIEWS: Final = 8
MAX_MIN_PART_FACES: Final = 100_000
MAX_PARTS_LIMIT: Final = 256
DEFAULT_MAX_PARTS: Final = 64

STRATEGY_LABELS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "components": "Geometry components",
        "semantic_regions": "Semantic regions",
        "semantic_parts": "Semantic parts",
        "per_part_regeneration": "Per-part regeneration",
    }
)
"""Display labels of the strategies (section 4.6.1), in ``/features`` order."""
STRATEGIES_REQUIRING_SEGMENTATION: Final[frozenset[str]] = frozenset(
    {"semantic_regions", "semantic_parts", "per_part_regeneration"}
)
RESERVED_STRATEGIES: Final[frozenset[str]] = frozenset({"per_part_regeneration"})
"""Reported ``available: false, code: "not_implemented"`` (deferred D2); a request for one is a 503."""


# --------------------------------------------------------------------------------------------------
# Requests
# --------------------------------------------------------------------------------------------------


class PartExportOptions(RequestModel):
    """``parts.export``, for workflows and direct analyses alike (there is no top-level ``export``)."""

    per_part_glbs: bool = False
    """Write ``parts/<id>.glb`` at run time (each embeds the full atlas); otherwise they are built on demand."""
    previews: bool = True
    """Render the front/back/left/right label-colour previews (needs the lifter)."""


class PartExtractionOptions(RequestModel):
    """The options of the ``part_extraction`` workflow node: ``PartAnalysisOptions`` without ``segmentation_id``
    (a workflow links its own ``sg_``) and without ``export`` (the ``export`` node carries it)."""

    strategy: StrategyId | None = None
    """``None`` → ``/features`` ``default_strategy``."""
    segmentation: SegmentationOptions | None = None
    """Options of the internal segmentation pass (``None`` → defaults)."""
    separate: list[SeparableGroup] = Field(default_factory=lambda: list(DEFAULT_SEPARATE))
    """Groups cut into physical parts (or deferred records); every other group becomes regions by policy."""
    views: int = Field(default=DEFAULT_VIEWS, ge=0, le=MAX_VIEWS)
    """Extra multi-view renders for lifting (0 = input view only)."""
    min_part_faces: int | None = Field(default=None, ge=0, le=MAX_MIN_PART_FACES)
    max_parts: int = Field(default=DEFAULT_MAX_PARTS, ge=1, le=MAX_PARTS_LIMIT)
    split_shells: bool = True

    @field_validator("separate")
    @classmethod
    def _unique_groups(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(value))


class PartAnalysisOptions(PartExtractionOptions):
    """``POST /jobs/{job_id}/part-analyses`` body and ``WorkflowRequest.parts``."""

    segmentation_id: str | None = None
    """Reuse a completed ``sg_`` (snapshot-copied) when its frame qualifies (section 4.6.3). Must be null inside
    workflows."""
    export: PartExportOptions = Field(default_factory=PartExportOptions)


# --------------------------------------------------------------------------------------------------
# Responses
# --------------------------------------------------------------------------------------------------


def part_analysis_url(pa_id: str) -> str:
    return f"{API_V1_PREFIX}/part-analyses/{pa_id}"


class PartRefinementOut(ApiModel):
    status: RefinementStatus
    locked: bool
    eligible: bool
    needs: list[RefinementNeed]

    @classmethod
    def from_domain(cls, refinement: PartRefinementSummary) -> PartRefinementOut:
        return cls(
            status=refinement.status,
            locked=refinement.locked,
            eligible=refinement.eligible,
            needs=list(refinement.needs),
        )


class PartSummaryOut(ApiModel):
    id: str
    label: str
    category: str
    group: str
    kind: Kind
    node_name: str | None
    region_value: int | None
    faces: int
    confidence: float | None
    side: Side | None
    model_url: str | None
    """``None`` for regions and deferred parts (they have no mesh node)."""
    download_url: str | None
    mask_url: str | None
    thumbnail_url: str | None
    refinement: PartRefinementOut

    @classmethod
    def from_domain(cls, pa_id: str, part: PartSummary) -> PartSummaryOut:
        base = part_analysis_url(pa_id)
        part_base = f"{base}/parts/{part.id}"
        return cls(
            id=part.id,
            label=part.label,
            category=part.category,
            group=part.group,
            kind=part.kind,
            node_name=part.node_name,
            region_value=part.region_value,
            faces=part.faces,
            confidence=part.confidence,
            side=part.side,
            model_url=f"{part_base}/model.glb" if part.has_mesh else None,
            download_url=f"{part_base}/export/glb" if part.has_mesh else None,
            mask_url=f"{base}/masks/{part.region_value}" if part.has_mask and part.region_value is not None else None,
            thumbnail_url=f"{base}/thumbs/{part.thumbnail}" if part.thumbnail else None,
            refinement=PartRefinementOut.from_domain(part.refinement),
        )


class PartAnalysisResultOut(ApiModel):
    status: PartAnalysisStatus
    manifest_url: str
    assembled_url: str
    """``model_parts.glb`` (always written; never null)."""
    download_url: str
    zip_url: str
    overlay_url: str | None
    """``None`` for the ``components`` strategy (no segmentation step)."""
    face_labels_url: str
    preview_urls: dict[str, str]
    """``{}`` when the previews step was skipped; otherwise one entry per rendered view."""
    region_attribute: Literal["_FEATURE_ID_0"]
    part_count: int
    physical_count: int
    deferred_count: int
    region_count: int
    camera_fit_iou: float | None
    unlabelled_face_ratio: float
    partner_filled_ratio: float
    cut_partner_consistency: float | None
    parts: list[PartSummaryOut]
    stats: MeshStatsOut | None
    """Statistics of ``model_parts.glb``."""
    timings: dict[str, float]

    @classmethod
    def from_domain(cls, pa_id: str, result: PartAnalysisResult) -> PartAnalysisResultOut:
        base = part_analysis_url(pa_id)
        return cls(
            status=result.status,
            manifest_url=f"{base}/manifest",
            assembled_url=f"{base}/model.glb",
            download_url=f"{base}/export/glb",
            zip_url=f"{base}/export/zip",
            overlay_url=f"{base}/segmentation/overlay.png" if result.has_overlay else None,
            face_labels_url=f"{base}/buffers/face_labels",
            preview_urls={view: f"{base}/previews/{view}.png" for view in result.previews},
            region_attribute=REGION_ATTRIBUTE,
            part_count=result.part_count,
            physical_count=result.physical_count,
            deferred_count=result.deferred_count,
            region_count=result.region_count,
            camera_fit_iou=result.camera_fit_iou,
            unlabelled_face_ratio=result.unlabelled_face_ratio,
            partner_filled_ratio=result.partner_filled_ratio,
            cut_partner_consistency=result.cut_partner_consistency,
            parts=[PartSummaryOut.from_domain(pa_id, part) for part in result.parts],
            stats=MeshStatsOut.model_validate(result.stats.model_dump()) if result.stats is not None else None,
            timings=dict(result.timings),
        )


class PartAnalysisOut(RunOutBase):
    kind: Literal["part_analysis"]
    job_id: str
    result: PartAnalysisResultOut | None

    @classmethod
    def from_run(cls, run: PartAnalysisRun) -> PartAnalysisOut:
        return cls(
            **run_out_fields(run),
            job_id=run.job_id,
            result=PartAnalysisResultOut.from_domain(run.id, run.result) if run.result is not None else None,
        )


class PartAnalysisList(ApiModel):
    items: list[PartAnalysisOut]
