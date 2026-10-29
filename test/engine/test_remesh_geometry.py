"""Fast GPU reproduction of the generation export's CuMesh geometry path.

Known closed inputs are passed through initial fill_holes, unsigned narrow-band
dual contouring, and simplify with the production band, followed by the production cleanup.
Raw native defects are recorded; final repaired topology is asserted.
These synthetic fixtures have no intended open boundaries. This does NOT impose
a watertightness requirement on general TRELLIS output.

Standalone (hold test/part_aware/gpu_lock.py around this command):
    backend/python/python.exe -s -B test/engine/test_remesh_geometry.py

No model weights, image generation, UV unwrap or texture baking is needed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[2]
TEST_ROOT = REPO / "test"
OUT = TEST_ROOT / "outputs/generation_quality/synthetic_remesh"
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import pytest
import trimesh

from mesh_quality_probe import analyze_mesh_arrays


def _input_mesh(kind: str) -> trimesh.Trimesh:
    if kind == "sphere":
        return trimesh.creation.icosphere(subdivisions=3, radius=0.35)
    if kind == "box":
        return trimesh.creation.box(extents=(0.6, 0.6, 0.6))
    raise ValueError(f"unknown fixture: {kind}")


def _surface_distance(kind: str, points: np.ndarray) -> np.ndarray:
    points = np.asarray(points, dtype=np.float64)
    if kind == "sphere":
        # Icosphere chords lie slightly inside the analytic sphere; this is
        # recorded for input too and is well below the voxel-size bound below.
        return np.abs(np.linalg.norm(points, axis=1) - 0.35)
    q = np.abs(points) - 0.3
    signed = np.linalg.norm(np.maximum(q, 0), axis=1) + np.minimum(q.max(axis=1), 0)
    return np.abs(signed)


def _metrics(kind: str, vertices: Any, faces: Any, source_bvh: Any = None) -> dict[str, Any]:
    if hasattr(vertices, "detach"):
        cpu_vertices = vertices.detach().cpu().numpy()
        cpu_faces = faces.detach().cpu().numpy()
    else:
        cpu_vertices, cpu_faces = np.asarray(vertices), np.asarray(faces)
    result = analyze_mesh_arrays(cpu_vertices, cpu_faces)
    distances = _surface_distance(kind, cpu_vertices)
    result["analytic_surface_distance"] = {
        "mean": float(distances.mean()), "p95": float(np.percentile(distances, 95)),
        "max": float(distances.max()),
    }
    if source_bvh is not None:
        # Every stage is compared with the SAME original triangle surface.
        # This checks drift independently of the topology/welding metric.
        distances = source_bvh.unsigned_distance(vertices.contiguous())[0].detach().cpu().numpy()
        result["original_surface_distance"] = {
            "mean": float(distances.mean()), "p95": float(np.percentile(distances, 95)),
            "max": float(distances.max()),
        }
    return result


def _defects(metrics: dict[str, Any]) -> dict[str, int]:
    keys = ("boundary_edges", "nonmanifold_edges", "degenerate_faces",
            "duplicate_faces", "inconsistent_winding_edges")
    return {key: metrics[key] for key in keys if metrics[key]}


def run_case(kind: str, *, resolution: int = 128, target: int = 10000,
             output: Path = OUT, save_meshes: bool = False) -> dict[str, Any]:
    """Exercise actual installed CuMesh; report all stages before asserting."""
    cache = TEST_ROOT / ".runtime/generation_quality/synthetic-remesh/cache"
    for name, subdir in (("CUDA_CACHE_PATH", "cuda"), ("TORCH_EXTENSIONS_DIR", "extensions")):
        destination = cache / subdir
        destination.mkdir(parents=True, exist_ok=True)
        os.environ[name] = str(destination)
    import torch
    import cumesh
    from cumesh.remeshing import remesh_narrow_band_dc

    if not torch.cuda.is_available():
        raise RuntimeError("real CUDA GPU required")
    if not output.resolve().is_relative_to(TEST_ROOT.resolve()):
        raise ValueError("outputs must stay under test/")
    output.mkdir(parents=True, exist_ok=True)
    fixture = _input_mesh(kind)
    original_vertices = np.asarray(fixture.vertices, dtype=np.float32)
    original_faces = np.asarray(fixture.faces, dtype=np.int32)
    vertices = torch.tensor(original_vertices, dtype=torch.float32, device="cuda").contiguous()
    faces = torch.tensor(original_faces, dtype=torch.int32, device="cuda").contiguous()
    center = torch.zeros(3, dtype=torch.float32, device="cuda")
    from inference.trellis.export import REMESH_BAND
    scale = (resolution + 3 * REMESH_BAND) / resolution  # unit AABB, remesh_band=1, as production
    voxel_size = scale / resolution
    result: dict[str, Any] = {
        "fixture": kind, "resolution": resolution, "decimation_target": target,
        "remesh_band": 1, "remesh_project": 0, "domain_scale": scale,
        "torch_version": torch.__version__, "cuda_version": torch.version.cuda,
        "cumesh_module": str(cumesh.__file__),
        "remesh_module": str(sys.modules["cumesh.remeshing"].__file__),
        "device": torch.cuda.get_device_name(0),
        "compute_capability": list(torch.cuda.get_device_capability(0)),
        "stages": {},
        "caveats": [
            "Index-space boundaries can be UV seams; exact-position welded topology is authoritative here.",
            "Only these known closed synthetic inputs are required to remain free of open/nonmanifold defects.",
            "UDF narrow-band remeshing may create inner and outer shells, so component count is reported, not assumed equal to input.",
            "Surface distance cannot detect a missing patch by itself; inspect topology and real-case renders too.",
        ],
    }
    stages = result["stages"]
    stages["input"] = _metrics(kind, original_vertices, original_faces)
    assert not _defects(stages["input"]), "synthetic fixture itself must be clean"

    mesh = cumesh.CuMesh()
    mesh.init(vertices, faces)
    mesh.fill_holes(max_hole_perimeter=3e-2)
    vertices, faces = mesh.read()
    bvh = cumesh.cuBVH(vertices, faces)
    stages["initial_fill_holes"] = _metrics(kind, vertices, faces, bvh)
    started = time.perf_counter()
    new_vertices, new_faces = remesh_narrow_band_dc(
        vertices, faces, center=center, scale=scale, resolution=resolution,
        band=REMESH_BAND, project_back=0, verbose=False, bvh=bvh,
    )
    torch.cuda.synchronize()
    result["remesh_seconds"] = round(time.perf_counter() - started, 4)
    stages["remeshed"] = _metrics(kind, new_vertices, new_faces, bvh)
    if save_meshes:
        np.savez_compressed(output / f"{kind}_{resolution}_remeshed.npz",
                            vertices=new_vertices.detach().cpu().numpy(),
                            faces=new_faces.detach().cpu().numpy())

    mesh.init(new_vertices, new_faces)
    started = time.perf_counter()
    mesh.simplify(target, verbose=False)
    torch.cuda.synchronize()
    result["simplify_seconds"] = round(time.perf_counter() - started, 4)
    final_vertices, final_faces = mesh.read()
    stages["simplified"] = _metrics(kind, final_vertices, final_faces, bvh)
    if save_meshes:
        np.savez_compressed(output / f"{kind}_{resolution}_simplified.npz",
                            vertices=final_vertices.detach().cpu().numpy(),
                            faces=final_faces.detach().cpu().numpy())

    # CuMesh's parallel simplifier can emit collapsed facets. The application
    # repairs them after simplification; gate the full production geometry path,
    # while retaining raw-stage defects as diagnostic evidence.
    from inference.trellis.mesh_quality import clean_export_mesh, fill_small_triangle_holes
    clean = trimesh.Trimesh(vertices=final_vertices.detach().cpu().numpy(),
                            faces=final_faces.detach().cpu().numpy(), process=False)
    clean.visual = trimesh.visual.TextureVisuals(uv=np.zeros((len(clean.vertices), 2)))
    repairs = clean_export_mesh(clean, resolution)
    repairs.update(fill_small_triangle_holes(clean, resolution))
    result["repair_stats"] = repairs
    stages["cleaned"] = _metrics(
        kind, torch.tensor(np.asarray(clean.vertices), dtype=torch.float32, device="cuda"),
        torch.tensor(np.asarray(clean.faces), dtype=torch.int32, device="cuda"), bvh,
    )
    result["raw_stage_defects"] = {name: _defects(stages[name])
                                  for name in ("initial_fill_holes", "remeshed", "simplified")}
    failures = []
    for name in ("initial_fill_holes", "remeshed", "simplified", "cleaned"):
        metrics = stages[name]
        defects = _defects(metrics)
        if defects and name == "cleaned":
            failures.append(f"{name}: {defects}")
        # One voxel of deliberate UDF offset, plus reconstruction/simplification
        # slack. This is a geometry sanity bound, not a visual-quality guarantee.
        maximum = metrics["original_surface_distance"]["max"]
        if maximum > 4 * voxel_size:
            failures.append(f"{name}: surface drift {maximum:.6g} > 4 voxels ({4 * voxel_size:.6g})")
    result["voxel_size"] = voxel_size
    result["failures"] = failures
    result["passed"] = not failures
    report_path = output / f"{kind}_{resolution}.json"
    report_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"fixture": kind, "resolution": resolution,
                      "remesh_seconds": result["remesh_seconds"],
                      "simplify_seconds": result["simplify_seconds"],
                      "remesh_components": stages["remeshed"]["connected_components"],
                      "final_components": stages["simplified"]["connected_components"],
                      "failures": failures, "report": str(report_path)}), flush=True)
    return result


@pytest.mark.gpu
@pytest.mark.parametrize("kind", ("sphere", "box"))
def test_closed_synthetic_export_geometry_does_not_gain_topology_defects(kind: str):
    import torch

    if not torch.cuda.is_available():
        pytest.skip("real CUDA GPU required")
    result = run_case(kind)
    assert result["passed"], "; ".join(result["failures"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", nargs="+", choices=("sphere", "box"), default=["sphere", "box"])
    parser.add_argument("--resolution", type=int, choices=(128, 256), default=128)
    parser.add_argument("--target", type=int, default=10000)
    parser.add_argument("--output", type=Path, default=OUT)
    parser.add_argument("--save-meshes", action="store_true")
    options = parser.parse_args(argv)
    if options.target < 4:
        parser.error("--target must be at least 4")
    results = [run_case(kind, resolution=options.resolution, target=options.target,
                        output=options.output, save_meshes=options.save_meshes)
               for kind in options.cases]
    return int(any(not result["passed"] for result in results))


if __name__ == "__main__":
    raise SystemExit(main())
