"""Probe (part-aware milestone): does Z-Image-Turbo load from local files with diffusers 0.39 + transformers 4.57.6
and produce a TRELLIS-friendly character image? Writes the image + timings to test/outputs/part_aware/probe/.

Run:  backend\\python\\python.exe -s test\\part_aware\\probes\\t2i_probe.py ["prompt"]
"""

from __future__ import annotations

import json
import site
import sys
import time
from pathlib import Path

_user_site = site.getusersitepackages()
sys.path[:] = [p for p in sys.path if p != _user_site]

import torch  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
MODEL = ROOT / "backend" / ".model" / "t2i" / "Z-Image-Turbo"
OUT = ROOT / "test" / "outputs" / "part_aware" / "probe"
TEMPLATE = ("{subject}. Single character, full body from head to toe with margin, standing in a neutral A-pose with arms "
            "slightly away from the torso, front view, eye-level camera, centered, plain seamless light gray background, "
            "no floor, no cast shadows, soft even studio lighting, sharp focus, high detail")


def main() -> int:
    subject = sys.argv[1] if len(sys.argv) > 1 else "an anime girl knight with long silver hair, blue ribbon, white and gold armored dress, thigh-high boots"
    OUT.mkdir(parents=True, exist_ok=True)
    from diffusers import ZImagePipeline

    report: dict = {"subject": subject}
    t0 = time.perf_counter()
    pipe = ZImagePipeline.from_pretrained(MODEL, torch_dtype=torch.bfloat16, local_files_only=True)
    report["load_s"] = round(time.perf_counter() - t0, 2)
    t0 = time.perf_counter()
    pipe.to("cuda")
    report["to_cuda_s"] = round(time.perf_counter() - t0, 2)
    prompt = TEMPLATE.format(subject=subject)
    for run in range(2):  # first call includes warm-up
        t0 = time.perf_counter()
        image = pipe(prompt=prompt, height=1152, width=896, num_inference_steps=9, guidance_scale=0.0,
                     generator=torch.Generator("cuda").manual_seed(42)).images[0]
        report[f"generate_s_{run}"] = round(time.perf_counter() - t0, 2)
    image.save(OUT / "t2i_zimage_turbo.png")
    report["peak_vram_gb"] = round(torch.cuda.max_memory_allocated() / 1e9, 2)
    pipe.to("cpu")
    torch.cuda.empty_cache()
    report["vram_after_offload_gb"] = round(torch.cuda.memory_allocated() / 1e9, 2)
    (OUT / "t2i_probe.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
