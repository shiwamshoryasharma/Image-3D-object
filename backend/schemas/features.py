"""Discovery schema: ``GET /features`` and the ``features.updated`` payload (part-aware DESIGN.md section 6.1).
Frozen in slice S1.

Every capability entry (pipeline, profile, strategy, node) carries ``available``, ``code`` (a ``ReasonCode``) and
``reason`` (``None`` when available), so the four UI modes gate their controls from one payload. ``/features``
returning 404 means a legacy backend (image-to-3D only).
"""

from __future__ import annotations

from typing import Final, Literal

from pydantic import ConfigDict, Field

from inference.providers.base import ModelUiState, ReasonCode
from parts import taxonomy as part_taxonomy
from parts.types import Kind, SegmentationProfile, StrategyId
from schemas.common import ApiModel
from schemas.image_generation import PromptPreset, PromptStyle
from schemas.model_registry import ModelOut
from schemas.runs import RunQueueOut

FEATURES_SCHEMA: Final = "img3d.features/1"
FEATURES_API_VERSION: Final = "1.1.0"
"""API version reported by ``/features.api.version`` (1.0 = the image-to-3D contract; 1.1 = the part-aware layer)."""
PAUSED_REASON: Final = "Paused until the part-aware backend layer is approved"


class CapabilityOut(ApiModel):
    available: bool
    code: ReasonCode
    reason: str | None = None


class FeaturesApiOut(ApiModel):
    version: str = FEATURES_API_VERSION
    workflows: bool
    part_analyses: bool
    image_generations: bool
    segmentations: bool


class FeatureFlagsOut(ApiModel):
    part_aware: bool
    image_generation: bool
    segmentation: bool
    part_analysis: bool
    workflows: bool
    mesh_tuning: bool
    part_refinement: bool
    persist_generation_intermediates: bool = False
    """Inert (SD1 not approved): always reported ``false`` whatever the environment says."""


class PromptToThreeDOut(CapabilityOut):
    max_prompt_chars: int
    presets: list[PromptPreset]
    styles: list[PromptStyle]
    supports_negative_prompt: bool
    default_model_id: str | None
    default_model_state: ModelUiState | None
    cold_start_s: float | None
    max_images: int
    sizes: list[list[int]]
    selection_modes: list[str]


class SegmentationProfileOut(CapabilityOut):
    id: SegmentationProfile


class SegmentationCapabilityOut(CapabilityOut):
    subjects: list[str]
    profiles: list[SegmentationProfileOut]
    default_profile: str
    max_extra_terms: int
    before_reconstruction: bool


class StrategyOut(CapabilityOut):
    id: StrategyId
    label: str
    requires_segmentation: bool


class PartGroupOut(ApiModel):
    id: str
    label: str
    default_kind: Kind
    default_selected: bool
    note: str | None

    @classmethod
    def from_group(cls, group: part_taxonomy.PartGroupInfo) -> PartGroupOut:
        return cls(
            id=group.id,
            label=group.label,
            default_kind=group.kind_selected,
            default_selected=group.id in part_taxonomy.DEFAULT_SEPARATE or not group.separable,
            note=group.note,
        )

    @classmethod
    def separable_groups(cls) -> list[PartGroupOut]:
        """The seven separable groups (``/features.pipeline.part_extraction.separable_groups``)."""
        return [cls.from_group(part_taxonomy.GROUPS_BY_ID[group_id]) for group_id in part_taxonomy.SEPARABLE_GROUP_IDS]

    @classmethod
    def region_groups(cls) -> list[PartGroupOut]:
        """The region-only groups listed next to them (``face``, ``body``; regions are always labelled)."""
        return [cls.from_group(part_taxonomy.GROUPS_BY_ID[group_id]) for group_id in part_taxonomy.REGION_GROUP_IDS]


class PartExtractionCapabilityOut(CapabilityOut):
    strategies: list[StrategyOut]
    default_strategy: StrategyId | None
    separable_groups: list[PartGroupOut]
    region_groups: list[PartGroupOut]
    max_views: int
    default_views: int
    rebuild: bool
    """Completed jobs (legacy ones included) can get a part analysis later."""
    per_part_glbs: Literal["on_demand", "always"]
    """``on_demand``: per-part GLBs are built on request into the export cache (default)."""


class PipelineOut(ApiModel):
    image_to_3d: CapabilityOut
    prompt_to_3d: PromptToThreeDOut
    segmentation: SegmentationCapabilityOut
    part_extraction: PartExtractionCapabilityOut


class WorkflowCapabilityOut(CapabilityOut):
    catalog_url: str
    catalog_etag: str
    max_nodes: int
    max_edges: int
    graph_max_bytes: int
    selection_modes: list[str]
    legacy_job_node_map: dict[str, str]
    """``JobStage`` value → node type, to light graph nodes for plain ``POST /jobs`` jobs."""


class FeatureNodeOut(ApiModel):
    type: str
    title: str
    category: str
    available: bool
    locked: bool
    code: ReasonCode
    reason: str | None
    hidden_by_default: bool


class ToggleOut(ApiModel):
    """State of a paused, flag-gated capability (``mesh_tuning``, ``part_refinement``)."""

    enabled: bool
    reason: str | None


class TaxonomyClassOut(ApiModel):
    value: int
    id: str
    group: str
    label: str
    default_kind: Kind
    attach_to: list[str]
    symmetric_with: str | None
    sided: bool


class TaxonomyOut(ApiModel):
    version: int
    groups: list[str]
    classes: list[TaxonomyClassOut]

    @classmethod
    def current(cls) -> TaxonomyOut:
        """Taxonomy v1 (``parts.taxonomy``)."""
        return cls.model_validate(part_taxonomy.to_out())


class QueuesOut(ApiModel):
    runs: RunQueueOut


class FeatureLimitsOut(ApiModel):
    prompt_max_chars: int
    negative_prompt_max_chars: int
    extra_terms_max: int
    views_max: int
    workflow_graph_max_bytes: int
    max_parts: int


class FeaturesOut(ApiModel):
    """``GET /features`` / ``features.updated`` (``img3d.features/1``)."""

    model_config = ConfigDict(
        extra="ignore", from_attributes=True, validate_by_name=True, validate_by_alias=True, serialize_by_alias=True
    )

    schema_id: Literal["img3d.features/1"] = Field(default=FEATURES_SCHEMA, alias="schema")
    api: FeaturesApiOut
    flags: FeatureFlagsOut
    pipeline: PipelineOut
    workflow: WorkflowCapabilityOut
    nodes: list[FeatureNodeOut]
    mesh_tuning: ToggleOut
    part_refinement: ToggleOut
    models: list[ModelOut]
    """Full ``ModelOut`` entries, ``hidden_by_default`` ones excluded; each equals ``GET /models/{id}``."""
    taxonomy: TaxonomyOut
    queues: QueuesOut
    limits: FeatureLimitsOut
