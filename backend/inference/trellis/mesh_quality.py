"""Remove numerical export artifacts without welding UVs or sealing valid openings.

TRELLIS exports duplicate positions at UV seams. Connectivity is therefore
measured on exact positions, while all edits retain the original UV vertices.
Only zero-area faces, tiny numerical lines and collapsed opposite-triangle
membranes are removed. Small compact accessories and ordinary thin/open surfaces survive.
"""
from __future__ import annotations

from typing import Any

import numpy as np
from scipy import sparse
from scipy.sparse import csgraph


def _collapsed_membranes(
    faces: np.ndarray, areas: np.ndarray, whole_diagonal_squared: float
) -> tuple[np.ndarray, int]:
    """Find tiny closed zero-volume pairs, without opening neighboring surfaces."""
    canonical = np.sort(faces, axis=1)
    _, groups, counts = np.unique(canonical, axis=0, return_inverse=True, return_counts=True)
    removed = np.zeros(len(faces), dtype=bool)
    pairs = np.flatnonzero(counts == 2)
    if not len(pairs):
        return removed, 0
    order = np.argsort(groups, kind="stable")
    offsets = np.cumsum(counts) - counts
    first, second = order[offsets[pairs]], order[offsets[pairs] + 1]
    parity = (faces[:, 0] > faces[:, 1]) ^ (faces[:, 0] > faces[:, 2]) ^ (faces[:, 1] > faces[:, 2])
    candidates = (parity[first] != parity[second]) & (
        areas[first] + areas[second] < 1e-5 * whole_diagonal_squared
    )
    removed[first[candidates]] = True
    removed[second[candidates]] = True
    if not np.any(removed):
        return removed, 0

    # A collapsed pair may touch another surface. Removing two edge uses is safe
    # only when it cannot turn an existing shared edge into an open boundary.
    edge_a = faces.reshape(-1)
    edge_b = np.roll(faces, -1, axis=1).reshape(-1)
    vertex_count = int(faces.max()) + 1
    keys = np.minimum(edge_a, edge_b) * vertex_count + np.maximum(edge_a, edge_b)
    _, edge_ids, edge_counts = np.unique(keys, return_inverse=True, return_counts=True)
    removed_uses = np.bincount(edge_ids[np.repeat(removed, 3)], minlength=len(edge_counts))
    unsafe_edges = (edge_counts > 1) & (edge_counts - removed_uses == 1)
    unsafe_faces = np.any(unsafe_edges[edge_ids.reshape(-1, 3)], axis=1)
    unsafe_groups = np.unique(groups[removed & unsafe_faces])
    if len(unsafe_groups):
        removed[np.isin(groups, unsafe_groups)] = False
    return removed, int(np.count_nonzero(removed) // 2)


def clean_export_mesh(mesh: Any, resolution: int) -> dict[str, int]:
    """Clean a textured trimesh in place; return counts of removed artifacts."""
    if resolution <= 0:
        raise ValueError("resolution must be positive")
    vertices = np.asarray(mesh.vertices)
    faces = np.asarray(mesh.faces)
    if vertices.ndim != 2 or vertices.shape[1] != 3 or not len(vertices):
        raise ValueError("export mesh has no usable vertices")
    if faces.ndim != 2 or faces.shape[1] != 3 or not len(faces):
        raise ValueError("export mesh has no usable triangular faces")
    if not np.isfinite(vertices).all():
        raise ValueError("export mesh contains non-finite vertices")
    if faces.dtype.kind not in "iu" or faces.min() < 0 or faces.max() >= len(vertices):
        raise ValueError("export mesh contains invalid face indices")

    corners = vertices[faces]
    area = 0.5 * np.linalg.norm(
        np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]), axis=1
    )
    # Do not use a model-wide small-area threshold: fine eye/hair faces can be valid.
    usable = np.isfinite(area) & (area > 0)
    removed_degenerate = int(np.count_nonzero(~usable))
    if not np.any(usable):
        raise ValueError("export mesh contains only degenerate faces")

    valid_faces = faces[usable]
    used = np.unique(valid_faces)
    points, inverse = np.unique(vertices[used], axis=0, return_inverse=True)
    original_to_welded = np.full(len(vertices), -1, dtype=np.int64)
    original_to_welded[used] = inverse
    welded_faces = original_to_welded[valid_faces]
    rows = welded_faces.reshape(-1)
    columns = np.roll(welded_faces, -1, axis=1).reshape(-1)
    graph = sparse.coo_array(
        (np.ones(len(rows), dtype=bool), (rows, columns)),
        shape=(len(points), len(points)),
    ).tocsr()
    count, vertex_labels = csgraph.connected_components(graph, directed=False)
    face_labels = vertex_labels[welded_faces[:, 0]]
    component_areas = np.bincount(face_labels, weights=area[usable], minlength=count)

    lower = np.full((count, 3), np.inf)
    upper = np.full((count, 3), -np.inf)
    np.minimum.at(lower, vertex_labels, points)
    np.maximum.at(upper, vertex_labels, points)
    diagonal_squared = np.sum((upper - lower) ** 2, axis=1)
    whole_diagonal_squared = float(np.sum((points.max(axis=0) - points.min(axis=0)) ** 2))
    # These bounds target numerical lines, not a general "remove small parts" policy.
    artifact = (
        (component_areas < 1e-5 * whole_diagonal_squared)
        & (diagonal_squared > (4.0 / resolution) ** 2)
        & (component_areas < 1e-5 * diagonal_squared)
    )
    artifact_faces = artifact[face_labels]
    removed_components = int(np.count_nonzero(artifact))
    membranes, _ = _collapsed_membranes(welded_faces, area[usable], whole_diagonal_squared)
    removed_components += int(np.count_nonzero(membranes & ~artifact_faces) // 2)
    artifact_faces = artifact_faces | membranes
    removed_artifact_faces = int(np.count_nonzero(artifact_faces))
    keep = usable.copy()
    keep[np.flatnonzero(usable)[artifact_faces]] = False
    if not np.any(keep):
        raise ValueError("export cleanup would leave an empty mesh")
    if removed_degenerate or removed_artifact_faces:
        # Authored CuMesh normals span UV chart seams. Trimesh invalidates them
        # when faces/vertices change; recomputing on split UV vertices facets
        # the whole model, even though only a tiny artifact was removed.
        vertex_normals = np.asarray(mesh.vertex_normals).copy()
        face_normals = np.asarray(mesh.face_normals).copy()
        used_vertices = np.unique(faces[keep])
        mesh.update_faces(keep)
        mesh.remove_unreferenced_vertices()
        mesh.face_normals = face_normals[keep]
        mesh.vertex_normals = vertex_normals[used_vertices]
    return {
        "removed_degenerate_faces": removed_degenerate,
        "removed_artifact_components": removed_components,
        "removed_artifact_faces": removed_artifact_faces,
    }


def fill_small_triangle_holes(mesh: Any, resolution: int) -> dict[str, int]:
    """Repair tiny triangular gaps and exact zero-width T-junction seams.

    Real caps span at most three voxels and sample a single adjacent UV chart.
    Exact collinear seams up to four voxels are stitched by splitting the long
    edge's face, preserving the surface and interpolating that chart's UVs.
    Existing positions, authored normals, material and texture pixels are kept.
    """
    if resolution <= 0:
        raise ValueError("resolution must be positive")
    empty = {"filled_small_holes": 0, "stitched_t_junctions": 0}
    uv = getattr(mesh.visual, "uv", None)
    if uv is None or len(uv) != len(mesh.vertices) or not len(mesh.faces):
        return empty
    vertices = np.asarray(mesh.vertices)
    faces = np.asarray(mesh.faces)
    used = np.unique(faces)
    points, inverse = np.unique(vertices[used], axis=0, return_inverse=True)
    index = np.full(len(vertices), -1, dtype=np.int64)
    index[used] = inverse
    welded = index[faces]
    edge_a = welded.reshape(-1)
    edge_b = np.roll(welded, -1, axis=1).reshape(-1)
    keys = np.minimum(edge_a, edge_b) * len(points) + np.maximum(edge_a, edge_b)
    _, edge_ids, edge_counts = np.unique(keys, return_inverse=True, return_counts=True)
    boundary = np.flatnonzero(edge_counts[edge_ids] == 1)
    if not len(boundary):
        return empty
    outgoing_count = np.bincount(edge_a[boundary], minlength=len(points))
    incoming_count = np.bincount(edge_b[boundary], minlength=len(points))
    outgoing = {int(edge_a[edge]): int(edge) for edge in boundary}
    visited: set[int] = set()
    split_faces: set[int] = set()
    plans = []
    for edge in boundary:
        start, second = int(edge_a[edge]), int(edge_b[edge])
        if start in visited or outgoing_count[start] != 1 or incoming_count[start] != 1:
            continue
        if outgoing_count[second] != 1 or incoming_count[second] != 1:
            continue
        next_edge = outgoing[second]
        third = int(edge_b[next_edge])
        if third in (start, second) or outgoing_count[third] != 1 or incoming_count[third] != 1:
            continue
        last_edge = outgoing[third]
        if int(edge_b[last_edge]) != start:
            continue
        visited.update((start, second, third))
        loop_edges = np.asarray([edge, next_edge, last_edge])
        lengths = np.linalg.norm(points[edge_a[loop_edges]] - points[edge_b[loop_edges]], axis=1)
        longest = float(lengths.max())
        corners = points[[start, third, second]].copy()  # reverse directed boundary
        normal = np.cross(corners[1] - corners[0], corners[2] - corners[0])
        magnitude = float(np.linalg.norm(normal))
        if magnitude == 0:
            if longest > 4.0 / resolution:
                continue
            long_edge = int(loop_edges[int(lengths.argmax())])
            source_face, corner = divmod(long_edge, 3)
            if source_face in split_faces:
                continue
            a, b = int(edge_a[long_edge]), int(edge_b[long_edge])
            middle = next(point for point in (start, second, third) if point not in (a, b))
            direction = points[b] - points[a]
            denominator = float(np.dot(direction, direction))
            if not denominator:
                continue
            fraction = float(np.dot(points[middle] - points[a], direction) / denominator)
            if not 0 < fraction < 1:
                continue
            split_faces.add(source_face)
            plans.append(("split", points[middle].copy(), source_face, corner, fraction))
        elif longest <= 3.0 / resolution:
            plans.append(("cap", corners, int(edge // 3), normal / magnitude))
    if not plans:
        return empty

    original_uv = np.asarray(uv).copy()
    original_vertex_normals = np.asarray(mesh.vertex_normals).copy()
    original_face_normals = np.asarray(mesh.face_normals).copy()
    face_materials = getattr(mesh.visual, "face_materials", None)
    if face_materials is not None:
        face_materials = np.asarray(face_materials).copy()
    replacement_faces = faces.copy()
    added_vertices, added_uvs, added_normals = [], [], []
    added_faces, added_face_normals, added_materials = [], [], []
    cursor = len(vertices)
    counts = empty.copy()
    for plan in plans:
        kind, positions, source_face = plan[:3]
        if kind == "cap":
            normal = plan[3]
            sample_uv = original_uv[faces[source_face]].mean(axis=0)
            added_vertices.append(positions)
            added_uvs.append(np.tile(sample_uv, (3, 1)))
            added_normals.append(np.tile(normal, (3, 1)))
            added_faces.append([cursor, cursor + 1, cursor + 2])
            added_face_normals.append(normal)
            cursor += 3
            counts["filled_small_holes"] += 1
        else:
            corner, fraction = plan[3:]
            a, b, c = (int(faces[source_face, (corner + offset) % 3]) for offset in range(3))
            normal = (1 - fraction) * original_vertex_normals[a] + fraction * original_vertex_normals[b]
            norm = float(np.linalg.norm(normal))
            normal = normal / norm if norm else original_face_normals[source_face]
            added_vertices.append(positions[None, :])
            added_uvs.append(((1 - fraction) * original_uv[a] + fraction * original_uv[b])[None, :])
            added_normals.append(normal[None, :])
            replacement_faces[source_face] = [a, cursor, c]
            added_faces.append([cursor, b, c])
            added_face_normals.append(original_face_normals[source_face])
            cursor += 1
            counts["stitched_t_junctions"] += 1
        if face_materials is not None:
            added_materials.append(face_materials[source_face])
    mesh.vertices = np.concatenate((vertices, *added_vertices))
    mesh.faces = np.concatenate((replacement_faces, np.asarray(added_faces)))
    mesh.visual.uv = np.concatenate((original_uv, *added_uvs))
    if face_materials is not None:
        mesh.visual.face_materials = np.concatenate((face_materials, np.asarray(added_materials)))
    mesh.face_normals = np.concatenate((original_face_normals, np.asarray(added_face_normals)))
    mesh.vertex_normals = np.concatenate((original_vertex_normals, *added_normals))
    return counts
