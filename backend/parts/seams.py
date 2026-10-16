"""Seams between mesh nodes (part-aware DESIGN.md section 3.5; slice S3a).

* **Seam edge.** A welded edge with at least one incident face owned by physical part ``a`` and at least one owned by
  another owner ``b``. A non-manifold edge whose faces have three owners yields one seam edge per owner pair. Edges
  that are open on the base (one incident face) are ``base_open``: they are counted by :func:`open_boundary_loops`
  (``geometry.open_boundary_loops``) and are not seams.
* **Pair convention.** ``a`` is the higher mesh index (the base, index 0, is always ``b``); ``host_opening`` is
  ``b == base``.
* **Loops.** The edges of each pair are chained by walking welded vertices. A vertex whose degree in the pair's edge
  set is not 2 (a chain end, or a branch point at a non-manifold junction) ends a chain, which is recorded with
  ``closed=false``; the remaining edges form closed loops. Loops follow the winding of ``a``'s faces where it is
  consistent: open chains start so that their first edge runs as in an ``a`` face, closed loops start at their
  smallest welded vertex and leave it along such an edge. The outer and inner sheets of a double-walled cut both
  contribute loops to the same record.
* **Output.** One record per pair (``seam_0``, ``seam_1``, ... ordered by ``(a, b)``) and the ``seam_loops`` buffer
  (uint32 welded vertex ids, every loop concatenated; each loop is a ``{start, count}`` slice of it).

Torch-free: numpy only.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from parts.components import MeshTopology
from parts.manifest import SeamLoop, SeamRecord
from parts.types import BASE_MESH_INDEX

SEAM_ID_PREFIX = "seam_"


@dataclass(frozen=True, slots=True)
class SeamLoopData:
    """One chain of welded vertices: ``seam_loops[start:start + count]``."""

    start: int
    count: int
    closed: bool
    length: float


@dataclass(frozen=True, slots=True)
class SeamData:
    """The cut between physical part ``a`` (mesh index ``a_index``) and owner ``b`` (``b_index < a_index``)."""

    id: str
    a: str
    b: str
    a_index: int
    b_index: int
    host_opening: bool
    boundary_edges: int
    length: float
    loops: tuple[SeamLoopData, ...]

    def to_manifest(self) -> SeamRecord:
        return SeamRecord(
            id=self.id,
            a=self.a,
            b=self.b,
            host_opening=self.host_opening,
            boundary_edges=self.boundary_edges,
            length=self.length,
            loops=[SeamLoop(start=lp.start, count=lp.count, closed=lp.closed, length=lp.length) for lp in self.loops],
        )


@dataclass(frozen=True, slots=True, eq=False)
class SeamSet:
    """Every seam record plus the ``seam_loops`` buffer (uint32 welded vertex ids)."""

    seams: tuple[SeamData, ...]
    loop_vertices: np.ndarray

    def manifest_records(self) -> list[SeamRecord]:
        return [seam.to_manifest() for seam in self.seams]

    def seam_ids_for(self, part_id: str) -> list[str]:
        """Ids of the seams that touch ``part_id`` (as ``a`` or ``b``), in record order."""
        return [seam.id for seam in self.seams if part_id in (seam.a, seam.b)]

    def loops_of(self, seam_id: str) -> list[np.ndarray]:
        """Welded vertex ids of every loop of one seam."""
        for seam in self.seams:
            if seam.id == seam_id:
                return [self.loop_vertices[lp.start:lp.start + lp.count] for lp in seam.loops]
        raise KeyError(seam_id)


@dataclass(frozen=True, slots=True)
class _Chain:
    vertices: list[int]
    closed: bool


def build_seams(topology: MeshTopology, face_owner: np.ndarray, part_ids: Sequence[str]) -> SeamSet:
    """Seam records of a partition (section 3.5). ``part_ids[k]`` is the part id of mesh index ``k``."""
    owner = _check_owner(face_owner, topology.face_count, len(part_ids))
    edge_ids, a_index, b_index, a_face = _seam_edges(topology, owner)
    if edge_ids.size == 0:
        return SeamSet(seams=(), loop_vertices=np.zeros(0, dtype=np.uint32))
    start, end = _directed_in_face(topology, edge_ids, a_face)
    points = topology.welded_positions.astype(np.float64)
    order = np.lexsort((b_index, a_index))
    a_sorted, b_sorted = a_index[order], b_index[order]
    boundaries = np.flatnonzero((np.diff(a_sorted) != 0) | (np.diff(b_sorted) != 0)) + 1
    groups = np.split(order, boundaries)

    seams: list[SeamData] = []
    buffer: list[int] = []
    for number, group in enumerate(groups):
        a, b = int(a_index[group[0]]), int(b_index[group[0]])
        loops: list[SeamLoopData] = []
        for chain in _chain_edges(start[group], end[group]):
            length = _chain_length(points, chain)
            loops.append(SeamLoopData(start=len(buffer), count=len(chain.vertices), closed=chain.closed,
                                      length=length))
            buffer.extend(chain.vertices)
        seams.append(
            SeamData(
                id=f"{SEAM_ID_PREFIX}{number}",
                a=part_ids[a],
                b=part_ids[b],
                a_index=a,
                b_index=b,
                host_opening=b == BASE_MESH_INDEX,
                boundary_edges=int(group.shape[0]),
                length=float(sum(lp.length for lp in loops)),
                loops=tuple(loops),
            )
        )
    return SeamSet(seams=tuple(seams), loop_vertices=np.asarray(buffer, dtype=np.uint32))


def open_boundary_loops(topology: MeshTopology, face_owner: np.ndarray, node_count: int) -> np.ndarray:
    """Number of base-open boundary loops (closed loops plus open chains) per mesh index, int64 ``[node_count]``.

    A base-open edge has exactly one incident face; it belongs to that face's owner. Seams are not counted.
    """
    owner = _check_owner(face_owner, topology.face_count, node_count)
    table = topology.edges
    boundary = table.boundary_edges()
    result = np.zeros(node_count, dtype=np.int64)
    if boundary.size == 0:
        return result
    faces = table.first_face()[boundary].astype(np.int64)
    start, end = _directed_in_face(topology, boundary, faces)
    owners = owner[faces]
    for index in np.unique(owners).tolist():
        selected = owners == index
        result[index] = len(_chain_edges(start[selected], end[selected]))
    return result


def _check_owner(face_owner: np.ndarray, face_count: int, node_count: int) -> np.ndarray:
    owner = np.asarray(face_owner)
    if owner.shape != (face_count,) or not np.issubdtype(owner.dtype, np.integer):
        raise ValueError(f"face_owner must be an integer array of shape ({face_count},)")
    owner = owner.astype(np.int64, copy=False)
    if owner.size and (int(owner.min()) < 0 or int(owner.max()) >= node_count):
        raise ValueError(f"face_owner values must be mesh indices in [0, {node_count})")
    return owner


def _seam_edges(
    topology: MeshTopology, owner: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """``(edge id, a index, b index, one a-owned face)`` for every (edge, owner pair) seam edge."""
    table = topology.edges
    counts = table.face_counts
    manifold = np.flatnonzero(counts == 2)
    first = table.edge_faces[table.face_offsets[manifold]].astype(np.int64)
    second = table.edge_faces[table.face_offsets[manifold] + 1].astype(np.int64)
    owner_first, owner_second = owner[first], owner[second]
    cut = owner_first != owner_second
    edge_ids = [manifold[cut]]
    a_index = [np.maximum(owner_first, owner_second)[cut]]
    b_index = [np.minimum(owner_first, owner_second)[cut]]
    a_face = [np.where(owner_first > owner_second, first, second)[cut]]

    extra_edges: list[int] = []
    extra_a: list[int] = []
    extra_b: list[int] = []
    extra_face: list[int] = []
    for edge in np.flatnonzero(counts > 2).tolist():
        faces = table.faces_of(edge).astype(np.int64)
        owners = owner[faces]
        distinct = np.unique(owners).tolist()
        for high_position, high in enumerate(distinct):
            for low in distinct[:high_position]:
                extra_edges.append(edge)
                extra_a.append(high)
                extra_b.append(low)
                extra_face.append(int(faces[np.argmax(owners == high)]))
    if extra_edges:
        edge_ids.append(np.asarray(extra_edges, dtype=np.int64))
        a_index.append(np.asarray(extra_a, dtype=np.int64))
        b_index.append(np.asarray(extra_b, dtype=np.int64))
        a_face.append(np.asarray(extra_face, dtype=np.int64))
    return (
        np.concatenate(edge_ids).astype(np.int64),
        np.concatenate(a_index).astype(np.int64),
        np.concatenate(b_index).astype(np.int64),
        np.concatenate(a_face).astype(np.int64),
    )


def _directed_in_face(topology: MeshTopology, edge_ids: np.ndarray, faces: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Welded ``(start, end)`` of each edge as it runs in the winding of the given incident face."""
    corner = np.argmax(topology.edges.face_edges[faces] == edge_ids[:, None], axis=1)
    welded = topology.welded.astype(np.int64)
    return welded[faces, corner], welded[faces, (corner + 1) % 3]


def _chain_edges(start: np.ndarray, end: np.ndarray) -> list[_Chain]:
    """Chain directed edges into open chains (ending at vertices of degree != 2) and closed loops."""
    starts = start.tolist()
    ends = end.tolist()
    incident: dict[int, list[int]] = defaultdict(list)
    for edge, (u, v) in enumerate(zip(starts, ends, strict=True)):
        incident[u].append(edge)
        incident[v].append(edge)
    visited = [False] * len(starts)

    def walk(origin: int, edge: int) -> list[int]:
        sequence = [origin]
        current = origin
        while True:
            visited[edge] = True
            following = ends[edge] if starts[edge] == current else starts[edge]
            sequence.append(following)
            if len(incident[following]) != 2:
                return sequence
            unvisited = [e for e in incident[following] if not visited[e]]
            if not unvisited:
                return sequence
            current, edge = following, unvisited[0]

    chains: list[_Chain] = []
    for vertex in sorted(v for v, edges in incident.items() if len(edges) != 2):
        for edge in incident[vertex]:
            if visited[edge]:
                continue
            sequence = walk(vertex, edge)
            if not (starts[edge] == sequence[0] and ends[edge] == sequence[1]):
                sequence.reverse()
            chains.append(_Chain(sequence, closed=False))
    for vertex in sorted(incident):
        candidates = [e for e in incident[vertex] if not visited[e]]
        if not candidates:
            continue
        outgoing = [e for e in candidates if starts[e] == vertex]
        if outgoing:
            edge = outgoing[0]
        else:
            edge = min(candidates, key=lambda e: ends[e] if starts[e] == vertex else starts[e])
        sequence = walk(vertex, edge)
        if len(sequence) > 1 and sequence[-1] == sequence[0]:
            sequence.pop()
        chains.append(_Chain(sequence, closed=True))
    return chains


def _chain_length(points: np.ndarray, chain: _Chain) -> float:
    vertices = np.asarray(chain.vertices, dtype=np.int64)
    if chain.closed:
        vertices = np.append(vertices, vertices[0])
    if vertices.shape[0] < 2:
        return 0.0
    return float(np.linalg.norm(np.diff(points[vertices], axis=0), axis=1).sum())
