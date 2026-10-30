"""Decode the FDG source without the corrupting native hole-centroid append."""
from __future__ import annotations

from types import SimpleNamespace
import pytest


@pytest.mark.parametrize("resolution", [512, 1024, 1536])
def test_decode_latent_preserves_raw_fdg_geometry_and_never_fills_holes(monkeypatch, resolution):
    import torch
    from inference.trellis import decoding
    import trellis2.representations as representations
    import trellis2.representations.mesh as mesh_representations

    calls, packed = [], []
    shape_slat, tex_slat = object(), object()
    subs = [object(), object()]
    meshes, voxels = [], []

    def forbid_fill(*args, **kwargs):
        pytest.fail("decode must not append native fill_holes centroids")

    for batch in range(2):
        vertices = torch.tensor([[0., 0., 0.], [.1, 0., 0.], [0., .1, 0.]]) + batch * .01
        faces = torch.tensor([[0, 1, 2]], dtype=torch.int32)
        meshes.append(SimpleNamespace(vertices=vertices, faces=faces, fill_holes=forbid_fill))
        coords = torch.tensor([[batch, 1, 2, 3], [batch, 4, 5, 6]], dtype=torch.int32)
        voxels.append(SimpleNamespace(coords=coords, feats=torch.full((2, 6), .2 + batch * .1),
                                      shape=torch.Size([1, 6]), spatial_shape=torch.Size([resolution] * 3)))
    snapshots = [(m.vertices.clone(), m.faces.clone()) for m in meshes]
    layout = {"base_color": slice(0, 3), "metallic": slice(3, 4)}

    def decode_shape(value, grid):
        assert value is shape_slat and grid == resolution
        assert not torch.is_grad_enabled(), "decode must retain upstream no_grad behavior"
        calls.append("shape")
        return meshes, subs

    def decode_tex(value, guide_subs):
        assert value is tex_slat and guide_subs is subs
        assert not torch.is_grad_enabled()
        calls.append("texture")
        return voxels

    def construct(vertices, faces, **kwargs):
        calls.append("pack")
        packed.append((vertices, faces, kwargs))
        return SimpleNamespace(vertices=vertices, faces=faces, **kwargs)

    monkeypatch.setattr(representations, "MeshWithVoxel", construct)
    monkeypatch.setattr(mesh_representations, "MeshWithVoxel", construct)
    monkeypatch.setattr(decoding, "MeshWithVoxel", construct, raising=False)
    pipeline = SimpleNamespace(decode_shape_slat=decode_shape, decode_tex_slat=decode_tex,
                               decode_latent=forbid_fill, pbr_attr_layout=layout)
    with torch.enable_grad():
        output = decoding.decode_latent(pipeline, shape_slat, tex_slat, resolution)
    assert calls == ["shape", "texture", "pack", "pack"]
    assert len(output) == 2
    for index, (vertices, faces, options) in enumerate(packed):
        assert vertices is meshes[index].vertices
        assert faces is meshes[index].faces
        assert output[index].vertices is vertices and output[index].faces is faces
        assert options["origin"] == [-.5, -.5, -.5]
        assert options["voxel_size"] == 1 / resolution
        torch.testing.assert_close(options["coords"], voxels[index].coords[:, 1:], rtol=0, atol=0)
        assert options["attrs"] is voxels[index].feats
        assert options["voxel_shape"] == torch.Size([1, 6, resolution, resolution, resolution])
        assert options["layout"] is layout
        torch.testing.assert_close(vertices, snapshots[index][0], rtol=0, atol=0)
        torch.testing.assert_close(faces, snapshots[index][1], rtol=0, atol=0)
