"""Welded connected components and the topology bundle of the ``prepare`` step (DESIGN.md section 4.6.5 step 1).

* Face geometry: area, centroid and unit geometric normal of every base face (float64 maths, float32 storage).
* Connected components over the welded face adjacency (``scipy.sparse.csgraph.connected_components``), renumbered by
  surface area so that component 0 is the largest (ties: more faces first, then the lowest face id). This is the
  ``face_component`` buffer of section 3.3.
* :func:`build_topology` runs weld, welded edges, adjacency, face geometry and components in one call; the shells
  (``parts/shells.py``), seams (``parts/seams.py``) and the label assignment of slice S3b consume the result.

Torch-free: numpy and scipy only.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import sparse
from scipy.sparse import csgraph

from parts.weld import EdgeTable, WeldResult, build_edge_table, face_adjacency, weld_vertices, welded_faces


@dataclass(frozen=True, slots=True, eq=False)
class FaceGeometry:
    """Per-face ``areas`` float64 ``[F]``, ``centroids`` float32 ``[F, 3]`` and unit ``normals`` float32 ``[F, 3]``.

    Normals follow the face winding (right-hand rule); a degenerate face has a zero normal.
    """

    areas: np.ndarray
    centroids: np.ndarray
    normals: np.ndarray


def face_geometry(positions: np.ndarray, faces: np.ndarray, *, chunk_size: int = 1 << 20) -> FaceGeometry:
    """Area, centroid and geometric unit normal of every face (computed in float64 chunks)."""
    points = np.asarray(positions)
    corners = np.asarray(faces)
    face_count = int(corners.shape[0])
    areas = np.empty(face_count, dtype=np.float64)
    centroids = np.empty((face_count, 3), dtype=np.float32)
    normals = np.zeros((face_count, 3), dtype=np.float32)
    for start in range(0, face_count, chunk_size):
        stop = min(start + chunk_size, face_count)
        triangle = points[corners[start:stop]].astype(np.float64)
        cross = np.cross(triangle[:, 1] - triangle[:, 0], triangle[:, 2] - triangle[:, 0])
        norm = np.linalg.norm(cross, axis=1)
        areas[start:stop] = 0.5 * norm
        centroids[start:stop] = triangle.mean(axis=1)
        nonzero = norm > 0.0
        normals[start:stop][nonzero] = cross[nonzero] / norm[nonzero, None]
    return FaceGeometry(areas=areas, centroids=centroids, normals=normals)


@dataclass(frozen=True, slots=True, eq=False)
class ComponentSet:
    """Welded connected components ordered by surface area (0 = largest).

    * ``face_component`` uint32 ``[F]``.
    * ``face_counts`` int64 ``[C]`` and ``areas`` float64 ``[C]``.
    * ``offsets`` int64 ``[C + 1]`` / ``faces``: the faces of component ``c`` are ``faces[offsets[c]:offsets[c + 1]]``
      in ascending face order.
    """

    face_component: np.ndarray
    face_counts: np.ndarray
    areas: np.ndarray
    offsets: np.ndarray
    faces: np.ndarray

    @property
    def count(self) -> int:
        return int(self.face_counts.shape[0])

    def faces_of(self, component: int) -> np.ndarray:
        return self.faces[self.offsets[component]:self.offsets[component + 1]]


def connected_components(adjacency: sparse.csr_array, face_areas: np.ndarray) -> ComponentSet:
    """Connected components of the welded face adjacency, renumbered so that 0 is the largest by area."""
    face_count = int(adjacency.shape[0])
    if face_count == 0:
        empty = np.zeros(0, dtype=np.int64)
        return ComponentSet(np.zeros(0, dtype=np.uint32), empty, np.zeros(0), np.zeros(1, dtype=np.int64),
                            np.zeros(0, dtype=np.uint32))
    count, raw = csgraph.connected_components(adjacency, directed=False)
    raw = raw.astype(np.int64, copy=False)
    areas = np.bincount(raw, weights=np.asarray(face_areas, dtype=np.float64), minlength=count)
    face_counts = np.bincount(raw, minlength=count).astype(np.int64)
    first_face = np.full(count, face_count, dtype=np.int64)
    np.minimum.at(first_face, raw, np.arange(face_count, dtype=np.int64))
    order = np.lexsort((first_face, -face_counts, -areas))
    rank = np.empty(count, dtype=np.int64)
    rank[order] = np.arange(count, dtype=np.int64)
    face_component = rank[raw]
    sorted_faces = np.argsort(face_component, kind="stable")
    offsets = np.zeros(count + 1, dtype=np.int64)
    np.cumsum(face_counts[order], out=offsets[1:])
    return ComponentSet(
        face_component=face_component.astype(np.uint32),
        face_counts=face_counts[order],
        areas=areas[order],
        offsets=offsets,
        faces=sorted_faces.astype(np.uint32),
    )


@dataclass(frozen=True, slots=True, eq=False)
class MeshTopology:
    """Everything the ``prepare`` step derives from the base arrays (base face space throughout).

    ``welded`` uint32 ``[F, 3]`` holds the welded corners of every face (``weld_map[faces]``).
    """

    weld: WeldResult
    welded: np.ndarray
    edges: EdgeTable
    adjacency: sparse.csr_array
    geometry: FaceGeometry
    components: ComponentSet

    @property
    def face_count(self) -> int:
        return int(self.welded.shape[0])

    @property
    def welded_positions(self) -> np.ndarray:
        return self.weld.welded_positions

    def component_vertices(self, component: int) -> np.ndarray:
        """Ascending welded vertex ids used by the faces of ``component``."""
        return np.unique(self.welded[self.components.faces_of(component)])

    def component_centroid(self, component: int) -> tuple[float, float, float]:
        """Area-weighted centroid of a component (plain mean of face centroids when its area is zero)."""
        faces = self.components.faces_of(component)
        weights = self.geometry.areas[faces]
        centroids = self.geometry.centroids[faces].astype(np.float64)
        total = float(weights.sum())
        point = (weights[:, None] * centroids).sum(axis=0) / total if total > 0.0 else centroids.mean(axis=0)
        return float(point[0]), float(point[1]), float(point[2])


def build_topology(positions: np.ndarray, faces: np.ndarray) -> MeshTopology:
    """Weld, welded edges, face adjacency, face geometry and components of one base primitive."""
    weld = weld_vertices(positions)
    welded = welded_faces(faces, weld.weld_map)
    edges = build_edge_table(welded, weld.welded_count)
    face_count = int(welded.shape[0])
    adjacency = face_adjacency(edges, face_count)
    geometry = face_geometry(positions, faces)
    components = connected_components(adjacency, geometry.areas)
    return MeshTopology(
        weld=weld,
        welded=welded,
        edges=edges,
        adjacency=adjacency,
        geometry=geometry,
        components=components,
    )
