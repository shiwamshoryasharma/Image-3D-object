"""Shared vocabulary of the part asset layer (part-aware DESIGN.md sections 3.1-3.10). Frozen in slice S1.

Every literal below is part of the ``img3d.parts/1`` manifest or of the per-face buffers next to it, so its values are
a file format: they are never renamed, and a new value is added only together with every reader of it.

Spaces (section 3.1):

* **base face space**: faces of the single ``model.glb`` primitive in index-accessor order, ``f in [0, F)``. Every
  per-face buffer is indexed by it.
* **base vertex space**: vertices of that primitive in accessor order, UV-seam duplicates included.
* **welded vertex space**: base vertices grouped by bit-identical position (``weld_map[v] = w``); used for adjacency,
  components and seams, never to reorder output.

Torch-free: numpy only.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Literal

if TYPE_CHECKING:
    import numpy as np

# --------------------------------------------------------------------------------------------------
# Format constants
# --------------------------------------------------------------------------------------------------

PARTS_SCHEMA: Final = "img3d.parts/1"
"""Value of the manifest ``schema`` field. It names the major version (asset invariant A9)."""
PARTS_SCHEMA_FAMILY: Final = "img3d.parts"
PARTS_SCHEMA_MAJOR: Final = 1

REGION_ATTRIBUTE: Final = "_FEATURE_ID_0"
"""Per-vertex glTF attribute (UNSIGNED_SHORT) that carries the taxonomy value of every face of a mesh node."""

BASE_MODEL_FILE: Final = "model.glb"
"""The job's base mesh, relative to the job directory (the one manifest path that is not relative to ``asset/``)."""
MANIFEST_FILE: Final = "manifest.json"
ASSEMBLED_GLB_FILE: Final = "model_parts.glb"
BUFFERS_DIR: Final = "buffers"
MASKS_DIR: Final = "masks"
THUMBS_DIR: Final = "thumbs"
PART_GLBS_DIR: Final = "parts"
SEGMENTATION_DIR: Final = "segmentation"
PREVIEWS_DIR: Final = "previews"

BASE_MESH_INDEX: Final = 0
"""Mesh index of the base (body or object): ``face_owner == 0``."""
NODE_NAME_PREFIX: Final = "part:"
"""glTF node names in ``model_parts.glb`` are ``part:<part id>`` (readers match ``extras.img3d.id`` first)."""

FACE_PART_NONE: Final = 0xFFFF
"""``face_part`` value of a face that no part record covers (floaters, unlabelled base faces)."""

FACE_FLAG_PARTNER: Final = 1 << 0
"""``face_flags`` bit 0: the label was taken from the opposite-sheet partner face (section 4.6.5 step 5.2)."""
FACE_FLAG_PROPAGATED: Final = 1 << 1
"""``face_flags`` bit 1: the label came from colour-affinity graph propagation (step 5.3)."""
FACE_FLAG_COMPONENT_FILL: Final = 1 << 2
"""``face_flags`` bit 2: the label came from component fill (step 5.6)."""
FACE_FLAGS_RESERVED_MASK: Final = 0b1111_1000
"""Bits 3-7 of ``face_flags`` are reserved and always zero."""

REFINEMENT_LOCK_REASON: Final = "part_refinement_disabled"
REFINEMENT_POLICY: Final = "locked_until_approved"
EXPECTED_MISSING_SUBJECT: Final = "asset"
"""``a`` of an ``expected_missing`` relation (``b`` is then a taxonomy class id, not a part id)."""

# --------------------------------------------------------------------------------------------------
# Vocabulary
# --------------------------------------------------------------------------------------------------

Kind = Literal["base", "physical", "physical_deferred", "region"]
"""``base``: the continuous remainder that owns every face not in a physical part. ``physical``: faces moved into their
own mesh node (an open surface patch with seams). ``physical_deferred``: a real part record (mask, surface region,
guides) whose geometry is left to the future tuning layer. ``region``: a semantic label on faces of a mesh node."""

Side = Literal["left", "right", "center"]
"""Character-relative side (the character's left is image-right in a front view)."""

PartGroup = Literal[
    "body", "face", "eyes", "eyebrows", "eyelashes", "hair", "clothes", "accessories", "detachable", "other"
]
SeparableGroup = Literal["eyes", "eyebrows", "eyelashes", "hair", "clothes", "accessories", "detachable"]
"""The groups a request may ``separate`` (section 2.2); the default is all seven."""
RegionGroup = Literal["face", "body"]

StrategyId = Literal["components", "semantic_regions", "semantic_parts", "per_part_regeneration"]
"""Part-analysis strategies (section 4.6.1). ``per_part_regeneration`` is reserved and never produced."""

SegmentationProfile = Literal["baseline", "anime", "realistic"]
SegmentationSubject = Literal["anime_character", "realistic_human", "object"]
SegmentationSourceKind = Literal["none", "internal", "reused", "persisted"]
"""Where the lifted 2D labels came from (section 4.6.3). ``persisted`` is reserved (SD1 deferred), never produced."""

ConditioningMode = Literal["alpha_crop", "background_removed", "untouched"]
FrameRecoveryMethod = Literal["recompute", "persisted", "template_match", "identity"]
"""How the conditioning frame was recovered (section 4.5.2). ``persisted`` is reserved, never produced."""

GeometryStatus = Literal["surface", "open_surface", "closed", "texture_only", "absent"]
GeometrySource = Literal["lifted", "component", "none"]
GeometryTag = Literal["inner_sheet_merged", "floater_candidate"]
"""``floater_candidate`` stays reserved in the schema but is no longer produced (section 2.4 rule 2)."""

PartFrameMethod = Literal["seam_centroid", "bbox_center", "area_centroid"]
PlacementMethod = Literal["identity", "registered"]
ProvenanceMethod = Literal["lifted", "components", "heuristic2d", "taxonomy_default", "prompt", "assumed_front"]

RefinementStatus = Literal["untouched", "queued", "running", "refined", "failed", "rejected"]
"""Reserved by the schema; ``untouched`` is the only value produced in this milestone (section 3.9)."""
RefinementNeed = Literal["occluded_geometry", "geometry", "closure", "low_confidence"]
"""Facts, not operations: what a future (approved) refinement consumer would have to supply."""

RelationType = Literal[
    "attached_to",
    "region_of",
    "adjacent_to",
    "symmetric_with",
    "layered_over",
    "occludes",
    "held_by",
    "expected_missing",
]
RelationSource = Literal["geometry", "taxonomy_default", "prompt", "extra_terms"]
ExpectationSource = Literal["prompt", "extra_terms"]

BufferDtype = Literal["uint8", "uint16", "uint32"]

BUFFER_ITEM_SIZES: Final[Mapping[str, int]] = MappingProxyType({"uint8": 1, "uint16": 2, "uint32": 4})
"""Bytes per component of each buffer dtype (all buffers are raw little-endian, no header)."""

# --------------------------------------------------------------------------------------------------
# Binary buffers (section 3.3; written by parts/buffers.py into <asset>/buffers/)
# --------------------------------------------------------------------------------------------------

INDEX_BASE_FACES: Final = "base_faces"
INDEX_BASE_VERTICES: Final = "base_vertices"
INDEX_WELDED_VERTICES: Final = "welded_vertices"
INDEX_NODE_VERTICES_PREFIX: Final = "node_vertices:"
"""``indexes`` of a ``vmap_<id>`` buffer is ``node_vertices:<part id>``."""

VMAP_PREFIX: Final = "vmap_"


@dataclass(frozen=True, slots=True)
class BufferSpec:
    """Static description of one fixed buffer of the part layer (manifest ``buffers[]`` minus count/size/hash)."""

    id: str
    file: str
    """Path relative to ``asset/``."""
    dtype: BufferDtype
    indexes: str
    description: str
    components: int = 1


BUFFER_SPECS: Final[tuple[BufferSpec, ...]] = (
    BufferSpec("face_labels", "buffers/face_labels.u16", "uint16", INDEX_BASE_FACES, "taxonomy value per base face"),
    BufferSpec("face_owner", "buffers/face_owner.u16", "uint16", INDEX_BASE_FACES, "mesh index per base face"),
    BufferSpec(
        "face_confidence",
        "buffers/face_confidence.u8",
        "uint8",
        INDEX_BASE_FACES,
        "label confidence x 255 per base face",
    ),
    BufferSpec(
        "face_component",
        "buffers/face_component.u32",
        "uint32",
        INDEX_BASE_FACES,
        "welded connected-component id per base face (0 = largest by area)",
    ),
    BufferSpec(
        "face_part",
        "buffers/face_part.u16",
        "uint16",
        INDEX_BASE_FACES,
        "index into parts[] of the finest record covering each base face (0xFFFF = none)",
    ),
    BufferSpec(
        "face_flags",
        "buffers/face_flags.u8",
        "uint8",
        INDEX_BASE_FACES,
        "label provenance bits per base face (bit 0 partner, bit 1 propagation, bit 2 component fill)",
    ),
    BufferSpec("weld_map", "buffers/weld_map.u32", "uint32", INDEX_BASE_VERTICES, "welded vertex id per base vertex"),
    BufferSpec(
        "seam_loops",
        "buffers/seam_loops.u32",
        "uint32",
        INDEX_WELDED_VERTICES,
        "welded vertex ids of every seam loop, concatenated (slices in seams[].loops)",
    ),
)
"""The fixed buffers in manifest order. One ``vmap_<part id>`` buffer per mesh node follows them."""

BUFFER_SPECS_BY_ID: Final[Mapping[str, BufferSpec]] = MappingProxyType({spec.id: spec for spec in BUFFER_SPECS})


def vmap_buffer_spec(part_id: str) -> BufferSpec:
    """Descriptor of the vertex map of mesh node ``part:<part_id>`` (base vertex id of each node vertex)."""
    return BufferSpec(
        id=f"{VMAP_PREFIX}{part_id}",
        file=f"{BUFFERS_DIR}/{VMAP_PREFIX}{part_id}.u32",
        dtype="uint32",
        indexes=f"{INDEX_NODE_VERTICES_PREFIX}{part_id}",
        description=f"base vertex id per vertex of {node_name(part_id)}",
    )


def node_name(part_id: str) -> str:
    """glTF node name of a mesh-owning part in ``model_parts.glb``."""
    return f"{NODE_NAME_PREFIX}{part_id}"


# --------------------------------------------------------------------------------------------------
# Mesh nodes (section 3.4; built by parts/partition.py)
# --------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MeshNode:
    """One mesh-owning part (the base at index 0, physical parts at 1..n) in base face space.

    * ``faces_base`` (uint32 ``[n]``): ``flatnonzero(face_owner == index)``, ascending base order.
    * ``vmap`` (uint32 ``[m]``): base vertex id of every node vertex. The first ``unique_count`` entries are the
      ascending unique base vertices of the node; region-border duplicates follow in ``(vertex, label)`` order.
    * ``local_faces`` (uint32 ``[n, 3]``): node-local corner indices; ``vmap[local_faces] == base.faces[faces_base]``.
    * ``feature_ids`` (uint16 ``[m]``): ``_FEATURE_ID_0`` per node vertex, the label of every face using it.

    Nothing is welded, recomputed or reordered, so gathering the base accessors by ``vmap`` reassembles the base
    bit-exactly (asset invariant A4).
    """

    index: int
    part_id: str
    faces_base: np.ndarray
    vmap: np.ndarray
    unique_count: int
    local_faces: np.ndarray
    feature_ids: np.ndarray

    @property
    def node_name(self) -> str:
        return node_name(self.part_id)

    @property
    def face_count(self) -> int:
        return int(self.faces_base.shape[0])

    @property
    def vertex_count(self) -> int:
        return int(self.vmap.shape[0])
