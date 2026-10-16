"""Typed relations between parts (part-aware DESIGN.md section 3.6; slice S3b).

Record: ``{id: "rel_<n>", type, a, b, via, metrics, source, confidence}``. Types, in record order:

* ``attached_to`` physical part -> the first ``attach_to`` candidate that is present and touches it: through the
  longest shared seam (``contact: "seam"``, ``via`` = seam id) or, for seamless shells, within
  ``contact_eps = 2 / effective_resolution`` (``contact: "proximity"``); confidence = min(label confidences);
* ``region_of`` region / physical_deferred -> the mesh node owning most of its faces;
* ``adjacent_to`` region <-> region on the same node (stored once, ``a < b``), from welded edges between faces of
  different records;
* ``symmetric_with`` ``*.left`` <-> ``*.right`` when both are present;
* ``layered_over`` ``clothing.*`` / ``hair`` -> body regions their 2D masks overlap in the input view;
* ``occludes`` a in front of b along shared 2D borders of the input-view face-ID/depth render;
* ``held_by`` ``object.held*`` -> the nearest ``body.hand.*`` region;
* ``expected_missing`` ``asset`` -> a class id that a prompt noun or an extra term asked for and that was not found.

Torch-free: numpy and scipy only.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

import numpy as np
from scipy.spatial import cKDTree

from parts.components import MeshTopology
from parts.manifest import RelationRecord
from parts.seams import SeamSet
from parts.taxonomy import TAXONOMY, PartClass
from parts.types import EXPECTED_MISSING_SUBJECT, FACE_PART_NONE, ExpectationSource, Kind
from parts.weld import edge_lengths

CONTACT_EPS_VOXELS: Final = 2.0
MIRROR_ERROR_SCALE: Final = 0.05
HELD_DISTANCE_SCALE: Final = 0.05
LAYERED_OVER_CONFIDENCE: Final = 0.5
OCCLUSION_MIN_BORDER_PX: Final = 10
OCCLUSION_MIN_FRONT_SHARE: Final = 0.6
OCCLUSION_FULL_CONFIDENCE_PX: Final = 200
COVERING_GROUPS: Final = frozenset({"clothes"})
COVERING_CLASSES: Final = frozenset({"hair"})
BODY_REGION_GROUPS: Final = frozenset({"body", "face"})


def contact_eps(effective_resolution: int) -> float:
    if effective_resolution <= 0:
        raise ValueError("effective_resolution must be positive")
    return CONTACT_EPS_VOXELS / effective_resolution


@dataclass(frozen=True, slots=True, eq=False)
class RelationPart:
    """What the relation builder needs of one part record (``faces``: int64 base faces the record covers)."""

    part_id: str
    cls: PartClass
    kind: Kind
    mesh_index: int | None
    host_index: int
    faces: np.ndarray
    confidence: float
    centroid: tuple[float, float, float]
    area: float

    @property
    def owns_mesh(self) -> bool:
        return self.kind in ("base", "physical")


@dataclass(frozen=True, slots=True)
class Attachment:
    target: str
    contact: str
    via: str | None
    shared_length: float
    distance: float


def resolve_attachments(
    parts: Sequence[RelationPart],
    seams: SeamSet,
    topology: MeshTopology,
    *,
    effective_resolution: int,
) -> dict[str, Attachment | None]:
    """``attach_to`` of every physical part: the first present candidate that touches it (seam or proximity).

    Every physical part maps to an :class:`Attachment`, or to ``None`` when no present candidate touches it (the part
    record then names the first present candidate without an ``attached_to`` relation).
    """
    by_id = {part.part_id: part for part in parts}
    eps = contact_eps(effective_resolution)
    result: dict[str, Attachment | None] = {}
    centroids = topology.geometry.centroids
    for part in parts:
        if part.kind != "physical" or not part.cls.attach_to:
            continue
        own_faces = part.faces
        own_seams = [seam for seam in seams.seams if part.mesh_index in (seam.a_index, seam.b_index)]
        tree: cKDTree | None = None
        chosen: Attachment | None = None
        for candidate_id in part.cls.attach_to:
            candidate = by_id.get(candidate_id)
            if candidate is None or candidate.part_id == part.part_id:
                continue
            candidate_node = candidate.mesh_index if candidate.owns_mesh else candidate.host_index
            touching = [
                seam for seam in own_seams
                if candidate_node == (seam.b_index if seam.a_index == part.mesh_index else seam.a_index)
            ]
            if touching:
                longest = max(touching, key=lambda seam: (seam.length, -int(seam.id.split("_")[1])))
                chosen = Attachment(candidate_id, "seam", longest.id, float(longest.length), 0.0)
                break
            if candidate.faces.size == 0 or own_faces.size == 0:
                continue
            if tree is None:
                tree = cKDTree(centroids[own_faces])
            distance, _ = tree.query(centroids[candidate.faces], k=1, distance_upper_bound=eps, workers=-1)
            nearest = float(distance.min()) if distance.size else float("inf")
            if np.isfinite(nearest):
                chosen = Attachment(candidate_id, "proximity", None, 0.0, nearest)
                break
        result[part.part_id] = chosen
    return result


def first_present_candidate(cls: PartClass, present: set[str]) -> str | None:
    return next((candidate for candidate in cls.attach_to if candidate in present), None)


class RelationBuilder:
    """Collects relation records in section 3.6 order with ids ``rel_0``, ``rel_1`` ..."""

    def __init__(self) -> None:
        self.records: list[RelationRecord] = []

    def add(
        self, type_: str, a: str, b: str, *, source: str, confidence: float, via: str | None = None,
        metrics: Mapping[str, object] | None = None,
    ) -> None:
        self.records.append(
            RelationRecord(
                id=f"rel_{len(self.records)}",
                type=type_,
                a=a,
                b=b,
                via=via,
                metrics=dict(metrics or {}),
                source=source,
                confidence=float(min(max(confidence, 0.0), 1.0)),
            )
        )


def build_relations(
    parts: Sequence[RelationPart],
    attachments: Mapping[str, Attachment | None],
    topology: MeshTopology,
    face_owner: np.ndarray,
    face_part: np.ndarray,
    mesh_part_ids: Sequence[str],
    *,
    effective_resolution: int,
    class_masks: Mapping[int, np.ndarray] | None = None,
    input_face_id: np.ndarray | None = None,
    input_depth: np.ndarray | None = None,
    expected_missing: Sequence[str] = (),
    expectation_source: ExpectationSource | None = None,
) -> list[RelationRecord]:
    """Every relation of an analysis (see the module docstring). Optional inputs switch types off when absent:
    ``class_masks`` (taxonomy value -> bool mask in conditioning space) for ``layered_over``, ``input_face_id`` /
    ``input_depth`` (input-view render) for ``occludes``."""
    builder = RelationBuilder()
    by_id = {part.part_id: part for part in parts}
    index_of = {part.part_id: index for index, part in enumerate(parts)}

    for part in parts:
        attachment = attachments.get(part.part_id)
        if part.kind != "physical" or attachment is None:
            continue
        target = by_id[attachment.target]
        builder.add(
            "attached_to", part.part_id, attachment.target, via=attachment.via, source="geometry",
            confidence=min(part.confidence, target.confidence),
            metrics={"contact": attachment.contact, "shared_length": attachment.shared_length,
                     "distance": attachment.distance},
        )

    for part in parts:
        if part.kind in ("region", "physical_deferred"):
            builder.add("region_of", part.part_id, mesh_part_ids[part.host_index], source="geometry",
                        confidence=1.0, metrics={"faces": int(part.faces.size)})

    for (a, b), (edges, length) in _adjacent_regions(parts, topology, face_owner, face_part).items():
        first, second = sorted((parts[a].part_id, parts[b].part_id))
        builder.add("adjacent_to", first, second, source="geometry", confidence=1.0,
                    metrics={"border_length": length, "border_edges": edges})

    for part in parts:
        cls = part.cls
        if not cls.sided or cls.side != "left" or part.part_id != cls.id or cls.symmetric_with not in by_id:
            continue
        other = by_id[cls.symmetric_with]
        mirrored = np.array([-part.centroid[0], part.centroid[1], part.centroid[2]])
        error = float(np.linalg.norm(mirrored - np.asarray(other.centroid)))
        largest = max(part.area, other.area)
        ratio = float(min(part.area, other.area) / largest) if largest > 0.0 else 1.0
        builder.add("symmetric_with", part.part_id, other.part_id, source="geometry",
                    confidence=1.0 - min(max(error / MIRROR_ERROR_SCALE, 0.0), 1.0),
                    metrics={"plane": "x=0", "centroid_mirror_error": error, "area_ratio": ratio})

    if class_masks:
        for cover in parts:
            if cover.part_id != cover.cls.id or not (
                cover.cls.group in COVERING_GROUPS or cover.cls.id in COVERING_CLASSES
            ):
                continue
            cover_mask = class_masks.get(cover.cls.value)
            if cover_mask is None:
                continue
            for body in parts:
                if body.kind != "region" or body.cls.group not in BODY_REGION_GROUPS or body.part_id != body.cls.id:
                    continue
                body_mask = class_masks.get(body.cls.value)
                if body_mask is None or np.shape(body_mask) != np.shape(cover_mask):
                    continue
                overlap = int(np.count_nonzero(np.logical_and(cover_mask, body_mask)))
                if overlap:
                    builder.add("layered_over", cover.part_id, body.part_id, source="taxonomy_default",
                                confidence=LAYERED_OVER_CONFIDENCE, metrics={"overlap_px": overlap})

    if input_face_id is not None and input_depth is not None:
        for front, back, border_px, gap in _occlusions(input_face_id, input_depth, face_part, len(parts)):
            builder.add("occludes", parts[front].part_id, parts[back].part_id, source="geometry",
                        confidence=min(1.0, border_px / OCCLUSION_FULL_CONFIDENCE_PX),
                        metrics={"view": "input", "border_px": border_px, "mean_depth_gap": gap})

    hands = [part for part in parts if part.cls.id.startswith("body.hand.") and part.faces.size]
    if hands:
        centroids = topology.geometry.centroids
        hand_trees = [(hand, cKDTree(centroids[hand.faces])) for hand in hands]
        for part in parts:
            if part.cls.id != "object.held" or part.faces.size == 0:
                continue
            best: tuple[float, RelationPart] | None = None
            for hand, tree in hand_trees:
                distance, _ = tree.query(centroids[part.faces], k=1, workers=-1)
                nearest = float(distance.min())
                if best is None or nearest < best[0]:
                    best = (nearest, hand)
            if best is not None:
                builder.add("held_by", part.part_id, best[1].part_id, source="geometry",
                            confidence=1.0 - min(best[0] / HELD_DISTANCE_SCALE, 1.0),
                            metrics={"distance": best[0]})

    if expected_missing:
        source = expectation_source or "prompt"
        for class_id in expected_missing:
            if class_id in TAXONOMY and class_id not in index_of:
                builder.add("expected_missing", EXPECTED_MISSING_SUBJECT, class_id, source=source, confidence=1.0)
    return builder.records


def _adjacent_regions(
    parts: Sequence[RelationPart], topology: MeshTopology, face_owner: np.ndarray, face_part: np.ndarray
) -> dict[tuple[int, int], tuple[int, float]]:
    """``(record a, record b) -> (border edges, border length)`` over manifold welded edges between faces of two
    different region/deferred records on the same node."""
    table = topology.edges
    manifold = np.flatnonzero(table.face_counts == 2)
    if manifold.size == 0:
        return {}
    first = table.edge_faces[table.face_offsets[manifold]].astype(np.int64)
    second = table.edge_faces[table.face_offsets[manifold] + 1].astype(np.int64)
    regional = np.zeros(FACE_PART_NONE + 1, dtype=bool)
    for index, part in enumerate(parts):
        regional[index] = part.kind in ("region", "physical_deferred")
    record_a = face_part[first].astype(np.int64)
    record_b = face_part[second].astype(np.int64)
    keep = (record_a != record_b) & regional[record_a] & regional[record_b] & (face_owner[first] == face_owner[second])
    if not keep.any():
        return {}
    lengths = edge_lengths(table, topology.welded_positions)[manifold[keep]]
    low = np.minimum(record_a[keep], record_b[keep])
    high = np.maximum(record_a[keep], record_b[keep])
    keys = low * (FACE_PART_NONE + 1) + high
    unique, inverse, counts = np.unique(keys, return_inverse=True, return_counts=True)
    totals = np.bincount(inverse.reshape(-1), weights=lengths)
    result: dict[tuple[int, int], tuple[int, float]] = {}
    for key, count, total in zip(unique.tolist(), counts.tolist(), totals.tolist(), strict=True):
        result[(key // (FACE_PART_NONE + 1), key % (FACE_PART_NONE + 1))] = (int(count), float(total))
    return dict(sorted(result.items(), key=lambda item: (parts[item[0][0]].part_id, parts[item[0][1]].part_id)))


def _occlusions(
    face_id: np.ndarray, depth: np.ndarray, face_part: np.ndarray, record_count: int
) -> list[tuple[int, int, int, float]]:
    """``(front record, back record, border px, mean depth gap)`` along 4-connected 2D borders of the render."""
    ids = np.asarray(face_id, dtype=np.int64)
    depths = np.asarray(depth, dtype=np.float64)
    if ids.shape != depths.shape or ids.ndim != 2:
        raise ValueError("input_face_id and input_depth must be 2D arrays of the same shape")
    record = np.full(ids.shape, -1, dtype=np.int64)
    visible = (ids >= 0) & (ids < face_part.shape[0])
    values = face_part[ids[visible]].astype(np.int64)
    values[values == FACE_PART_NONE] = -1
    record[visible] = values
    a_parts: list[np.ndarray] = []
    b_parts: list[np.ndarray] = []
    gaps: list[np.ndarray] = []
    for slicer_a, slicer_b in (
        ((slice(None), slice(None, -1)), (slice(None), slice(1, None))),
        ((slice(None, -1), slice(None)), (slice(1, None), slice(None))),
    ):
        ra, rb = record[slicer_a], record[slicer_b]
        border = (ra >= 0) & (rb >= 0) & (ra != rb)
        a_parts.append(ra[border])
        b_parts.append(rb[border])
        gaps.append(depths[slicer_b][border] - depths[slicer_a][border])
    ra = np.concatenate(a_parts)
    rb = np.concatenate(b_parts)
    gap = np.concatenate(gaps)
    if ra.size == 0:
        return []
    low = np.minimum(ra, rb)
    high = np.maximum(ra, rb)
    signed = np.where(ra == low, gap, -gap)  # > 0: the lower record is in front
    keys = low * record_count + high
    unique, inverse, counts = np.unique(keys, return_inverse=True, return_counts=True)
    inverse = inverse.reshape(-1)
    low_front = np.bincount(inverse, weights=(signed > 0).astype(np.float64))
    signed_sum = np.bincount(inverse, weights=signed)
    result: list[tuple[int, int, int, float]] = []
    for key, count, front_count, total in zip(unique.tolist(), counts.tolist(), low_front.tolist(),
                                              signed_sum.tolist(), strict=True):
        if count < OCCLUSION_MIN_BORDER_PX:
            continue
        a, b = key // record_count, key % record_count
        share = front_count / count
        if share >= OCCLUSION_MIN_FRONT_SHARE and total > 0.0:
            result.append((a, b, int(count), float(total / count)))
        elif 1.0 - share >= OCCLUSION_MIN_FRONT_SHARE and total < 0.0:
            result.append((b, a, int(count), float(-total / count)))
    return result


__all__ = [
    "CONTACT_EPS_VOXELS",
    "Attachment",
    "RelationBuilder",
    "RelationPart",
    "build_relations",
    "contact_eps",
    "first_present_candidate",
    "resolve_attachments",
]
