"""GLB export with small-gap reconstruction and UV-preserving artifact cleanup."""

from __future__ import annotations

import contextlib
import logging
import os
import time
from pathlib import Path
from typing import Any

from inference.base import CancellationToken, GenerationParams, ProgressReporter
from inference.trellis.mesh_quality import (
    clean_export_mesh, count_uncovered_source, count_unsupported_vertices,
    fill_small_triangle_holes, repair_small_boundary_loops,
)

logger = logging.getLogger(__name__)

GLB_AABB: list[list[float]] = [[-0.5, -0.5, -0.5], [0.5, 0.5, 0.5]]
EXPORT_STEPS = 2  # (1) remesh + simplify + UV unwrap + texture bake, (2) write the .glb
# A one-voxel UDF band leaves small reconstruction punctures (including eye surfaces).
# Two voxels close those gaps; larger intentional openings are regression-tested.
REMESH_BAND = 2
MAX_REMESH_RESOLUTION = 1536


def export_glb(
    mesh: Any,
    pipeline: Any,
    resolution: int,
    params: GenerationParams,
    out_path: Path,
    reporter: ProgressReporter,
    cancel: CancellationToken,
) -> dict[str, Any]:
    """Convert a ``MeshWithVoxel`` into a textured GLB at ``out_path`` and return raw export statistics.

    Arguments follow upstream ``app.py`` ``extract_glb``: ``decimation_target=polygon_target``, ``texture_size``,
    ``remesh=True, remesh_band=2, remesh_project=0``, ``grid_size=resolution`` (the *effective* resolution) and the
    unit AABB. The remeshing grid can be retried independently from the decoded
    attribute grid when source validation fails. Native hole filling is bypassed
    because it can append corrupt vertices before remeshing.
    Full decoded geometry/attributes still drive texture baking. Source support
    and surface coverage are validated before atomic publication.
    """
    from inference.trellis.postprocess import to_glb

    cancel.raise_if_cancelled()
    # CuMesh/cuBVH allocate outside PyTorch's caching allocator. Hand off only
    # after decoder kernels finish and release unused cached blocks so native
    # allocations do not compete with the decoder's unused reserved memory.
    import torch

    if getattr(mesh.vertices, "is_cuda", False):
        with torch.cuda.device(mesh.vertices.device):
            torch.cuda.synchronize()
            torch.cuda.empty_cache()
    decoded_vertices = int(mesh.vertices.shape[0])
    decoded_faces = int(mesh.faces.shape[0])
    support_points = None
    if mesh.coords is not None:
        import numpy as np

        source_coords = mesh.coords.detach().cpu().numpy().astype(np.float64)
        centers = (source_coords + 0.5) / resolution - 0.5
        support_points = np.column_stack((centers[:, 0], centers[:, 2], -centers[:, 1]))
    bake_seconds = cleanup_seconds = validation_seconds = 0.0
    discarded_remesh_vertices = 0
    bounded_resolution = min(int(resolution), MAX_REMESH_RESOLUTION)
    attempts = (bounded_resolution, max(32, bounded_resolution // 2))
    for remesh_resolution in attempts:
        bake_started = time.perf_counter()
        glb = to_glb(
            vertices=mesh.vertices,
            faces=mesh.faces,
            attr_volume=mesh.attrs,
            coords=mesh.coords,
            attr_layout=pipeline.pbr_attr_layout,
            grid_size=int(resolution),
            aabb=GLB_AABB,
            decimation_target=int(params.polygon_target),
            texture_size=int(params.texture_size),
            remesh=True,
            remesh_band=REMESH_BAND,
            remesh_resolution=remesh_resolution,
            remesh_project=0,
            verbose=False,
            use_tqdm=False,
        )
        bake_seconds += time.perf_counter() - bake_started
        cleanup_started = time.perf_counter()
        cleanup_stats = clean_export_mesh(glb, remesh_resolution)
        cleanup_stats.update(fill_small_triangle_holes(glb, remesh_resolution))
        cleanup_stats.update(repair_small_boundary_loops(glb, remesh_resolution))
        cleanup_seconds += time.perf_counter() - cleanup_started
        validation_started = time.perf_counter()
        unsupported = (count_unsupported_vertices(glb, support_points, remesh_resolution)
                       if support_points is not None else 0)
        uncovered, sampled = (count_uncovered_source(glb, support_points, remesh_resolution, mesh.vertices.device)
                              if support_points is not None else (0, 0))
        uncovered_fraction = uncovered / sampled if sampled else 0.0
        validation_seconds += time.perf_counter() - validation_started
        if not unsupported and uncovered_fraction <= 0.05:
            break
        if remesh_resolution == attempts[-1]:
            raise ValueError("Exported geometry failed decoded source support or surface coverage validation")
        logger.warning("Discarding remesh with %d unsupported vertices and %.2f%% missing source surface; retrying a bounded grid",
                       unsupported, 100 * uncovered_fraction)
        discarded_remesh_vertices = unsupported
        del glb
        cancel.raise_if_cancelled()
        if getattr(mesh.vertices, "is_cuda", False):
            with torch.cuda.device(mesh.vertices.device):
                torch.cuda.synchronize()
                torch.cuda.empty_cache()
    if any(cleanup_stats.values()):
        logger.info("Export mesh repairs: %s", cleanup_stats)
    vertices = int(len(glb.vertices))
    faces = int(len(glb.faces))
    reporter.step(1, EXPORT_STEPS, f"Baked {params.texture_size}px PBR textures onto {faces:,} faces")
    cancel.raise_if_cancelled()

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_name(out_path.name + ".tmp")
    started_write = time.perf_counter()
    try:
        glb.export(str(tmp_path), file_type="glb", extension_webp=bool(params.glb_webp_textures))
        os.replace(tmp_path, out_path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp_path.unlink()
        raise
    write_seconds = time.perf_counter() - started_write
    size = out_path.stat().st_size
    reporter.step(EXPORT_STEPS, EXPORT_STEPS, f"Wrote GLB ({size / 1_048_576:.1f} MB)")
    logger.info(
        "Exported GLB %s: %d vertices, %d faces (decoded %d/%d), texture %dpx, %.1f MB, bake %.1fs, write %.1fs",
        out_path.name, vertices, faces, decoded_vertices, decoded_faces, params.texture_size, size / 1_048_576,
        bake_seconds, write_seconds,
    )
    return {
        "vertices": vertices,
        "faces": faces,
        "decoded_vertices": decoded_vertices,
        "decoded_faces": decoded_faces,
        "texture_size": int(params.texture_size),
        "polygon_target": int(params.polygon_target),
        "webp_textures": bool(params.glb_webp_textures),
        "file_size_bytes": int(size),
        "bake_seconds": round(bake_seconds, 3),
        "write_seconds": round(write_seconds, 3),
        "cleanup_seconds": round(cleanup_seconds, 3),
        "validation_seconds": round(validation_seconds, 3),
        "source_support_checked": support_points is not None,
        "unsupported_vertices": unsupported,
        "discarded_remesh_vertices": discarded_remesh_vertices,
        "source_surface_coverage": round(1 - uncovered_fraction, 6),
        "sampled_source_points": sampled,
        "remesh_band": REMESH_BAND,
        "remesh_resolution": remesh_resolution,
        "export_strategy": "narrow_band_remesh",
        "fallback_used": remesh_resolution != bounded_resolution,
        **cleanup_stats,
    }
