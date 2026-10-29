"""CPU-only needle/sliver comparison for saved generation/replay stages."""
from __future__ import annotations
import argparse
import json
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
TEST_ROOT = REPO / "test"
sys.path.insert(0, str(HERE))

import numpy as np
from mesh_quality_probe import triangle_shape_metrics


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("meshes", type=Path, nargs="+")
    parser.add_argument("--resolution", type=int, default=1024)
    parser.add_argument("--output", type=Path,
                        default=TEST_ROOT / "outputs/generation_quality/Alice/sliver_stages.json")
    parser.add_argument("--max-needle-faces", type=int, default=None,
                        help="optional regression gate for a known fixture: long edge >=1 percent diagonal, aspect >=100")
    opts = parser.parse_args()
    if opts.resolution <= 0 or (opts.max_needle_faces is not None and opts.max_needle_faces < 0):
        parser.error("resolution must be positive and needle limit nonnegative")
    if not opts.output.resolve().is_relative_to(TEST_ROOT.resolve()):
        parser.error("output must stay under test/")
    results = []
    for path in opts.meshes:
        if not path.resolve().is_relative_to(TEST_ROOT.resolve()):
            parser.error("inputs must stay under test/")
        started = time.perf_counter()
        if path.suffix.lower() == ".npz":
            with np.load(path, allow_pickle=False) as saved:
                metric = triangle_shape_metrics(saved["vertices"], saved["faces"], voxel_size=1 / opts.resolution)
        else:
            import trimesh
            mesh = trimesh.load_scene(str(path), process=False).to_mesh()
            metric = triangle_shape_metrics(mesh.vertices, mesh.faces, voxel_size=1 / opts.resolution)
            del mesh
        metric.update({"path": str(path.resolve()), "seconds": round(time.perf_counter() - started, 3)})
        results.append(metric)
        opts.output.parent.mkdir(parents=True, exist_ok=True)
        opts.output.write_text(json.dumps({"resolution": opts.resolution, "stages": results}, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"path": str(path), "faces": metric["faces"],
                          "edge_voxels_max": metric["longest_edge_in_voxels"]["p100"],
                          "aspect_p99_9": metric["aspect_longest_edge_over_altitude"].get("p99.9"),
                          "long_slivers": metric["long_sliver_counts"],
                          "needle_area_fraction": metric["needle_area_fraction"],
                          "seconds": metric["seconds"]}), flush=True)
    return int(opts.max_needle_faces is not None and any(
        item["needle_faces_aspect_100"] > opts.max_needle_faces for item in results))


if __name__ == "__main__":
    raise SystemExit(main())
