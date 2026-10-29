"""Regression contract for numerical export-artifact cleanup (no GPU/weights).

The helper is deliberately narrower than anatomical repair or general hole
closure. It must not destroy valid open surfaces, UV seams or small parts.
"""
from __future__ import annotations

import numpy as np
import pytest
import trimesh
from trimesh.visual.material import PBRMaterial
from trimesh.visual.texture import TextureVisuals

from inference.trellis.mesh_quality import clean_export_mesh


COUNTS = ("removed_degenerate_faces", "removed_artifact_components", "removed_artifact_faces")


def _material():
    return PBRMaterial(name="kept_material", baseColorFactor=[80, 140, 220, 255],
                       roughnessFactor=0.6, metallicFactor=0.1)


def _body():
    body = trimesh.creation.box()
    # Each triangle has independent UV/material vertices. Its geometry is
    # connected only after exact-position welding for ANALYSIS.
    vertices = np.asarray(body.vertices)[body.faces].reshape(-1, 3)
    faces = np.arange(len(vertices)).reshape(-1, 3)
    return vertices, faces


def _mesh_with(*pieces, scale=1.0, translation=(0, 0, 0)):
    pieces = (_body(), *pieces)
    vertices, faces = [], []
    offset = 0
    for points, triangles in pieces:
        vertices.append(np.asarray(points, dtype=np.float64) * scale + translation)
        faces.append(np.asarray(triangles, dtype=np.int64) + offset)
        offset += len(points)
    vertices = np.concatenate(vertices)
    faces = np.concatenate(faces)
    # Deliberately distinct UVs at duplicate positions: merging vertices would
    # corrupt this fixture even when the rendered geometry still looked valid.
    uv = np.arange(len(vertices) * 2, dtype=np.float64).reshape(-1, 2) / (len(vertices) * 2)
    return trimesh.Trimesh(vertices=vertices, faces=faces,
                           visual=TextureVisuals(uv=uv, material=_material()),
                           process=False)


def _strip(length=0.03, width=1e-8, origin=(0.7, 0.1, 0.1)):
    points = np.array([[0, 0, 0], [length, 0, 0],
                       [length, width, 0], [0, width, 0]], dtype=np.float64)
    return points + origin, np.array([[0, 1, 2], [0, 2, 3]])


def _assert_counts(stats, degenerate=0, components=0, artifact_faces=0):
    assert {key: stats[key] for key in COUNTS} == {
        "removed_degenerate_faces": degenerate,
        "removed_artifact_components": components,
        "removed_artifact_faces": artifact_faces,
    }


def _corner_data(mesh, count=None):
    faces = mesh.faces if count is None else mesh.faces[:count]
    return mesh.vertices[faces].copy(), mesh.visual.uv[faces].copy()


def _assert_material_preserved(mesh):
    material = mesh.visual.material
    assert material.name == "kept_material"
    np.testing.assert_array_equal(material.baseColorFactor, [80, 140, 220, 255])
    assert material.roughnessFactor == pytest.approx(0.6)
    assert material.metallicFactor == pytest.approx(0.1)


def test_floating_numerical_needle_removed_without_welding_uv_vertices():
    mesh = _mesh_with(_strip())
    kept_positions, kept_uvs = _corner_data(mesh, 12)
    stats = clean_export_mesh(mesh, resolution=1024)
    _assert_counts(stats, components=1, artifact_faces=2)
    assert len(mesh.faces) == 12
    np.testing.assert_array_equal(mesh.vertices[mesh.faces], kept_positions)
    np.testing.assert_array_equal(mesh.visual.uv[mesh.faces], kept_uvs)
    # The body retains independent chart vertices at identical positions.
    assert len(np.unique(mesh.faces)) == 36
    _assert_material_preserved(mesh)


def test_uv_seam_connected_sliver_is_not_misclassified_as_a_floating_component():
    points, triangles = _body()
    a, b = points[triangles[0, :2]]
    # A positive-area, very thin triangle shares an EXACT geometric edge with
    # the body through duplicated UV vertices. It belongs to the body component.
    direction = b - a
    perpendicular = np.cross(direction, [0, 0, 1])
    if np.linalg.norm(perpendicular) == 0:
        perpendicular = np.cross(direction, [0, 1, 0])
    perpendicular = perpendicular / np.linalg.norm(perpendicular) * 1e-8
    attached = (np.array([a, b, a + direction * 0.8 + perpendicular]), np.array([[0, 1, 2]]))
    mesh = _mesh_with(attached)
    positions, uvs = _corner_data(mesh)
    stats = clean_export_mesh(mesh, resolution=1024)
    _assert_counts(stats)
    np.testing.assert_array_equal(mesh.vertices[mesh.faces], positions)
    np.testing.assert_array_equal(mesh.visual.uv[mesh.faces], uvs)


def test_compact_tiny_accessory_is_retained_even_below_global_area_threshold():
    accessory = trimesh.creation.icosphere(subdivisions=0, radius=1e-5)
    accessory.vertices += [0.7, 0.1, 0.1]
    mesh = _mesh_with((accessory.vertices, accessory.faces), _strip(origin=(0.8, 0.2, 0.2)))
    kept_count = 12 + len(accessory.faces)
    positions, uvs = _corner_data(mesh, kept_count)
    stats = clean_export_mesh(mesh, resolution=1024)
    _assert_counts(stats, components=1, artifact_faces=2)
    assert len(mesh.faces) == kept_count
    np.testing.assert_array_equal(mesh.vertices[mesh.faces], positions)
    np.testing.assert_array_equal(mesh.visual.uv[mesh.faces], uvs)


def test_legitimate_thin_open_sheet_is_preserved():
    # Aspect ratio is high, but component area/diagonal^2 is 2e-5, above
    # the numerical-needle compactness threshold of 1e-5.
    mesh = _mesh_with(_strip(length=0.1, width=2e-6))
    positions, uvs = _corner_data(mesh)
    stats = clean_export_mesh(mesh, resolution=1024)
    _assert_counts(stats)
    np.testing.assert_array_equal(mesh.vertices[mesh.faces], positions)
    np.testing.assert_array_equal(mesh.visual.uv[mesh.faces], uvs)


def test_ordinary_whisker_tube_is_preserved():
    tube = trimesh.creation.cylinder(radius=1e-4, height=0.04, sections=12)
    tube.vertices += [0.7, 0, 0.1]
    mesh = _mesh_with((tube.vertices, tube.faces))
    positions, uvs = _corner_data(mesh)
    stats = clean_export_mesh(mesh, resolution=1024)
    _assert_counts(stats)
    np.testing.assert_array_equal(mesh.vertices[mesh.faces], positions)
    np.testing.assert_array_equal(mesh.visual.uv[mesh.faces], uvs)


@pytest.mark.parametrize("resolution,removed", ((512, False), (4096, True)))
def test_short_numerical_component_is_removed_only_when_longer_than_four_voxels(resolution, removed):
    mesh = _mesh_with(_strip(length=0.002, width=1e-9))
    stats = clean_export_mesh(mesh, resolution=resolution)
    _assert_counts(stats, components=int(removed), artifact_faces=2 * int(removed))
    assert len(mesh.faces) == (12 if removed else 14)


@pytest.mark.parametrize("scale,translation", ((1.0, (0, 0, 0)), (0.01, (5, -3, 2)), (7, (-2, 8, -1))))
def test_noop_preserves_positions_scale_uvs_faces_and_material(scale, translation):
    mesh = _mesh_with(scale=scale, translation=translation)
    vertices, faces, uv = mesh.vertices.copy(), mesh.faces.copy(), mesh.visual.uv.copy()
    stats = clean_export_mesh(mesh, resolution=1024)
    _assert_counts(stats)
    np.testing.assert_array_equal(mesh.vertices, vertices)
    np.testing.assert_array_equal(mesh.faces, faces)
    np.testing.assert_array_equal(mesh.visual.uv, uv)
    _assert_material_preserved(mesh)


def test_true_zero_area_faces_removed_but_positive_area_compact_face_retained():
    zero_area = (np.array([[0.7, 0, 0], [0.71, 0, 0], [0.72, 0, 0]]),
                 np.array([[0, 1, 2], [0, 0, 1]]))
    tiny_triangle = (np.array([[0.8, 0, 0], [0.8000001, 0, 0], [0.8, 1e-7, 0]]),
                     np.array([[0, 1, 2]]))
    mesh = _mesh_with(tiny_triangle, zero_area)
    positions, uvs = _corner_data(mesh, 13)
    stats = clean_export_mesh(mesh, resolution=1024)
    _assert_counts(stats, degenerate=2)
    assert len(mesh.faces) == 13
    np.testing.assert_array_equal(mesh.vertices[mesh.faces], positions)
    np.testing.assert_array_equal(mesh.visual.uv[mesh.faces], uvs)


@pytest.mark.parametrize("case", ("empty", "only_degenerate", "nonfinite", "bad_index"))
def test_invalid_or_all_empty_geometry_raises_value_error(case):
    if case == "empty":
        mesh = trimesh.Trimesh(vertices=np.empty((0, 3)), faces=np.empty((0, 3), dtype=np.int64), process=False)
    elif case == "only_degenerate":
        mesh = trimesh.Trimesh(vertices=[[0, 0, 0], [1, 0, 0], [2, 0, 0]], faces=[[0, 1, 2]], process=False)
    elif case == "nonfinite":
        mesh = trimesh.Trimesh(vertices=[[np.nan, 0, 0], [1, 0, 0], [0, 1, 0]], faces=[[0, 1, 2]], process=False)
    else:
        mesh = trimesh.Trimesh(vertices=[[0, 0, 0], [1, 0, 0], [0, 1, 0]], faces=[[0, 1, 99]], process=False)
    with pytest.raises(ValueError):
        clean_export_mesh(mesh, resolution=1024)


def test_invalid_resolution_raises_value_error():
    with pytest.raises(ValueError):
        clean_export_mesh(_mesh_with(), resolution=0)



def _opposite_membrane(origin=(0.7, 0.1, 0.1)):
    points = np.array([[0, 0, 0], [0.006, 0, 0], [0, 0.0002, 0]], dtype=np.float64)
    return points + origin, np.array([[0, 1, 2], [0, 2, 1]])


def test_tiny_opposite_duplicate_membrane_removed_with_uvs_and_material_preserved():
    mesh = _mesh_with(_opposite_membrane())
    positions, uvs = _corner_data(mesh, 12)
    stats = clean_export_mesh(mesh, resolution=1024)
    _assert_counts(stats, components=1, artifact_faces=2)
    assert len(mesh.faces) == 12
    np.testing.assert_array_equal(mesh.vertices[mesh.faces], positions)
    np.testing.assert_array_equal(mesh.visual.uv[mesh.faces], uvs)
    _assert_material_preserved(mesh)


def test_duplicate_pair_retained_when_removal_would_open_attached_surface():
    points, _ = _opposite_membrane()
    adjacent = (np.array([points[0], points[1], points[0] + [0, 0, 0.02]]),
                np.array([[0, 1, 2]]))
    mesh = _mesh_with(_opposite_membrane(), adjacent)
    positions, uvs = _corner_data(mesh)
    stats = clean_export_mesh(mesh, resolution=1024)
    # The shared pair/adjacent edge currently has three faces. Dropping the
    # pair would make it a NEW one-face boundary; keep the pair instead.
    _assert_counts(stats)
    np.testing.assert_array_equal(mesh.vertices[mesh.faces], positions)
    np.testing.assert_array_equal(mesh.visual.uv[mesh.faces], uvs)


def test_same_winding_duplicate_pair_is_not_opposite_membrane_candidate():
    points, _ = _opposite_membrane()
    mesh = _mesh_with((points, np.array([[0, 1, 2], [0, 1, 2]])))
    positions, uvs = _corner_data(mesh)
    stats = clean_export_mesh(mesh, resolution=1024)
    _assert_counts(stats)
    np.testing.assert_array_equal(mesh.vertices[mesh.faces], positions)
    np.testing.assert_array_equal(mesh.visual.uv[mesh.faces], uvs)



def test_multiple_duplicate_pair_removals_cannot_collectively_create_a_boundary():
    from collections import Counter

    points, _ = _opposite_membrane()
    second_points = points.copy()
    second_points[2] = points[0] + [0, -0.0002, 0]
    adjacent = (np.array([points[0], points[1], points[0] + [0, 0, 0.02]]),
                np.array([[0, 1, 2]]))
    mesh = _mesh_with(_opposite_membrane(),
                      (second_points, np.array([[0, 1, 2], [0, 2, 1]])),
                      adjacent)

    def boundaries(value):
        counts = Counter()
        for corners in value.vertices[value.faces]:
            for index in range(3):
                edge = tuple(sorted((tuple(corners[index]), tuple(corners[(index + 1) % 3]))))
                counts[edge] += 1
        return {edge for edge, count in counts.items() if count == 1}

    before = boundaries(mesh)
    attached_positions = mesh.vertices[mesh.faces[-1]].copy()
    stats = clean_export_mesh(mesh, resolution=1024)
    # The shared edge begins at5 faces. Each pair alone appears safe (5->3),
    # but dropping BOTH would leave1 face. Evaluate the combined removal mask.
    assert stats["removed_artifact_components"] in (0, 1)
    assert stats["removed_artifact_faces"] == 2 * stats["removed_artifact_components"]
    assert not (boundaries(mesh) - before)
    np.testing.assert_array_equal(mesh.vertices[mesh.faces[-1]], attached_positions)



@pytest.mark.parametrize("artifact", ("zero", "needle", "membrane"))
def test_cleanup_preserves_authored_smooth_normals_across_uv_seams(artifact):
    if artifact == "zero":
        piece = (np.array([[0.7, 0, 0], [0.71, 0, 0], [0.72, 0, 0]]),
                 np.array([[0, 1, 2]]))
    elif artifact == "needle":
        piece = _strip()
    else:
        piece = _opposite_membrane()
    mesh = _mesh_with(piece)
    # Authored smooth normals are identical at duplicate geometric positions,
    # although UV chart vertices stay distinct. Automatic recomputation after
    # pruning would turn each UV chart into a flat-shaded triangle.
    smooth = mesh.vertices.copy()
    smooth /= np.linalg.norm(smooth, axis=1)[:, None]
    mesh.vertex_normals = smooth
    expected_positions = mesh.vertices[mesh.faces[:12]].copy()
    expected_vertex_normals = mesh.vertex_normals[mesh.faces[:12]].copy()
    expected_face_normals = mesh.face_normals[:12].copy()
    stats = clean_export_mesh(mesh, resolution=1024)
    if artifact == "zero":
        _assert_counts(stats, degenerate=1)
    else:
        _assert_counts(stats, components=1, artifact_faces=2)
    np.testing.assert_array_equal(mesh.vertices[mesh.faces], expected_positions)
    np.testing.assert_array_equal(mesh.vertex_normals[mesh.faces], expected_vertex_normals)
    np.testing.assert_array_equal(mesh.face_normals, expected_face_normals)
