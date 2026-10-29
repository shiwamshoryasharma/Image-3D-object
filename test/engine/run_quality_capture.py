"""Capture a real decoded test mesh and run the unchanged production GLB export."""
from __future__ import annotations
import importlib.util
import json
import sys
from pathlib import Path

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

sys.path.insert(0, str(ROOT / "backend"))
CAPTURE_ROOT = ROOT / "test" / "outputs" / "generation_quality"
spec = importlib.util.spec_from_file_location("img3d_generation_smoke", ROOT / "test/engine/run_generation_smoke.py")
smoke = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = smoke
spec.loader.exec_module(smoke)

def main() -> int:
    import torch
    from inference.trellis import generation
    original_export = generation.export_glb
    image_args = sys.argv[sys.argv.index("--image") + 1] if "--image" in sys.argv else str(smoke.DEFAULT_IMAGE)
    image = Path(image_args).resolve()
    if not image.is_relative_to(ROOT / "test/images"):
        raise ValueError("Quality validation input must be under test/images")
    label = image.stem
    output = CAPTURE_ROOT / label
    output.mkdir(parents=True, exist_ok=True)
    smoke.OUTPUT_ROOT = output / "baseline"

    def capture(mesh, pipeline, resolution, params, out_path, reporter, cancel):
        snapshot = {
            "vertices": mesh.vertices.detach().cpu(),
            "faces": mesh.faces.detach().cpu(),
            "attrs": mesh.attrs.detach().cpu(),
            "coords": mesh.coords.detach().cpu(),
            "layout": {key: (value.start, value.stop, value.step) for key, value in pipeline.pbr_attr_layout.items()},
            "resolution": int(resolution),
            "texture_size": int(params.texture_size),
            "polygon_target": int(params.polygon_target),
            "webp_textures": bool(params.glb_webp_textures),
        }
        raw_path = output / "decoded.pt"
        torch.save(snapshot, raw_path)
        print(f"[quality] Decoded mesh saved: {raw_path}; {len(mesh.vertices)} vertices / {len(mesh.faces)} faces", flush=True)
        (output / "input.json").write_text(json.dumps({"image": str(image.relative_to(ROOT)), "argv": sys.argv[1:]}, indent=2) + "\n", encoding="utf-8")
        return original_export(mesh, pipeline, resolution, params, out_path, reporter, cancel)

    generation.export_glb = capture
    try:
        return smoke.main()
    finally:
        generation.export_glb = original_export

if __name__ == "__main__":
    raise SystemExit(main())
