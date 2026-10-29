"""GLB export with small-gap reconstruction and UV-preserving artifact cleanup."""

from __future__ import annotations

import contextlib
import logging
import os
import time
from pathlib import Path
from typing import Any

from inference.base import CancellationToken, GenerationParams, ProgressReporter
from inference.trellis.mesh_quality import clean_export_mesh, fill_small_triangle_holes

logger = logging.getLogger(__name__)

GLB_AABB: list[list[float]] = [[-0.5, -0.5, -0.5], [0.5, 0.5, 0.5]]
EXPORT_STEPS = 2  # (1) remesh + simplify + UV unwrap + texture bake, (2) write the .glb
# A one-voxel UDF band leaves small reconstruction punctures (including eye surfaces).
# Two voxels close those gaps; larger intentional openings are regression-tested.
REMESH_BAND = 2


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
    unit AABB. The wider band repairs small gaps; numerical disconnected needles
    are removed without merging UV vertices. The file is written atomically.
    """
    import o_voxel

    cancel.raise_if_cancelled()
    started = time.perf_counter()
    decoded_vertices = int(mesh.vertices.shape[0])
    decoded_faces = int(mesh.faces.shape[0])
    glb = o_voxel.postprocess.to_glb(
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
        remesh_project=0,
        verbose=False,
        use_tqdm=False,
    )
    bake_seconds = time.perf_counter() - started
    cleanup_started = time.perf_counter()
    cleanup_stats = clean_export_mesh(glb, resolution)
    cleanup_stats.update(fill_small_triangle_holes(glb, resolution))
    cleanup_seconds = time.perf_counter() - cleanup_started
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
        "remesh_band": REMESH_BAND,
        **cleanup_stats,
    }
