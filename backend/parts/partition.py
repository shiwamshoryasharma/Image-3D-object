"""Mesh nodes and vertex maps (part-aware DESIGN.md section 3.4; asset invariants A1-A4; slice S3a).

For every mesh index ``k`` (0 = base, 1..n = physical parts) :func:`build_mesh_nodes` takes the faces
``flatnonzero(face_owner == k)`` in ascending base order and the ascending unique base vertices ``U`` they use. Where
incident faces of the node carry different labels, a vertex is duplicated once per extra label: the smallest label
keeps the original local vertex, every other label ``l`` gets ONE appended duplicate, appended in ``(vertex, label)``
order, and the corners of faces with label ``l`` use it. So ``vmap = concat(U, duplicate base ids)`` and every node
vertex carries exactly one ``_FEATURE_ID_0`` value. Nothing is welded, recomputed or reordered: gathering the base
accessors by ``vmap`` reassembles the base bit-exactly (A4).

Torch-free: numpy only.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from parts.gltf_io import BaseMesh
from parts.types import MeshNode

_LABEL_BITS = 16


def build_mesh_nodes(
    base: BaseMesh,
    face_owner: np.ndarray,
    face_labels: np.ndarray,
    *,
    part_ids: Sequence[str],
) -> list[MeshNode]:
    """Build one :class:`~parts.types.MeshNode` per mesh index (section 3.4).

    ``part_ids[k]`` is the part id of mesh index ``k``. Every mesh index must own at least one face, and
    ``face_owner`` must cover every base face (A1).
    """
    faces = np.asarray(base.faces)
    face_count = int(faces.shape[0])
    owner = _check_per_face(face_owner, face_count, "face_owner")
    labels = _check_per_face(face_labels, face_count, "face_labels")
    if labels.size and (int(labels.min()) < 0 or int(labels.max()) > 0xFFFF):
        raise ValueError("face_labels must be taxonomy values in [0, 65535]")
    node_count = len(part_ids)
    if len(set(part_ids)) != node_count:
        raise ValueError("part ids must be unique")
    if owner.size and (int(owner.min()) < 0 or int(owner.max()) >= node_count):
        raise ValueError(f"face_owner values must be mesh indices in [0, {node_count})")
    counts = np.bincount(owner, minlength=node_count)
    empty = np.flatnonzero(counts == 0)
    if empty.size:
        raise ValueError(f"mesh index {int(empty[0])} ({part_ids[int(empty[0])]!r}) owns no faces")

    order = np.argsort(owner, kind="stable")
    offsets = np.zeros(node_count + 1, dtype=np.int64)
    np.cumsum(counts, out=offsets[1:])
    nodes: list[MeshNode] = []
    for index, part_id in enumerate(part_ids):
        node_faces = order[offsets[index]:offsets[index + 1]].astype(np.uint32)
        nodes.append(_build_node(index, part_id, node_faces, faces[node_faces], labels[node_faces]))
    return nodes


def _check_per_face(values: np.ndarray, face_count: int, name: str) -> np.ndarray:
    array = np.asarray(values)
    if array.shape != (face_count,):
        raise ValueError(f"{name} must have shape ({face_count},), got {array.shape}")
    if not np.issubdtype(array.dtype, np.integer):
        raise ValueError(f"{name} must be an integer array, got {array.dtype.name}")
    return array.astype(np.int64, copy=False)


def _build_node(
    index: int, part_id: str, node_faces: np.ndarray, corners: np.ndarray, labels: np.ndarray
) -> MeshNode:
    corner_vertices = corners.reshape(-1).astype(np.int64)
    corner_labels = np.repeat(labels, 3)
    keys = (corner_vertices << _LABEL_BITS) | corner_labels
    pairs, inverse = np.unique(keys, return_inverse=True)
    pair_vertex = pairs >> _LABEL_BITS
    pair_label = (pairs & 0xFFFF).astype(np.uint16)
    is_first = np.ones(pairs.shape[0], dtype=bool)
    is_first[1:] = pair_vertex[1:] != pair_vertex[:-1]
    unique_count = int(is_first.sum())
    local_of_pair = np.empty(pairs.shape[0], dtype=np.int64)
    local_of_pair[is_first] = np.arange(unique_count, dtype=np.int64)
    local_of_pair[~is_first] = unique_count + np.arange(pairs.shape[0] - unique_count, dtype=np.int64)
    vmap = np.concatenate([pair_vertex[is_first], pair_vertex[~is_first]]).astype(np.uint32)
    feature_ids = np.concatenate([pair_label[is_first], pair_label[~is_first]])
    local_faces = local_of_pair[inverse.reshape(-1)].reshape(-1, 3).astype(np.uint32)
    return MeshNode(
        index=index,
        part_id=part_id,
        faces_base=node_faces,
        vmap=vmap,
        unique_count=unique_count,
        local_faces=local_faces,
        feature_ids=feature_ids,
    )


def face_owner_from_nodes(nodes: Sequence[MeshNode], face_count: int) -> np.ndarray:
    """Rebuild ``face_owner`` (uint16 ``[F]``) from mesh nodes; raises if a face is owned zero or several times."""
    owner = np.full(face_count, -1, dtype=np.int64)
    for node in nodes:
        faces = node.faces_base.astype(np.int64)
        if faces.size and (faces.min() < 0 or faces.max() >= face_count):
            raise ValueError(f"node {node.part_id!r} references faces outside [0, {face_count})")
        if np.any(owner[faces] != -1):
            raise ValueError(f"node {node.part_id!r} shares faces with another node")
        owner[faces] = node.index
    if np.any(owner < 0):
        raise ValueError("some faces belong to no mesh node")
    return owner.astype(np.uint16)


def gather_node_attributes(
    base: BaseMesh, node: MeshNode
) -> tuple[np.ndarray, np.ndarray | None, np.ndarray | None]:
    """``(positions, normals, uv)`` of a node: the base accessors gathered by ``node.vmap`` (A4)."""
    vmap = node.vmap
    return (
        base.positions[vmap],
        None if base.normals is None else base.normals[vmap],
        None if base.uv is None else base.uv[vmap],
    )
