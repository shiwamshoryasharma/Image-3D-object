"""Bounded UV-safe polygon-hole repair regression, including concave loops."""
from __future__ import annotations

import numpy as np
import pytest
import trimesh
from trimesh.visual.material import PBRMaterial
from trimesh.visual.texture import TextureVisuals

from inference.trellis import mesh_quality


def _repair(mesh):
    # Before the new helper exists, run the actual current triangle-only
    # implementation so RED demonstrates the missing repair, not just an import.
    helper = getattr(mesh_quality, "repair_small_boundary_loops",
                     mesh_quality.fill_small_triangle_holes)
    return helper(mesh, resolution=1024)


def _textured(points, faces):
    positions = np.asarray(points)[faces].reshape(-1, 3)
    split_faces = np.arange(len(positions)).reshape(-1, 3)
    corners = positions[split_faces]
    normals = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    normals /= np.linalg.norm(normals, axis=1)[:, None]
    authored = normals + [0.06, 0.02, 0.03]
    authored /= np.linalg.norm(authored, axis=1)[:, None]
    uv = np.tile([[0.02, 0.02], [0.12, 0.02], [0.02, 0.12]], (len(faces), 1))
    uv += np.repeat(np.arange(len(faces)) * 0.15, 3)[:, None]
    material = PBRMaterial(name="polygon_material", baseColorFactor=[90, 130, 190, 255],
                           roughnessFactor=0.55, metallicFactor=0.15)
    return trimesh.Trimesh(vertices=positions, faces=split_faces,
                           vertex_normals=np.repeat(authored, 3, axis=0),
                           visual=TextureVisuals(uv=uv, material=material), process=False)


def _prism_arrays(ring, *, bottom_triangles=None, height=0.006, top_offsets=None):
    ring = np.asarray(ring, dtype=np.float64)
    n = len(ring)
    lower = np.column_stack((ring, np.full(n, -height / 2)))
    top_z = np.full(n, height / 2)
    if top_offsets is not None:
        top_z += top_offsets
    upper = np.column_stack((ring, top_z))
    points = np.concatenate((lower, upper))
    faces = []
    for index in range(n):
        other = (index + 1) % n
        faces.extend(([index, other, other + n], [index, other + n, index + n]))
    bottom = (np.array([[0, index, index + 1] for index in range(1, n - 1)])
              if bottom_triangles is None else np.asarray(bottom_triangles))
    faces.extend(bottom[:, ::-1].tolist())
    return points, np.asarray(faces, dtype=np.int64)


def _circle_ring(count=8, span_voxels=5):
    angles = np.arange(count) * 2 * np.pi / count
    radius = span_voxels / (1024 * 2 * np.sqrt(2))
    return np.column_stack((np.cos(angles), np.sin(angles))) * radius


def _edge_counts(mesh):
    _, inverse = np.unique(mesh.vertices, axis=0, return_inverse=True)
    welded = inverse[mesh.faces]
    starts = welded.reshape(-1)
    ends = np.roll(welded, -1, axis=1).reshape(-1)
    edges = np.sort(np.stack((starts, ends), axis=1), axis=1)
    _, ids, counts = np.unique(edges, axis=0, return_inverse=True, return_counts=True)
    signs = np.where(starts < ends, 1, -1)
    return counts, np.bincount(ids, weights=signs)


def _area2d(ring):
    ring = np.asarray(ring)
    return abs(float(np.sum(ring[:, 0] * np.roll(ring[:, 1], -1)
                            - ring[:, 1] * np.roll(ring[:, 0], -1)) / 2))


def _assert_repair_preserved_source(mesh, original, face_count, expected_cap_area):
    old_vertices, old_faces, old_uv, old_vertex_normals, old_face_normals, old_area = original
    assert len(mesh.faces) == len(old_faces) + face_count
    np.testing.assert_array_equal(mesh.vertices[:len(old_vertices)], old_vertices)
    np.testing.assert_array_equal(mesh.faces[:len(old_faces)], old_faces)
    np.testing.assert_array_equal(mesh.visual.uv[:len(old_uv)], old_uv)
    np.testing.assert_array_equal(mesh.vertex_normals[:len(old_vertex_normals)], old_vertex_normals)
    np.testing.assert_array_equal(mesh.face_normals[:len(old_face_normals)], old_face_normals)
    caps = mesh.faces[len(old_faces):]
    assert np.all(caps >= len(old_vertices)), "caps need their own UV-chart vertices"
    cap_uv = mesh.visual.uv[caps]
    for triangle_uv in cap_uv:
        np.testing.assert_array_equal(triangle_uv, np.repeat(triangle_uv[:1], 3, axis=0))
    counts, directions = _edge_counts(mesh)
    assert np.all(counts == 2)
    assert np.all(directions == 0)
    assert mesh.area == pytest.approx(old_area + expected_cap_area, rel=1e-10, abs=1e-18)
    corners = mesh.vertices[mesh.faces]
    area = np.linalg.norm(np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]), axis=1)
    assert np.all(area > 0), "repair must not add collapsed triangles"
    assert np.all(mesh.face_normals[len(old_faces):, 2] > 0.999), "cap winding should point out of the prism"
    material = mesh.visual.material
    assert material.name == "polygon_material"
    np.testing.assert_array_equal(material.baseColorFactor, [90, 130, 190, 255])
    assert material.roughnessFactor == pytest.approx(0.55)
    assert material.metallicFactor == pytest.approx(0.15)


def _snapshot(mesh):
    return (mesh.vertices.copy(), mesh.faces.copy(), mesh.visual.uv.copy(),
            mesh.vertex_normals.copy(), mesh.face_normals.copy(), mesh.area)


@pytest.mark.parametrize("sides", (6, 8, 12))
def test_small_planar_polygon_loop_is_repaired_with_uv_and_authored_normal_preservation(sides):
    ring = _circle_ring(sides)
    mesh = _textured(*_prism_arrays(ring))
    assert np.count_nonzero(_edge_counts(mesh)[0] == 1) == sides
    original = _snapshot(mesh)
    _repair(mesh)
    _assert_repair_preserved_source(mesh, original, sides - 2, _area2d(ring))


def test_concave_u_shaped_loop_is_triangulated_without_covering_its_notch():
    units = np.array([[-2, -2], [2, -2], [2, 2], [1, 2],
                      [1, -1], [-1, -1], [-1, 2], [-2, 2]], dtype=np.float64)
    scale = 5 / (1024 * 4 * np.sqrt(2))
    ring = units * scale
    bottom = [[0, 1, 4], [0, 4, 5], [0, 5, 7],
              [5, 6, 7], [1, 2, 3], [1, 3, 4]]
    mesh = _textured(*_prism_arrays(ring, bottom_triangles=bottom))
    original = _snapshot(mesh)
    _repair(mesh)
    _assert_repair_preserved_source(mesh, original, len(ring) - 2, _area2d(ring))
    centers = mesh.vertices[mesh.faces[len(original[1]):]].mean(axis=1)[:, :2] / scale
    assert not np.any((np.abs(centers[:, 0]) < 1 - 1e-12) & (centers[:, 1] > -1 + 1e-12)), (
        "triangles must not bridge the U-shaped intentional concavity")
    # The cap covers area10*scale^2, not the convex hull's16*scale^2.
    assert _area2d(ring) == pytest.approx(10 * scale * scale)


def test_large_intentional_polygon_opening_is_unchanged():
    mesh = _textured(*_prism_arrays(_circle_ring(8, span_voxels=12)))
    original = _snapshot(mesh)
    _repair(mesh)
    np.testing.assert_array_equal(mesh.vertices, original[0])
    np.testing.assert_array_equal(mesh.faces, original[1])
    np.testing.assert_array_equal(mesh.visual.uv, original[2])
    np.testing.assert_array_equal(mesh.vertex_normals, original[3])
    assert np.count_nonzero(_edge_counts(mesh)[0] == 1) == 8


def test_branched_boundary_openings_are_not_automatically_closed():
    side = 0.002
    one = np.array([[-side, -side], [0, -side], [0, 0], [-side, 0]])
    two = np.array([[0, 0], [side, 0], [side, side], [0, side]])
    points_a, faces_a = _prism_arrays(one)
    points_b, faces_b = _prism_arrays(two)
    mesh = _textured(np.concatenate((points_a, points_b)),
                     np.concatenate((faces_a, faces_b + len(points_a))))
    original = _snapshot(mesh)
    _repair(mesh)
    np.testing.assert_array_equal(mesh.vertices, original[0])
    np.testing.assert_array_equal(mesh.faces, original[1])
    np.testing.assert_array_equal(mesh.visual.uv, original[2])
    assert np.count_nonzero(_edge_counts(mesh)[0] == 1) == 8


def test_nonplanar_ambiguous_small_loop_is_not_closed():
    ring = _circle_ring(8)
    # Under the8-voxel size limit, but one-voxel height alternation makes this
    # an ambiguous spatial loop rather than a planar missing surface.
    offsets = np.array([0, 1, 0, -1, 0, 1, 0, -1]) / 1024
    mesh = _textured(*_prism_arrays(ring, top_offsets=offsets))
    original = _snapshot(mesh)
    _repair(mesh)
    np.testing.assert_array_equal(mesh.vertices, original[0])
    np.testing.assert_array_equal(mesh.faces, original[1])
    np.testing.assert_array_equal(mesh.visual.uv, original[2])
    assert np.count_nonzero(_edge_counts(mesh)[0] == 1) == 8
