"""CPU-only source-surface coverage checks with a fake native BVH.

Native distances are deliberately untrustworthy. Real CPU closest-point math
must validate the triangle identified by each native result.
"""
from __future__ import annotations

import sys
from types import SimpleNamespace
import numpy as np
import pytest
import trimesh
from trimesh.visual.material import PBRMaterial
from trimesh.visual.texture import TextureVisuals
from inference.trellis import mesh_quality


def _box():
    mesh = trimesh.creation.box()
    mesh.visual = TextureVisuals(
        uv=np.arange(len(mesh.vertices) * 2, dtype=float).reshape(-1, 2) / 16,
        material=PBRMaterial(name="coverage_material"),
    )
    mesh.vertex_normals = np.tile([0., 0., 1.], (len(mesh.vertices), 1))
    return mesh


def _face_points():
    grid = np.array([(x, y) for x in (-0.35, 0., 0.35) for y in (-0.35, 0., 0.35)])
    points = []
    for axis in range(3):
        for side in (-0.5, 0.5):
            xyz = np.empty((len(grid), 3))
            xyz[:, axis] = side
            xyz[:, [i for i in range(3) if i != axis]] = grid
            points.append(xyz)
    return np.concatenate(points)


def _fake_bvh(monkeypatch, *, face_id=None, forbid=False):
    import torch
    calls = []

    class FakeBVH:
        def __init__(self, vertices, faces):
            assert not forbid, "eight or fewer triangles must use the CPU path"
            self.mesh = trimesh.Trimesh(
                vertices=vertices.detach().cpu().numpy(),
                faces=faces.detach().cpu().numpy(), process=False,
            )

        def unsigned_distance(self, points, return_uvw=False):
            assert points.device.type == "cpu", "tests must never allocate CUDA tensors"
            query = points.detach().cpu().numpy()
            calls.append(query.copy())
            if face_id is None:
                _, _, ids = trimesh.proximity.closest_point_naive(self.mesh, query)
            else:
                ids = np.full(len(query), face_id, dtype=np.int64)
            # Reproduce CuBVH's dangerous no-hit distance-zero sentinel.
            return torch.zeros(len(query)), torch.as_tensor(ids, dtype=torch.int64), None

    monkeypatch.setitem(sys.modules, "cumesh", SimpleNamespace(cuBVH=FakeBVH))
    return calls


def _coverage(mesh, source):
    return mesh_quality.count_uncovered_source(mesh, source, 512, device="cpu")


def test_coarse_complete_surface_covers_source_points_far_from_all_export_vertices(monkeypatch):
    calls = _fake_bvh(monkeypatch)
    mesh, source = _box(), _face_points()
    source.setflags(write=False)
    # A vertex-neighbor test would reject these valid coarse face interiors.
    assert np.linalg.norm(source[:, None] - mesh.vertices[None], axis=2).min() > 8 / 512
    snapshots = [mesh.vertices.copy(), mesh.faces.copy(), mesh.visual.uv.copy(),
                 mesh.vertex_normals.copy(), mesh.face_normals.copy(), source.copy()]
    material = mesh.visual.material
    assert _coverage(mesh, source) == (0, len(source))
    assert calls, "the twelve-triangle fixture must exercise native returned face IDs"
    current = [mesh.vertices, mesh.faces, mesh.visual.uv, mesh.vertex_normals, mesh.face_normals, source]
    for value, before in zip(current, snapshots):
        np.testing.assert_array_equal(value, before)
    assert mesh.visual.material is material


def test_only_top_plate_fails_coverage_of_other_five_sides_using_small_mesh_cpu_path(monkeypatch):
    _fake_bvh(monkeypatch, forbid=True)
    vertices = np.array([[-.5, -.5, .5], [.5, -.5, .5], [.5, .5, .5], [-.5, .5, .5]])
    plate = trimesh.Trimesh(vertices=vertices, faces=[[0, 1, 2], [0, 2, 3]], process=False)
    source = _face_points()
    assert _coverage(plate, source) == (45, 54)


def test_native_zero_distance_does_not_hide_wrong_triangle_results(monkeypatch):
    _fake_bvh(monkeypatch, face_id=0)
    uncovered, samples = _coverage(_box(), _face_points())
    assert samples == 54
    assert uncovered >= 40, "CPU distance to the returned triangle must expose false native zeros"


@pytest.mark.parametrize("face_id", [-1, 12])
def test_invalid_native_triangle_ids_are_rejected_before_indexing(monkeypatch, face_id):
    _fake_bvh(monkeypatch, face_id=face_id)
    with pytest.raises(ValueError, match="(?i)face|triangle|index|BVH"):
        _coverage(_box(), _face_points())


def test_source_sampling_is_bounded_deterministic_linspace(monkeypatch):
    calls = _fake_bvh(monkeypatch)
    source = np.tile(_face_points(), (1215, 1))  # 65,610 > 65,536
    assert _coverage(_box(), source) == (0, 65536)
    queried = np.concatenate(calls)
    expected = source[np.linspace(0, len(source) - 1, 65536, dtype=np.int64)]
    np.testing.assert_allclose(queried, expected, atol=2e-8, rtol=0)
