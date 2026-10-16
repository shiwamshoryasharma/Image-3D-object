"""Part analysis run (``pa_``) domain record (part-aware DESIGN.md section 4.6). Frozen in slice S1.

Persisted as ``runtime/jobs/<job>/parts/<pa>/run.json`` from queue time; the published, immutable part layer is
``jobs/<job>/parts/<pa>/asset/`` (manifest ``img3d.parts/1``, ``model_parts.glb``, buffers, masks, thumbnails,
segmentation, previews) and failed runs keep a ``log.txt`` tail. The job's ``model.glb``, ``preview.png`` and
``job.json`` are only ever read.

``options`` holds the effective request (``PartAnalysisOptions`` with the resolved strategy and ``min_part_faces``).
The result keeps what the API needs to build its URLs; everything else is in the manifest.
"""

from __future__ import annotations

from typing import Final, Literal

from pydantic import Field

from models.job import MeshStats
from models.runs import RunRecord
from models.types import DomainModel
from parts.types import Kind, RefinementNeed, RefinementStatus, Side

PartAnalysisStatus = Literal["completed", "partial"]
"""``partial``: the frame was unverified, the camera fit was rejected or the segmentation degraded (with warnings)."""

PREVIEW_VIEWS: Final[tuple[str, ...]] = ("front", "back", "left", "right")


class PartRefinementSummary(DomainModel):
    """The locked refinement state of a part (section 3.9); ``untouched`` and locked in this milestone."""

    status: RefinementStatus = "untouched"
    locked: bool = True
    eligible: bool = True
    needs: list[RefinementNeed] = Field(default_factory=list)


class PartSummary(DomainModel):
    """One manifest part record, reduced to what ``PartAnalysisOut.result.parts[]`` shows."""

    id: str
    label: str
    category: str
    group: str
    kind: Kind
    node_name: str | None = None
    """``part:<id>`` for mesh-owning parts (base, physical); ``None`` for regions and deferred parts."""
    region_value: int | None = None
    faces: int = 0
    confidence: float | None = None
    side: Side | None = None
    has_mask: bool = False
    """``masks/<region_value>.png`` exists in the asset."""
    thumbnail: str | None = None
    """File name under ``thumbs/`` (e.g. ``hair.webp``), from the manifest ``files.thumbnail``."""
    refinement: PartRefinementSummary = Field(default_factory=PartRefinementSummary)

    @property
    def has_mesh(self) -> bool:
        return self.kind in ("base", "physical")


class PartAnalysisResult(DomainModel):
    status: PartAnalysisStatus = "completed"
    part_count: int = 0
    physical_count: int = 0
    deferred_count: int = 0
    region_count: int = 0
    camera_fit_iou: float | None = None
    """``None`` for the ``components`` strategy (no camera fit)."""
    unlabelled_face_ratio: float = 0.0
    partner_filled_ratio: float = 0.0
    cut_partner_consistency: float | None = None
    """``None`` when no part was cut."""
    has_overlay: bool = False
    """``asset/segmentation/overlay.png`` exists (semantic strategies)."""
    previews: list[str] = Field(default_factory=list)
    """Rendered preview views (subset of :data:`PREVIEW_VIEWS`); empty when the previews step was skipped."""
    parts: list[PartSummary] = Field(default_factory=list)
    stats: MeshStats | None = None
    """Statistics of ``model_parts.glb``."""
    timings: dict[str, float] = Field(default_factory=dict)

    def part(self, part_id: str) -> PartSummary | None:
        return next((p for p in self.parts if p.id == part_id), None)


class PartAnalysisRun(RunRecord):
    kind: Literal["part_analysis"] = "part_analysis"
    job_id: str
    result: PartAnalysisResult | None = None
