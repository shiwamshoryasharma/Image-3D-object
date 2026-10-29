"""UV-safe three-edge micro-hole capping contract (CPU only, no model weights)."""
from __future__ import annotations

import numpy as np
import pytest
import trimesh
from trimesh.visual.material import PBRMaterial
from trimesh.visual.texture import TextureVisuals

from inference.trellis.mesh_quality import fill_small_triangle_holes


def _tetra(scale=0.001):
    points = np.array([[0, 0, 0], [scale, 0, 0],
                       [0, scale, 0], [0, 0, scale]], dtype=np.float64)
    # Consistent outward orientation. Last triangle is the missing +XYZ face.
    return points, np.array([[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]])


def _textured_mesh(points, faces):
    positions = np.asarray(points)[faces].reshape(-1, 3)
    split_faces = np.arange(len(positions)).reshape(-1, 3)
    chart = np.array([[0.05, 0.05], [0.15, 0.05], [0.05, 0.15]])
    uv = np.concatenate([chart + index * 0.2 for index in range(len(faces))])
    corners = positions[split_faces]
    normals = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    normals /= np.linalg.norm(normals, axis=1)[:, None]
    # Authored vertex normals differ from automatic geometric normals; capping
    # must preserve them, as it preserves the original exported mesh's normals.
    custom = normals + np.array([0.08, 0.03, 0.02])
    custom /= np.linalg.norm(custom, axis=1)[:, None]
    material = PBRMaterial(name="cap_test_material", baseColorFactor=[45, 120, 200, 255],
                           roughnessFactor=0.7, metallicFactor=0.2)
    return trimesh.Trimesh(vertices=positions, faces=split_faces,
                           vertex_normals=np.repeat(custom, 3, axis=0),
                           visual=TextureVisuals(uv=uv, material=material), process=False)


def _welded_edges(mesh):
    _, inverse = np.unique(mesh.vertices, axis=0, return_inverse=True)
    faces = inverse[mesh.faces]
    starts, ends = faces.reshape(-1), np.roll(faces, -1, axis=1).reshape(-1)
    edges = np.sort(np.stack((starts, ends), axis=1), axis=1)
    unique, counts = np.unique(edges, axis=0, return_counts=True)
    # Sum directed edge orientation: zero for coherently wound closed pairs.
    keys = edges[:, 0] * len(mesh.vertices) + edges[:, 1]
    _, ids = np.unique(keys, return_inverse=True)
    directions = np.where(starts < ends, 1, -1)
    orientation = np.bincount(ids, weights=directions)
    return unique, counts, orientation


def test_small_missing_triangle_is_capped_with_distinct_uv_vertices_and_coherent_winding():
    points, faces = _tetra()
    mesh = _textured_mesh(points, faces[:-1])
    before_vertices, before_faces = mesh.vertices.copy(), mesh.faces.copy()
    before_uv = mesh.visual.uv.copy()
    before_normals = mesh.vertex_normals.copy()
    before_face_normals = mesh.face_normals.copy()
    stats = fill_small_triangle_holes(mesh, resolution=1024)
    assert stats == {"filled_small_holes": 1, "stitched_t_junctions": 0}
    assert len(mesh.faces) == len(before_faces) + 1
    assert len(mesh.vertices) == len(before_vertices) + 3
    np.testing.assert_array_equal(mesh.vertices[:len(before_vertices)], before_vertices)
    np.testing.assert_array_equal(mesh.faces[:len(before_faces)], before_faces)
    np.testing.assert_array_equal(mesh.visual.uv[:len(before_uv)], before_uv)
    np.testing.assert_array_equal(mesh.vertex_normals[:len(before_normals)], before_normals)
    np.testing.assert_array_equal(mesh.face_normals[:len(before_face_normals)], before_face_normals)
    assert len(np.unique(mesh.faces[-1])) == 3
    assert np.all(mesh.faces[-1] >= len(before_vertices))
    # All three UV corners use one local representative sample, never an
    # interpolation spanning unrelated pre-existing atlas charts.
    np.testing.assert_array_equal(mesh.visual.uv[-3:], np.repeat(mesh.visual.uv[-1:], 3, axis=0))
    sample = mesh.visual.uv[-1]
    acceptable_samples = np.concatenate((before_uv, before_uv.reshape(-1, 3, 2).mean(axis=1)))
    assert any(np.allclose(sample, candidate, rtol=0, atol=1e-12) for candidate in acceptable_samples)
    expected = np.array([1, 1, 1]) / np.sqrt(3)
    assert np.dot(mesh.face_normals[-1], expected) > 0.999
    _, counts, directions = _welded_edges(mesh)
    assert np.all(counts == 2)
    assert np.all(directions == 0)
    material = mesh.visual.material
    assert material.name == "cap_test_material"
    np.testing.assert_array_equal(material.baseColorFactor, [45, 120, 200, 255])
    assert material.roughnessFactor == pytest.approx(0.7)
    assert material.metallicFactor == pytest.approx(0.2)


def test_larger_triangular_opening_is_preserved():
    points, faces = _tetra(scale=0.01)
    mesh = _textured_mesh(points, faces[:-1])
    original = mesh.vertices.copy(), mesh.faces.copy(), mesh.visual.uv.copy()
    assert fill_small_triangle_holes(mesh, resolution=1024) == {"filled_small_holes": 0, "stitched_t_junctions": 0}
    np.testing.assert_array_equal(mesh.vertices, original[0])
    np.testing.assert_array_equal(mesh.faces, original[1])
    np.testing.assert_array_equal(mesh.visual.uv, original[2])
    assert np.count_nonzero(_welded_edges(mesh)[1] == 1) == 3


def test_exact_position_uv_seams_are_not_holes():
    points, faces = _tetra()
    mesh = _textured_mesh(points, faces)
    before_vertices, before_faces, before_uv = mesh.vertices.copy(), mesh.faces.copy(), mesh.visual.uv.copy()
    assert fill_small_triangle_holes(mesh, resolution=1024) == {"filled_small_holes": 0, "stitched_t_junctions": 0}
    np.testing.assert_array_equal(mesh.vertices, before_vertices)
    np.testing.assert_array_equal(mesh.faces, before_faces)
    np.testing.assert_array_equal(mesh.visual.uv, before_uv)


def test_branched_boundary_component_is_not_capped():
    points, faces = _tetra()
    # Missing base triangles meet at one geometric vertex, so the boundary
    # graph is branched (degree4), not an isolated triangular loop.
    both_points = np.concatenate((points, -points))
    both_faces = np.concatenate((faces[1:], faces[1:] + len(points)))
    mesh = _textured_mesh(both_points, both_faces)
    count = len(mesh.faces)
    assert fill_small_triangle_holes(mesh, resolution=1024) == {"filled_small_holes": 0, "stitched_t_junctions": 0}
    assert len(mesh.faces) == count
    assert np.count_nonzero(_welded_edges(mesh)[1] == 1) == 6


def test_small_four_edge_opening_is_not_a_triangle_hole():
    box = trimesh.creation.box(extents=(0.001, 0.001, 0.001))
    keep = box.face_normals[:, 2] < 0.99
    mesh = _textured_mesh(box.vertices, box.faces[keep])
    count = len(mesh.faces)
    assert fill_small_triangle_holes(mesh, resolution=1024) == {"filled_small_holes": 0, "stitched_t_junctions": 0}
    assert len(mesh.faces) == count
    assert np.count_nonzero(_welded_edges(mesh)[1] == 1) == 4


def test_inconsistently_oriented_boundary_loop_is_not_capped():
    points, faces = _tetra()
    faces = faces[:-1].copy()
    faces[0] = faces[0, ::-1]
    mesh = _textured_mesh(points, faces)
    count = len(mesh.faces)
    assert fill_small_triangle_holes(mesh, resolution=1024) == {"filled_small_holes": 0, "stitched_t_junctions": 0}
    assert len(mesh.faces) == count


def test_nontextured_mesh_is_unsupported_and_unchanged():
    points, faces = _tetra()
    mesh = trimesh.Trimesh(vertices=points, faces=faces[:-1], process=False)
    original = mesh.vertices.copy(), mesh.faces.copy()
    assert fill_small_triangle_holes(mesh, resolution=1024) == {"filled_small_holes": 0, "stitched_t_junctions": 0}
    np.testing.assert_array_equal(mesh.vertices, original[0])
    np.testing.assert_array_equal(mesh.faces, original[1])


def test_invalid_hole_repair_resolution_raises_value_error():
    points, faces = _tetra()
    with pytest.raises(ValueError):
        fill_small_triangle_holes(_textured_mesh(points, faces[:-1]), resolution=0)



def _t_junction_box(long_edge_voxels=2.0, middle_offset=0.0):
    side = long_edge_voxels / (1024 * np.sqrt(2))
    box = trimesh.creation.box(extents=(side, side, side))
    points = np.asarray(box.vertices).copy()
    faces = np.asarray(box.faces).copy()
    top = np.flatnonzero(box.face_normals[:, 2] > 0.99)
    first, long_face = int(top[0]), int(top[1])
    common = set(faces[first]) & set(faces[long_face])
    tri = faces[first]
    for index in range(3):
        if {int(tri[index]), int(tri[(index + 1) % 3])} == common:
            a, b, c = tri[index], tri[(index + 1) % 3], tri[(index + 2) % 3]
            break
    else:
        raise AssertionError("box top triangles have no shared edge")
    endpoint_a, endpoint_b = points[a].copy(), points[b].copy()
    middle = (endpoint_a + endpoint_b) * 0.5
    middle[2] += middle_offset
    midpoint_index = len(points)
    points = np.concatenate((points, middle[None]))
    faces[first] = [a, midpoint_index, c]
    faces = np.concatenate((faces, [[midpoint_index, b, c]]))
    mesh = _textured_mesh(points, faces)
    long_indices = mesh.faces[long_face]
    original_a = next(int(vertex) for vertex in long_indices
                      if np.array_equal(mesh.vertices[vertex], endpoint_a))
    original_b = next(int(vertex) for vertex in long_indices
                      if np.array_equal(mesh.vertices[vertex], endpoint_b))
    normals = mesh.vertex_normals.copy()
    normals[original_a] = [0, 0, 1]
    normals[original_b] = [0, 0.6, 0.8]
    mesh.vertex_normals = normals
    return mesh, long_face, original_a, original_b, middle


@pytest.mark.parametrize("long_edge_voxels", (2.0, 3.5))
def test_exact_small_collinear_t_junction_is_stitched_without_moving_geometry(long_edge_voxels):
    mesh, long_face, a, b, middle = _t_junction_box(long_edge_voxels)
    original_vertices, original_faces = mesh.vertices.copy(), mesh.faces.copy()
    original_uv, original_normals = mesh.visual.uv.copy(), mesh.vertex_normals.copy()
    original_face_normals = mesh.face_normals.copy()
    original_area = mesh.area
    assert np.count_nonzero(_welded_edges(mesh)[1] == 1) == 3
    assert np.array_equal(np.cross(original_vertices[b] - original_vertices[a],
                                   middle - original_vertices[a]), np.zeros(3))
    stats = fill_small_triangle_holes(mesh, resolution=1024)
    # Split the face adjacent to the long edge, rather than inserting a
    # zero-area cap. The second case is above the3-voxel cap limit but below
    # the4-voxel exact-collinear stitching limit.
    assert len(mesh.faces) == len(original_faces) + 1
    assert len(mesh.vertices) == len(original_vertices) + 1
    assert stats == {"filled_small_holes": 0, "stitched_t_junctions": 1}
    np.testing.assert_array_equal(mesh.vertices[:len(original_vertices)], original_vertices)
    np.testing.assert_array_equal(mesh.visual.uv[:len(original_uv)], original_uv)
    np.testing.assert_array_equal(mesh.vertex_normals[:len(original_normals)], original_normals)
    np.testing.assert_array_equal(mesh.vertices[-1], middle)
    expected_uv = (original_uv[a] + original_uv[b]) * 0.5
    np.testing.assert_allclose(mesh.visual.uv[-1], expected_uv, rtol=0, atol=1e-15)
    blend = (original_normals[a] + original_normals[b]) * 0.5
    # A linear normal blend may be normalized for shading; its direction
    # must still be the exact endpoint interpolation in the selected UV chart.
    np.testing.assert_allclose(mesh.vertex_normals[-1] / np.linalg.norm(mesh.vertex_normals[-1]),
                               blend / np.linalg.norm(blend), rtol=0, atol=1e-15)
    new_vertex = len(original_vertices)
    assert np.count_nonzero(mesh.faces == new_vertex) == 2
    remaining = {tuple(face) for face in mesh.faces}
    for index, face in enumerate(original_faces):
        if index != long_face:
            assert tuple(face) in remaining
    _, counts, directions = _welded_edges(mesh)
    assert np.all(counts == 2)
    assert np.all(directions == 0)
    corners = mesh.vertices[mesh.faces]
    assert np.all(np.linalg.norm(np.cross(corners[:, 1] - corners[:, 0],
                                          corners[:, 2] - corners[:, 0]), axis=1) > 0)
    assert mesh.area == pytest.approx(original_area, rel=1e-12, abs=0)
    # Geometry of all untouched triangles and their cached normals stays exact.
    for index, face in enumerate(original_faces):
        if index == long_face:
            continue
        where = next(i for i, item in enumerate(mesh.faces) if np.array_equal(item, face))
        np.testing.assert_array_equal(mesh.face_normals[where], original_face_normals[index])
    assert mesh.visual.material.name == "cap_test_material"
    np.testing.assert_array_equal(mesh.visual.material.baseColorFactor, [45, 120, 200, 255])


def test_large_exact_collinear_t_junction_is_preserved():
    mesh, _, _, _, _ = _t_junction_box(long_edge_voxels=8.0)
    before = mesh.vertices.copy(), mesh.faces.copy(), mesh.visual.uv.copy(), mesh.vertex_normals.copy()
    assert fill_small_triangle_holes(mesh, resolution=1024) == {
        "filled_small_holes": 0, "stitched_t_junctions": 0}
    np.testing.assert_array_equal(mesh.vertices, before[0])
    np.testing.assert_array_equal(mesh.faces, before[1])
    np.testing.assert_array_equal(mesh.visual.uv, before[2])
    np.testing.assert_array_equal(mesh.vertex_normals, before[3])


def test_nearly_collinear_loop_is_not_stitched_by_a_tolerance():
    mesh, _, a, b, middle = _t_junction_box(long_edge_voxels=3.5, middle_offset=1e-12)
    assert np.linalg.norm(np.cross(mesh.vertices[b] - mesh.vertices[a],
                                   middle - mesh.vertices[a])) > 0
    before = mesh.vertices.copy(), mesh.faces.copy(), mesh.visual.uv.copy()
    assert fill_small_triangle_holes(mesh, resolution=1024) == {
        "filled_small_holes": 0, "stitched_t_junctions": 0}
    np.testing.assert_array_equal(mesh.vertices, before[0])
    np.testing.assert_array_equal(mesh.faces, before[1])
    np.testing.assert_array_equal(mesh.visual.uv, before[2])


def test_noncollinear_cap_above_three_voxels_remains_open():
    points, faces = _tetra(scale=3.5 / (1024 * np.sqrt(2)))
    mesh = _textured_mesh(points, faces[:-1])
    count = len(mesh.faces)
    assert fill_small_triangle_holes(mesh, resolution=1024) == {
        "filled_small_holes": 0, "stitched_t_junctions": 0}
    assert len(mesh.faces) == count
    assert np.count_nonzero(_welded_edges(mesh)[1] == 1) == 3
