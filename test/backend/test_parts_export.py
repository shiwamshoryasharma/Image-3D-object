"""Part asset layer export, validation, publish and lineage (part-aware DESIGN.md sections 3.3-3.14; slice S3b).

The synthetic "character" (``character_mesh``) is one welded body sphere with painted hair, face, eyes and eyebrows,
plus a separate hat shell, an unlabelled shell and three tiny floaters, written as a TRELLIS.2-like ``model.glb``.
:func:`build_layer` runs the CPU pipeline (topology, shells, partners, label assignment, policy, export) into a staging
directory laid out like the runtime (``jobs/<job>/parts/<pa>/`` + ``temp/parts/<pa>/out``); the reader tests reuse it.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from parts_fixtures import SyntheticMesh, combine, floaters, uv_sphere, write_base_glb
from test_taxonomy_manifest import example

from core.feature_errors import JobDeletedError
from parts import publish as publish_module
from parts.components import MeshTopology, build_topology
from parts.export import ExportOptions, ExportResult, LabelSource, export_part_layer
from parts.frames import ViewCameraSpec, input_view
from parts.gltf_io import BaseMesh, read_base_glb, read_parts_glb
from parts.labels import LabelAssignment, VoteTable, assign_labels
from parts.lineage import compute_lineage, find_earlier_analyses
from parts.manifest import GeneratorInfo, PartsManifest, StrategyInfo, StrategySegmentation
from parts.policy import PartPlan, plan_parts
from parts.publish import PublishConflictError, StagingInvalidError, publish, validate_and_publish
from parts.shells import ShellReport, classify_shells, global_shell_eps, pair_opposite_faces
from parts.taxonomy import DEFAULT_SEPARATE, TAXONOMY
from parts.validate import validate_staging

RESOLUTION = 64
JOB_ID = "job_0123456789ab"
HAIR, FACE, EYE_L, EYE_R, BROW_L, BROW_R, BODY, HAT = 30, 3, 20, 21, 22, 23, 1, 50
IMAGE_SIZE = 64


# --------------------------------------------------------------------------------------------------
# Fixture and pipeline (shared with test_reader_consumers.py)
# --------------------------------------------------------------------------------------------------


def character_mesh() -> tuple[SyntheticMesh, np.ndarray]:
    """The character mesh and the painted label of every face (0 = no vote)."""
    body = uv_sphere(8000, radius=0.4)
    hat = uv_sphere(600, radius=0.1, center=(0.0, 0.62, 0.0))
    loose = uv_sphere(400, radius=0.08, center=(0.75, 0.0, 0.0))
    mesh = combine(body, hat, loose, floaters(3), names=("body", "hat", "loose", "floaters"))
    centroid = mesh.positions[mesh.faces].astype(np.float64).mean(axis=1)
    x, y, z = centroid[:, 0], centroid[:, 1], centroid[:, 2]
    labels = np.zeros(mesh.face_count, dtype=np.uint16)
    on_body = mesh.group_mask("body")
    labels[on_body] = BODY
    front = on_body & (z > 0.2)
    labels[front & (y > -0.15) & (y < 0.2)] = FACE
    labels[front & (y > 0.0) & (y < 0.08) & (x > 0.05) & (x < 0.16)] = EYE_L
    labels[front & (y > 0.0) & (y < 0.08) & (x < -0.05) & (x > -0.16)] = EYE_R
    labels[front & (y > 0.11) & (y < 0.15) & (x > 0.05) & (x < 0.16)] = BROW_L
    labels[front & (y > 0.11) & (y < 0.15) & (x < -0.05) & (x > -0.16)] = BROW_R
    labels[on_body & (y > 0.26)] = HAIR
    labels[mesh.group_mask("hat")] = HAT
    return mesh, labels


def votes_for(labels: np.ndarray) -> VoteTable:
    table = VoteTable(labels.shape[0])
    faces = np.flatnonzero(labels > 0)
    table.add(faces, labels[faces], np.ones(faces.size))
    return table


def class_masks_for(values: Sequence[int]) -> dict[int, np.ndarray]:
    """Deterministic, overlapping masks in a 64 x 64 conditioning space (hair overlaps the face)."""
    masks: dict[int, np.ndarray] = {}
    for offset, value in enumerate(values):
        mask = np.zeros((IMAGE_SIZE, IMAGE_SIZE), dtype=bool)
        mask[4 + offset: 30 + offset, 8 + 2 * offset: 40 + 2 * offset] = True
        masks[value] = mask
    return masks


def conditioning_image() -> np.ndarray:
    ramp = np.linspace(0, 255, IMAGE_SIZE, dtype=np.uint8)
    return np.dstack([np.tile(ramp, (IMAGE_SIZE, 1)), np.tile(ramp[:, None], (1, IMAGE_SIZE)),
                      np.full((IMAGE_SIZE, IMAGE_SIZE), 128, np.uint8), np.full((IMAGE_SIZE, IMAGE_SIZE), 255,
                                                                                np.uint8)])


@dataclass
class Layer:
    root: Path
    job_dir: Path
    analysis_id: str
    staging: Path
    target: Path
    base: BaseMesh
    topology: MeshTopology
    shells: ShellReport
    partners: np.ndarray
    assignment: LabelAssignment
    plan: PartPlan
    result: ExportResult

    @property
    def manifest(self) -> PartsManifest:
        return self.result.manifest


def make_job(root: Path, mesh: SyntheticMesh, job_id: str = JOB_ID) -> Path:
    job_dir = root / "jobs" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    if not (job_dir / "model.glb").exists():
        write_base_glb(job_dir / "model.glb", mesh)
    return job_dir


def generator() -> GeneratorInfo:
    return GeneratorInfo(app_version="1.0.0", engine="trellis2", model="TRELLIS.2-4B", pipeline_type="1536_cascade",
                         seed=1, resolution=RESOLUTION, effective_resolution=RESOLUTION, texture_size=1024,
                         polygon_target=10000)


def build_layer(
    root: Path,
    mesh: SyntheticMesh,
    labels: np.ndarray | None,
    *,
    analysis_id: str = "pa_000000000001",
    strategy: str = "semantic_parts",
    min_part_faces: int = 10,
    max_parts: int = 64,
    per_part_glbs: bool = False,
    earlier: Sequence[Any] = (),
    expected: Sequence[str] = (),
    masks: Mapping[int, np.ndarray] | None = None,
    fit_accepted: bool = True,
    render: tuple[np.ndarray, np.ndarray] | None = None,
) -> Layer:
    job_dir = make_job(root, mesh)
    (job_dir / "parts" / analysis_id).mkdir(parents=True, exist_ok=True)
    staging = root / "temp" / "parts" / analysis_id / "out"
    base = read_base_glb(job_dir / "model.glb")
    topology = build_topology(base.positions, base.faces)
    shells = classify_shells(topology, RESOLUTION)
    eps = global_shell_eps(topology, RESOLUTION)
    partners = pair_opposite_faces(topology.geometry.centroids, topology.geometry.normals, eps)
    if strategy == "components":
        from parts.labels import unlabelled_assignment

        assignment = unlabelled_assignment(topology.face_count)
    else:
        assert labels is not None
        assignment = assign_labels(votes_for(labels).summary(), topology, partners=partners, shells=shells,
                                   effective_resolution=RESOLUTION, fit_accepted=fit_accepted)
    plan = plan_parts(topology, shells, assignment.labels, strategy=strategy, separate=DEFAULT_SEPARATE,
                      min_part_faces=min_part_faces, max_parts=max_parts, face_flags=assignment.flags,
                      donor=assignment.donor)
    semantic = strategy != "components"
    views = [input_view(ViewCameraSpec(0.0, 0.0, 30.0, 1.93), method="silhouette_grid_refine",
                        iou=0.91 if fit_accepted else 0.4, accepted=fit_accepted, candidates_tested=1361)] \
        if semantic else []
    options = ExportOptions(
        analysis_id=analysis_id,
        job_id=JOB_ID,
        generator=generator(),
        strategy=StrategyInfo(
            id=strategy,
            segmentation=StrategySegmentation(source="internal" if semantic else "none",
                                              profile="baseline" if semantic else None),
            separate=list(DEFAULT_SEPARATE),
            min_part_faces=min_part_faces,
            max_parts=max_parts,
            split_shells=True,
            views=0,
        ),
        effective_resolution=RESOLUTION,
        views=views,
        label_source=LabelSource(provider="baseline", model="sam2_1_hiera_large") if semantic else None,
        class_masks=masks,
        conditioning_image=conditioning_image() if masks else None,
        per_part_glbs=per_part_glbs,
        expected_classes=list(expected),
        expectation_source="prompt" if expected else None,
        earlier=list(earlier),
        input_face_id=None if render is None else render[0],
        input_depth=None if render is None else render[1],
    )
    result = export_part_layer(staging, base, topology, shells, assignment, plan, options, partners=partners)
    target = job_dir / "parts" / analysis_id / "asset"
    return Layer(root, job_dir, analysis_id, staging, target, base, topology, shells, partners, assignment, plan,
                 result)


@pytest.fixture(scope="module")
def character() -> tuple[SyntheticMesh, np.ndarray]:
    return character_mesh()


@pytest.fixture
def layer(tmp_path: Path, character: tuple[SyntheticMesh, np.ndarray]) -> Layer:
    mesh, labels = character
    return build_layer(tmp_path, mesh, labels, expected=["object.held", "hair"],
                       masks=class_masks_for([HAIR, FACE, EYE_L, BROW_L]))


def _always() -> bool:
    return True


# --------------------------------------------------------------------------------------------------
# Full export
# --------------------------------------------------------------------------------------------------


def _keys(value: Any) -> set[str]:
    return set(value) if isinstance(value, dict) else set()


def test_export_manifest_validates_and_has_the_example_shape(layer: Layer) -> None:
    written = json.loads((layer.staging / "manifest.json").read_text(encoding="utf-8"))
    manifest = PartsManifest.model_validate(written)
    assert manifest.schema_id == "img3d.parts/1"
    assert validate_staging(layer.staging, manifest, layer.job_dir / "model.glb") == []
    reference = example()
    assert _keys(written) == _keys(reference)
    for key in ("generator", "source", "frames", "strategy", "artifacts", "expectations", "provenance",
                "refinement", "reassembly", "summary"):
        assert _keys(written[key]) == _keys(reference[key]), key
    reference_part = reference["parts"][0]
    for part in written["parts"]:
        assert _keys(part) == _keys(reference_part), part["id"]
        for key in ("region", "files", "stats", "frame", "placement", "geometry", "uv", "provenance", "refinement",
                    "lineage"):
            assert _keys(part[key]) == _keys(reference_part[key]), (part["id"], key)
    for key, sample in (("seams", reference["seams"][0]), ("relations", reference["relations"][0]),
                        ("buffers", reference["buffers"][0]), ("meshes", reference["meshes"][0]),
                        ("views", reference["views"][0])):
        for entry in written[key]:
            assert _keys(entry) == _keys(sample), key
    assert written["region_attribute"] == "_FEATURE_ID_0"
    assert written["source"]["model_sha256"] == hashlib.sha256((layer.job_dir / "model.glb").read_bytes()).hexdigest()


def test_export_parts_kinds_and_policy(layer: Layer) -> None:
    manifest = layer.manifest
    kinds = {part.id: part.kind for part in manifest.parts}
    assert kinds["body"] == "base"
    assert kinds["hair"] == "physical"
    assert kinds["eye.left"] == kinds["eye.right"] == "physical"
    assert kinds["eyebrow.left"] == kinds["eyebrow.right"] == "physical_deferred"
    assert kinds["body.head.face"] == "region"
    assert kinds["accessory.headwear"] == "physical"
    assert kinds["component_001"] == "physical"
    body = manifest.part("body")
    assert body is not None and len(body.geometry.floaters) == 3
    assert all(floater.faces == 4 for floater in body.geometry.floaters)
    assert {w.code for w in manifest.warnings} >= {"floater_candidates", "charts_cross_boundary"}
    summary = manifest.summary
    assert summary.physical_count == 5 and summary.deferred_count == 2
    assert summary.part_count == summary.physical_count + summary.deferred_count
    assert summary.unlabelled_face_ratio == pytest.approx(
        float((layer.plan.face_labels == 0).mean()), abs=1e-6)
    assert summary.cut_partner_consistency is not None and summary.camera_fit_iou == pytest.approx(0.91)
    meshes = {mesh.part_id: mesh for mesh in manifest.meshes}
    assert sum(mesh.faces for mesh in manifest.meshes) == manifest.source.faces
    assert [mesh.index for mesh in manifest.meshes] == list(range(len(manifest.meshes)))
    assert meshes["body"].index == 0


def test_export_buffers_match_their_sha256(layer: Layer) -> None:
    manifest = layer.manifest
    ids = [buffer.id for buffer in manifest.buffers]
    assert ids[:8] == ["face_labels", "face_owner", "face_confidence", "face_component", "face_part", "face_flags",
                       "weld_map", "seam_loops"]
    assert set(ids[8:]) == {f"vmap_{mesh.part_id}" for mesh in manifest.meshes}
    for buffer in manifest.buffers:
        data = (layer.staging / buffer.file).read_bytes()
        assert len(data) == buffer.byte_length
        assert hashlib.sha256(data).hexdigest() == buffer.sha256
    owner = np.fromfile(layer.staging / "buffers/face_owner.u16", dtype="<u2")
    assert np.array_equal(owner, layer.plan.face_owner)
    face_part = np.fromfile(layer.staging / "buffers/face_part.u16", dtype="<u2")
    assert np.array_equal(face_part, layer.plan.face_part)
    flags = np.fromfile(layer.staging / "buffers/face_flags.u8", dtype="u1")
    assert not np.any(flags & 0b1111_1000)


def test_export_relations(layer: Layer) -> None:
    manifest = layer.manifest
    by_type: dict[str, list[Any]] = {}
    for relation in manifest.relations:
        by_type.setdefault(relation.type, []).append(relation)
    assert [r.id for r in manifest.relations] == [f"rel_{i}" for i in range(len(manifest.relations))]
    eye = next(r for r in by_type["attached_to"] if r.a == "eye.left")
    assert eye.b == "body.head.face" and eye.metrics["contact"] == "seam"
    eye_seams = [seam for seam in manifest.seams if "eye.left" in (seam.a, seam.b)]
    assert eye.via == max(eye_seams, key=lambda seam: seam.length).id
    assert eye.metrics["shared_length"] == pytest.approx(max(seam.length for seam in eye_seams))
    symmetric = {(r.a, r.b) for r in by_type["symmetric_with"]}
    assert ("eye.left", "eye.right") in symmetric and ("eyebrow.left", "eyebrow.right") in symmetric
    region_of = {r.a: r.b for r in by_type["region_of"]}
    assert region_of["eyebrow.left"] == "body" and region_of["body.head.face"] == "body"
    missing = [(r.a, r.b, r.source) for r in by_type["expected_missing"]]
    assert missing == [("asset", "object.held", "prompt")]
    assert manifest.expectations.missing == ["object.held"]
    assert manifest.part("hair").expected is True  # type: ignore[union-attr]
    assert any(r.a == "hair" and r.b == "body.head.face" for r in by_type["layered_over"])
    for relation in by_type.get("adjacent_to", []):
        assert relation.a < relation.b


def test_export_frames_views_and_part_frames(layer: Layer) -> None:
    manifest = layer.manifest
    assert manifest.frames.asset.up == "+Y" and manifest.frames.asset.front == "+Z"
    assert manifest.frames.trellis.to_asset == [1, 0, 0, 0, 0, 0, 1, 0, 0, -1, 0, 0, 0, 0, 0, 1]
    view = manifest.views[0]
    assert view.id == "input" and view.weight == 1.0
    assert view.camera.world_to_camera == pytest.approx([1, 0, 0, 0, 0, -1, 0, 0, 0, 0, -1, 1.93, 0, 0, 0, 1])
    methods = {part.id: part.frame.method for part in manifest.parts}
    assert methods["body"] == "area_centroid"
    assert methods["eye.left"] == "seam_centroid" and methods["hair"] == "seam_centroid"
    assert methods["accessory.headwear"] == "bbox_center"
    assert methods["eyebrow.left"] == methods["body.head.face"] == "area_centroid"
    for part in manifest.parts:
        axes = np.asarray(part.frame.axes)
        assert np.allclose(axes @ axes.T, np.eye(3), atol=1e-6)
        assert np.linalg.det(axes) == pytest.approx(1.0, abs=1e-6)
        assert part.placement.method == "identity"
    eye_left = manifest.part("eye.left")
    assert eye_left is not None and eye_left.side == "left" and eye_left.image_side == "right"
    assert eye_left.symmetric_with == "eye.right" and eye_left.attach_to == "body.head.face"
    hat = manifest.part("accessory.headwear")
    assert hat is not None and hat.seams == [] and hat.parent == "body"


def test_export_refinement_locked_and_needs(layer: Layer) -> None:
    manifest = layer.manifest
    assert manifest.refinement.enabled is False and manifest.refinement.consumers == []
    for part in manifest.parts:
        assert part.refinement.status == "untouched" and part.refinement.locked
        assert part.refinement.lock_reason == "part_refinement_disabled"
    needs = {part.id: part.refinement.needs for part in manifest.parts}
    assert "occluded_geometry" in needs["body"]
    assert "geometry" in needs["eyebrow.left"]
    assert "closure" in needs["hair"] and "occluded_geometry" in needs["eye.left"]
    assert manifest.part("component_001").refinement.eligible is False  # type: ignore[union-attr]
    assert manifest.part("hair").uv.charts_cross_boundary is True  # type: ignore[union-attr]


def test_export_masks_thumbnails_and_glb(layer: Layer) -> None:
    from PIL import Image

    manifest = layer.manifest
    hair = manifest.part("hair")
    assert hair is not None and hair.files.mask == "masks/30.png" and hair.files.thumbnail == "thumbs/hair.webp"
    with Image.open(layer.staging / hair.files.thumbnail) as thumb:
        assert max(thumb.size) <= 256 and thumb.mode in ("RGBA", "RGB")
    face = manifest.part("body.head.face")
    assert face is not None and face.files.mask == "masks/3.png" and face.files.thumbnail is None
    assert manifest.artifacts.masks_dir == "masks"
    glb = read_parts_glb(layer.staging / "model_parts.glb")
    assert {node.part_id for node in glb.nodes} == {mesh.part_id for mesh in manifest.meshes}
    for node in glb.nodes:
        assert node.extras["id"] == node.part_id and "kind" in node.extras and node.feature_ids is not None
    assert glb.scene_extras["manifest"] == "manifest.json"
    assert manifest.reassembly.all_exact
    assert manifest.artifacts.assembled_glb.nodes == len(manifest.meshes)


def test_export_components_strategy(tmp_path: Path, character: tuple[SyntheticMesh, np.ndarray]) -> None:
    mesh, _labels = character
    layer = build_layer(tmp_path, mesh, None, strategy="components")
    manifest = layer.manifest
    assert manifest.views == [] and manifest.summary.camera_fit_iou is None
    assert [p.id for p in manifest.parts if p.kind == "physical"] == ["component_001", "component_002"]
    assert all(p.category == "unknown" and p.parent is None for p in manifest.parts if p.id.startswith("component"))
    assert len(manifest.part("body").geometry.floaters) == 3  # type: ignore[union-attr]
    confidence = np.fromfile(layer.staging / "buffers/face_confidence.u8", dtype="u1")
    assert np.all(confidence == 255)
    assert manifest.summary.cut_partner_consistency is None


def test_export_rejected_fit_assumes_front(tmp_path: Path, character: tuple[SyntheticMesh, np.ndarray]) -> None:
    mesh, labels = character
    layer = build_layer(tmp_path, mesh, labels, fit_accepted=False)
    eye = layer.manifest.part("eye.left")
    assert eye is not None and eye.side == "left"
    assert [s.method for s in eye.provenance.sources] == ["lifted", "assumed_front"]
    assert eye.confidence is not None and eye.confidence <= 0.5 + 1e-6


def test_export_occludes_from_the_input_render(tmp_path: Path, character: tuple[SyntheticMesh, np.ndarray]) -> None:
    mesh, labels = character
    centroid = mesh.positions[mesh.faces].astype(np.float64).mean(axis=1)
    hair_face = int(np.flatnonzero((labels == HAIR) & (centroid[:, 1] > 0.38))[0])
    body_face = int(np.flatnonzero((labels == BODY) & (centroid[:, 1] < -0.3))[0])
    face_id = np.full((32, 32), -1, dtype=np.int64)
    face_id[4:28, 4:16] = hair_face
    face_id[4:28, 16:28] = body_face
    depth = np.where(face_id == hair_face, 1.0, 2.0)
    layer = build_layer(tmp_path, mesh, labels, render=(face_id, depth))
    occludes = [r for r in layer.manifest.relations if r.type == "occludes"]
    assert [(r.a, r.b) for r in occludes] == [("hair", "body")]
    assert occludes[0].metrics == {"view": "input", "border_px": 24, "mean_depth_gap": 1.0}


# --------------------------------------------------------------------------------------------------
# Lineage
# --------------------------------------------------------------------------------------------------


def test_lineage_between_two_analyses(tmp_path: Path, character: tuple[SyntheticMesh, np.ndarray]) -> None:
    mesh, labels = character
    first = build_layer(tmp_path, mesh, labels, analysis_id="pa_00000000000a")
    publish(first.staging, first.target, _always)
    parts_root = first.job_dir / "parts"
    earlier = find_earlier_analyses(parts_root, model_sha256=first.base.sha256, exclude="pa_00000000000b")
    assert [source.analysis_id for source in earlier] == ["pa_00000000000a"]
    assert find_earlier_analyses(parts_root, model_sha256="0" * 64) == []
    second = build_layer(tmp_path, mesh, labels, analysis_id="pa_00000000000b", earlier=earlier)
    for part in second.manifest.parts:
        assert part.lineage.derived_from, part.id
        best = part.lineage.derived_from[0]
        assert best.part_id == part.id and best.analysis_id == "pa_00000000000a"
        assert best.face_iou == pytest.approx(1.0)
        assert len(part.lineage.derived_from) <= 3
        assert all(entry.face_iou >= 0.5 for entry in part.lineage.derived_from)
    # A different cut of the same base: hair grows, so its IoU drops below 1 but stays >= 0.5.
    relabelled = labels.copy()
    centroid = mesh.positions[mesh.faces].astype(np.float64).mean(axis=1)
    relabelled[mesh.group_mask("body") & (centroid[:, 1] > 0.24) & (relabelled == BODY)] = HAIR
    third = build_layer(tmp_path, mesh, relabelled, analysis_id="pa_00000000000c", earlier=earlier)
    entry = third.manifest.part("hair").lineage.derived_from[0]  # type: ignore[union-attr]
    assert 0.5 <= entry.face_iou < 1.0
    assert compute_lineage(earlier[0], earlier) == {pid: [] for pid in earlier[0].part_ids}


# --------------------------------------------------------------------------------------------------
# Validation and publish
# --------------------------------------------------------------------------------------------------


def test_publish_is_atomic_retries_and_refuses_existing_target(
    layer: Layer, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_replace = os.replace
    calls: list[tuple[str, str]] = []

    def flaky_replace(src: Any, dst: Any) -> None:
        calls.append((str(src), str(dst)))
        if len(calls) <= 2:
            raise PermissionError("file in use by another process")
        real_replace(src, dst)

    monkeypatch.setattr(publish_module.os, "replace", flaky_replace)
    published = validate_and_publish(layer.staging, layer.target, layer.manifest, layer.job_dir / "model.glb",
                                     _always)
    monkeypatch.setattr(publish_module.os, "replace", real_replace)
    assert published == layer.target and len(calls) == 3
    assert not layer.staging.exists() and (layer.target / "manifest.json").is_file()
    assert validate_staging(layer.target, layer.manifest, layer.job_dir / "model.glb") == []
    other = layer.root / "temp" / "parts" / layer.analysis_id / "again"
    other.mkdir(parents=True)
    with pytest.raises(PublishConflictError) as conflict:
        publish(other, layer.target, _always)
    assert conflict.value.code == "publish_conflict"
    assert other.is_dir()


def test_publish_job_deleted(layer: Layer) -> None:
    with pytest.raises(JobDeletedError) as deleted:
        publish(layer.staging, layer.target, lambda: False)
    assert deleted.value.code == "job_deleted" and deleted.value.job_id == JOB_ID
    assert layer.staging.is_dir() and not layer.target.exists()
    # The analysis folder is never recreated when the job folder disappeared.
    missing_parent = layer.root / "jobs" / "job_ffffffffffff" / "parts" / layer.analysis_id / "asset"
    with pytest.raises(JobDeletedError):
        publish(layer.staging, missing_parent, _always)
    assert not missing_parent.parent.exists()


def _rewrite_buffer(staging: Path, manifest: PartsManifest, buffer_id: str, data: np.ndarray) -> PartsManifest:
    record = manifest.buffer(buffer_id)
    assert record is not None
    raw = np.ascontiguousarray(data.astype(record.dtype)).tobytes()
    (staging / record.file).write_bytes(raw)
    updated = record.model_copy(update={"sha256": hashlib.sha256(raw).hexdigest()})
    return manifest.model_copy(update={"buffers": [updated if b.id == buffer_id else b for b in manifest.buffers]})


def test_validation_rejects_tampering(layer: Layer) -> None:
    base_path = layer.job_dir / "model.glb"
    manifest = layer.manifest
    assert validate_staging(layer.staging, manifest, base_path) == []

    # 1. A tampered buffer (manifest unchanged).
    labels_path = layer.staging / "buffers/face_labels.u16"
    original = labels_path.read_bytes()
    tampered = bytearray(original)
    tampered[0] ^= 0xFF
    labels_path.write_bytes(bytes(tampered))
    problems = validate_staging(layer.staging, manifest, base_path)
    assert any("face_labels" in p and "sha256" in p for p in problems)
    with pytest.raises(StagingInvalidError) as invalid:
        validate_and_publish(layer.staging, layer.target, manifest, base_path, _always)
    assert invalid.value.code == "export_failed" and not layer.target.exists()
    labels_path.write_bytes(original)

    # 2. A base mesh whose sha256 no longer matches (same size).
    base_copy = layer.root / "model_changed.glb"
    data = bytearray(base_path.read_bytes())
    data[-1] ^= 0x01
    base_copy.write_bytes(bytes(data))
    assert any("sha256" in p and "base" in p for p in validate_staging(layer.staging, manifest, base_copy))

    # 3. An inconsistent face_part (hashes updated, so only the consistency check can catch it).
    face_part = layer.plan.face_part.copy()
    hair_index = manifest.part_index("hair")
    body_faces = np.flatnonzero(layer.plan.face_owner == 0)
    face_part[body_faces[:5]] = hair_index
    bad = _rewrite_buffer(layer.staging, manifest, "face_part", face_part)
    problems = validate_staging(layer.staging, bad, base_path)
    assert any("face_part" in p and "hair" in p for p in problems)
    face_part[body_faces[:5]] = len(manifest.parts) + 3
    bad = _rewrite_buffer(layer.staging, manifest, "face_part", face_part)
    assert any("0xFFFF" in p for p in validate_staging(layer.staging, bad, base_path))

    # 4. Unlocked refinement and a path escaping the staging directory.
    hair = manifest.part("hair")
    assert hair is not None
    unlocked = hair.model_copy(update={"refinement": hair.refinement.model_copy(update={"locked": False})})
    escaped = unlocked.model_copy(update={"files": hair.files.model_copy(update={"mask": "masks/../../x.png"})})
    bad_parts = [escaped if p.id == "hair" else p for p in manifest.parts]
    problems = validate_staging(layer.staging, manifest.model_copy(update={"parts": bad_parts}), base_path)
    assert any("refinement" in p for p in problems) and any("mask" in p for p in problems)


def test_reserved_ids_and_counts_are_checked(layer: Layer) -> None:
    manifest = layer.manifest
    meshes = [m.model_copy(update={"faces": m.faces + 1}) if m.index == 0 else m for m in manifest.meshes]
    problems = validate_staging(layer.staging, manifest.model_copy(update={"meshes": meshes}), None)
    assert any("sum" in p for p in problems) and any("face_owner has" in p for p in problems)
    table = [entry for entry in manifest.label_table if entry.value != HAIR]
    problems = validate_staging(layer.staging, manifest.model_copy(update={"label_table": table}), None)
    assert any("label_table" in p for p in problems)
    assert TAXONOMY["hair"].value == HAIR


def test_validate_rejects_missing_staging(tmp_path: Path, layer: Layer) -> None:
    assert validate_staging(tmp_path / "nope", layer.manifest, None)
