"""GPU regression: close tiny reconstruction gaps while retaining large openings."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import trimesh

ROOT = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True


@pytest.mark.gpu
@pytest.mark.parametrize(("radius_voxels", "expect_front_surface"), [(2.5, True), (8.0, False)])
def test_export_closes_small_gap_and_preserves_large_opening(
    monkeypatch, tmp_path: Path, radius_voxels: float, expect_front_surface: bool
):
    torch = pytest.importorskip("torch")
    if not torch.cuda.is_available():
        pytest.skip("requires CUDA")
    cumesh = pytest.importorskip("cumesh")
    from inference.base import CancellationToken, GenerationParams
    from inference.trellis.export import export_glb

    resolution = 128
    sphere = trimesh.creation.icosphere(subdivisions=5, radius=0.25)
    centers = np.asarray(sphere.triangles_center)
    aperture = (np.linalg.norm(centers[:, :2], axis=1) < radius_voxels / resolution) & (centers[:, 2] > 0)
    vertices = torch.as_tensor(np.asarray(sphere.vertices), dtype=torch.float32, device="cuda")
    faces = torch.as_tensor(np.asarray(sphere.faces)[~aperture].copy(), dtype=torch.int32, device="cuda")
    captured = {}

    def geometry_only_export(**kwargs):
        # Keep the production adapter's choice of remeshing parameters, running
        # the real native geometry path but omitting unrelated UV/texture baking.
        handler = cumesh.CuMesh()
        handler.init(kwargs["vertices"], kwargs["faces"])
        handler.fill_holes(max_hole_perimeter=3e-2)
        source_vertices, source_faces = handler.read()
        bvh = cumesh.cuBVH(source_vertices, source_faces)
        band = kwargs["remesh_band"]
        handler.init(*cumesh.remeshing.remesh_narrow_band_dc(
            source_vertices, source_faces, center=torch.zeros(3, device="cuda"),
            scale=(resolution + 3 * band) / resolution,
            resolution=resolution, band=band, project_back=kwargs["remesh_project"], bvh=bvh,
        ))
        handler.simplify(kwargs["decimation_target"])
        final_vertices, final_faces = handler.read()
        mesh = trimesh.Trimesh(vertices=final_vertices.cpu().numpy(),faces=final_faces.cpu().numpy(),process=False)
        captured["mesh"] = mesh
        return mesh

    monkeypatch.setitem(sys.modules,"o_voxel",SimpleNamespace(postprocess=SimpleNamespace(to_glb=geometry_only_export)))
    reporter = SimpleNamespace(step=lambda *args: None)
    export_glb(
        SimpleNamespace(vertices=vertices,faces=faces,attrs=None,coords=None),
        SimpleNamespace(pbr_attr_layout={}),resolution,
        GenerationParams(resolution=resolution,polygon_target=10000,texture_size=512),
        tmp_path/"model.glb",reporter,CancellationToken(),
    )
    final = captured["mesh"]
    bvh = cumesh.cuBVH(np.asarray(final.vertices,dtype=np.float32),np.asarray(final.faces,dtype=np.int32))
    hit, face_id, _ = bvh.ray_trace(
        torch.tensor([[0.0,0.0,0.75]],device="cuda"),
        torch.tensor([[0.0,0.0,-1.0]],device="cuda"),
    )
    assert int(face_id[0]) >= 0, "the test sphere must have a ray intersection"
    actual_front_surface = float(hit[0,2]) > 0.1
    assert actual_front_surface == expect_front_surface, (
        f"opening radius {radius_voxels} voxels: hit z={float(hit[0,2]):.6f}; "
        f"expected front surface={expect_front_surface}"
    )
