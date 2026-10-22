"""Validation of a staged part layer before publish (part-aware DESIGN.md section 3.14; slice S3b).

:func:`validate_staging` returns every problem as a list of strings (empty = publishable). It checks what the pydantic
manifest model cannot see:

* ids are well formed and unique (the manifest re-validates; ``manifest.json`` on disk matches it);
* every file path is relative, has no ``..``, resolves inside the staging directory and exists;
* each buffer's dtype, count, byte length and sha256 match (face buffers have ``source.faces`` entries, ``weld_map``
  ``source.vertices``, every ``vmap_<id>`` its mesh's vertex count) and ``face_flags`` keeps bits 3-7 zero;
* every region value appears in ``label_table`` and every label-table entry names its taxonomy class;
* mesh indices are contiguous, every mesh is a mesh-owning record, ``face_owner < len(meshes)`` and each node's face
  count matches ``face_owner``; the mesh face counts sum to ``source.faces``;
* ``face_part`` is ``0xFFFF`` or ``< len(parts)``; the faces of record ``i`` carry its ``region.value`` and lie on its
  host (its own node for mesh-owning records), and ``region.face_count`` counts them; floaters have ``face_part ==
  0xFFFF``;
* every ``reassembly`` flag is true and refinement is locked;
* the assembled GLB matches its record, and the base ``model.glb``, re-hashed now, matches ``source.model_sha256``.

Torch-free: numpy only.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Final

import numpy as np
from pydantic import ValidationError

from parts.buffers import buffer_problem, file_sha256, read_buffer
from parts.manifest import BufferRecord, PartsManifest, safe_relative_path_problem
from parts.taxonomy import BY_VALUE, part_id_problem
from parts.types import (
    BUFFER_SPECS,
    FACE_FLAGS_RESERVED_MASK,
    FACE_PART_NONE,
    INDEX_BASE_FACES,
    INDEX_BASE_VERTICES,
    MANIFEST_FILE,
    REFINEMENT_LOCK_REASON,
    vmap_buffer_spec,
)

MAX_PROBLEMS: Final = 200


class _Problems:
    def __init__(self) -> None:
        self.items: list[str] = []

    def add(self, message: str) -> None:
        if len(self.items) < MAX_PROBLEMS:
            self.items.append(message)
        elif len(self.items) == MAX_PROBLEMS:
            self.items.append("... further problems omitted")

    def extend(self, messages: Iterable[str]) -> None:
        for message in messages:
            self.add(message)


def validate_staging(staging_dir: Path, manifest: PartsManifest, base_path: Path | None) -> list[str]:
    """Every reason why ``staging_dir`` is not a publishable part layer for ``manifest`` (empty list = valid).

    ``base_path`` is the job's ``model.glb``; ``None`` skips only the base re-hash (export checks before the publish
    step; :func:`parts.publish.validate_and_publish` always passes the path).
    """
    root = Path(staging_dir)
    problems = _Problems()
    if not root.is_dir():
        return [f"staging directory {root} does not exist"]
    _check_model(problems, root, manifest)
    _check_paths(problems, root, manifest)
    buffers = _check_buffers(problems, root, manifest)
    _check_labels(problems, manifest)
    owner = _check_meshes(problems, manifest, buffers.get("face_owner"))
    _check_face_part(problems, manifest, buffers, owner)
    if "face_flags" in buffers:
        flag_problem = check_face_flags(buffers["face_flags"])
        if flag_problem is not None:
            problems.add(flag_problem)
    _check_flags(problems, manifest)
    _check_assembled(problems, root, manifest)
    if base_path is not None:
        _check_base(problems, Path(base_path), manifest)
    return problems.items


# --------------------------------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------------------------------


def _check_model(problems: _Problems, root: Path, manifest: PartsManifest) -> None:
    document = manifest.to_json_dict()
    try:
        PartsManifest.model_validate(document)
    except ValidationError as exc:
        problems.extend(f"manifest: {error['msg']} at {'.'.join(map(str, error['loc']))}" for error in exc.errors())
    for part in manifest.parts:
        problem = part_id_problem(part.id)
        if problem is not None:
            problems.add(f"part id {part.id!r}: {problem}")
    path = root / MANIFEST_FILE
    if not path.is_file():
        problems.add(f"{MANIFEST_FILE} is missing")
        return
    try:
        written = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        problems.add(f"{MANIFEST_FILE} cannot be read: {exc}")
        return
    if written != json.loads(json.dumps(document)):
        problems.add(f"{MANIFEST_FILE} differs from the manifest being published")


def _manifest_files(manifest: PartsManifest) -> list[tuple[str, str, bool]]:
    """``(what, path, is_directory)`` of every file the manifest names (``source.model_file`` excluded)."""
    files: list[tuple[str, str, bool]] = [(f"buffer {b.id}", b.file, False) for b in manifest.buffers]
    artifacts = manifest.artifacts
    files.append(("artifacts.assembled_glb", artifacts.assembled_glb.file, False))
    files.extend((f"artifacts.part_glbs.{key}", value, False) for key, value in artifacts.part_glbs.items())
    if artifacts.segmentation is not None:
        for name in ("labels", "overlay", "frame", "source"):
            files.append((f"artifacts.segmentation.{name}", getattr(artifacts.segmentation, name), False))
    if artifacts.masks_dir is not None:
        files.append(("artifacts.masks_dir", artifacts.masks_dir, True))
    files.extend(("artifacts.previews", preview, False) for preview in artifacts.previews)
    if artifacts.log is not None:
        files.append(("artifacts.log", artifacts.log, False))
    if manifest.frames.conditioning is not None:
        files.append(("frames.conditioning.file", manifest.frames.conditioning.file, False))
    for part in manifest.parts:
        for name in ("glb", "mask", "thumbnail"):
            value = getattr(part.files, name)
            if value is not None:
                files.append((f"parts[{part.id}].files.{name}", value, False))
    return files


def _check_paths(problems: _Problems, root: Path, manifest: PartsManifest) -> None:
    resolved_root = root.resolve()
    for what, value, is_dir in _manifest_files(manifest):
        problem = safe_relative_path_problem(value)
        if problem is not None:
            problems.add(f"{what}: {value!r} {problem}")
            continue
        target = (root / value).resolve()
        if target != resolved_root and resolved_root not in target.parents:
            problems.add(f"{what}: {value!r} resolves outside the staging directory")
            continue
        if is_dir and not target.is_dir():
            problems.add(f"{what}: directory {value} is missing")
        elif not is_dir and not target.is_file():
            problems.add(f"{what}: file {value} is missing")


def _expected_buffer(record: BufferRecord, manifest: PartsManifest) -> tuple[str, int | None, str] | None:
    """``(dtype, count, indexes)`` the manifest implies for a buffer id (``None`` = unknown buffer)."""
    for spec in BUFFER_SPECS:
        if spec.id == record.id:
            if spec.indexes == INDEX_BASE_FACES:
                count: int | None = manifest.source.faces
            elif spec.indexes == INDEX_BASE_VERTICES:
                count = manifest.source.vertices
            else:
                count = sum(loop.count for seam in manifest.seams for loop in seam.loops)
            return spec.dtype, count, spec.indexes
    for mesh in manifest.meshes:
        if mesh.vertex_map == record.id:
            spec = vmap_buffer_spec(mesh.part_id)
            return spec.dtype, mesh.vertices, spec.indexes
    return None


def _check_buffers(problems: _Problems, root: Path, manifest: PartsManifest) -> dict[str, np.ndarray]:
    present = {record.id for record in manifest.buffers}
    for spec in BUFFER_SPECS:
        if spec.id not in present:
            problems.add(f"buffer {spec.id!r} is missing from the manifest")
    for mesh in manifest.meshes:
        if mesh.vertex_map not in present:
            problems.add(f"vertex map {mesh.vertex_map!r} of mesh {mesh.index} is missing from the manifest")
    arrays: dict[str, np.ndarray] = {}
    for record in manifest.buffers:
        expected = _expected_buffer(record, manifest)
        if expected is None:
            problems.add(f"buffer {record.id!r} is not part of the img3d.parts/1 layout")
            continue
        dtype, count, indexes = expected
        if record.dtype != dtype:
            problems.add(f"buffer {record.id!r}: dtype {record.dtype} != {dtype}")
            continue
        if record.indexes != indexes:
            problems.add(f"buffer {record.id!r}: indexes {record.indexes!r} != {indexes!r}")
        if count is not None and record.count != count:
            problems.add(f"buffer {record.id!r}: count {record.count} != {count}")
            continue
        problem = buffer_problem(root, record, check_hash=True)
        if problem is not None:
            problems.add(problem)
            continue
        if record.id in ("face_labels", "face_owner", "face_part", "face_flags", "face_component", "seam_loops"):
            arrays[record.id] = read_buffer(root, record, check_hash=False)
    loops = manifest.buffer("seam_loops")
    if loops is not None:
        for seam in manifest.seams:
            for loop in seam.loops:
                if loop.start + loop.count > loops.count:
                    problems.add(f"{seam.id}: loop [{loop.start}, {loop.start + loop.count}) exceeds seam_loops")
    return arrays


def _check_labels(problems: _Problems, manifest: PartsManifest) -> None:
    table = {entry.value: entry.category for entry in manifest.label_table}
    for value, category in table.items():
        cls = BY_VALUE.get(value)
        if cls is None or cls.id != category:
            problems.add(f"label_table: value {value} is not taxonomy class {category!r}")
    for part in manifest.parts:
        if part.region.value not in table:
            problems.add(f"part {part.id!r}: region value {part.region.value} is missing from label_table")


def _check_meshes(
    problems: _Problems, manifest: PartsManifest, face_owner: np.ndarray | None
) -> np.ndarray | None:
    meshes = sorted(manifest.meshes, key=lambda mesh: mesh.index)
    if [mesh.index for mesh in meshes] != list(range(len(meshes))):
        problems.add("mesh indices are not contiguous from 0")
    if not meshes or meshes[0].index != 0:
        problems.add("there is no base mesh (index 0)")
    for mesh in meshes:
        part = manifest.part(mesh.part_id)
        if part is None or not part.owns_mesh or part.mesh_index != mesh.index:
            problems.add(f"mesh {mesh.index}: part {mesh.part_id!r} is not a mesh-owning record with that index")
    owning = [part for part in manifest.parts if part.owns_mesh]
    if len(owning) != len(meshes):
        problems.add(f"{len(owning)} mesh-owning records but {len(meshes)} meshes")
    if sum(mesh.faces for mesh in meshes) != manifest.source.faces:
        problems.add(f"mesh face counts sum to {sum(m.faces for m in meshes)}, source has {manifest.source.faces}")
    if face_owner is None:
        return None
    owner = face_owner.astype(np.int64)
    if owner.size and int(owner.max()) >= len(meshes):
        problems.add(f"face_owner references mesh index {int(owner.max())} (only {len(meshes)} meshes)")
        return None
    counts = np.bincount(owner, minlength=len(meshes))
    for mesh in meshes:
        if mesh.index < counts.shape[0] and int(counts[mesh.index]) != mesh.faces:
            problems.add(f"mesh {mesh.part_id!r}: {mesh.faces} faces in meshes[], face_owner has {counts[mesh.index]}")
    return owner


def _check_face_part(
    problems: _Problems, manifest: PartsManifest, buffers: dict[str, np.ndarray], owner: np.ndarray | None
) -> None:
    face_part = buffers.get("face_part")
    labels = buffers.get("face_labels")
    if face_part is None or labels is None or owner is None:
        return
    record_count = len(manifest.parts)
    part_index = face_part.astype(np.int64)
    invalid = (part_index != FACE_PART_NONE) & (part_index >= record_count)
    if invalid.any():
        problems.add(f"face_part holds {int(invalid.sum())} values that are neither 0xFFFF nor < {record_count}")
        return
    covered = part_index != FACE_PART_NONE
    order = np.argsort(part_index[covered], kind="stable")
    faces_sorted = np.flatnonzero(covered)[order]
    counts = np.bincount(part_index[covered], minlength=record_count)
    offsets = np.zeros(record_count + 1, dtype=np.int64)
    np.cumsum(counts, out=offsets[1:])
    mesh_of = {mesh.part_id: mesh.index for mesh in manifest.meshes}
    for index, part in enumerate(manifest.parts):
        faces = faces_sorted[offsets[index]:offsets[index + 1]]
        if part.owns_mesh:
            node = part.mesh_index
        else:
            node = mesh_of.get(part.host) if part.host is not None else None
            if node is None:
                problems.add(f"part {part.id!r}: host {part.host!r} is not a mesh node")
                continue
        if faces.size and np.any(labels[faces] != part.region.value):
            problems.add(f"part {part.id!r}: face_part faces carry labels other than {part.region.value}")
        if faces.size and np.any(owner[faces] != node):
            problems.add(f"part {part.id!r}: face_part faces lie outside mesh {node}")
        if int(faces.size) != part.region.face_count:
            problems.add(f"part {part.id!r}: region.face_count {part.region.face_count} != {faces.size} faces")
    components = buffers.get("face_component")
    if components is None:
        return
    for part in manifest.parts:
        for floater in part.geometry.floaters:
            faces = np.flatnonzero(components == floater.component)
            if faces.size == 0:
                problems.add(f"floater component {floater.component} has no faces")
            elif np.any(part_index[faces] != FACE_PART_NONE):
                problems.add(f"floater component {floater.component} is covered by a part record")


def _check_flags(problems: _Problems, manifest: PartsManifest) -> None:
    if not manifest.reassembly.all_exact:
        failed = [name for name in ("verified", "positions_exact", "uvs_exact", "normals_exact", "faces_exact")
                  if not getattr(manifest.reassembly, name)]
        problems.add(f"reassembly is not exact: {', '.join(failed)}")
    if manifest.refinement.enabled or manifest.refinement.consumers:
        problems.add("refinement must stay disabled with no consumers while part refinement is locked")
    for part in manifest.parts:
        refinement = part.refinement
        if refinement.status != "untouched" or not refinement.locked or refinement.lock_reason != REFINEMENT_LOCK_REASON:
            problems.add(f"part {part.id!r}: refinement is not locked/untouched")


def _check_assembled(problems: _Problems, root: Path, manifest: PartsManifest) -> None:
    record = manifest.artifacts.assembled_glb
    path = root / record.file
    if not path.is_file():
        return  # reported by the path check
    if path.stat().st_size != record.bytes:
        problems.add(f"{record.file}: {path.stat().st_size} bytes, manifest says {record.bytes}")
    elif file_sha256(path) != record.sha256:
        problems.add(f"{record.file}: sha256 mismatch")
    if record.nodes != len(manifest.meshes):
        problems.add(f"{record.file}: {record.nodes} nodes, manifest has {len(manifest.meshes)} meshes")


def _check_base(problems: _Problems, base_path: Path, manifest: PartsManifest) -> None:
    if not base_path.is_file():
        problems.add(f"the base mesh {base_path.name} is missing")
        return
    if base_path.stat().st_size != manifest.source.model_bytes:
        problems.add(f"the base mesh changed size ({base_path.stat().st_size} != {manifest.source.model_bytes})")
    elif file_sha256(base_path) != manifest.source.model_sha256:
        problems.add("the base mesh sha256 differs from source.model_sha256")


def check_face_flags(face_flags: np.ndarray) -> str | None:
    """Why a ``face_flags`` array is invalid (reserved bits set), or ``None``."""
    reserved = (np.asarray(face_flags, dtype=np.uint8) & FACE_FLAGS_RESERVED_MASK) != 0
    return f"face_flags has reserved bits set on {int(reserved.sum())} faces" if reserved.any() else None


__all__ = ["MAX_PROBLEMS", "check_face_flags", "validate_staging"]
