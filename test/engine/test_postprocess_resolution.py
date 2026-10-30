"""Exercise the first-party postprocess adapter up to DC without any GPU work."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import numpy as np
import pytest
import trimesh

ROOT = Path(__file__).resolve().parents[2]
ADAPTER = ROOT / "backend" / "inference" / "trellis" / "postprocess.py"


@pytest.mark.parametrize(("source_grid", "cap", "expected"),
                         [(1536, None, 1536), (1536, 768, 768),
                          (1536, 1024, 1024), (1024, 512, 512),
                          (512, 1024, 512), (512, None, 512)])
def test_first_party_adapter_caps_only_remeshing_grid_and_keeps_original_bvh_source(
    monkeypatch, source_grid, cap, expected
):
    import torch

    # Tensor.cuda is a pure identity for this test. No real extension or GL
    # rasterizer is imported, and execution stops before native remeshing.
    monkeypatch.setattr(torch.Tensor, "cuda", lambda self, *args, **kwargs: self)
    captured = {}

    class ReachedRemesh(RuntimeError):
        pass

    class FakeMesh:
        def init(self, vertices, faces):
            self.vertices, self.faces = vertices, faces
            captured["initialized"] = (vertices, faces)

        def fill_holes(self, **kwargs):
            pytest.fail("postprocess must not append corrupt native fill_holes centroids")

        def read(self):
            return self.vertices, self.faces

    def bvh(vertices, faces):
        captured["bvh_source"] = (vertices, faces)
        captured["bvh"] = object()
        return captured["bvh"]

    def remesh(vertices, faces, **kwargs):
        captured["remesh_source"] = (vertices, faces)
        captured["remesh"] = kwargs
        raise ReachedRemesh

    monkeypatch.setitem(sys.modules, "cumesh", SimpleNamespace(
        CuMesh=FakeMesh, cuBVH=bvh, remeshing=SimpleNamespace(remesh_narrow_band_dc=remesh)))
    for name in ("flex_gemm", "flex_gemm.ops", "flex_gemm.ops.grid_sample", "nvdiffrast", "nvdiffrast.torch"):
        monkeypatch.setitem(sys.modules, name, ModuleType(name))
    sys.modules["flex_gemm.ops.grid_sample"].grid_sample_3d = lambda *args, **kwargs: pytest.fail("baking reached")
    sys.modules["nvdiffrast"].torch = sys.modules["nvdiffrast.torch"]

    spec = importlib.util.spec_from_file_location("inference.trellis._postprocess_resolution_test", ADAPTER)
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)

    box = trimesh.creation.box()
    vertices = torch.as_tensor(np.asarray(box.vertices), dtype=torch.float32)
    faces = torch.as_tensor(np.asarray(box.faces), dtype=torch.int32)
    coords = torch.tensor([[1, 2, 3]], dtype=torch.int32)
    grid = torch.tensor([source_grid] * 3, dtype=torch.int32)
    options = {} if cap is None else {"remesh_resolution": cap}
    with pytest.raises(ReachedRemesh):
        adapter.to_glb(
            vertices, faces, torch.zeros((1, 6)), coords, {},
            aabb=[[-.5, -.5, -.5], [.5, .5, .5]], grid_size=grid,
            remesh=True, remesh_band=2, remesh_project=0,
            decimation_target=2_000_000, texture_size=4096, **options,
        )
    for stage in ("initialized", "bvh_source", "remesh_source"):
        assert captured[stage][0] is vertices
        assert captured[stage][1] is faces
    assert captured["remesh"]["bvh"] is captured["bvh"]
    assert captured["remesh"]["resolution"] == expected
    assert captured["remesh"]["band"] == 2
    assert captured["remesh"]["project_back"] == 0
    assert captured["remesh"]["scale"] == pytest.approx((expected + 6) / expected)
    np.testing.assert_array_equal(captured["remesh"]["center"].numpy(), [0., 0., 0.])
    np.testing.assert_array_equal(grid.numpy(), [source_grid] * 3)
    assert "fill" not in captured
