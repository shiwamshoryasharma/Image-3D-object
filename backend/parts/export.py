"""Export of one part layer into a staging directory (part-aware DESIGN.md sections 3.3-3.12 and 4.6.5 step 7; slice
S3b).

:func:`export_part_layer` turns a partition plan (:func:`parts.policy.plan_parts`) into the files of
``jobs/<job>/parts/<pa>/asset/`` inside a staging directory (``runtime/temp/parts/<pa>/out/``):

1. mesh nodes (section 3.4), seams (3.5), open boundary loops, per-record stats, frames (3.7), sides, relations (3.6),
   refinement state (3.9) and lineage (3.8);
2. the raw buffers (3.3), ``model_parts.glb`` (3.12), optional ``parts/<id>.glb``, ``masks/<value>.png`` (conditioning
   space) and ``thumbs/<id>.webp`` (a masked crop of the conditioning image, max side 256);
3. the bit-exact reassembly check **on the written files** (A4; ``ReassemblyError`` -> run error ``partition_failed``);
4. ``manifest.json`` (``img3d.parts/1``), then :func:`parts.validate.validate_staging`
   (``StagingInvalidError`` -> ``export_failed``).

Segmentation artifacts (``segmentation/*``) and previews (``previews/*``) are written into the staging directory by
the caller (S10) before the export; the export only references them. Publishing is :mod:`parts.publish`.

:func:`write_part_glb_from_reader` builds an on-demand per-part GLB from a published layer (``services/part_files``).

Torch-free: numpy, Pillow (masks, thumbnails), json.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import numpy as np

from parts.buffers import read_buffer, write_asset_buffers
from parts.components import MeshTopology
from parts.frames import ASSUMED_FRONT_CONFIDENCE, bbox_center, build_frames, part_frame, resolve_side
from parts.gltf_io import BaseMesh, GlbWriteReport, NodeSpec, write_parts_glb
from parts.labels import LabelAssignment
from parts.lineage import LineageSource, compute_lineage
from parts.manifest import (
    Artifacts,
    AssembledGlb,
    ConditioningFrameInfo,
    Expectations,
    GeneratorInfo,
    Guide2D,
    ImageOrigin,
    LabelEntry,
    ManifestProvenance,
    ManifestRefinement,
    ManifestWarning,
    MeshEntry,
    PartFiles,
    PartGeometryInfo,
    PartLineage,
    PartProvenance,
    PartRecord,
    PartRefinement,
    PartRegion,
    PartsManifest,
    PartStats,
    PartUv,
    PromptOrigin,
    ProvenanceSource,
    SegmentationArtifacts,
    SourceInfo,
    StrategyInfo,
    Summary,
    ViewRecord,
)
from parts.partition import build_mesh_nodes
from parts.policy import PartPlan, PlannedPart, kinds_summary
from parts.publish import StagingInvalidError
from parts.reassembly import verify_reassembly
from parts.relations import RelationPart, build_relations, first_present_candidate, resolve_attachments
from parts.seams import SeamSet, build_seams, open_boundary_loops
from parts.shells import NO_PARTNER, ShellReport
from parts.taxonomy import BODY, BY_VALUE, UNKNOWN
from parts.types import (
    ASSEMBLED_GLB_FILE,
    FACE_FLAG_PARTNER,
    MANIFEST_FILE,
    MASKS_DIR,
    PART_GLBS_DIR,
    PARTS_SCHEMA,
    REGION_ATTRIBUTE,
    THUMBS_DIR,
    MeshNode,
    RefinementNeed,
    node_name,
)
from parts.validate import validate_staging
from utils.timeutil import utcnow_iso

THUMBNAIL_MAX_SIDE: Final = 256
THUMBNAIL_QUALITY: Final = 90
LOW_CONFIDENCE: Final = 0.5
GEOMETRY_PROVIDER: Final = "img3d"
DEFAULT_LIFT_PROVIDER: Final = "lifter"
CHARTS_CROSS_BOUNDARY: Final = "charts_cross_boundary"
_ROUND: Final = 6


@dataclass(frozen=True, slots=True)
class LabelSource:
    """Who produced the lifted labels: the segmentation profile (``provider``) and its primary model id."""

    provider: str
    model: str | None = None


@dataclass(frozen=True, slots=True, eq=False)
class ExportOptions:
    """Everything the manifest needs besides the geometry (sections 3.7, 3.8, 3.10).

    * ``class_masks``: taxonomy value -> bool mask in conditioning space (written as ``masks/<value>.png``);
      ``conditioning_image`` (uint8 ``[H, W, 3|4]``) gives the thumbnails.
    * ``segmentation`` / ``previews``: files the caller already wrote into the staging directory.
    * ``earlier``: earlier published analyses of the same base (:func:`parts.lineage.find_earlier_analyses`).
    * ``input_face_id`` / ``input_depth``: the input-view face-ID/depth render (``occludes`` relations).
    """

    analysis_id: str
    job_id: str
    generator: GeneratorInfo
    strategy: StrategyInfo
    effective_resolution: int
    created_at: str | None = None
    primary_image_id: str | None = None
    origin: ImageOrigin | PromptOrigin | None = None
    views: Sequence[ViewRecord] = ()
    conditioning: ConditioningFrameInfo | None = None
    label_source: LabelSource | None = None
    class_masks: Mapping[int, np.ndarray] | None = None
    conditioning_image: np.ndarray | None = None
    segmentation: SegmentationArtifacts | None = None
    previews: Sequence[str] = ()
    per_part_glbs: bool = False
    expected_classes: Sequence[str] = ()
    expectation_source: str | None = None
    provenance: ManifestProvenance | None = None
    warnings: Sequence[ManifestWarning] = ()
    annotations: Mapping[int, Mapping[str, Guide2D]] = field(default_factory=dict)
    earlier: Sequence[LineageSource] = ()
    input_face_id: np.ndarray | None = None
    input_depth: np.ndarray | None = None


@dataclass(frozen=True, slots=True, eq=False)
class ExportResult:
    manifest: PartsManifest
    nodes: tuple[MeshNode, ...]
    seams: SeamSet
    assembled: GlbWriteReport
    timings: dict[str, float]


@dataclass(slots=True, eq=False)
class _Facts:
    """Derived per-record facts used by the relation builder and the part records."""

    stat_faces: np.ndarray
    base_vertices: np.ndarray
    vertex_count: int
    area: float
    centroid: tuple[float, float, float]
    bbox_min: list[float]
    bbox_max: list[float]
    uv_bbox: list[float]
    welded_points: np.ndarray
    confidence: float | None
    votes: float = 0.0


def _rounded(values: Sequence[float] | np.ndarray) -> list[float]:
    return [round(float(v), _ROUND) for v in values]


def _write_bytes_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def write_manifest(staging_dir: Path, manifest: PartsManifest) -> Path:
    """Write ``manifest.json`` atomically (UTF-8, ``schema`` key, explicit nulls)."""
    text = json.dumps(manifest.to_json_dict(), ensure_ascii=False, indent=1, allow_nan=False)
    path = Path(staging_dir) / MANIFEST_FILE
    _write_bytes_atomic(path, (text + "\n").encode("utf-8"))
    return path


# --------------------------------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------------------------------


def export_part_layer(
    staging_dir: Path,
    base: BaseMesh,
    topology: MeshTopology,
    shells: ShellReport,
    assignment: LabelAssignment,
    plan: PartPlan,
    options: ExportOptions,
    *,
    partners: np.ndarray | None = None,
) -> ExportResult:
    """Write the complete part layer into ``staging_dir`` and return its validated manifest (see the module doc).

    ``partners`` (``pair_opposite_faces``) gives ``summary.cut_partner_consistency``; without it the value is null.
    """
    staging = Path(staging_dir)
    staging.mkdir(parents=True, exist_ok=True)
    face_count = topology.face_count
    if base.face_count != face_count or assignment.face_count != face_count or plan.face_owner.shape != (face_count,):
        raise ValueError("base, topology, assignment and plan must share one base face space")
    timings: dict[str, float] = {}
    clock = time.perf_counter()

    nodes = build_mesh_nodes(base, plan.face_owner, plan.face_labels, part_ids=plan.mesh_part_ids)
    seams = build_seams(topology, plan.face_owner, plan.mesh_part_ids)
    base_open = open_boundary_loops(topology, plan.face_owner, plan.node_count)
    timings["nodes_seams"] = time.perf_counter() - clock

    clock = time.perf_counter()
    fit = options.views[0].fit if options.views and options.views[0].id == "input" else None
    fit_accepted = None if fit is None else fit.accepted
    fit_yaw = options.views[0].camera.yaw_deg if fit is not None else None
    facts = [_facts(base, topology, assignment, part, nodes) for part in plan.parts]
    relation_parts = [
        RelationPart(
            part_id=part.part_id, cls=part.cls, kind=part.kind, mesh_index=part.mesh_index,
            host_index=part.host_index, faces=part.faces, confidence=fact.confidence if fact.confidence is not None
            else 1.0, centroid=fact.centroid, area=fact.area,
        )
        for part, fact in zip(plan.parts, facts, strict=True)
    ]
    attachments = resolve_attachments(relation_parts, seams, topology,
                                      effective_resolution=options.effective_resolution)
    present_ids = {part.part_id for part in plan.parts}
    expected = [class_id for class_id in dict.fromkeys(options.expected_classes) if class_id]
    missing = [class_id for class_id in expected if class_id not in {p.cls.id for p in plan.parts}]
    relations = build_relations(
        relation_parts, attachments, topology, plan.face_owner, plan.face_part, plan.mesh_part_ids,
        effective_resolution=options.effective_resolution, class_masks=options.class_masks,
        input_face_id=options.input_face_id, input_depth=options.input_depth, expected_missing=missing,
        expectation_source=options.expectation_source,  # type: ignore[arg-type]
    )
    current = LineageSource(
        analysis_id=options.analysis_id,
        part_ids=tuple(part.part_id for part in plan.parts),
        mesh_indices=tuple(part.mesh_index if part.owns_mesh else None for part in plan.parts),
        face_owner=plan.face_owner,
        face_part=plan.face_part,
    )
    lineage = compute_lineage(current, options.earlier)
    timings["records"] = time.perf_counter() - clock

    clock = time.perf_counter()
    mask_files = _write_masks(staging, options, {part.value for part in plan.parts})
    inner_mask = shells.inner_sheet_face_mask(topology.components.face_component) if shells.root.size else None
    host_cut = any(seam.host_opening for seam in seams.seams)
    records: list[PartRecord] = []
    for part, fact in zip(plan.parts, facts, strict=True):
        seam_ids = seams.seam_ids_for(part.part_id) if part.owns_mesh else []
        loops = 0
        if part.owns_mesh and part.mesh_index is not None:
            loops = int(base_open[part.mesh_index]) + sum(
                len(seam.loops) for seam in seams.seams if part.mesh_index in (seam.a_index, seam.b_index))
        attachment = attachments.get(part.part_id)
        records.append(_record(
            part, fact, options, plan, seams, seam_ids, loops, host_cut, inner_mask, topology, present_ids,
            attachment.target if attachment is not None else None, mask_files, staging, fit_accepted, fit_yaw,
            lineage.get(part.part_id, []), part.cls.id in expected,
        ))
    timings["thumbnails"] = time.perf_counter() - clock

    clock = time.perf_counter()
    face_labels = plan.face_labels
    reset = face_labels != assignment.labels
    if plan.strategy == "components":
        confidence = np.full(face_count, 255, dtype=np.uint8)
    else:
        confidence = assignment.confidence_u8()
        confidence[face_labels == UNKNOWN.value] = 0
    flags = assignment.flags.copy()
    flags[reset] = 0
    buffers = write_asset_buffers(
        staging,
        {
            "face_labels": face_labels,
            "face_owner": plan.face_owner,
            "face_confidence": confidence,
            "face_component": topology.components.face_component.astype(np.uint32),
            "face_part": plan.face_part,
            "face_flags": flags,
            "weld_map": topology.weld.weld_map.astype(np.uint32),
            "seam_loops": seams.loop_vertices,
        },
        [(node.part_id, node.vmap) for node in nodes],
    )
    timings["buffers"] = time.perf_counter() - clock

    clock = time.perf_counter()
    by_id = {record.id: record for record in records}
    scene_extras = {
        "schema": PARTS_SCHEMA,
        "analysis_id": options.analysis_id,
        "job_id": options.job_id,
        "region_attribute": REGION_ATTRIBUTE,
        "manifest": MANIFEST_FILE,
    }
    specs = [NodeSpec.from_mesh_node(base, node, node_extras(by_id[node.part_id], face_labels[node.faces_base]))
             for node in nodes]
    assembled_path = staging / ASSEMBLED_GLB_FILE
    assembled = write_parts_glb(assembled_path, base, specs, scene_extras=scene_extras)
    part_glbs: dict[str, str] = {}
    if options.per_part_glbs:
        (staging / PART_GLBS_DIR).mkdir(parents=True, exist_ok=True)
        for spec in specs:
            relative = f"{PART_GLBS_DIR}/{spec.part_id}.glb"
            write_parts_glb(staging / relative, base, [spec], scene_extras=scene_extras)
            part_glbs[spec.part_id] = relative
        records = [
            record.model_copy(update={"files": record.files.model_copy(update={"glb": part_glbs[record.id]})})
            if record.id in part_glbs else record
            for record in records
        ]
    timings["glb"] = time.perf_counter() - clock

    # A4 on the written files: face_owner and every vmap are read back from disk.
    clock = time.perf_counter()
    buffer_by_id = {record.id: record for record in buffers}
    written_owner = read_buffer(staging, buffer_by_id["face_owner"])
    written_vmaps = [read_buffer(staging, buffer_by_id[f"vmap_{node.part_id}"]) for node in nodes]
    report = verify_reassembly(base, assembled_path, part_ids=[node.part_id for node in nodes],
                               vmaps=written_vmaps, face_owner=written_owner)
    report.raise_if_inexact()
    timings["reassembly"] = time.perf_counter() - clock

    clock = time.perf_counter()
    warnings = [*plan.warnings, *options.warnings]
    if seams.seams:
        warnings.append(ManifestWarning(code=CHARTS_CROSS_BOUNDARY,
                                        message="UV charts of cut parts may cross part borders.", part_id=None))
    counts = kinds_summary(plan.parts)
    manifest = PartsManifest(
        analysis_id=options.analysis_id,
        job_id=options.job_id,
        created_at=options.created_at or utcnow_iso(),
        generator=options.generator,
        source=SourceInfo(
            model_sha256=base.sha256,
            model_bytes=base.byte_length,
            vertices=base.vertex_count,
            faces=base.face_count,
            materials=base.material_count,
            images=base.image_count,
            primary_image_id=options.primary_image_id,
            origin=options.origin or ImageOrigin(),
        ),
        frames=build_frames(options.conditioning),
        views=list(options.views),
        strategy=options.strategy.model_copy(update={"id": plan.strategy, "min_part_faces": plan.min_part_faces}),
        label_table=_label_table(face_labels, records),
        meshes=[
            MeshEntry(index=node.index, part_id=node.part_id, node_name=node.node_name, faces=node.face_count,
                      vertices=node.vertex_count, unique_vertices=node.unique_count,
                      vertex_map=f"vmap_{node.part_id}")
            for node in nodes
        ],
        parts=records,
        seams=seams.manifest_records(),
        relations=relations,
        buffers=buffers,
        artifacts=Artifacts(
            assembled_glb=AssembledGlb(file=ASSEMBLED_GLB_FILE, sha256=assembled.sha256, bytes=assembled.bytes,
                                       nodes=assembled.nodes),
            part_glbs=part_glbs,
            segmentation=options.segmentation,
            masks_dir=MASKS_DIR if mask_files else None,
            previews=list(options.previews),
            log=None,
        ),
        expectations=Expectations(
            source=options.expectation_source if expected else None,  # type: ignore[arg-type]
            expected_classes=expected,
            missing=missing,
        ),
        provenance=options.provenance or ManifestProvenance(),
        refinement=ManifestRefinement(),
        reassembly=report.to_manifest(),
        summary=Summary(
            part_count=counts["physical"] + counts["physical_deferred"],
            physical_count=counts["physical"],
            deferred_count=counts["physical_deferred"],
            region_count=counts["region"],
            unlabelled_face_ratio=round(float((face_labels == UNKNOWN.value).mean()) if face_count else 0.0, _ROUND),
            partner_filled_ratio=round(float(((flags & FACE_FLAG_PARTNER) != 0).mean()) if face_count else 0.0,
                                       _ROUND),
            cut_partner_consistency=cut_partner_consistency(plan, seams, partners),
            camera_fit_iou=None if fit is None else fit.iou,
        ),
        warnings=warnings,
    )
    write_manifest(staging, manifest)
    problems = validate_staging(staging, manifest, None)
    if problems:
        raise StagingInvalidError(problems)
    timings["manifest"] = time.perf_counter() - clock
    return ExportResult(manifest=manifest, nodes=tuple(nodes), seams=seams, assembled=assembled, timings=timings)


def cut_partner_consistency(plan: PartPlan, seams: SeamSet, partners: np.ndarray | None) -> float | None:
    """Face-weighted share, over all cut physical parts, of the partnered faces whose opposite-sheet partner has the
    same owner (faces without a partner are not counted; 1.0 when no cut face has a partner). ``None`` without cuts.

    Weighted by faces, not the minimum over parts (user decision 2026-10-22): a few-dozen-face fragment instance can
    break a quarter of its wall pairs on its label boundary alone, and must not decide the score of the whole cut."""
    if partners is None:
        return None
    partner = np.asarray(partners, dtype=np.int64)
    owner = plan.face_owner.astype(np.int64)
    cut_nodes = {seam.a_index for seam in seams.seams} | {seam.b_index for seam in seams.seams}
    any_cut = False
    paired_total = 0
    consistent_total = 0
    for part in plan.parts:
        if part.kind != "physical" or part.mesh_index not in cut_nodes:
            continue
        any_cut = True
        faces = np.flatnonzero(owner == part.mesh_index)
        paired = faces[partner[faces] != NO_PARTNER]
        paired_total += int(paired.size)
        consistent_total += int(np.count_nonzero(owner[partner[paired]] == part.mesh_index))
    if not any_cut:
        return None
    return round(consistent_total / paired_total, _ROUND) if paired_total else 1.0


# --------------------------------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------------------------------


def _facts(
    base: BaseMesh, topology: MeshTopology, assignment: LabelAssignment, part: PlannedPart, nodes: Sequence[MeshNode]
) -> _Facts:
    if part.owns_mesh and part.mesh_index is not None:
        stat_faces = nodes[part.mesh_index].faces_base.astype(np.int64)
    else:
        stat_faces = np.asarray(part.faces, dtype=np.int64)
    geometry = topology.geometry
    if stat_faces.size == 0:
        zero = [0.0, 0.0, 0.0]
        return _Facts(stat_faces, np.zeros(0, dtype=np.int64), 0, 0.0, (0.0, 0.0, 0.0), zero, zero,
                      [0.0, 0.0, 0.0, 0.0], np.zeros((0, 3), dtype=np.float32), None)
    vertices = np.unique(np.asarray(base.faces)[stat_faces].reshape(-1)).astype(np.int64)
    points = np.asarray(base.positions)[vertices].astype(np.float64)
    areas = geometry.areas[stat_faces].astype(np.float64)
    centroids = geometry.centroids[stat_faces].astype(np.float64)
    total = float(areas.sum())
    centroid = (areas[:, None] * centroids).sum(axis=0) / total if total > 0.0 else centroids.mean(axis=0)
    if base.uv is not None:
        uv = np.asarray(base.uv)[vertices].astype(np.float64)
        uv_bbox = _rounded([*uv.min(axis=0), *uv.max(axis=0)])
    else:
        uv_bbox = [0.0, 0.0, 0.0, 0.0]
    welded = np.unique(topology.welded[stat_faces].reshape(-1)).astype(np.int64)
    if part.cls.id in (BODY.id, UNKNOWN.id):
        confidence: float | None = 1.0
    else:
        covered = np.asarray(part.faces, dtype=np.int64)
        confidence = round(float(assignment.confidence[covered].mean()), 4) if covered.size else None
    votes = 0.0 if part.cls.id in (BODY.id, UNKNOWN.id) else round(float(
        assignment.votes[np.asarray(part.faces, dtype=np.int64)].astype(np.float64).sum()), 3)
    return _Facts(
        stat_faces=stat_faces,
        base_vertices=vertices,
        vertex_count=nodes[part.mesh_index].vertex_count if part.owns_mesh and part.mesh_index is not None
        else int(vertices.size),
        area=total,
        centroid=(float(centroid[0]), float(centroid[1]), float(centroid[2])),
        bbox_min=_rounded(points.min(axis=0)),
        bbox_max=_rounded(points.max(axis=0)),
        uv_bbox=uv_bbox,
        welded_points=topology.welded_positions[welded],
        confidence=confidence,
        votes=votes,
    )


def _label(part: PlannedPart) -> str:
    if part.is_component:
        return f"Component {part.instance}"
    return part.cls.label if part.instance == 1 else f"{part.cls.label} {part.instance}"


def _parent(part: PlannedPart) -> str | None:
    if part.kind == "base" or part.is_component:
        return None
    if part.cls.group == "detachable" and not part.cls.attach_to:
        return None
    return BODY.id


def _needs(part: PlannedPart, loops: int, host_cut: bool, confidence: float | None) -> list[RefinementNeed]:
    needs: list[RefinementNeed] = []
    if (part.kind == "base" and host_cut) or part.cls.group == "eyes":
        needs.append("occluded_geometry")
    if part.kind == "physical_deferred":
        needs.append("geometry")
    if part.kind == "physical" and loops > 0:
        needs.append("closure")
    if confidence is not None and confidence < LOW_CONFIDENCE:
        needs.append("low_confidence")
    return needs


def _record(
    part: PlannedPart,
    fact: _Facts,
    options: ExportOptions,
    plan: PartPlan,
    seams: SeamSet,
    seam_ids: list[str],
    loops: int,
    host_cut: bool,
    inner_mask: np.ndarray | None,
    topology: MeshTopology,
    present_ids: set[str],
    attached: str | None,
    mask_files: Mapping[int, str],
    staging: Path,
    fit_accepted: bool | None,
    fit_yaw: float | None,
    lineage: list[Any],
    expected: bool,
) -> PartRecord:
    cls = part.cls
    owns = part.owns_mesh
    half_width = (fact.bbox_max[0] - fact.bbox_min[0]) / 2.0
    side = resolve_side(cls, fact.centroid[0], half_width, fit_accepted=fit_accepted, fit_yaw_deg=fit_yaw)

    if part.kind == "physical" and seam_ids:
        loop_vertices = np.concatenate(
            [seams.loop_vertices[lp.start:lp.start + lp.count] for seam in seams.seams if seam.id in seam_ids
             for lp in seam.loops]).astype(np.int64)
        pivot = topology.welded_positions[loop_vertices].astype(np.float64).mean(axis=0)
        frame = part_frame("seam_centroid", _rounded(pivot), fact.welded_points)
    elif part.kind == "physical":
        frame = part_frame("bbox_center", _rounded(bbox_center(fact.welded_points)), fact.welded_points)
    else:
        frame = part_frame("area_centroid", _rounded(fact.centroid), fact.welded_points)

    faces = int(fact.stat_faces.size)
    if owns:
        status = "open_surface" if loops > 0 else "closed"
    elif faces == 0:
        status = "absent"
    else:
        status = "texture_only" if part.kind == "physical_deferred" else "surface"
    tags = ["inner_sheet_merged"] if owns and inner_mask is not None and bool(inner_mask[fact.stat_faces].any()) \
        else []
    geometry = PartGeometryInfo(
        status=status,
        open_boundary_loops=loops,
        watertight=owns and loops == 0,
        occluded_geometry_missing=(part.kind == "base" and host_cut) or cls.group == "eyes",
        components=part.components,
        source=part.source,
        tags=tags,
        floaters=list(plan.floaters) if part.kind == "base" else [],
    )

    if cls.id in (BODY.id, UNKNOWN.id):
        sources = [ProvenanceSource(method="components", provider=GEOMETRY_PROVIDER, model=None, score=1.0)]
    else:
        label_source = options.label_source
        sources = [ProvenanceSource(
            method="lifted",
            provider=label_source.provider if label_source is not None else DEFAULT_LIFT_PROVIDER,
            model=label_source.model if label_source is not None else None,
            score=fact.confidence,
        )]
    if side.assumed_front:
        sources.append(ProvenanceSource(method="assumed_front", provider=GEOMETRY_PROVIDER, model=None,
                                        score=ASSUMED_FRONT_CONFIDENCE))

    host = None if owns else plan.mesh_part_ids[part.host_index]
    if part.kind == "physical":
        attach_to = attached if attached is not None else first_present_candidate(cls, present_ids)
    elif part.kind == "base":
        attach_to = None
    else:
        attach_to = first_present_candidate(cls, present_ids)
    symmetric = cls.symmetric_with if part.instance == 1 and cls.symmetric_with in present_ids else None
    mask = mask_files.get(part.value) if not part.is_component else None
    thumbnail = None
    if part.kind in ("physical", "physical_deferred") and mask is not None and options.conditioning_image is not None:
        thumbnail = _write_thumbnail(staging, part.part_id, options.conditioning_image,
                                     np.asarray(options.class_masks[part.value], dtype=bool))  # type: ignore[index]
    annotations = dict(options.annotations.get(part.value, {})) if part.instance == 1 else {}

    return PartRecord(
        id=part.part_id,
        category=cls.id,
        group=cls.group,
        label=_label(part),
        kind=part.kind,
        side=side.side,
        image_side=side.image_side,
        parent=_parent(part),
        attach_to=attach_to,
        host=host,
        symmetric_with=symmetric,
        node_name=node_name(part.part_id) if owns else None,
        mesh_index=part.mesh_index if owns else None,
        region=PartRegion(value=part.value, face_count=int(part.faces.size)),
        files=PartFiles(glb=None, mask=mask, thumbnail=thumbnail),
        stats=PartStats(
            faces=faces,
            vertices=fact.vertex_count,
            area=round(fact.area, 8),
            bbox_min=fact.bbox_min,
            bbox_max=fact.bbox_max,
            centroid=_rounded(fact.centroid),
        ),
        frame=frame,
        geometry=geometry,
        uv=PartUv(uv_bbox=fact.uv_bbox, charts_cross_boundary=owns and bool(seam_ids)),
        seams=seam_ids,
        annotations=annotations,
        confidence=fact.confidence,
        expected=expected,
        provenance=PartProvenance(sources=sources, votes=fact.votes),
        refinement=PartRefinement(
            eligible=not part.is_component,
            needs=_needs(part, loops, host_cut, fact.confidence),
        ),
        lineage=PartLineage(derived_from=lineage),
    )


def node_extras(record: PartRecord, node_labels: np.ndarray) -> dict[str, Any]:
    """``extras.img3d`` of a mesh node in ``model_parts.glb`` (section 3.12)."""
    regions = [{"value": int(value), "category": BY_VALUE[int(value)].id}
               for value in np.unique(np.asarray(node_labels)).tolist()]
    extras: dict[str, Any] = {"id": record.id, "kind": record.kind, "parent": record.parent, "regions": regions}
    if record.kind != "base":
        extras.update({"category": record.category, "group": record.group, "attach_to": record.attach_to,
                       "seams": list(record.seams)})
    return extras


def _label_table(face_labels: np.ndarray, records: Sequence[PartRecord]) -> list[LabelEntry]:
    values = set(np.unique(face_labels).tolist()) | {record.region.value for record in records}
    return [LabelEntry(value=int(value), category=BY_VALUE[int(value)].id) for value in sorted(values)]


# --------------------------------------------------------------------------------------------------
# Masks and thumbnails
# --------------------------------------------------------------------------------------------------


def _write_masks(staging: Path, options: ExportOptions, values: set[int]) -> dict[int, str]:
    """``masks/<value>.png`` (8-bit, 255 = class) for every non-empty class mask a record uses."""
    if not options.class_masks:
        return {}
    from PIL import Image

    shape = None if options.conditioning_image is None else np.shape(options.conditioning_image)[:2]
    written: dict[int, str] = {}
    for value, mask in sorted(options.class_masks.items()):
        if value not in values or value == UNKNOWN.value:
            continue
        array = np.asarray(mask, dtype=bool)
        if array.ndim != 2:
            raise ValueError(f"class mask {value} must be 2D")
        if shape is not None and array.shape != shape:
            raise ValueError(f"class mask {value} has shape {array.shape}, the conditioning image {shape}")
        if not array.any():
            continue
        relative = f"{MASKS_DIR}/{value}.png"
        path = staging / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(array.astype(np.uint8) * 255, mode="L").save(path, format="PNG", optimize=True)
        written[value] = relative
    return written


def _write_thumbnail(staging: Path, part_id: str, image: np.ndarray, mask: np.ndarray) -> str | None:
    """``thumbs/<id>.webp``: the conditioning image cropped to the mask bbox, alpha = mask, max side 256."""
    from PIL import Image

    pixels = np.asarray(image)
    if pixels.ndim != 3 or pixels.shape[2] not in (3, 4) or pixels.dtype != np.uint8:
        raise ValueError("conditioning_image must be uint8 [H, W, 3|4]")
    rows = np.flatnonzero(mask.any(axis=1))
    cols = np.flatnonzero(mask.any(axis=0))
    if rows.size == 0:
        return None
    y0, y1, x0, x1 = int(rows[0]), int(rows[-1]) + 1, int(cols[0]), int(cols[-1]) + 1
    rgb = pixels[y0:y1, x0:x1, :3]
    alpha = mask[y0:y1, x0:x1].astype(np.uint8) * 255
    if pixels.shape[2] == 4:
        alpha = np.minimum(alpha, pixels[y0:y1, x0:x1, 3])
    thumb = Image.fromarray(np.dstack([rgb, alpha]), mode="RGBA")
    thumb.thumbnail((THUMBNAIL_MAX_SIDE, THUMBNAIL_MAX_SIDE), Image.Resampling.LANCZOS)
    relative = f"{THUMBS_DIR}/{part_id}.webp"
    path = staging / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    thumb.save(path, format="WEBP", quality=THUMBNAIL_QUALITY)
    return relative


# --------------------------------------------------------------------------------------------------
# On-demand per-part GLB (services/part_files.py)
# --------------------------------------------------------------------------------------------------


def write_part_glb_from_reader(reader: Any, part_id: str, path: Path) -> GlbWriteReport:
    """Write ``parts/<id>.glb`` (one node, full atlas) for a mesh-owning part of a published layer.

    ``reader`` is a :class:`parts.reader.AssetReader`; the node is gathered from the base by the part's ``vmap``
    (bit-exact, A4) with the node extras of ``model_parts.glb``.
    """
    geometry = reader.part(part_id)
    record = reader.record(part_id)
    base = reader.base()
    manifest = reader.manifest
    labels = reader.buffer("face_labels")[geometry.base_faces]
    spec = NodeSpec(
        part_id=part_id,
        positions=np.asarray(base.positions)[geometry.vertex_map],
        normals=None if base.normals is None else np.asarray(base.normals)[geometry.vertex_map],
        uv=None if base.uv is None else np.asarray(base.uv)[geometry.vertex_map],
        faces=geometry.faces,
        feature_ids=geometry.feature_ids if geometry.feature_ids is not None
        else np.zeros(geometry.vertex_map.shape[0], dtype=np.uint16),
        extras=node_extras(record, labels),
    )
    scene_extras = {"schema": PARTS_SCHEMA, "analysis_id": manifest.analysis_id, "job_id": manifest.job_id,
                    "region_attribute": REGION_ATTRIBUTE, "manifest": MANIFEST_FILE}
    return write_parts_glb(Path(path), base, [spec], scene_extras=scene_extras)


__all__ = [
    "CHARTS_CROSS_BOUNDARY",
    "ExportOptions",
    "ExportResult",
    "LabelSource",
    "cut_partner_consistency",
    "export_part_layer",
    "node_extras",
    "write_manifest",
    "write_part_glb_from_reader",
]
