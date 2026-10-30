"""Read-only, CPU-only source support quality gate for exported geometry."""
from __future__ import annotations

import numpy as np
import pytest
import trimesh
from trimesh.visual.material import PBRMaterial
from trimesh.visual.texture import TextureVisuals
from inference.trellis import mesh_quality


def _mesh(vertices):
    vertices = np.asarray(vertices, dtype=np.float64)
    faces = np.array([[0, 1, 2]], dtype=np.int64)
    uv = np.arange(len(vertices) * 2, dtype=np.float64).reshape(-1, 2) / (2 * len(vertices))
    mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False,
        visual=TextureVisuals(uv=uv, material=PBRMaterial(name="source_support_material")))
    mesh.vertex_normals = np.tile([0., 0., 1.], (len(vertices), 1))
    mesh.face_normals = np.tile([0., 0., 1.], (len(faces), 1))
    return mesh


def _count(mesh, support, resolution):
    return mesh_quality.count_unsupported_vertices(mesh, support, resolution)


def test_support_gate_counts_distant_vertices_without_pruning_or_changing_visuals():
    resolution = 512
    # Include a UV seam duplicate and a compact detached piece: every vertex
    # within eight voxels survives regardless of component size or connectivity.
    points = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0],
                       [0, 0, 0], [5.5, 0, 0], [9, 0, 0], [9, 0, 0]], dtype=float) / resolution
    mesh = _mesh(points)
    support = np.array([[0., 0., 0.], [0., 1. / resolution, 0.]])
    support.setflags(write=False)
    before = {"vertices": mesh.vertices.copy(), "faces": mesh.faces.copy(),
              "uv": mesh.visual.uv.copy(), "vertex_normals": mesh.vertex_normals.copy(),
              "face_normals": mesh.face_normals.copy(), "support": support.copy()}
    material = mesh.visual.material
    assert _count(mesh, support, resolution) == 2
    for key in ("vertices", "faces", "vertex_normals", "face_normals"):
        np.testing.assert_array_equal(getattr(mesh, key), before[key])
    np.testing.assert_array_equal(mesh.visual.uv, before["uv"])
    np.testing.assert_array_equal(support, before["support"])
    assert mesh.visual.material is material


def test_support_gate_is_strictly_more_than_eight_voxels_and_euclidean():
    resolution = 512
    boundary = 8. / resolution
    points = [[boundary, 0, 0], [-boundary, 0, 0], [0, 0, boundary],
              [np.nextafter(boundary, np.inf), 0, 0],
              [5. / resolution, 5. / resolution, 0], [6. / resolution, 6. / resolution, 0]]
    assert _count(_mesh(points), np.array([[0., 0., 0.]]), resolution) == 2


def test_support_gate_uses_already_transformed_glb_points_without_axis_conversion():
    support = np.array([[0.1, 0.3, -0.2], [0.2, 0.4, -0.1], [0.3, 0.2, -0.4]])
    assert _count(_mesh(support), support.copy(), 1536) == 0


@pytest.mark.parametrize("support", [np.empty((0, 3)), np.zeros(3), np.zeros((3, 2)),
                                     np.zeros((2, 2, 3)), [[0, np.nan, 0]], [[0, 0, np.inf]]])
def test_support_gate_rejects_invalid_source_support(support):
    with pytest.raises(ValueError):
        _count(_mesh([[0, 0, 0], [1, 0, 0], [0, 1, 0]]), support, 512)


@pytest.mark.parametrize("resolution", [0, -1, np.nan, np.inf])
def test_support_gate_rejects_invalid_resolution(resolution):
    with pytest.raises(ValueError):
        _count(_mesh([[0, 0, 0], [1, 0, 0], [0, 1, 0]]), [[0, 0, 0]], resolution)


def test_support_gate_rejects_nonfinite_export_vertices():
    mesh = _mesh([[0, 0, 0], [np.nan, 0, 0], [0, 1, 0]])
    with pytest.raises(ValueError):
        _count(mesh, [[0, 0, 0]], 512)
