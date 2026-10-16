"""Bit-exact reassembly check on the written files (part-aware DESIGN.md asset invariants A1 and A4; slice S3a).

For every mesh node the written ``POSITION``, ``NORMAL`` and ``TEXCOORD_0`` accessors must equal the base accessors
gathered by the node's vertex map, compared on the raw float32 bits (so ``-0.0`` versus ``0.0`` or a changed NaN
payload counts as a difference), and ``vmap[local_faces]`` must equal ``base.faces[node_faces]`` row for row, where
``node_faces = flatnonzero(face_owner == k)``. Every base face must belong to exactly one written node. The check reads
the GLB back from disk (``parts.gltf_io.read_parts_glb``) and is run inside every analysis; any mismatch fails the run
with ``partition_failed`` (section 4.6.5 step 7).

Torch-free: numpy only.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

import numpy as np

from parts.gltf_io import BaseMesh, GlbFormatError, GlbMeshNode, read_parts_glb
from parts.manifest import Reassembly
from parts.types import MeshNode

_MAX_PROBLEMS: Final = 20


class ReassemblyError(RuntimeError):
    """The written part layer does not reassemble to the base bit-exactly (run error code below)."""

    code: Final = "partition_failed"


@dataclass(frozen=True, slots=True)
class ReassemblyReport:
    """Outcome of the A4 check. ``verified`` means every node was found in the file and compared."""

    verified: bool
    positions_exact: bool
    normals_exact: bool
    uvs_exact: bool
    faces_exact: bool
    nodes_checked: int
    problems: tuple[str, ...] = ()

    @property
    def all_exact(self) -> bool:
        return self.verified and self.positions_exact and self.normals_exact and self.uvs_exact and self.faces_exact

    def to_manifest(self) -> Reassembly:
        return Reassembly(
            verified=self.verified,
            positions_exact=self.positions_exact,
            uvs_exact=self.uvs_exact,
            normals_exact=self.normals_exact,
            faces_exact=self.faces_exact,
        )

    def raise_if_inexact(self) -> None:
        """Raise :class:`ReassemblyError` (``partition_failed``) unless every flag is true."""
        if not self.all_exact:
            detail = "; ".join(self.problems) or "unknown mismatch"
            raise ReassemblyError(f"The reassembly check failed: {detail}")


@dataclass(slots=True)
class _Tally:
    positions: bool = True
    normals: bool = True
    uvs: bool = True
    faces: bool = True
    verified: bool = True
    problems: list[str] = field(default_factory=list)

    def fail(self, flag: str, message: str) -> None:
        setattr(self, flag, False)
        if len(self.problems) < _MAX_PROBLEMS:
            self.problems.append(message)


def verify_reassembly(
    base: BaseMesh,
    glb_path: Path | str,
    *,
    part_ids: Sequence[str],
    vmaps: Sequence[np.ndarray],
    face_owner: np.ndarray,
) -> ReassemblyReport:
    """Check the written GLB against the base (A1, A4).

    ``part_ids[k]`` / ``vmaps[k]`` describe mesh index ``k`` (normally read back from the written ``vmap_<id>`` and
    ``face_owner`` buffers, so the check covers the files a consumer will read).
    """
    if len(part_ids) != len(vmaps):
        raise ValueError("part_ids and vmaps must have the same length")
    tally = _Tally()
    try:
        written = read_parts_glb(glb_path)
    except (OSError, GlbFormatError) as exc:
        tally.fail("verified", f"cannot read {Path(glb_path).name}: {exc}")
        tally.positions = tally.normals = tally.uvs = tally.faces = False
        return _report(tally, 0)

    face_count = base.face_count
    owner = np.asarray(face_owner)
    if owner.shape != (face_count,) or not np.issubdtype(owner.dtype, np.integer):
        tally.fail("faces", f"face_owner must have shape ({face_count},)")
        return _report(tally, 0)
    owner = owner.astype(np.int64, copy=False)
    if owner.size and (int(owner.min()) < 0 or int(owner.max()) >= len(part_ids)):
        tally.fail("faces", "face_owner references a mesh index without a node")
    by_id: dict[str, GlbMeshNode] = {}
    for node in written.nodes:
        if node.part_id in by_id:
            tally.fail("faces", f"the GLB has two nodes for part {node.part_id!r}")
        by_id[node.part_id] = node
    unexpected = sorted(set(by_id) - set(part_ids))
    if unexpected:
        tally.fail("faces", f"the GLB has nodes without a mesh index: {', '.join(unexpected)}")

    order = np.argsort(owner, kind="stable")
    counts = np.bincount(owner[(owner >= 0) & (owner < len(part_ids))], minlength=len(part_ids))
    offsets = np.zeros(len(part_ids) + 1, dtype=np.int64)
    np.cumsum(counts, out=offsets[1:])
    checked = 0
    for index, (part_id, vmap) in enumerate(zip(part_ids, vmaps, strict=True)):
        node = by_id.get(part_id)
        if node is None:
            tally.fail("verified", f"node part:{part_id} is missing from the GLB")
            tally.faces = False
            continue
        node_faces = order[offsets[index]:offsets[index + 1]]
        _check_node(tally, base, node, np.asarray(vmap), node_faces)
        checked += 1
    return _report(tally, checked)


def verify_mesh_nodes(base: BaseMesh, glb_path: Path | str, nodes: Sequence[MeshNode]) -> ReassemblyReport:
    """:func:`verify_reassembly` with the vertex maps and ``face_owner`` taken from in-memory mesh nodes."""
    ordered = sorted(nodes, key=lambda node: node.index)
    if [node.index for node in ordered] != list(range(len(ordered))):
        raise ValueError("mesh node indices must be 0..n-1")
    owner = np.full(base.face_count, -1, dtype=np.int64)
    for node in ordered:
        owner[node.faces_base.astype(np.int64)] = node.index
    if np.any(owner < 0):
        tally = _Tally()
        tally.fail("faces", f"{int((owner < 0).sum())} base faces belong to no mesh node")
        return _report(tally, 0)
    return verify_reassembly(
        base,
        glb_path,
        part_ids=[node.part_id for node in ordered],
        vmaps=[node.vmap for node in ordered],
        face_owner=owner,
    )


def _check_node(tally: _Tally, base: BaseMesh, node: GlbMeshNode, vmap: np.ndarray, node_faces: np.ndarray) -> None:
    label = f"part:{node.part_id}"
    vertex_count = base.vertex_count
    if vmap.ndim != 1 or vmap.shape[0] != node.positions.shape[0]:
        tally.fail("positions", f"{label}: vmap has {vmap.shape} entries, the node has {node.positions.shape[0]}")
        return
    if vmap.size and int(vmap.max()) >= vertex_count:
        tally.fail("positions", f"{label}: vmap points past the base vertices")
        return
    if not _bits_equal(node.positions, base.positions[vmap]):
        tally.fail("positions", f"{label}: POSITION differs from the base")
    for flag, name, written, source in (
        ("normals", "NORMAL", node.normals, base.normals),
        ("uvs", "TEXCOORD_0", node.uv, base.uv),
    ):
        if (written is None) != (source is None):
            tally.fail(flag, f"{label}: {name} is {'missing' if written is None else 'unexpected'}")
        elif written is not None and source is not None and not _bits_equal(written, source[vmap]):
            tally.fail(flag, f"{label}: {name} differs from the base")
    faces = node.faces
    if faces.shape[0] != node_faces.shape[0]:
        tally.fail("faces", f"{label}: {faces.shape[0]} faces written, face_owner assigns {node_faces.shape[0]}")
        return
    if faces.size and int(faces.max()) >= vmap.shape[0]:
        tally.fail("faces", f"{label}: an index points past the node vertices")
        return
    if not np.array_equal(vmap[faces], base.faces[node_faces]):
        tally.fail("faces", f"{label}: vmap[local_faces] differs from the base faces")


def _bits_equal(written: np.ndarray, expected: np.ndarray) -> bool:
    if written.shape != expected.shape or written.dtype != np.float32 or expected.dtype != np.float32:
        return False
    return bool(np.array_equal(np.ascontiguousarray(written).view(np.uint32),
                               np.ascontiguousarray(expected).view(np.uint32)))


def _report(tally: _Tally, checked: int) -> ReassemblyReport:
    return ReassemblyReport(
        verified=tally.verified,
        positions_exact=tally.positions,
        normals_exact=tally.normals,
        uvs_exact=tally.uvs,
        faces_exact=tally.faces,
        nodes_checked=checked,
        problems=tuple(tally.problems),
    )
