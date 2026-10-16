"""Real-weight smoke test of the Z-Image-Turbo provider (slice S4; part-aware DESIGN.md sections 4.4.4 and 10.2).

Opt-in (it loads 32.85 GB of weights and peaks at ~23 GB of VRAM), and ALWAYS under the GPU lock::

    $env:IMG3D_GPU_SMOKE = "1"
    backend\\python\\python.exe -s test\\part_aware\\gpu_lock.py --label s4-zimage -- ^
        backend\\python\\python.exe -s -m pytest -c test\\pytest.ini test\\aux_models\\test_smoke_zimage.py -q -s

What it checks, exactly like the run executor uses the provider:

1. ``load()`` on a CPU thread (never CUDA): the weights are read fully into RAM;
2. ``generate()`` on another thread inside ``torch_globals_guard()``: the first ``.to("cuda")`` after the CPU load is
   timed (target < 30 s, logged and recorded; DESIGN.md asks to report the number, not to fail on it), one 512x512
   image at 2 steps has real (non-constant, finite) pixels and < 1 GB stays allocated after the offload;
3. one 896x1152, 9-step image from the character template (warm timing + readiness of a real candidate).

Outputs: ``test/outputs/part_aware/s4_zimage_smoke.json`` (+ the two PNGs) and the ``[torch_guard] restored ...``
records under key ``s4_zimage`` of ``test/outputs/part_aware/torch_guard.json`` (informational).
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import json
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image

TEST_ROOT = Path(__file__).resolve().parents[1]
BACKEND = TEST_ROOT.parent / "backend"
MODEL_DIR = BACKEND / ".model" / "t2i" / "Z-Image-Turbo"
OUT_DIR = TEST_ROOT / "outputs" / "part_aware"
RUNTIME = TEST_ROOT / ".runtime" / "s4"
PROMPT = "an anime girl knight with long silver hair, blue ribbon, white and gold armored dress, thigh-high boots"
TO_CUDA_TARGET_S = 30.0
GB = 1024**3

pytestmark = [
    pytest.mark.gpu,
    pytest.mark.slow,
    pytest.mark.skipif(os.environ.get("IMG3D_GPU_SMOKE") != "1", reason="opt-in: set IMG3D_GPU_SMOKE=1 (GPU lock!)"),
    pytest.mark.skipif(not (MODEL_DIR / "model_index.json").is_file(), reason="Z-Image-Turbo weights not present"),
]


def _guard() -> Any:
    try:
        from inference.providers.vram import torch_globals_guard
    except ImportError:  # registry slice not merged: run unguarded (informational only)
        return contextlib.nullcontext()
    return torch_globals_guard()


def _record_torch_guard(records: list[logging.LogRecord]) -> None:
    path = OUT_DIR / "torch_guard.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError):
        data = {}
    data["s4_zimage"] = [
        {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(r.created)), "message": r.getMessage()}
        for r in records
        if "[torch_guard]" in r.getMessage()
    ]
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")


def test_zimage_load_on_cpu_then_generate_on_gpu(caplog: pytest.LogCaptureFixture) -> None:
    import torch

    if not torch.cuda.is_available():
        pytest.skip("CUDA is not available")
    from config.settings import Settings
    from inference.base import CancellationToken
    from inference.imagegen.base import ImageGenRequest
    from inference.imagegen.zimage import ZImageTurboProvider
    from inference.providers.catalog import CATALOG
    from pipelines.prompt_templates import build_prompt
    from processing.imagegen_readiness import assess_readiness

    spec = next(s for s in CATALOG if s.id == "z_image_turbo")
    settings = Settings(_env_file=None, runtime_dir=RUNTIME, preload_model=False)
    provider = ZImageTurboProvider(spec, settings, model_dir=MODEL_DIR)
    out_dir = RUNTIME / "generated"
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cancel = CancellationToken()
    report: dict[str, Any] = {"model_dir": str(MODEL_DIR.relative_to(BACKEND)), "device": settings.device}
    progress_calls: list[float] = []

    cpu = concurrent.futures.ThreadPoolExecutor(1, thread_name_prefix="parts-cpu")
    gpu = concurrent.futures.ThreadPoolExecutor(1, thread_name_prefix="gpu-worker")
    try:
        # 1. CPU load (must not touch CUDA)
        cuda_before = torch.cuda.memory_allocated()
        started = time.perf_counter()
        cpu.submit(provider.load, cancel).result()
        report["load_s"] = round(time.perf_counter() - started, 2)
        report["load_breakdown"] = dict(provider.last_load_timings)
        assert provider.status().state == "loaded" and provider.status().loaded_device == "cpu"
        assert torch.cuda.memory_allocated() == cuda_before, "load() allocated CUDA memory"

        def generate(width: int, height: int, steps: int, prompt: str, seed: int, sub: str) -> Any:
            target = out_dir / sub
            target.mkdir(parents=True, exist_ok=True)
            request = ImageGenRequest(
                run_id="ig_000000000000",
                prompt=prompt,
                negative_prompt=None,
                width=width,
                height=height,
                steps=steps,
                guidance=0.0,
                seeds=(seed,),
                output_dir=target,
                device=settings.device,
            )
            with _guard():
                return provider.generate(request, lambda f, m=None: progress_calls.append(f), cancel)

        # 2. first GPU transfer after the CPU load + one small image
        with caplog.at_level(logging.INFO, logger="inference.providers.vram"):
            small = gpu.submit(generate, 512, 512, 2, "a red apple on a table", 42, "small").result()
            report["first_to_cuda_s"] = small.timings["to_device_s"]
            report["small"] = {"timings": small.timings, "metadata": small.metadata}
            report["allocated_after_offload_gb"] = round(torch.cuda.memory_allocated() / GB, 3)

            # 3. a real character candidate (warm)
            prompt = build_prompt(PROMPT, "character", "anime", True)
            full = gpu.submit(generate, 896, 1152, 9, prompt, 987654, "character").result()
            report["character"] = {"timings": full.timings, "metadata": full.metadata}
        _record_torch_guard(caplog.records)
    finally:
        cpu.shutdown(wait=True)
        gpu.shutdown(wait=True)
        provider.unload()

    small_image = np.asarray(Image.open(small.paths[0]).convert("RGB"), dtype=np.float32)
    assert small_image.shape == (512, 512, 3)
    assert np.isfinite(small_image).all() and float(small_image.std()) > 5.0, "the image is blank"
    assert report["allocated_after_offload_gb"] < 1.0
    assert progress_calls and max(progress_calls) == pytest.approx(1.0)

    character = Image.open(full.paths[0])
    assert character.size == (896, 1152)
    readiness = assess_readiness(full.paths[0], preset="character")
    report["character"]["readiness"] = {"ok": readiness.ok, "issues": list(readiness.issues), **readiness.metrics}
    shutil.copyfile(small.paths[0], OUT_DIR / "s4_zimage_smoke_512.png")
    shutil.copyfile(full.paths[0], OUT_DIR / "s4_zimage_smoke_character.png")
    report["first_to_cuda_target_s"] = TO_CUDA_TARGET_S
    report["first_to_cuda_within_target"] = report["first_to_cuda_s"] < TO_CUDA_TARGET_S
    report_path = OUT_DIR / "s4_zimage_smoke.json"
    if report_path.is_file():  # keep the previous measurement for comparison
        report_path.replace(OUT_DIR / "s4_zimage_smoke.previous.json")
    report_path.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(json.dumps(report, indent=1))
    if not report["first_to_cuda_within_target"]:
        logging.getLogger(__name__).warning(
            "First .to('cuda') after the CPU load took %.1f s (target < %.0f s)",
            report["first_to_cuda_s"],
            TO_CUDA_TARGET_S,
        )
