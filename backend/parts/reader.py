"""Read API of a published part layer (part-aware DESIGN.md section 3.13; slice S3b).

:class:`AssetReader` is the ONLY read API that future consumers (tuning, refinement, DCC export) may use. It opens
``jobs/<job>/parts/<pa>/asset/``, parses ``manifest.json`` (``img3d.parts/1``; unknown fields ignored) and serves:

* ``base()``: the job's ``model.glb`` (``asset_dir.parents[2]``), checked against ``source.model_sha256``;
* ``buffer(id)``: a raw buffer after checking its byte length (and sha256 unless ``verify="none"``);
* ``part(id)``: the geometry of a mesh-owning part, read from ``model_parts.glb`` (``ValueError`` for regions and
  deferred parts, which own no mesh);
* ``region_faces(id)``: the base faces whose finest covering record is ``id`` (``face_part == index``);
* ``seams(id)``, ``relations(id)``;
* ``reassemble()``: the inverse of ``build_mesh_nodes``: the base arrays rebuilt from the GLB nodes, ``vmap_<id>`` and
  ``face_owner``.

``verify``: ``"none"`` skips hashes; ``"hashes"`` (default) checks each buffer, the assembled GLB and the base when they
are first read; ``"full"`` checks every hash up front and that ``reassemble()`` equals the base bit for bit.

Torch-free: numpy only (plus the json/struct GLB reader of :mod:`parts.gltf_io`).
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

import numpy as np

from parts.buffers import BufferError, buffer_problem, file_sha256, read_buffer
from parts.gltf_io import BaseMesh, PartsGlb, read_base_glb, read_parts_glb
from parts.manifest import PartFrame, PartRecord, PartsManifest, RelationRecord, SeamRecord
from parts.types import BASE_MODEL_FILE, FACE_PART_NONE, MANIFEST_FILE

VerifyMode = Literal["none", "hashes", "full"]
VERIFY_MODES: Final = ("none", "hashes", "full")


class AssetReadError(ValueError):
    """The part layer cannot be read or does not match its manifest."""


@dataclass(frozen=True, slots=True, eq=False)
class SeamLoops:
    """One seam of a part: its loops as welded vertex ids and asset-frame positions.

    ``other`` is the part on the other side of the cut; ``host_opening`` is ``True`` when that side is the base.
    A consumer that closes or remeshes the part keeps these positions fixed while ``seam_lock`` is set (section 3.5).
    """

    seam_id: str
    a: str
    b: str
    other: str
    host_opening: bool
    loops: tuple[np.ndarray, ...]
    """uint32 welded vertex ids per loop (slices of the ``seam_loops`` buffer)."""
    points: tuple[np.ndarray, ...]
    """float32 ``[n, 3]`` positions per loop (asset frame)."""
    closed: tuple[bool, ...]


@dataclass(frozen=True, slots=True, eq=False)
class PartGeometry:
    """Geometry of one mesh-owning part, exactly as written in ``model_parts.glb`` (asset frame).

    ``vertex_map[i]`` is the base vertex of node vertex ``i``; ``base_faces[j]`` the base face of local face ``j``, so
    ``vertex_map[faces] == base.faces[base_faces]`` row for row (asset invariant A4).
    """

    part_id: str
    positions: np.ndarray
    normals: np.ndarray | None
    uv: np.ndarray | None
    faces: np.ndarray
    vertex_map: np.ndarray
    base_faces: np.ndarray
    seams: list[SeamLoops]
    frame: PartFrame
    feature_ids: np.ndarray | None = None


class AssetReader:
    """The ONLY read API that future consumers (tuning, refinement, DCC export) may use."""

    def __init__(self, asset_dir: Path, *, verify: VerifyMode = "hashes") -> None:
        if verify not in VERIFY_MODES:
            raise ValueError(f"verify must be one of {', '.join(VERIFY_MODES)}")
        self.asset_dir = Path(asset_dir)
        self.verify: VerifyMode = verify
        manifest_path = self.asset_dir / MANIFEST_FILE
        try:
            document = json.loads(manifest_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise AssetReadError(f"{MANIFEST_FILE} is missing in {self.asset_dir}") from exc
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AssetReadError(f"cannot read {MANIFEST_FILE}: {exc}") from exc
        self.manifest: PartsManifest = PartsManifest.model_validate(document)
        self._buffers: dict[str, np.ndarray] = {}
        self._glb: PartsGlb | None = None
        self._base: BaseMesh | None = None
        self._welded_positions: np.ndarray | None = None
        if verify == "full":
            self.verify_hashes()
            self._verify_reassembly()

    # ------------------------------------------------------------------ paths
    @property
    def job_dir(self) -> Path:
        """``jobs/<job>/`` (``asset/../../..``)."""
        return self.asset_dir.parents[2]

    @property
    def base_path(self) -> Path:
        return self.job_dir / BASE_MODEL_FILE

    @property
    def assembled_path(self) -> Path:
        return self.asset_dir / self.manifest.artifacts.assembled_glb.file

    # ------------------------------------------------------------------ sources
    def base(self) -> BaseMesh:
        """The job's ``model.glb``; its sha256 must equal ``source.model_sha256`` (unless ``verify="none"``)."""
        if self._base is None:
            expected = None if self.verify == "none" else self.manifest.source.model_sha256
            self._base = read_base_glb(self.base_path, expected_sha256=expected)
        return self._base

    def buffer(self, buffer_id: str) -> np.ndarray:
        """A buffer by id (dtype, count and byte length checked; sha256 too unless ``verify="none"``)."""
        cached = self._buffers.get(buffer_id)
        if cached is not None:
            return cached
        record = self.manifest.buffer(buffer_id)
        if record is None:
            raise KeyError(f"the manifest has no buffer {buffer_id!r}")
        data = read_buffer(self.asset_dir, record, check_hash=self.verify != "none")
        data.setflags(write=False)
        self._buffers[buffer_id] = data
        return data

    def assembled(self) -> PartsGlb:
        """``model_parts.glb`` (its sha256 is checked against ``artifacts.assembled_glb`` unless ``verify="none"``)."""
        if self._glb is None:
            record = self.manifest.artifacts.assembled_glb
            path = self.assembled_path
            if self.verify != "none":
                if not path.is_file():
                    raise AssetReadError(f"{record.file} is missing")
                if path.stat().st_size != record.bytes or file_sha256(path) != record.sha256:
                    raise AssetReadError(f"{record.file} does not match its manifest record (size or sha256)")
            self._glb = read_parts_glb(path)
        return self._glb

    def verify_hashes(self) -> None:
        """Check every buffer, the assembled GLB and the base hash now (raises on the first mismatch)."""
        if self.verify == "none":
            return
        for record in self.manifest.buffers:
            problem = buffer_problem(self.asset_dir, record, check_hash=True)
            if problem is not None:
                raise BufferError(problem)
        self.assembled()
        self.base()

    # ------------------------------------------------------------------ records
    def record(self, part_id: str) -> PartRecord:
        part = self.manifest.part(part_id)
        if part is None:
            raise KeyError(f"the manifest has no part {part_id!r}")
        return part

    def part(self, part_id: str) -> PartGeometry:
        """Geometry of a mesh-owning part (base or physical). ``ValueError`` for regions and deferred parts."""
        record = self.record(part_id)
        if not record.owns_mesh or record.mesh_index is None:
            raise ValueError(f"part {part_id!r} is a {record.kind} record and owns no mesh; use region_faces()")
        mesh = self.manifest.mesh(part_id)
        if mesh is None or mesh.index != record.mesh_index:
            raise AssetReadError(f"part {part_id!r} has no matching meshes[] entry")
        node = self.assembled().node(part_id)
        vertex_map = self.buffer(mesh.vertex_map)
        base_faces = np.flatnonzero(self.buffer("face_owner") == mesh.index).astype(np.uint32)
        if node.positions.shape[0] != vertex_map.shape[0] or node.faces.shape[0] != base_faces.shape[0]:
            raise AssetReadError(f"node part:{part_id} does not match vmap/face_owner")
        return PartGeometry(
            part_id=part_id,
            positions=node.positions,
            normals=node.normals,
            uv=node.uv,
            faces=node.faces,
            vertex_map=vertex_map,
            base_faces=base_faces,
            seams=self.seams(part_id),
            frame=record.frame,
            feature_ids=node.feature_ids,
        )

    def region_faces(self, part_id: str) -> np.ndarray:
        """Base faces whose finest covering record is ``part_id`` (``flatnonzero(face_part == index)``)."""
        index = self.manifest.part_index(part_id)
        return np.flatnonzero(self.buffer("face_part") == index)

    def unassigned_faces(self) -> np.ndarray:
        """Base faces no record covers (``face_part == 0xFFFF``: floaters and unlabelled faces)."""
        return np.flatnonzero(self.buffer("face_part") == FACE_PART_NONE)

    def seams(self, part_id: str) -> list[SeamLoops]:
        """Every seam that touches ``part_id`` (as ``a`` or ``b``), in record order."""
        self.record(part_id)
        return [self._seam_loops(seam, part_id) for seam in self.manifest.seams if part_id in (seam.a, seam.b)]

    def relations(self, part_id: str | None = None) -> list[RelationRecord]:
        if part_id is None:
            return list(self.manifest.relations)
        return self.manifest.relations_of(part_id)

    # ------------------------------------------------------------------ reassembly
    def reassemble(self) -> BaseMesh:
        """Rebuild the base arrays from the GLB nodes, the vertex maps and ``face_owner`` (inverse of
        ``build_mesh_nodes``). Metadata (document, images, sha256) comes from the base; base vertices that no face
        references (none in TRELLIS.2 output) are zero."""
        base = self.base()
        glb = self.assembled()
        owner = self.buffer("face_owner").astype(np.int64)
        face_count = int(owner.shape[0])
        vertex_count = self.manifest.source.vertices
        positions = np.zeros((vertex_count, 3), dtype=np.float32)
        normals = None if base.normals is None else np.zeros((vertex_count, 3), dtype=np.float32)
        uv = None if base.uv is None else np.zeros((vertex_count, 2), dtype=np.float32)
        faces = np.zeros((face_count, 3), dtype=np.uint32)
        for mesh in self.manifest.meshes:
            node = glb.node(mesh.part_id)
            vmap = self.buffer(mesh.vertex_map).astype(np.int64)
            if vmap.size and int(vmap.max()) >= vertex_count:
                raise AssetReadError(f"{mesh.vertex_map} references vertices outside the base")
            positions[vmap] = node.positions
            if normals is not None and node.normals is not None:
                normals[vmap] = node.normals
            if uv is not None and node.uv is not None:
                uv[vmap] = node.uv
            node_faces = np.flatnonzero(owner == mesh.index)
            if node_faces.shape[0] != node.faces.shape[0]:
                raise AssetReadError(f"node part:{mesh.part_id} has a different face count than face_owner")
            faces[node_faces] = vmap[node.faces.astype(np.int64)].astype(np.uint32)
        return dataclasses.replace(base, positions=positions, normals=normals, uv=uv, faces=faces)

    def _verify_reassembly(self) -> None:
        base = self.base()
        rebuilt = self.reassemble()
        used = np.zeros(base.vertex_count, dtype=bool)
        used[np.asarray(base.faces, dtype=np.int64).reshape(-1)] = True
        problems: list[str] = []
        if not np.array_equal(rebuilt.faces, base.faces):
            problems.append("faces")
        for name in ("positions", "normals", "uv"):
            original = getattr(base, name)
            if original is None:
                continue
            written = getattr(rebuilt, name)
            if not np.array_equal(written[used].view(np.uint32), np.asarray(original)[used].view(np.uint32)):
                problems.append(name)
        if problems:
            raise AssetReadError(f"the part layer does not reassemble to the base: {', '.join(problems)} differ")

    # ------------------------------------------------------------------ helpers
    def _welded(self) -> np.ndarray:
        """Position of every welded vertex (first base vertex of each weld group)."""
        if self._welded_positions is None:
            weld_map = self.buffer("weld_map").astype(np.int64)
            welded_count = int(weld_map.max()) + 1 if weld_map.size else 0
            first = np.full(welded_count, -1, dtype=np.int64)
            first[weld_map[::-1]] = np.arange(weld_map.shape[0] - 1, -1, -1, dtype=np.int64)
            self._welded_positions = np.asarray(self.base().positions)[first].astype(np.float32)
        return self._welded_positions

    def _seam_loops(self, seam: SeamRecord, part_id: str) -> SeamLoops:
        loop_buffer = self.buffer("seam_loops")
        welded = self._welded()
        loops: list[np.ndarray] = []
        points: list[np.ndarray] = []
        for loop in seam.loops:
            end = loop.start + loop.count
            if end > loop_buffer.shape[0]:
                raise AssetReadError(f"{seam.id} loop [{loop.start}, {end}) lies outside the seam_loops buffer")
            ids = loop_buffer[loop.start:end]
            loops.append(ids)
            points.append(welded[ids.astype(np.int64)])
        return SeamLoops(
            seam_id=seam.id,
            a=seam.a,
            b=seam.b,
            other=seam.b if seam.a == part_id else seam.a,
            host_opening=seam.host_opening,
            loops=tuple(loops),
            points=tuple(points),
            closed=tuple(loop.closed for loop in seam.loops),
        )


__all__ = [
    "VERIFY_MODES",
    "AssetReadError",
    "AssetReader",
    "PartGeometry",
    "SeamLoops",
    "VerifyMode",
]
