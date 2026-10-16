"""GPU verification of the auxiliary models used by TRELLIS.2 (aux-models track).

Run with the embedded interpreter (from anywhere)::

    E:\\14-OCT-2026\\Img3D\\backend\\python\\python.exe E:\\14-OCT-2026\\Img3D\\test\\aux_models\\verify_aux_models.py

Checks
------
1. ``inference.trellis.assets.resolve_aux_models`` on ``backend/.model`` (reports rembg_kind / missing repos).
2. DINOv3: ``DINOv3ViTModel.from_pretrained(local_dir)`` + TRELLIS.2's ``DinoV3FeatureExtractor`` (loaded from its
   single source file) on a 512x512 image -> expects ``[1, N, 1024]``. If the gated weights are not present, a
   STRUCTURAL check runs instead with a randomly initialised ViT-L/16 (test double, clearly reported as such) so the
   transformers-internals code path is still exercised; the DINOv3 check is then reported as BLOCKED.
3. Background removal: TRELLIS.2's ``BiRefNet`` wrapper (``AutoModelForImageSegmentation`` + ``trust_remote_code``)
   on ``test/images/ALICE/ALICE_full_front_view.png`` composited on white; saves the RGBA cutout and reports the
   mask IoU against the image's own ground-truth alpha.
4. SS decoder: both files exist, JSON is valid (``name == "SparseStructureDecoder"``), safetensors loads; if the
   ``trellis2`` package is importable (native extensions built), the decoder is instantiated via
   ``trellis2.models.from_pretrained`` and decodes a random latent to ``[1, 1, 64, 64, 64]``.

Outputs (all under ``test/aux_models``): ``verify_aux_models.log``, ``outputs/verify_results.json``,
``outputs/ALICE_full_front_view_rembg_<kind>.png``, ``outputs/ALICE_full_front_view_rembg_mask_<kind>.png``.
"""

from __future__ import annotations

import importlib.util
import json
import os
import site
import sys
import time
import traceback
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(r"E:\14-OCT-2026\Img3D")
BACKEND = ROOT / "backend"
TEST_DIR = ROOT / "test" / "aux_models"
OUT_DIR = TEST_DIR / "outputs"
LOG_FILE = TEST_DIR / "verify_aux_models.log"
AUX_ROOT = BACKEND / ".model"
TRELLIS_SRC = BACKEND / "third_party" / "TRELLIS.2"
SAMPLE_IMAGE = ROOT / "test" / "images" / "ALICE" / "ALICE_full_front_view.png"

# --- interpreter bootstrap (isolated embedded Python) ----------------------------------------------
_user_site = os.path.normcase(os.path.abspath(site.getusersitepackages()))
sys.path[:] = [p for p in sys.path if os.path.normcase(os.path.abspath(p)) != _user_site]
sys.path.insert(0, str(BACKEND))
# Test-only caches (remote-code module cache etc.) live under test/; verification must not touch the network.
os.environ["HF_HOME"] = str(TEST_DIR / ".hf_home")
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

OUT_DIR.mkdir(parents=True, exist_ok=True)
_log_fh = LOG_FILE.open("w", encoding="utf-8")


def log(msg: str = "") -> None:
    print(msg, flush=True)
    _log_fh.write(msg + "\n")
    _log_fh.flush()


def load_single_file(name: str, path: Path) -> ModuleType:
    """Import one source file without importing the whole ``trellis2`` package (native deps may be missing)."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, path
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def dir_size(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file() and ".cache" not in p.relative_to(path).parts)


def mb(n: int) -> float:
    return round(n / 1024**2, 1)


# Architecture of facebook/dinov3-vitl16-pretrain-lvd1689m (ViT-L/16, 4 register tokens, RoPE). Used ONLY for the
# structural fallback check when the gated weights are not downloaded (random init, never used by the app).
DINOV3_VITL16_ARCH: dict[str, Any] = {
    "hidden_size": 1024,
    "intermediate_size": 4096,
    "num_hidden_layers": 24,
    "num_attention_heads": 16,
    "patch_size": 16,
    "num_register_tokens": 4,
    "layerscale_value": 1.0,
    "rope_theta": 100.0,
    "query_bias": True,
    "key_bias": False,
    "value_bias": True,
    "proj_bias": True,
    "mlp_bias": True,
    "use_gated_mlp": False,
    "hidden_act": "gelu",
    "layer_norm_eps": 1e-5,
}


# --------------------------------------------------------------------------------------------------


def check_resolve() -> dict[str, Any]:
    from inference.trellis.assets import AuxModelMissingError, resolve_aux_models

    try:
        paths = resolve_aux_models(AUX_ROOT)
    except AuxModelMissingError as exc:
        log(f"resolve_aux_models -> AuxModelMissingError missing={exc.missing}")
        log(exc.message)
        return {"status": "MISSING", "missing": exc.missing, "reasons": exc.reasons}
    log(f"resolve_aux_models -> OK {paths.as_dict()}")
    return {"status": "OK", **paths.as_dict()}


def pick_rembg_dir() -> tuple[Path, str] | None:
    """Same preference as assets.resolve_aux_models, but independent of the other models."""
    for dirname, kind in (("RMBG-2.0", "rmbg2"), ("BiRefNet", "birefnet")):
        d = AUX_ROOT / dirname
        if (d / "config.json").is_file() and (d / "model.safetensors").is_file():
            return d, kind
    return None


def check_dinov3(torch: Any) -> dict[str, Any]:
    from torchvision import transforms
    from transformers import DINOv3ViTConfig, DINOv3ViTModel

    from PIL import Image

    mod = load_single_file("trellis2_image_feature_extractor", TRELLIS_SRC / "trellis2" / "modules" / "image_feature_extractor.py")
    local_dir = AUX_ROOT / "dinov3-vitl16-pretrain-lvd1689m"
    real = (local_dir / "config.json").is_file() and (local_dir / "model.safetensors").is_file()

    t0 = time.perf_counter()
    if real:
        log(f"DINOv3: loading real weights from {local_dir} ({mb(dir_size(local_dir))} MB)")
        extractor = mod.DinoV3FeatureExtractor(str(local_dir))  # DINOv3ViTModel.from_pretrained(local_dir)
        mode = "real_weights"
    else:
        log("DINOv3: gated weights NOT present -> STRUCTURAL check with randomly initialised ViT-L/16 (test double)")
        model = DINOv3ViTModel(DINOv3ViTConfig(**DINOV3_VITL16_ARCH)).eval()
        extractor = mod.DinoV3FeatureExtractor.__new__(mod.DinoV3FeatureExtractor)
        extractor.model_name = "structural-test-double"
        extractor.model = model
        extractor.image_size = 512
        extractor.transform = transforms.Compose(
            [transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])]
        )
        mode = "structural_random_init"
    load_s = time.perf_counter() - t0

    extractor.cuda()
    extractor.image_size = 512
    image = Image.open(SAMPLE_IMAGE).convert("RGB")
    torch.cuda.synchronize()
    t0 = time.perf_counter()
    feats = extractor([image])  # resizes to 512x512, normalises, runs extract_features on CUDA
    torch.cuda.synchronize()
    infer_s = time.perf_counter() - t0
    t0 = time.perf_counter()
    extractor([image])
    torch.cuda.synchronize()
    warm_s = time.perf_counter() - t0

    patches = (512 // 16) ** 2
    expected_n = 1 + DINOV3_VITL16_ARCH["num_register_tokens"] + patches
    shape = list(feats.shape)
    ok_shape = len(shape) == 3 and shape[0] == 1 and shape[2] == 1024
    result = {
        "status": ("OK" if ok_shape else "FAIL") if real else ("BLOCKED_GATED_STRUCTURAL_OK" if ok_shape else "FAIL"),
        "mode": mode,
        "local_dir": str(local_dir),
        "size_mb": mb(dir_size(local_dir)) if local_dir.exists() else 0,
        "output_shape": shape,
        "expected_N": expected_n,
        "N_matches_cls_plus_registers_plus_patches": shape[1] == expected_n if len(shape) == 3 else False,
        "dtype": str(feats.dtype),
        "device": str(feats.device),
        "finite": bool(torch.isfinite(feats).all().item()),
        "mean": float(feats.float().mean().item()),
        "std": float(feats.float().std().item()),
        "load_s": round(load_s, 2),
        "infer_first_call_s": round(infer_s, 3),
        "infer_warm_s": round(warm_s, 3),
        "peak_vram_mb": mb(torch.cuda.max_memory_allocated()),
    }
    extractor.cpu()
    del extractor, feats
    torch.cuda.empty_cache()
    log(f"DINOv3: {json.dumps(result)}")
    return result


def check_rembg(torch: Any) -> dict[str, Any]:
    import numpy as np
    from PIL import Image

    picked = pick_rembg_dir()
    if picked is None:
        return {"status": "MISSING", "message": "neither RMBG-2.0 nor BiRefNet is present"}
    rembg_dir, kind = picked
    log(f"rembg: loading {kind} from {rembg_dir} ({mb(dir_size(rembg_dir))} MB) via TRELLIS.2 BiRefNet wrapper")

    mod = load_single_file("trellis2_rembg_birefnet", TRELLIS_SRC / "trellis2" / "pipelines" / "rembg" / "BiRefNet.py")
    t0 = time.perf_counter()
    wrapper = mod.BiRefNet(model_name=str(rembg_dir))  # AutoModelForImageSegmentation(..., trust_remote_code=True)
    load_s = time.perf_counter() - t0
    wrapper.cuda()
    n_params = sum(p.numel() for p in wrapper.model.parameters())

    src = Image.open(SAMPLE_IMAGE)
    gt_alpha = np.array(src.convert("RGBA"))[:, :, 3] if src.mode in ("RGBA", "LA", "P") else None
    # Simulate a photo without alpha: composite on white, exactly what users upload when remove_background=true.
    white = Image.new("RGBA", src.size, (255, 255, 255, 255))
    rgb = Image.alpha_composite(white, src.convert("RGBA")).convert("RGB")
    # Same pre-resize as Trellis2ImageTo3DPipeline.preprocess_image.
    scale = min(1, 1024 / max(rgb.size))
    if scale < 1:
        rgb = rgb.resize((int(rgb.width * scale), int(rgb.height * scale)), Image.Resampling.LANCZOS)
        if gt_alpha is not None:
            gt_alpha = np.array(Image.fromarray(gt_alpha).resize(rgb.size, Image.Resampling.LANCZOS))

    torch.cuda.reset_peak_memory_stats()
    torch.cuda.synchronize()
    t0 = time.perf_counter()
    out = wrapper(rgb.copy())  # returns RGBA with predicted alpha
    torch.cuda.synchronize()
    infer_s = time.perf_counter() - t0
    t0 = time.perf_counter()
    wrapper(rgb.copy())
    torch.cuda.synchronize()
    warm_s = time.perf_counter() - t0

    cutout_path = OUT_DIR / f"{SAMPLE_IMAGE.stem}_rembg_{kind}.png"
    mask_path = OUT_DIR / f"{SAMPLE_IMAGE.stem}_rembg_mask_{kind}.png"
    out.save(cutout_path)
    pred_alpha = np.array(out)[:, :, 3]
    Image.fromarray(pred_alpha).save(mask_path)

    fg = pred_alpha > 0.8 * 255  # same threshold TRELLIS.2 uses for its bbox crop
    ys, xs = np.nonzero(fg)
    iou = None
    if gt_alpha is not None:
        gt = gt_alpha > 127
        pr = pred_alpha > 127
        union = np.logical_or(gt, pr).sum()
        iou = float(np.logical_and(gt, pr).sum() / union) if union else None

    result = {
        "status": "OK" if out.mode == "RGBA" and fg.any() and (iou is None or iou > 0.9) else "FAIL",
        "rembg_kind": kind,
        "local_dir": str(rembg_dir),
        "size_mb": mb(dir_size(rembg_dir)),
        "model_class": type(wrapper.model).__name__,
        "params_m": round(n_params / 1e6, 1),
        "input_size": list(rgb.size),
        "output_mode": out.mode,
        "foreground_fraction": round(float(fg.mean()), 4),
        "foreground_bbox_xyxy": [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())] if fg.any() else None,
        "iou_vs_ground_truth_alpha": round(iou, 4) if iou is not None else None,
        "load_s": round(load_s, 2),
        "infer_first_call_s": round(infer_s, 3),
        "infer_warm_s": round(warm_s, 3),
        "peak_vram_mb": mb(torch.cuda.max_memory_allocated()),
        "cutout_png": str(cutout_path),
        "mask_png": str(mask_path),
    }
    wrapper.cpu()
    del wrapper
    torch.cuda.empty_cache()
    log(f"rembg: {json.dumps(result)}")
    return result


def check_ss_decoder(torch: Any) -> dict[str, Any]:
    from safetensors import safe_open

    prefix = AUX_ROOT / "TRELLIS-image-large" / "ckpts" / "ss_dec_conv3d_16l8_fp16"
    json_path, st_path = prefix.with_name(prefix.name + ".json"), prefix.with_name(prefix.name + ".safetensors")
    result: dict[str, Any] = {
        "prefix": str(prefix),
        "json_exists": json_path.is_file(),
        "safetensors_exists": st_path.is_file(),
    }
    if not (json_path.is_file() and st_path.is_file()):
        result["status"] = "MISSING"
        log(f"ss_decoder: {json.dumps(result)}")
        return result

    config = json.loads(json_path.read_text(encoding="utf-8"))
    result["json"] = config
    result["json_name_ok"] = config.get("name") == "SparseStructureDecoder"
    result["safetensors_mb"] = mb(st_path.stat().st_size)
    with safe_open(str(st_path), framework="pt", device="cpu") as fh:
        keys = list(fh.keys())
        dtypes = sorted({str(fh.get_slice(k).get_dtype()) for k in keys})
        n_params = sum(int(torch.Size(fh.get_slice(k).get_shape()).numel()) for k in keys)
    result.update({"tensors": len(keys), "dtypes": dtypes, "params_m": round(n_params / 1e6, 2)})

    # Optional full load through trellis2 (needs the vendored package + its native extensions to import).
    try:
        if str(TRELLIS_SRC) not in sys.path:
            sys.path.insert(0, str(TRELLIS_SRC))
        import trellis2.models as tm  # noqa: PLC0415

        decoder = tm.from_pretrained(str(prefix)).cuda().eval()
        with torch.no_grad():
            z = torch.randn(1, config["args"]["latent_channels"], 16, 16, 16, device="cuda")
            occ = decoder(z)
        result["trellis2_decode"] = {"output_shape": list(occ.shape), "dtype": str(occ.dtype)}
        del decoder, occ
        torch.cuda.empty_cache()
    except Exception as exc:  # noqa: BLE001 - optional step, depends on other tracks (native extension builds)
        result["trellis2_decode"] = f"skipped: trellis2 not importable/usable yet ({type(exc).__name__}: {str(exc)[:200]})"

    result["status"] = "OK" if result["json_name_ok"] and len(keys) > 0 else "FAIL"
    log(f"ss_decoder: {json.dumps(result)}")
    return result


def main() -> int:
    import torch

    log(f"python {sys.version.split()[0]} | torch {torch.__version__} | cuda {torch.version.cuda}")
    import transformers

    log(f"transformers {transformers.__version__}")
    if not torch.cuda.is_available():
        log("CUDA not available - aborting")
        return 2
    log(f"GPU {torch.cuda.get_device_name(0)} sm_{''.join(map(str, torch.cuda.get_device_capability(0)))}")

    results: dict[str, Any] = {}
    for name, fn in (
        ("resolve", lambda: check_resolve()),
        ("dinov3", lambda: check_dinov3(torch)),
        ("rembg", lambda: check_rembg(torch)),
        ("ss_decoder", lambda: check_ss_decoder(torch)),
    ):
        log(f"\n=== {name} ===")
        try:
            results[name] = fn()
        except Exception as exc:  # noqa: BLE001 - report every failure with traceback
            tb = traceback.format_exc()
            log(tb)
            results[name] = {"status": "ERROR", "error": f"{type(exc).__name__}: {exc}"}

    (OUT_DIR / "verify_results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    log("\n=== summary ===")
    for name, res in results.items():
        log(f"{name:11s}: {res.get('status')}")
    hard_fail = any(r.get("status") in ("ERROR", "FAIL") for r in results.values())
    return 1 if hard_fail else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        _log_fh.close()
