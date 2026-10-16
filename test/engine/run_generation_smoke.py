"""End-to-end smoke test of the TRELLIS.2 engine (real weights, real GPU, no mocks).

Loads the engine exactly like the backend does (``TrellisEngine(settings).load()``), generates a model from
``test/images/ALICE/ALICE_full_front_view.png`` at 512 (texture 1024, 100k polygons) and writes everything to
``test/outputs/engine_smoke/``::

    <out>/<job_id>/inputs/<image_id>.png   input copy (as the job store does)
    <out>/<job_id>/preview.png             processed conditioning image
    <out>/<job_id>/model.glb               exported GLB
    <out>/<job_id>/summary.json            timings, stats, progress log, device samples

Usage (embedded interpreter only)::

    E:\\14-OCT-2026\\Img3D\\backend\\python\\python.exe E:\\14-OCT-2026\\Img3D\\test\\engine\\run_generation_smoke.py
        [--resolution 512|1024|1536] [--texture-size 1024] [--polygons 100000] [--seed 42]
        [--image PATH] [--force-rmbg] [--no-low-vram] [--cancel-at STAGE]

Exit codes: 0 success, 2 engine not ready, 3 generation failed, 4 output validation failed.
"""

from __future__ import annotations

import os
import site
import sys

# Embedded-interpreter bootstrap: drop the per-user site-packages (it shadows backend\python\Lib\site-packages).
_USER_SITE = os.path.normcase(os.path.abspath(site.getusersitepackages()))
sys.path[:] = [p for p in sys.path if os.path.normcase(os.path.abspath(p)) != _USER_SITE]

import argparse  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
import shutil  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from typing import Any  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = PROJECT_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from core.exceptions import AppError, JobCancelledError  # noqa: E402
from inference.base import (  # noqa: E402
    CancellationToken,
    ConditioningImage,
    GenerationParams,
    GenerationRequest,
)
from inference.trellis.engine import TrellisEngine  # noqa: E402
from models.enums import EngineState, JobStage, ReferenceSlot  # noqa: E402

DEFAULT_IMAGE = PROJECT_ROOT / "test" / "images" / "ALICE" / "ALICE_full_front_view.png"
OUTPUT_ROOT = PROJECT_ROOT / "test" / "outputs" / "engine_smoke"

log = logging.getLogger("engine_smoke")


def build_settings(args: argparse.Namespace) -> Any:
    """Real ``config.settings.Settings`` (reads backend/.env) with test-local runtime dir; stand-in if missing."""
    overrides: dict[str, Any] = {"runtime_dir": OUTPUT_ROOT / "runtime", "preload_model": False}
    if args.no_low_vram:
        overrides["low_vram"] = False
    if args.attention:
        overrides["attention_backend"] = args.attention
    try:
        from config.settings import Settings
    except ImportError as exc:  # backend-core track not finished: minimal stand-in with contract defaults
        log.warning("config.settings unavailable (%s); using a stand-in settings object", exc)
        return SimpleNamespace(
            model_dir=BACKEND_DIR / ".model" / "TRELLIS.2-4B",
            aux_model_root=BACKEND_DIR / ".model",
            trellis_source_dir=BACKEND_DIR / "third_party" / "TRELLIS.2",
            hf_home=BACKEND_DIR / ".model" / ".hf_cache",
            hf_token=None,
            allow_model_downloads=False,
            device="cuda",
            low_vram=overrides.get("low_vram", True),
            attention_backend=overrides.get("attention_backend", "auto"),
            runtime_dir=overrides["runtime_dir"],
        )
    return Settings(**overrides)


class PrintingReporter:
    """ProgressReporter that prints and records every event (thread-safe)."""

    def __init__(self, cancel: CancellationToken, cancel_at: str | None) -> None:
        self._lock = threading.Lock()
        self._started = time.perf_counter()
        self._cancel = cancel
        self._cancel_at = cancel_at
        self.events: list[dict[str, Any]] = []
        self.stage_name: str | None = None

    def _record(self, event: dict[str, Any]) -> None:
        event["t"] = round(time.perf_counter() - self._started, 3)
        with self._lock:
            self.events.append(event)

    def stage(self, stage: JobStage, message: str | None = None, total_steps: int | None = None) -> None:
        self.stage_name = str(stage)
        self._record({"type": "stage", "stage": str(stage), "message": message, "total_steps": total_steps})
        print(f"[{time.perf_counter() - self._started:7.2f}s] == {stage} == {message or ''}"
              + (f" ({total_steps} steps)" if total_steps else ""), flush=True)
        if self._cancel_at and str(stage) == self._cancel_at:
            print(f"           -> requesting cancellation at stage {stage}", flush=True)
            self._cancel.cancel(f"smoke test cancel at {stage}")

    def step(self, step: int, total_steps: int | None = None, message: str | None = None) -> None:
        self._record({"type": "step", "stage": self.stage_name, "step": step, "total_steps": total_steps, "message": message})
        print(f"[{time.perf_counter() - self._started:7.2f}s]    {step}/{total_steps or '?'} {message or ''}", flush=True)


class DeviceSampler(threading.Thread):
    """Polls engine.device_info() from another thread while generating (thread-safety check + VRAM peak)."""

    def __init__(self, engine: TrellisEngine, interval: float = 1.0) -> None:
        super().__init__(name="device-sampler", daemon=True)
        self._engine = engine
        self._interval = interval
        self._stop = threading.Event()
        self.samples: list[dict[str, Any]] = []
        self.errors: list[str] = []

    def run(self) -> None:
        while not self._stop.is_set():
            started = time.perf_counter()
            try:
                info = self._engine.device_info()
                self.samples.append({"latency_ms": round((time.perf_counter() - started) * 1000, 2), **(info or {})})
            except Exception as exc:  # noqa: BLE001
                self.errors.append(repr(exc))
            self._stop.wait(self._interval)

    def stop(self) -> None:
        self._stop.set()
        self.join(timeout=5)


def prepare_job(args: argparse.Namespace) -> tuple[str, Path, Path]:
    job_id = f"job_smoke{time.strftime('%H%M%S')}"
    job_dir = OUTPUT_ROOT / job_id
    inputs = job_dir / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    image_id = "img_smoke0000001"
    target = inputs / f"{image_id}.png"
    if args.force_rmbg:
        # Flatten onto white so the background-removal model has to run (exercises RMBG/BiRefNet).
        from PIL import Image

        with Image.open(args.image) as src:
            rgba = src.convert("RGBA")
            flat = Image.new("RGB", rgba.size, (255, 255, 255))
            flat.paste(rgba, mask=rgba.getchannel("A"))
            flat.save(target)
    else:
        shutil.copyfile(args.image, target)
    return job_id, job_dir, target


def validate_glb(path: Path) -> dict[str, Any]:
    import trimesh

    scene = trimesh.load(path, force="scene")
    meshes = list(scene.geometry.values())
    vertices = sum(len(m.vertices) for m in meshes)
    faces = sum(len(m.faces) for m in meshes)
    has_uv = all(getattr(m.visual, "uv", None) is not None for m in meshes)
    bounds = scene.bounds.tolist() if scene.bounds is not None else None
    return {"meshes": len(meshes), "vertices": vertices, "faces": faces, "has_uv": has_uv, "bounds": bounds}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--image", type=Path, default=DEFAULT_IMAGE)
    parser.add_argument("--resolution", type=int, default=512, choices=(512, 1024, 1536))
    parser.add_argument("--texture-size", type=int, default=1024, choices=(512, 1024, 2048, 4096))
    parser.add_argument("--polygons", type=int, default=100_000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--force-rmbg", action="store_true", help="flatten the input onto white to force RMBG")
    parser.add_argument("--no-low-vram", action="store_true")
    parser.add_argument("--attention", choices=("auto", "flash_attn", "xformers", "sdpa"))
    parser.add_argument("--cancel-at", choices=[str(s) for s in JobStage], help="cancel when this stage starts")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

    settings = build_settings(args)
    engine = TrellisEngine(settings)
    print("device (before load):", json.dumps(engine.device_info()), flush=True)

    load_started = time.perf_counter()
    engine.load(on_status=lambda s: print(f"[load] {s.state}: {s.message}", flush=True))
    load_seconds = time.perf_counter() - load_started
    status = engine.status()
    print(json.dumps(status.to_dict(), indent=2, default=str), flush=True)
    if status.state is not EngineState.READY:
        print(f"ENGINE NOT READY ({status.state}):\n{status.error}", flush=True)
        return 2

    job_id, job_dir, image_path = prepare_job(args)
    image = ConditioningImage(image_id="img_smoke0000001", slot=ReferenceSlot.FRONT, path=image_path, is_primary=True)
    params = GenerationParams(
        resolution=args.resolution,
        seed=args.seed,
        polygon_target=args.polygons,
        texture_size=args.texture_size,
        remove_background=True,
        glb_webp_textures=bool(getattr(settings, "glb_webp_textures", True)),
    )
    request = GenerationRequest(job_id=job_id, images=[image], all_references=[image], params=params, output_dir=job_dir)
    cancel = CancellationToken()
    reporter = PrintingReporter(cancel, args.cancel_at)
    sampler = DeviceSampler(engine)
    sampler.start()

    summary: dict[str, Any] = {
        "job_id": job_id,
        "load_seconds": round(load_seconds, 1),
        "engine_status": status.to_dict(),
        "params": {
            "resolution": args.resolution, "texture_size": args.texture_size, "polygons": args.polygons,
            "seed": args.seed, "force_rmbg": args.force_rmbg,
        },
    }
    exit_code = 0
    try:
        output = engine.generate(request, reporter, cancel)
    except JobCancelledError as exc:
        print(f"CANCELLED: {exc.message}", flush=True)
        summary["outcome"] = "cancelled"
        exit_code = 0 if args.cancel_at else 3
    except AppError as exc:
        print(f"GENERATION FAILED [{exc.code}]: {exc.message}\n{exc.details}", flush=True)
        summary["outcome"] = {"failed": exc.to_dict()}
        exit_code = 3
    else:
        summary["outcome"] = "completed"
        summary["output"] = {
            "glb_path": str(output.glb_path),
            "preview_path": str(output.preview_path),
            "effective_resolution": output.effective_resolution,
            "timings": output.timings,
            "mesh_stats": output.mesh_stats,
            "metadata": output.metadata,
        }
        try:
            summary["glb_check"] = validate_glb(output.glb_path)
            assert output.glb_path.is_file() and output.glb_path.stat().st_size > 0
            assert output.preview_path is not None and output.preview_path.is_file()
            assert summary["glb_check"]["faces"] > 0 and summary["glb_check"]["has_uv"]
        except Exception as exc:  # noqa: BLE001
            print(f"OUTPUT VALIDATION FAILED: {exc!r}", flush=True)
            exit_code = 4
        print(json.dumps(summary["output"]["timings"], indent=2), flush=True)
        print(json.dumps(summary.get("glb_check"), indent=2), flush=True)
    finally:
        sampler.stop()

    summary["progress_events"] = reporter.events
    used = [s.get("vram_used_mb") for s in sampler.samples if s.get("vram_used_mb") is not None]
    summary["device_samples"] = {
        "count": len(sampler.samples),
        "errors": sampler.errors,
        "max_latency_ms": max((s["latency_ms"] for s in sampler.samples), default=None),
        "peak_vram_used_mb": max(used, default=None),
    }
    summary["device_after_generate"] = engine.device_info()
    engine.unload()
    summary["device_after_unload"] = engine.device_info()
    summary["engine_state_after_unload"] = str(engine.status().state)
    (job_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(f"summary: {job_dir / 'summary.json'}", flush=True)
    print(f"device samples: {summary['device_samples']}", flush=True)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
