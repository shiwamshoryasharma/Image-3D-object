"""The ``img3d.parts/1`` manifest (part-aware DESIGN.md sections 3.5-3.11). Pydantic; frozen in slice S1.

``manifest.json`` sits at the root of a published part layer (``jobs/<job>/parts/<pa>/asset/``). Every path in it is
relative to ``asset/`` except ``source.model_file``, which is the literal ``model.glb`` of the job directory.

Compatibility rules (asset invariant A9):

* readers ignore unknown fields (every record uses ``extra="ignore"``);
* ``schema`` names the major version; a manifest of another major is rejected, a breaking change needs
  ``img3d.parts/2`` plus a reader-side migration in ``parts/reader.py``.

This module validates structure only: field types and enumerations, well-formed and unique ids, safe relative paths,
self-consistent buffer sizes and the schema major. Cross-checks against the files on disk (hashes, buffer contents,
``face_part`` consistency, region values present in ``label_table``, contiguous mesh indices, refinement lock, base
hash) belong to ``parts/validate.py``, which reports them as a list instead of raising.

Torch-free and numpy-free.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Any, Final, Literal

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field, field_validator, model_validator

from parts.taxonomy import TAXONOMY_VERSION, part_id_problem
from parts.types import (
    BASE_MODEL_FILE,
    BUFFER_ITEM_SIZES,
    EXPECTED_MISSING_SUBJECT,
    INDEX_BASE_FACES,
    INDEX_BASE_VERTICES,
    INDEX_NODE_VERTICES_PREFIX,
    INDEX_WELDED_VERTICES,
    NODE_NAME_PREFIX,
    PARTS_SCHEMA,
    PARTS_SCHEMA_FAMILY,
    PARTS_SCHEMA_MAJOR,
    REFINEMENT_LOCK_REASON,
    REFINEMENT_POLICY,
    REGION_ATTRIBUTE,
    BufferDtype,
    ConditioningMode,
    ExpectationSource,
    FrameRecoveryMethod,
    GeometrySource,
    GeometryStatus,
    GeometryTag,
    Kind,
    PartFrameMethod,
    PartGroup,
    PlacementMethod,
    ProvenanceMethod,
    RefinementNeed,
    RefinementStatus,
    RelationSource,
    RelationType,
    SegmentationProfile,
    SegmentationSourceKind,
    SegmentationSubject,
    SeparableGroup,
    Side,
    StrategyId,
)
from utils.ids import is_valid_id
from utils.timeutil import isoformat_z, parse_iso

IDENTITY_4X4: Final[tuple[float, ...]] = (
    1.0, 0.0, 0.0, 0.0,
    0.0, 1.0, 0.0, 0.0,
    0.0, 0.0, 1.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
)  # fmt: skip
"""Row-major 4x4 identity (column vectors)."""
TRELLIS_TO_ASSET: Final[tuple[float, ...]] = (
    1.0, 0.0, 0.0, 0.0,
    0.0, 0.0, 1.0, 0.0,
    0.0, -1.0, 0.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
)  # fmt: skip
"""Row-major, column vectors: x' = x, y' = z, z' = -y (TRELLIS Blender frame → glTF asset frame, section 3.7)."""
UNIT_AABB: Final[tuple[tuple[float, float, float], tuple[float, float, float]]] = (
    (-0.5, -0.5, -0.5),
    (0.5, 0.5, 0.5),
)

_SCHEMA_PATTERN: Final = re.compile(r"^" + re.escape(PARTS_SCHEMA_FAMILY) + r"/(\d+)$")
_SEAM_ID_PATTERN: Final = re.compile(r"^seam_\d+$")
_RELATION_ID_PATTERN: Final = re.compile(r"^rel_\d+$")
_VIEW_ID_PATTERN: Final = re.compile(r"^(input|view_\d+)$")
_BUFFER_ID_PATTERN: Final = re.compile(r"^[a-z][a-z0-9_.]{0,95}$")
_ANALYSIS_PREFIX: Final = "pa"
_JOB_PREFIX: Final = "job"


# --------------------------------------------------------------------------------------------------
# Field helpers
# --------------------------------------------------------------------------------------------------


def safe_relative_path_problem(value: str) -> str | None:
    """Why ``value`` is not a safe manifest path, or ``None``.

    A manifest path is a relative POSIX path that stays inside ``asset/``: no absolute path, drive letter, backslash,
    empty segment, ``.`` or ``..`` segment and no control character.
    """
    if not isinstance(value, str) or not value:
        return "a path must be a non-empty string"
    if any(ord(ch) < 32 for ch in value):
        return "a path must not contain control characters"
    if "\\" in value:
        return "a path uses '/' separators only"
    if value.startswith("/") or ":" in value:
        return "a path must be relative (no leading '/', no drive or scheme)"
    for segment in value.split("/"):
        if segment in ("", ".", ".."):
            return "a path must not contain empty, '.' or '..' segments"
    return None


def _check_relative_path(value: str) -> str:
    problem = safe_relative_path_problem(value)
    if problem is not None:
        raise ValueError(f"Unsafe manifest path {value!r}: {problem}")
    return value


def _check_part_id(value: str) -> str:
    problem = part_id_problem(value)
    if problem is not None:
        raise ValueError(f"Invalid part id {value!r}: {problem}")
    return value


def _coerce_timestamp(value: Any) -> Any:
    if isinstance(value, datetime):
        return isoformat_z(value)
    return value


def _check_timestamp(value: str) -> str:
    if not value.endswith("Z"):
        raise ValueError("timestamps are ISO-8601 UTC with a 'Z' suffix")
    try:
        parse_iso(value)
    except ValueError as exc:
        raise ValueError(f"invalid ISO-8601 timestamp {value!r}") from exc
    return value


RelPath = Annotated[str, AfterValidator(_check_relative_path)]
"""A path relative to ``asset/`` (see :func:`safe_relative_path_problem`)."""
PartId = Annotated[str, AfterValidator(_check_part_id)]
"""A part id (section 2.3)."""
Timestamp = Annotated[str, BeforeValidator(_coerce_timestamp), AfterValidator(_check_timestamp)]
"""ISO-8601 UTC text with a ``Z`` suffix, kept verbatim (a ``datetime`` is formatted with millisecond precision)."""
UnitFloat = Annotated[float, Field(ge=0.0, le=1.0)]
NonNegInt = Annotated[int, Field(ge=0)]
Vec2 = Annotated[list[float], Field(min_length=2, max_length=2)]
Vec3 = Annotated[list[float], Field(min_length=3, max_length=3)]
Bbox2D = Annotated[list[float], Field(min_length=4, max_length=4)]
"""``[u0, v0, u1, v1]`` (or ``[x0, y0, x1, y1]``)."""
Matrix4 = Annotated[list[float], Field(min_length=16, max_length=16)]
"""Row-major 4x4 matrix (column vectors)."""
Axes3 = Annotated[list[Vec3], Field(min_length=3, max_length=3)]


class ManifestModel(BaseModel):
    """Base of every manifest record: unknown fields are ignored (forward compatibility, A9)."""

    model_config = ConfigDict(
        extra="ignore",
        validate_by_name=True,
        validate_by_alias=True,
        serialize_by_alias=True,
    )


# --------------------------------------------------------------------------------------------------
# Provenance: generator, source, models (section 3.8)
# --------------------------------------------------------------------------------------------------


class ModelRef(ManifestModel):
    """A registry model (or the lifter) used by the analysis."""

    id: str
    revision: str | None = None
    license_id: str


class GeneratorInfo(ManifestModel):
    """How the base mesh was generated (copied from the job result and its metadata)."""

    app_version: str
    engine: str
    model: str
    pipeline_type: str | None = None
    seed: int
    resolution: int
    effective_resolution: int
    texture_size: int
    polygon_target: int


class ImageOrigin(ManifestModel):
    kind: Literal["image"] = "image"


class PromptOrigin(ManifestModel):
    """The base mesh came from a prompt-generated image (``ReferenceImage.source`` of the primary image)."""

    kind: Literal["prompt"] = "prompt"
    image_generation_id: str
    model_id: str
    model_revision: str | None = None
    license_id: str
    seed: int
    final_prompt_sha256: str


Origin = Annotated[ImageOrigin | PromptOrigin, Field(discriminator="kind")]


class SourceInfo(ManifestModel):
    """The base mesh the layer was cut from. ``model_file`` is relative to the job directory (``asset/../../..``)."""

    model_file: Literal["model.glb"] = BASE_MODEL_FILE
    model_sha256: str
    model_bytes: NonNegInt
    vertices: NonNegInt
    faces: NonNegInt
    materials: NonNegInt
    images: NonNegInt
    face_order: Literal["gltf_indices"] = "gltf_indices"
    vertex_order: Literal["gltf_accessor"] = "gltf_accessor"
    primary_image_id: str | None = None
    origin: Origin = Field(default_factory=ImageOrigin)


# --------------------------------------------------------------------------------------------------
# Frames and views (section 3.7)
# --------------------------------------------------------------------------------------------------


class AssetFrame(ManifestModel):
    """The frame of every coordinate unless a record names another one (A6): glTF, +Y up, +Z front, unit AABB."""

    convention: Literal["gltf"] = "gltf"
    up: Literal["+Y"] = "+Y"
    front: Literal["+Z"] = "+Z"
    units: Literal["normalized"] = "normalized"
    aabb: Annotated[list[Vec3], Field(min_length=2, max_length=2)] = Field(
        default_factory=lambda: [list(corner) for corner in UNIT_AABB]
    )


class TrellisFrame(ManifestModel):
    """TRELLIS' internal Blender frame. Only future latent/voxel providers use it (deferred D1/D8)."""

    convention: Literal["blender"] = "blender"
    up: Literal["+Z"] = "+Z"
    front: Literal["-Y"] = "-Y"
    to_asset: Matrix4 = Field(default_factory=lambda: list(TRELLIS_TO_ASSET))


class ConditioningFrameInfo(ManifestModel):
    """The image space the 2D masks live in (section 4.5.2). ``file`` is the conditioning image inside ``asset/``."""

    file: RelPath
    width: Annotated[int, Field(gt=0)]
    height: Annotated[int, Field(gt=0)]
    source_image_id: str | None = None
    source_size: Annotated[list[int], Field(min_length=2, max_length=2)]
    scale: float
    crop_box: Annotated[list[int], Field(min_length=4, max_length=4)] | None = None
    """PIL-rounded ``[x0, y0, x1, y1]`` in resized pixels (may exceed the bounds); ``None`` for untouched inputs."""
    alpha_threshold: float = 0.8
    mode: ConditioningMode
    method: FrameRecoveryMethod
    verified: bool
    residual: float
    """Mean absolute difference over foreground pixels, uint8 units."""


class Frames(ManifestModel):
    asset: AssetFrame = Field(default_factory=AssetFrame)
    trellis: TrellisFrame = Field(default_factory=TrellisFrame)
    conditioning: ConditioningFrameInfo | None = None
    """``None`` when no 2D segmentation was lifted (the ``components`` strategy)."""


class CameraIntrinsics(ManifestModel):
    fx: float
    fy: float
    cx: float
    cy: float
    normalized: bool = True


class ViewCamera(ManifestModel):
    """A lifting camera. Yaw/pitch are in the asset frame; the camera sits at ``d·(sin yaw·cos pitch, sin pitch,
    cos yaw·cos pitch)`` looking at the origin (yaw 0 = front view)."""

    model: Literal["perspective"] = "perspective"
    yaw_deg: float
    pitch_deg: float
    fov_deg: float
    distance: float
    world_to_camera: Matrix4
    intrinsics: CameraIntrinsics
    convention: Literal["opencv"] = "opencv"


class ViewFit(ManifestModel):
    """How the input-view camera was fitted to the conditioning silhouette."""

    method: str
    iou: UnitFloat
    accepted: bool
    candidates_tested: NonNegInt


class ViewRecord(ManifestModel):
    """One camera used for lifting: ``input`` (weight 1.0) or an extra view ``view_<i>`` (weight 0.5)."""

    id: str
    image_frame: str = "conditioning"
    camera: ViewCamera
    fit: ViewFit | None = None
    weight: Annotated[float, Field(ge=0.0)]

    @field_validator("id")
    @classmethod
    def _view_id(cls, value: str) -> str:
        if not _VIEW_ID_PATTERN.match(value):
            raise ValueError("a view id is 'input' or 'view_<i>'")
        return value


# --------------------------------------------------------------------------------------------------
# Strategy, label table, meshes (section 3.10)
# --------------------------------------------------------------------------------------------------


class StrategySegmentation(ManifestModel):
    """Where the lifted labels came from. ``segmentation_id`` keeps a given ``sg_`` id even when it was not reused."""

    source: SegmentationSourceKind
    segmentation_id: str | None = None
    profile: SegmentationProfile | None = None
    subject: SegmentationSubject | None = None


class StrategyInfo(ManifestModel):
    """The effective options of the analysis (``min_part_faces`` is the resolved number)."""

    id: StrategyId
    segmentation: StrategySegmentation
    separate: list[SeparableGroup] = Field(default_factory=list)
    min_part_faces: NonNegInt
    max_parts: Annotated[int, Field(ge=1)]
    split_shells: bool
    views: NonNegInt
    models: list[ModelRef] = Field(default_factory=list)


class LabelEntry(ManifestModel):
    """One taxonomy value present in ``face_labels``."""

    value: Annotated[int, Field(ge=0, le=0xFFFF)]
    category: str


class MeshEntry(ManifestModel):
    """One mesh node of ``model_parts.glb`` (index 0 = base)."""

    index: NonNegInt
    part_id: PartId
    node_name: str
    faces: NonNegInt
    vertices: NonNegInt
    unique_vertices: NonNegInt
    vertex_map: str
    """Buffer id of the node's vertex map (``vmap_<part id>``)."""

    @model_validator(mode="after")
    def _node_name_matches(self) -> MeshEntry:
        if self.node_name != f"{NODE_NAME_PREFIX}{self.part_id}":
            raise ValueError(f"node_name must be '{NODE_NAME_PREFIX}{self.part_id}'")
        return self


# --------------------------------------------------------------------------------------------------
# Part record (section 3.10.1)
# --------------------------------------------------------------------------------------------------


class PartRegion(ManifestModel):
    """The faces that carry this record's class value (``_FEATURE_ID_0``)."""

    attribute: Literal["_FEATURE_ID_0"] = REGION_ATTRIBUTE
    value: Annotated[int, Field(ge=0, le=0xFFFF)]
    face_count: NonNegInt


class PartFiles(ManifestModel):
    """Per-part files. ``mask`` is a PNG in conditioning space; ``glb`` only exists with ``export.per_part_glbs``."""

    glb: RelPath | None = None
    mask: RelPath | None = None
    thumbnail: RelPath | None = None


class PartStats(ManifestModel):
    """Asset-frame statistics of the part's faces."""

    faces: NonNegInt
    vertices: NonNegInt
    area: Annotated[float, Field(ge=0.0)]
    bbox_min: Vec3
    bbox_max: Vec3
    centroid: Vec3


class PartFrame(ManifestModel):
    """Pivot and right-handed PCA axes of the part (identity axes with fewer than 3 vertices)."""

    pivot: Vec3
    axes: Axes3
    method: PartFrameMethod


class PartPlacement(ManifestModel):
    """Part vertices stay in the asset frame, so the placement is the identity in this milestone."""

    transform: Matrix4 = Field(default_factory=lambda: list(IDENTITY_4X4))
    method: PlacementMethod = "identity"
    error: float | None = None


class Floater(ManifestModel):
    """A small unlabelled shell left on the base without a part record (section 2.4 rule 2); never deleted."""

    component: NonNegInt
    faces: NonNegInt
    centroid: Vec3


class PartGeometryInfo(ManifestModel):
    """What geometry the part has. ``floaters`` is non-empty only on the base record; the key is always present."""

    status: GeometryStatus
    open_boundary_loops: NonNegInt = 0
    watertight: bool = False
    occluded_geometry_missing: bool = False
    components: NonNegInt = 0
    source: GeometrySource
    tags: list[GeometryTag] = Field(default_factory=list)
    floaters: list[Floater] = Field(default_factory=list)


class PartUv(ManifestModel):
    """Every node uses material 0 and the one shared atlas (A5)."""

    material: Literal[0] = 0
    atlas: Literal["shared"] = "shared"
    uv_bbox: Bbox2D
    charts_cross_boundary: bool
    """``True`` for cut parts: atlas charts are not split at part borders."""


class Guide2D(ManifestModel):
    """A 2D guide such as an eyelash ``lid_curve`` (3D guides are deferred)."""

    space: Literal["conditioning", "source"] = "conditioning"
    points: list[Vec2] = Field(default_factory=list)
    method: str
    confidence: UnitFloat | None = None


class ProvenanceSource(ManifestModel):
    method: ProvenanceMethod
    provider: str
    model: str | None = None
    score: float | None = None


class PartProvenance(ManifestModel):
    """Honest provenance (A7): every part lists at least one source."""

    sources: Annotated[list[ProvenanceSource], Field(min_length=1)]
    votes: Annotated[float, Field(ge=0.0)] = 0.0


class RefinementConstraints(ManifestModel):
    """What a future consumer must keep (``seam_lock``: loop vertex positions stay fixed for crack-free reassembly)."""

    seam_lock: bool = True
    keep_uv_atlas: bool = True
    keep_frame: bool = True


class PartRefinement(ManifestModel):
    """Description only; nothing consumes it in this milestone (section 3.9). Locked while refinement is disabled."""

    status: RefinementStatus = "untouched"
    locked: bool = True
    lock_reason: str | None = REFINEMENT_LOCK_REASON
    eligible: bool = True
    needs: list[RefinementNeed] = Field(default_factory=list)
    constraints: RefinementConstraints = Field(default_factory=RefinementConstraints)
    history: list[dict[str, Any]] = Field(default_factory=list)


class LineageEntry(ManifestModel):
    """An earlier part of the same base mesh whose face set overlaps this one (IoU >= 0.5)."""

    part_id: PartId
    analysis_id: str
    face_iou: UnitFloat


class PartLineage(ManifestModel):
    derived_from: Annotated[list[LineageEntry], Field(max_length=3)] = Field(default_factory=list)
    """At most 3 entries, highest IoU first."""


class PartRecord(ManifestModel):
    """One part: the base, a physical part, a deferred part record or a region (section 3.10.1)."""

    id: PartId
    """Also the file stem of the part's files."""
    category: str
    """Taxonomy class id (``unknown`` for ``component_###`` shells)."""
    group: PartGroup
    label: str
    kind: Kind
    side: Side | None = None
    image_side: Side | None = None
    parent: PartId | None = None
    attach_to: PartId | None = None
    host: PartId | None = None
    """The mesh node owning a region or deferred part; ``None`` for mesh-owning parts."""
    symmetric_with: PartId | None = None
    node_name: str | None = None
    """``part:<id>`` for mesh-owning parts; ``None`` for regions and deferred parts."""
    mesh_index: NonNegInt | None = None
    region: PartRegion
    files: PartFiles = Field(default_factory=PartFiles)
    stats: PartStats
    frame: PartFrame
    placement: PartPlacement = Field(default_factory=PartPlacement)
    geometry: PartGeometryInfo
    uv: PartUv
    seams: list[str] = Field(default_factory=list)
    annotations: dict[str, Guide2D] = Field(default_factory=dict)
    confidence: UnitFloat | None = None
    expected: bool = False
    provenance: PartProvenance
    refinement: PartRefinement = Field(default_factory=PartRefinement)
    lineage: PartLineage = Field(default_factory=PartLineage)

    @field_validator("category")
    @classmethod
    def _category_is_class_id(cls, value: str) -> str:
        problem = part_id_problem(value)
        if problem is not None:
            raise ValueError(f"Invalid category {value!r}: {problem}")
        return value

    @model_validator(mode="after")
    def _node_name_matches(self) -> PartRecord:
        if self.node_name is not None and self.node_name != f"{NODE_NAME_PREFIX}{self.id}":
            raise ValueError(f"node_name must be '{NODE_NAME_PREFIX}{self.id}' or null")
        return self

    @property
    def owns_mesh(self) -> bool:
        """``True`` for the base and physical parts (they have a mesh node)."""
        return self.kind in ("base", "physical")


# --------------------------------------------------------------------------------------------------
# Seams and relations (sections 3.5, 3.6)
# --------------------------------------------------------------------------------------------------


class SeamLoop(ManifestModel):
    """One boundary loop: a slice ``[start, start + count)`` of the ``seam_loops`` buffer (welded vertex ids)."""

    start: NonNegInt
    count: NonNegInt
    closed: bool
    """``False`` for a chain that ended at a branch vertex (non-manifold junction)."""
    length: Annotated[float, Field(ge=0.0)]


class SeamRecord(ManifestModel):
    """The cut between physical part ``a`` and owner ``b``; outer- and inner-sheet loops share one record."""

    id: str
    a: PartId
    b: PartId
    host_opening: bool
    """``True`` when ``b`` is the base: the cut left a recorded, never filled opening there."""
    boundary_edges: NonNegInt
    length: Annotated[float, Field(ge=0.0)]
    loops: list[SeamLoop] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _seam_id(cls, value: str) -> str:
        if not _SEAM_ID_PATTERN.match(value):
            raise ValueError("a seam id is 'seam_<n>'")
        return value


class RelationRecord(ManifestModel):
    """A typed, directed edge between two parts, or between the asset and a missing class (``expected_missing``)."""

    id: str
    type: RelationType
    a: str
    """A part id, or ``asset`` for ``expected_missing``."""
    b: str
    """A part id, or a taxonomy class id for ``expected_missing``."""
    via: str | None = None
    """Seam id the relation was derived from (``attached_to`` by seam contact)."""
    metrics: dict[str, Any] = Field(default_factory=dict)
    source: RelationSource
    confidence: UnitFloat

    @field_validator("id")
    @classmethod
    def _relation_id(cls, value: str) -> str:
        if not _RELATION_ID_PATTERN.match(value):
            raise ValueError("a relation id is 'rel_<n>'")
        return value

    @model_validator(mode="after")
    def _endpoints(self) -> RelationRecord:
        if (self.type == "expected_missing") != (self.a == EXPECTED_MISSING_SUBJECT):
            raise ValueError(
                f"'{EXPECTED_MISSING_SUBJECT}' is the subject of every expected_missing relation, and only of those"
            )
        if self.a != EXPECTED_MISSING_SUBJECT:
            _check_part_id(self.a)
        _check_part_id(self.b)
        return self


# --------------------------------------------------------------------------------------------------
# Buffers and artifacts (sections 3.3, 3.10)
# --------------------------------------------------------------------------------------------------


class BufferRecord(ManifestModel):
    """A raw little-endian buffer without header, read with ``np.fromfile(path, dtype)`` after checking its size and
    sha256. ``byte_length == count * components * itemsize``."""

    id: str
    file: RelPath
    dtype: BufferDtype
    components: Annotated[int, Field(ge=1)] = 1
    count: NonNegInt
    byte_length: NonNegInt
    sha256: str
    indexes: str
    """``base_faces``, ``base_vertices``, ``welded_vertices`` or ``node_vertices:<part id>``."""
    description: str = ""

    @field_validator("id")
    @classmethod
    def _buffer_id(cls, value: str) -> str:
        if not _BUFFER_ID_PATTERN.match(value):
            raise ValueError("a buffer id is lower-case [a-z0-9_.] starting with a letter")
        return value

    @field_validator("indexes")
    @classmethod
    def _indexes(cls, value: str) -> str:
        if value in (INDEX_BASE_FACES, INDEX_BASE_VERTICES, INDEX_WELDED_VERTICES):
            return value
        if value.startswith(INDEX_NODE_VERTICES_PREFIX):
            _check_part_id(value[len(INDEX_NODE_VERTICES_PREFIX) :])
            return value
        raise ValueError(
            "indexes must be base_faces, base_vertices, welded_vertices or node_vertices:<part id>"
        )

    @model_validator(mode="after")
    def _size(self) -> BufferRecord:
        expected = self.count * self.components * BUFFER_ITEM_SIZES[self.dtype]
        if self.byte_length != expected:
            raise ValueError(
                f"byte_length {self.byte_length} != count x components x itemsize ({expected}) for buffer {self.id!r}"
            )
        return self


class AssembledGlb(ManifestModel):
    """``model_parts.glb``: always written, never null."""

    file: RelPath
    sha256: str
    bytes: NonNegInt
    nodes: NonNegInt


class SegmentationArtifacts(ManifestModel):
    """The 2D segmentation that was actually lifted (``asset/segmentation/``)."""

    labels: RelPath
    overlay: RelPath
    frame: RelPath
    source: RelPath


class Artifacts(ManifestModel):
    assembled_glb: AssembledGlb
    part_glbs: dict[str, RelPath] = Field(default_factory=dict)
    """Per-part GLBs written at run time (only with ``export.per_part_glbs``), keyed by part id."""
    segmentation: SegmentationArtifacts | None = None
    masks_dir: RelPath | None = None
    previews: list[RelPath] = Field(default_factory=list)
    log: RelPath | None = None

    @field_validator("part_glbs")
    @classmethod
    def _part_glb_keys(cls, value: dict[str, str]) -> dict[str, str]:
        for part_id in value:
            _check_part_id(part_id)
        return value


class Expectations(ManifestModel):
    """Classes a prompt noun or an extra term asked for, and which of them were not found."""

    source: ExpectationSource | None = None
    expected_classes: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)


class ProvenanceStep(ManifestModel):
    id: str
    models: list[ModelRef] = Field(default_factory=list)
    duration_s: float | None = None


class ManifestProvenance(ManifestModel):
    steps: list[ProvenanceStep] = Field(default_factory=list)
    licences: list[str] = Field(default_factory=list)
    """Every licence involved: TRELLIS.2/BiRefNet (mit), dinov3-license, nvdiffrast, plus the models actually used."""


class ConsumerRecord(ManifestModel):
    """A registered part consumer (``ConsumerInfo``). None is registered while refinement is disabled."""

    id: str
    label: str
    version: str
    handles_needs: list[RefinementNeed] = Field(default_factory=list)


class ManifestRefinement(ManifestModel):
    enabled: bool = False
    policy: str = REFINEMENT_POLICY
    consumers: list[ConsumerRecord] = Field(default_factory=list)


class Reassembly(ManifestModel):
    """Result of the bit-exact reassembly check on the written files (A4); every flag must be true to publish."""

    method: Literal["vmap_gather"] = "vmap_gather"
    verified: bool
    positions_exact: bool
    uvs_exact: bool
    normals_exact: bool
    faces_exact: bool

    @property
    def all_exact(self) -> bool:
        return self.verified and self.positions_exact and self.uvs_exact and self.normals_exact and self.faces_exact


class Summary(ManifestModel):
    """Headline numbers. ``partner_filled_ratio`` = share of faces with ``face_flags`` bit 0;
    ``cut_partner_consistency`` = min over cut parts of the share of faces whose partner has the same owner (``None``
    without cuts); ``camera_fit_iou`` is ``None`` for the ``components`` strategy."""

    part_count: NonNegInt
    physical_count: NonNegInt
    deferred_count: NonNegInt
    region_count: NonNegInt
    unlabelled_face_ratio: UnitFloat
    partner_filled_ratio: UnitFloat = 0.0
    cut_partner_consistency: UnitFloat | None = None
    camera_fit_iou: UnitFloat | None = None


class ManifestWarning(ManifestModel):
    """A warning of the analysis (codes of section 6.8)."""

    code: str
    message: str
    part_id: PartId | None = None


# --------------------------------------------------------------------------------------------------
# Manifest
# --------------------------------------------------------------------------------------------------


class PartsManifest(ManifestModel):
    """``manifest.json`` of a published part layer (``img3d.parts/1``)."""

    schema_id: str = Field(default=PARTS_SCHEMA, alias="schema")
    analysis_id: str
    job_id: str
    created_at: Timestamp
    taxonomy_version: Annotated[int, Field(ge=1)] = TAXONOMY_VERSION
    generator: GeneratorInfo
    source: SourceInfo
    frames: Frames = Field(default_factory=Frames)
    views: list[ViewRecord] = Field(default_factory=list)
    """Lifting cameras (``[]`` for the ``components`` strategy)."""
    strategy: StrategyInfo
    region_attribute: Literal["_FEATURE_ID_0"] = REGION_ATTRIBUTE
    label_table: list[LabelEntry] = Field(default_factory=list)
    meshes: list[MeshEntry] = Field(default_factory=list)
    parts: list[PartRecord] = Field(default_factory=list)
    seams: list[SeamRecord] = Field(default_factory=list)
    relations: list[RelationRecord] = Field(default_factory=list)
    buffers: list[BufferRecord] = Field(default_factory=list)
    artifacts: Artifacts
    expectations: Expectations = Field(default_factory=Expectations)
    provenance: ManifestProvenance = Field(default_factory=ManifestProvenance)
    refinement: ManifestRefinement = Field(default_factory=ManifestRefinement)
    reassembly: Reassembly
    summary: Summary
    warnings: list[ManifestWarning] = Field(default_factory=list)

    @field_validator("schema_id")
    @classmethod
    def _schema_major(cls, value: str) -> str:
        match = _SCHEMA_PATTERN.match(value)
        if match is None:
            raise ValueError(f"schema must be '{PARTS_SCHEMA_FAMILY}/<major>', got {value!r}")
        if int(match.group(1)) != PARTS_SCHEMA_MAJOR:
            raise ValueError(f"unsupported manifest schema {value!r}; this reader supports {PARTS_SCHEMA!r}")
        return value

    @field_validator("analysis_id")
    @classmethod
    def _analysis_id(cls, value: str) -> str:
        if not is_valid_id(value, _ANALYSIS_PREFIX):
            raise ValueError("analysis_id must be a 'pa_<12 hex>' id")
        return value

    @field_validator("job_id")
    @classmethod
    def _job_id(cls, value: str) -> str:
        if not is_valid_id(value, _JOB_PREFIX):
            raise ValueError("job_id must be a 'job_<12 hex>' id")
        return value

    @model_validator(mode="after")
    def _unique_ids(self) -> PartsManifest:
        _require_unique("part id", [part.id for part in self.parts])
        _require_unique("mesh index", [mesh.index for mesh in self.meshes])
        _require_unique("mesh part_id", [mesh.part_id for mesh in self.meshes])
        _require_unique("seam id", [seam.id for seam in self.seams])
        _require_unique("relation id", [relation.id for relation in self.relations])
        _require_unique("buffer id", [buffer.id for buffer in self.buffers])
        _require_unique("buffer file", [buffer.file for buffer in self.buffers])
        _require_unique("label_table value", [entry.value for entry in self.label_table])
        return self

    # ------------------------------------------------------------------ lookups
    def part(self, part_id: str) -> PartRecord | None:
        return next((part for part in self.parts if part.id == part_id), None)

    def part_index(self, part_id: str) -> int:
        """Index of ``part_id`` in ``parts[]`` (the value stored in ``face_part``). Raises :class:`KeyError`."""
        for index, part in enumerate(self.parts):
            if part.id == part_id:
                return index
        raise KeyError(part_id)

    def buffer(self, buffer_id: str) -> BufferRecord | None:
        return next((buffer for buffer in self.buffers if buffer.id == buffer_id), None)

    def mesh(self, part_id: str) -> MeshEntry | None:
        return next((mesh for mesh in self.meshes if mesh.part_id == part_id), None)

    def relations_of(self, part_id: str) -> list[RelationRecord]:
        return [relation for relation in self.relations if part_id in (relation.a, relation.b)]

    def to_json_dict(self) -> dict[str, Any]:
        """The JSON object written to ``manifest.json`` (``schema`` key, explicit nulls kept)."""
        return self.model_dump(mode="json", by_alias=True)


def _require_unique(what: str, values: list[Any]) -> None:
    seen: set[Any] = set()
    duplicates: list[str] = []
    for value in values:
        if value in seen:
            duplicates.append(str(value))
        seen.add(value)
    if duplicates:
        raise ValueError(f"duplicate {what}(s): {', '.join(sorted(set(duplicates)))}")
