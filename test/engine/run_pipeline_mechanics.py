"""Pipeline *mechanics* run with a DINOv3 TEST DOUBLE (use only while the gated DINOv3 weights are unavailable).

Everything is real except the image-conditioning encoder: the engine is loaded through ``TrellisEngine.load()``
(real ``prepare_runtime``, real TRELLIS.2-4B weights, real background-removal model, SDPA fallback inside the real
DiTs, flex_gemm/Triton sparse convs, decoders, cumesh, o_voxel GLB export), but ``DinoV3FeatureExtractor`` is
replaced by :class:`FakeDinoV3` which returns deterministic pseudo-features of the right shape. The produced
geometry is therefore NOT meaningful — this script validates the plumbing, memory behaviour, progress, timings,
cancellation and cleanup. Outputs go to ``test/outputs/engine_mechanics/``.

Usage::

    E:\\14-OCT-2026\\Img3D\\backend\\python\\python.exe E:\\14-OCT-2026\\Img3D\\test\\engine\\run_pipeline_mechanics.py
        [--resolution 512] [--texture-size 1024] [--polygons 100000] [--cancel-at shape] [--no-low-vram]
"""

from __future__ import annotations

import os
import site
import sys

_USER_SITE = os.path.normcase(os.path.abspath(site.getusersitepackages()))
sys.path[:] = [p for p in sys.path if os.path.normcase(os.path.abspath(p)) != _USER_SITE]

import argparse  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = PROJECT_ROOT / "backend"
ENGINE_TEST_DIR = Path(__file__).resolve().parent
for _p in (BACKEND_DIR, ENGINE_TEST_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import run_generation_smoke as smoke  # noqa: E402  (reuses reporter / device sampler / GLB validation)

OUTPUT_ROOT = PROJECT_ROOT / "test" / "outputs" / "engine_mechanics"
log = logging.getLogger("engine_mechanics")


class FakeDinoV3:
    """Test double with the ``DinoV3FeatureExtractor`` interface (``image_size``, ``to/cuda/cpu``, ``__call__``).

    Returns layer-normed pseudo-random ``[B, 5 + (size/16)^2, 1024]`` features seeded from the image pixels.
    """

    def __init__(self, model_name: str, image_size: int = 512) -> None:
        self.model_name = model_name
        self.image_size = image_size

    def to(self, device: Any) -> None:
        return None

    def cuda(self) -> None:
        return None

    def cpu(self) -> None:
        return None

    def __call__(self, image: Any) -> Any:
        import numpy as np
        import torch

        feats = []
        for img in image:
            arr = np.asarray(img.convert("RGB").resize((64, 64)))
            seed = int.from_bytes(hashlib.sha256(arr.tobytes()).digest()[:4], "little") + self.image_size
            generator = torch.Generator(device="cpu").manual_seed(seed)
            tokens = 5 + (self.image_size // 16) ** 2
            feats.append(torch.randn(tokens, 1024, generator=generator))
        out = torch.stack(feats).cuda()
        return torch.nn.functional.layer_norm(out, out.shape[-1:])


def install_test_doubles() -> None:
    """Swap DINOv3 for the double and resolve aux paths without DINOv3 (test-process only)."""
    from inference.trellis import loader as loader_module

    aux_root = BACKEND_DIR / ".model"
    rmbg = aux_root / "RMBG-2.0"
    rembg_dir, kind = (rmbg, "rmbg2") if (rmbg / "model.safetensors").is_file() else (aux_root / "BiRefNet", "birefnet")

    def resolve_aux(self: Any) -> Any:
        # Imported lazily (like the engine does): assets imports huggingface_hub, which must only happen after
        # prepare_runtime() has pointed HF_HOME at settings.hf_home.
        from inference.trellis.assets import AuxModelPaths

        return AuxModelPaths(
            dinov3_dir=aux_root / "dinov3-vitl16-pretrain-lvd1689m",
            rembg_dir=rembg_dir,
            rembg_kind=kind,
            ss_decoder_prefix=aux_root / "TRELLIS-image-large" / "ckpts" / "ss_dec_conv3d_16l8_fp16",
        )

    loader_module.TrellisPipelineLoader.resolve_aux = resolve_aux  # type: ignore[method-assign]

    original_load = loader_module.TrellisPipelineLoader.load

    def load_with_fake_dino(self: Any, plan: Any = None) -> Any:
        from trellis2.modules import image_feature_extractor

        image_feature_extractor.DinoV3FeatureExtractor = FakeDinoV3  # type: ignore[attr-defined]
        return original_load(self, plan)

    loader_module.TrellisPipelineLoader.load = load_with_fake_dino  # type: ignore[method-assign]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--resolution", type=int, default=512, choices=(512, 1024, 1536))
    parser.add_argument("--texture-size", type=int, default=1024)
    parser.add_argument("--polygons", type=int, default=100_000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--force-rmbg", action="store_true")
    parser.add_argument("--no-low-vram", action="store_true")
    parser.add_argument("--cancel-at", default=None)
    parser.add_argument("--repeat", type=int, default=1, help="generate N times (checks memory is released)")
    args = parser.parse_args()
    args.image = smoke.DEFAULT_IMAGE
    args.attention = None

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    smoke.OUTPUT_ROOT = OUTPUT_ROOT
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    install_test_doubles()

    from core.exceptions import AppError, JobCancelledError
    from inference.base import CancellationToken, ConditioningImage, GenerationParams, GenerationRequest
    from inference.trellis.engine import TrellisEngine
    from models.enums import EngineState, ReferenceSlot

    engine = TrellisEngine(smoke.build_settings(args))
    started = time.perf_counter()
    engine.load(on_status=lambda s: print(f"[load] {s.state}: {s.message}", flush=True))
    print(f"load took {time.perf_counter() - started:.1f}s", flush=True)
    status = engine.status()
    if status.state is not EngineState.READY:
        print(f"ENGINE NOT READY ({status.state}):\n{status.error}")
        return 2

    results: list[dict[str, Any]] = []
    exit_code = 0
    for attempt in range(args.repeat):
        job_id, job_dir, image_path = smoke.prepare_job(args)
        image = ConditioningImage("img_smoke0000001", ReferenceSlot.FRONT, image_path, True)
        request = GenerationRequest(
            job_id=job_id, images=[image], all_references=[image], output_dir=job_dir,
            params=GenerationParams(
                resolution=args.resolution, seed=args.seed + attempt, polygon_target=args.polygons,
                texture_size=args.texture_size, remove_background=True,
            ),
        )
        cancel = CancellationToken()
        reporter = smoke.PrintingReporter(cancel, args.cancel_at)
        sampler = smoke.DeviceSampler(engine, interval=0.5)
        sampler.start()
        record: dict[str, Any] = {"job_id": job_id, "attempt": attempt}
        try:
            output = engine.generate(request, reporter, cancel)
            record["outcome"] = "completed"
            record["timings"] = output.timings
            record["mesh_stats"] = output.mesh_stats
            record["metadata"] = output.metadata
            record["glb_check"] = smoke.validate_glb(output.glb_path)
        except JobCancelledError as exc:
            record["outcome"] = f"cancelled: {exc.message}"
        except AppError as exc:
            record["outcome"] = {"failed": exc.to_dict()}
            exit_code = 3
        finally:
            sampler.stop()
        used = [s["vram_used_mb"] for s in sampler.samples if s.get("vram_used_mb") is not None]
        record["peak_vram_used_mb"] = max(used, default=None)
        record["device_sampler"] = {
            "samples": len(sampler.samples), "errors": sampler.errors,
            "max_latency_ms": max((s["latency_ms"] for s in sampler.samples), default=None),
        }
        record["device_after"] = engine.device_info()
        record["progress_events"] = reporter.events
        (job_dir / "summary.json").write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
        print(json.dumps({k: v for k, v in record.items() if k != "progress_events"}, indent=2, default=str), flush=True)
        results.append(record)

    engine.unload()
    print("after unload:", json.dumps(engine.device_info()), str(engine.status().state), flush=True)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
