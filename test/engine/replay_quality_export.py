"""Replay exports from one captured decoded mesh; no model weights or sampling."""
from __future__ import annotations
import argparse
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
# Keep validation state local even when this runner is invoked from another shell.
import os
_quality_runtime = ROOT / "test/.runtime/generation_quality"
_quality_cache = _quality_runtime / "cache"
for _key, _value in {
    "IMG3D_RUNTIME_DIR": _quality_runtime,
    "IMG3D_LOG_DIR": ROOT / "test/.logs/generation_quality",
    "IMG3D_HF_HOME": _quality_runtime / "hf_home",
    "HF_HOME": _quality_runtime / "hf_home",
    "TRITON_CACHE_DIR": _quality_cache / "triton",
    "TORCH_EXTENSIONS_DIR": _quality_cache / "torch_extensions",
    "CUDA_CACHE_PATH": _quality_cache / "cuda",
    "TEMP": _quality_runtime / "temp",
    "TMP": _quality_runtime / "temp",
}.items():
    _value.mkdir(parents=True, exist_ok=True)
    os.environ[_key] = str(_value)
os.environ["FLEX_GEMM_AUTOTUNE_CACHE_PATH"] = str(_quality_cache / "flex_gemm/autotune_cache.json")
(_quality_cache / "flex_gemm").mkdir(parents=True, exist_ok=True)
os.environ["IMG3D_ALLOW_MODEL_DOWNLOADS"] = "false"
sys.dont_write_bytecode = True

sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "test/engine"), str(ROOT / "backend/third_party/TRELLIS.2")]

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("--variant", choices=("baseline", "standard", "band2", "projected", "cleanup", "skinny", "islands", "skinny_islands", "scaled", "scaled_standard", "production"), default="baseline")
    parser.add_argument("--texture-size", type=int, default=512)
    parser.add_argument("--polygons", type=int)
    parser.add_argument("--no-audit", action="store_true")
    parser.add_argument("--synchronize-stages", action="store_true")
    parser.add_argument("--no-grad", action="store_true")
    parser.add_argument("--remesh-cap", type=int, choices=(128,256,512,1024,1536))
    parser.add_argument("--run-name")
    args = parser.parse_args()
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    capture = args.capture.resolve()
    if not capture.is_relative_to(ROOT / "test/outputs"):
        parser.error("capture must be under test/outputs")
    import numpy as np
    import torch
    import cumesh
    import o_voxel
    from mesh_quality_probe import analyze_mesh_arrays
    torch.set_grad_enabled(not args.no_grad)
    data = torch.load(capture, map_location="cpu", weights_only=True)
    if args.run_name and not args.run_name.replace("-", "").replace("_", "").isalnum():
        parser.error("run-name must be alphanumeric")
    output = capture.parent / "replay" / (args.run_name or args.variant)
    output.mkdir(parents=True, exist_ok=True)
    layout = {key: slice(*value) for key, value in data["layout"].items()}
    stages = []
    original_class = cumesh.CuMesh
    next_id = 0

    class AuditedMesh(original_class):
        def record(self, stage):
            nonlocal next_id
            if args.synchronize_stages:
                torch.cuda.synchronize()
            if args.no_audit:
                return
            next_id += 1
            vertices, faces = self.read()
            points, triangles = vertices.detach().cpu().numpy(), faces.detach().cpu().numpy()
            np.savez(output / f"{next_id:02d}_{stage}.npz", vertices=points, faces=triangles)
            metrics = analyze_mesh_arrays(points, triangles)
            metrics["stage"] = stage
            stages.append(metrics)
            print(json.dumps({key:metrics.get(key) for key in ("stage","faces","boundary_edges","nonmanifold_edges","duplicate_faces","inconsistent_winding_edges","connected_components")}),flush=True)

        def init(self, *values, **kwargs):
            result = super().init(*values, **kwargs)
            self.record("init")
            return result

        def fill_holes(self, *values, **kwargs):
            result = super().fill_holes(*values, **kwargs)
            self.record("fill")
            return result

        def simplify(self, *values, **kwargs):
            if args.variant in ('skinny', 'skinny_islands'):
                kwargs['options'] = {'lambda_skinny': 0.1}
            result = super().simplify(*values, **kwargs)
            self.record("simplify")
            return result

        def uv_unwrap(self, *values, **kwargs):
            if args.variant in ('islands', 'skinny_islands'):
                super().remove_small_connected_components(1e-5)
                self.record('remove_islands')
            if args.variant == "cleanup":
                super().remove_duplicate_faces()
                super().remove_degenerate_faces()
                super().repair_non_manifold_edges()
                super().fill_holes(max_hole_perimeter=3e-2)
                super().unify_face_orientations()
                self.record("cleanup")
            return super().uv_unwrap(*values, **kwargs)

    export_stats = {}
    started = time.perf_counter()
    cumesh.CuMesh = AuditedMesh
    try:
        vertices = data["vertices"].cuda()
        faces = data["faces"].cuda()
        attrs = data["attrs"].cuda()
        coords = data["coords"].cuda()
        out_path = output / "model.glb"
        if args.variant == "production":
            from inference.base import CancellationToken, GenerationParams
            from inference.trellis import export as export_module
            if args.remesh_cap is not None:
                export_module.MAX_REMESH_RESOLUTION = args.remesh_cap
            export_glb = export_module.export_glb
            class Reporter:
                def step(self, *values): print("[production]", *values, flush=True)
            params = GenerationParams(polygon_target=args.polygons or data["polygon_target"], texture_size=args.texture_size, glb_webp_textures=data["webp_textures"])
            export_stats = export_glb(SimpleNamespace(vertices=vertices,faces=faces,attrs=attrs,coords=coords), SimpleNamespace(pbr_attr_layout=layout),data["resolution"],params,out_path,Reporter(),CancellationToken())
            import trimesh
            glb = trimesh.load_scene(out_path,process=False).to_mesh()
        else:
            scale = 1024.0 if args.variant in ("scaled", "scaled_standard") else 1.0
            glb = o_voxel.postprocess.to_glb(vertices=vertices * scale,faces=faces,attr_volume=attrs,coords=coords,
                attr_layout=layout,grid_size=data["resolution"],aabb=[[-.5*scale]*3,[.5*scale]*3],
                decimation_target=args.polygons or data["polygon_target"],texture_size=args.texture_size,
                remesh=args.variant not in ("standard", "scaled_standard"),remesh_band=2 if args.variant in ("band2", "scaled") else 1,
                remesh_project=.9 if args.variant=="projected" else 0,verbose=True,use_tqdm=False)
            authored_normals = np.asarray(glb.vertex_normals).copy()
            glb.vertices /= scale
            glb.vertex_normals = authored_normals
            from inference.trellis.mesh_quality import clean_export_mesh, fill_small_triangle_holes, repair_small_boundary_loops
            if args.variant in ("scaled", "standard", "scaled_standard"):
                clean_export_mesh(glb,data["resolution"]); fill_small_triangle_holes(glb,data["resolution"]);repair_small_boundary_loops(glb,data["resolution"])
            glb.export(str(out_path),file_type="glb",extension_webp=data["webp_textures"])
        metrics=analyze_mesh_arrays(np.asarray(glb.vertices),np.asarray(glb.faces))
        report={"variant":args.variant,"capture":str(capture),"seconds":round(time.perf_counter()-started,3),
                "stages":stages,"exported":metrics,"export_stats":export_stats,"path":str(out_path)}
        (output/"quality.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
        print(json.dumps({"variant":args.variant,"seconds":report["seconds"],"path":str(out_path),"metrics":{k:metrics[k] for k in ("faces","boundary_edges","nonmanifold_edges","duplicate_faces","connected_components")}}),flush=True)
        return 0
    finally:
        cumesh.CuMesh = original_class

if __name__ == "__main__":
    raise SystemExit(main())
