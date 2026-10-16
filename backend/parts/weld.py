"""Welded vertex space and welded edges (part-aware DESIGN.md sections 3.1 and 4.6.5 step 1; slice S3a).

Base vertices are grouped by **bit-identical** position: ``weld_map[v] = w``. TRELLIS.2 duplicates vertices along UV
seams, so the base vertex space is not connected across chart borders; the welded space is. It is used for adjacency,
components and seams, and never to reorder output (section 3.1).

The welded edge table lists every undirected welded edge once, with the faces that use it (any number: non-manifold
edges are kept, section 4.6.5 "regardless of manifoldness"). Degenerate corner edges (both ends on one welded vertex)
are not edges.

Torch-free: numpy and scipy only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import numpy as np
from scipy import sparse

NO_EDGE: Final = -1
"""``face_edges`` value of a degenerate corner edge."""


@dataclass(frozen=True, slots=True, eq=False)
class WeldResult:
    """``weld_map`` uint32 ``[V]`` (welded id of every base vertex) and the welded positions float32 ``[W, 3]``."""

    weld_map: np.ndarray
    welded_positions: np.ndarray

    @property
    def vertex_count(self) -> int:
        return int(self.weld_map.shape[0])

    @property
    def welded_count(self) -> int:
        return int(self.welded_positions.shape[0])


def weld_vertices(positions: np.ndarray) -> WeldResult:
    """Group base vertices by bit-identical float32 position (section 4.6.5 step 1).

    On numpy 2.4 the inverse of ``np.unique`` over a ``(V, 1)`` void view keeps the shape ``(V, 1)``, which would make
    ``weld_map[faces]`` silently ``(F, 3, 1)``; the ``.ravel()`` / ``.reshape(-1)`` pair below is mandatory.
    """
    array = np.asarray(positions)
    if array.dtype != np.float32 or array.ndim != 2 or array.shape[1] != 3:
        raise ValueError(f"positions must be float32 (V, 3), got {array.dtype.name} {array.shape}")
    contiguous = np.ascontiguousarray(array)
    if contiguous.shape[0] == 0:
        return WeldResult(np.zeros(0, dtype=np.uint32), np.zeros((0, 3), dtype=np.float32))
    keys = contiguous.view(np.dtype((np.void, 12))).ravel()
    _unique, first, inverse = np.unique(keys, return_index=True, return_inverse=True)
    weld_map = inverse.reshape(-1).astype(np.uint32)
    return WeldResult(weld_map=weld_map, welded_positions=contiguous[first])


def welded_faces(faces: np.ndarray, weld_map: np.ndarray) -> np.ndarray:
    """``weld_map[faces]`` as uint32 ``[F, 3]`` (shape-checked, see :func:`weld_vertices`)."""
    result = np.asarray(weld_map)[np.asarray(faces)]
    if result.ndim != 2 or result.shape[1] != 3:
        raise ValueError(f"welded faces must have shape (F, 3), got {result.shape}")
    return result.astype(np.uint32, copy=False)


@dataclass(frozen=True, slots=True, eq=False)
class EdgeTable:
    """Undirected welded edges and their incident faces.

    * ``edges`` uint32 ``[E, 2]``: welded vertex ids, ``edges[:, 0] < edges[:, 1]``, sorted ascending.
    * ``face_edges`` int64 ``[F, 3]``: edge id of corner edge ``i`` of each face, i.e. of the welded corners
      ``(i, (i + 1) % 3)``; :data:`NO_EDGE` for a degenerate corner edge.
    * ``face_offsets`` int64 ``[E + 1]`` / ``edge_faces`` uint32: the faces of edge ``e`` are
      ``edge_faces[face_offsets[e]:face_offsets[e + 1]]`` in ascending face order.
    """

    edges: np.ndarray
    face_edges: np.ndarray
    face_offsets: np.ndarray
    edge_faces: np.ndarray

    @property
    def edge_count(self) -> int:
        return int(self.edges.shape[0])

    @property
    def face_counts(self) -> np.ndarray:
        """Number of incident faces per edge (1 = open boundary, 2 = manifold, > 2 = non-manifold)."""
        return np.diff(self.face_offsets)

    def faces_of(self, edge: int) -> np.ndarray:
        return self.edge_faces[self.face_offsets[edge]:self.face_offsets[edge + 1]]

    def boundary_edges(self) -> np.ndarray:
        """Edge ids used by exactly one face (open on the base)."""
        return np.flatnonzero(self.face_counts == 1)

    def nonmanifold_edges(self) -> np.ndarray:
        """Edge ids used by more than two faces."""
        return np.flatnonzero(self.face_counts > 2)

    def first_face(self) -> np.ndarray:
        """The lowest incident face of every edge, uint32 ``[E]``."""
        return self.edge_faces[self.face_offsets[:-1]]


def build_edge_table(welded: np.ndarray, welded_count: int) -> EdgeTable:
    """Build the welded edge table of welded faces ``welded`` (uint32 ``[F, 3]``)."""
    faces = np.asarray(welded, dtype=np.int64)
    face_count = int(faces.shape[0])
    starts = faces.reshape(-1)
    ends = np.roll(faces, -1, axis=1).reshape(-1)
    low = np.minimum(starts, ends)
    high = np.maximum(starts, ends)
    valid = low != high
    keys = low[valid] * np.int64(max(welded_count, 1)) + high[valid]
    unique_keys, inverse = np.unique(keys, return_inverse=True)
    inverse = inverse.reshape(-1)
    face_edges = np.full(face_count * 3, NO_EDGE, dtype=np.int64)
    face_edges[valid] = inverse
    face_edges = face_edges.reshape(face_count, 3)

    divisor = np.int64(max(welded_count, 1))
    edges = np.stack([unique_keys // divisor, unique_keys % divisor], axis=1).astype(np.uint32)
    corner_faces = np.repeat(np.arange(face_count, dtype=np.int64), 3)[valid]
    order = np.lexsort((corner_faces, inverse))
    counts = np.bincount(inverse, minlength=unique_keys.shape[0])
    offsets = np.zeros(unique_keys.shape[0] + 1, dtype=np.int64)
    np.cumsum(counts, out=offsets[1:])
    return EdgeTable(
        edges=edges,
        face_edges=face_edges,
        face_offsets=offsets,
        edge_faces=corner_faces[order].astype(np.uint32),
    )


def face_adjacency(table: EdgeTable, face_count: int) -> sparse.csr_array:
    """Symmetric ``F x F`` adjacency of faces that share a welded edge, regardless of manifoldness.

    A manifold edge links its two faces; the faces of a non-manifold edge are linked pairwise. A face that shares
    two edges with another face still yields one entry, and no face is its own neighbour. ``data`` is ``int8``
    ones.
    """
    counts = table.face_counts
    rows: list[np.ndarray] = []
    cols: list[np.ndarray] = []
    for size in np.unique(counts[counts >= 2]).tolist():
        edge_ids = np.flatnonzero(counts == size)
        members = table.edge_faces[table.face_offsets[edge_ids][:, None] + np.arange(size)[None, :]]
        first, second = np.triu_indices(size, k=1)
        rows.append(members[:, first].reshape(-1))
        cols.append(members[:, second].reshape(-1))
    if rows:
        row = np.concatenate(rows).astype(np.int64)
        col = np.concatenate(cols).astype(np.int64)
    else:
        row = col = np.zeros(0, dtype=np.int64)
    distinct = row != col
    row, col = row[distinct], col[distinct]
    both_rows = np.concatenate([row, col])
    both_cols = np.concatenate([col, row])
    matrix = sparse.coo_array(
        (np.ones(both_rows.shape[0], dtype=np.int8), (both_rows, both_cols)), shape=(face_count, face_count)
    ).tocsr()
    matrix.data[:] = 1
    return matrix


def edge_lengths(table: EdgeTable, welded_positions: np.ndarray) -> np.ndarray:
    """Euclidean length of every welded edge, float64 ``[E]``."""
    points = np.asarray(welded_positions, dtype=np.float64)
    return np.linalg.norm(points[table.edges[:, 1]] - points[table.edges[:, 0]], axis=1)
