"""Decode TRELLIS geometry without native hole-center corruption.

The upstream decode_latent calls CuMesh.fill_holes before returning its mesh.
On the supported Windows runtime that step can append invalid center vertices.
Keep the original decoded surface and repair bounded gaps after remeshing.
"""
from __future__ import annotations
from typing import Any


def decode_latent(pipeline: Any, shape_slat: Any, tex_slat: Any, resolution: int) -> list[Any]:
    import torch
    from trellis2.representations.mesh import MeshWithVoxel

    with torch.no_grad():
        meshes, subdivisions = pipeline.decode_shape_slat(shape_slat, resolution)
        voxels = pipeline.decode_tex_slat(tex_slat, subdivisions)
        return [MeshWithVoxel(
            mesh.vertices, mesh.faces,
            origin=[-0.5, -0.5, -0.5],
            voxel_size=1 / resolution,
            coords=voxel.coords[:, 1:],
            attrs=voxel.feats,
            voxel_shape=torch.Size([*voxel.shape, *voxel.spatial_shape]),
            layout=pipeline.pbr_attr_layout,
        ) for mesh, voxel in zip(meshes, voxels)]