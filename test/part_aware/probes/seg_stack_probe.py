"""Probe (part-aware milestone): do the approved transformers-native segmenters load and run on the pinned stack?

Grounding DINO (boxes from text) -> SAM 2.1 (masks from boxes), plus Florence-2 referring segmentation, on one image.
Writes overlays + timings to test/outputs/part_aware/probe/. Not a pytest test (needs the GPU + downloaded weights).

Run:  backend\\python\\python.exe -s test\\part_aware\\probes\\seg_stack_probe.py [image]
"""

from __future__ import annotations

import json
import site
import sys
import time
from pathlib import Path

_user_site = site.getusersitepackages()
sys.path[:] = [p for p in sys.path if p != _user_site]

import numpy as np  # noqa: E402
import torch  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
SEG = ROOT / "backend" / ".model" / "seg"
OUT = ROOT / "test" / "outputs" / "part_aware" / "probe"
PROMPTS = ["hair", "eye", "eyebrow", "mouth", "shirt", "skirt", "shoe", "ribbon", "hand"]
COLORS = [(255, 80, 80), (80, 200, 255), (255, 210, 60), (255, 120, 220), (120, 255, 120), (160, 120, 255),
          (255, 160, 60), (60, 255, 200), (200, 200, 200)]


def overlay(img: Image.Image, masks: list[np.ndarray], labels: list[str], boxes: list[list[float]], path: Path) -> None:
    base = np.asarray(img.convert("RGB")).astype(np.float32)
    for i, m in enumerate(masks):
        c = np.array(COLORS[i % len(COLORS)], np.float32)
        base[m] = base[m] * 0.45 + c * 0.55
    out = Image.fromarray(base.clip(0, 255).astype(np.uint8))
    draw = ImageDraw.Draw(out)
    for i, (b, lab) in enumerate(zip(boxes, labels)):
        draw.rectangle(b, outline=COLORS[i % len(COLORS)], width=3)
        draw.text((b[0] + 3, b[1] + 3), lab, fill=COLORS[i % len(COLORS)])
    out.save(path)


def main() -> int:
    image_path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "test" / "images" / "ALICE" / "ALICE_full_front_view.png"
    OUT.mkdir(parents=True, exist_ok=True)
    img = Image.open(image_path).convert("RGB")
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    report: dict = {"image": str(image_path), "size": img.size, "device": dev}

    from transformers import (AutoModelForZeroShotObjectDetection, AutoProcessor, Sam2Model, Sam2Processor)

    t0 = time.perf_counter()
    gd_proc = AutoProcessor.from_pretrained(SEG / "grounding-dino-base", local_files_only=True)
    gd = AutoModelForZeroShotObjectDetection.from_pretrained(SEG / "grounding-dino-base", local_files_only=True).to(dev)
    report["gdino_load_s"] = round(time.perf_counter() - t0, 2)
    text = ". ".join(PROMPTS) + "."
    t0 = time.perf_counter()
    with torch.inference_mode():
        enc = gd_proc(images=img, text=text, return_tensors="pt").to(dev)
        res = gd_proc.post_process_grounded_object_detection(
            gd(**enc), enc.input_ids, threshold=0.25, text_threshold=0.2, target_sizes=[img.size[::-1]])[0]
    report["gdino_run_s"] = round(time.perf_counter() - t0, 2)
    boxes = res["boxes"].float().cpu().numpy().tolist()
    labels = [str(x) for x in res.get("text_labels", res.get("labels"))]
    scores = res["scores"].float().cpu().numpy().round(3).tolist()
    report["gdino"] = [{"label": l, "score": s, "box": [round(v, 1) for v in b]} for l, s, b in zip(labels, scores, boxes)]
    del gd
    torch.cuda.empty_cache()

    t0 = time.perf_counter()
    sam_proc = Sam2Processor.from_pretrained(SEG / "sam2.1-hiera-large", local_files_only=True)
    sam = Sam2Model.from_pretrained(SEG / "sam2.1-hiera-large", local_files_only=True).to(dev)
    report["sam2_load_s"] = round(time.perf_counter() - t0, 2)
    masks: list[np.ndarray] = []
    if boxes:
        t0 = time.perf_counter()
        with torch.inference_mode():
            enc = sam_proc(images=img, input_boxes=[boxes], return_tensors="pt").to(dev)
            out = sam(**enc, multimask_output=False)
            post = sam_proc.post_process_masks(out.pred_masks.cpu(), enc["original_sizes"].cpu())[0]
        report["sam2_run_s"] = round(time.perf_counter() - t0, 2)
        masks = [m.squeeze().numpy().astype(bool) for m in post]
        report["sam2_mask_px"] = [int(m.sum()) for m in masks]
    del sam
    torch.cuda.empty_cache()
    overlay(img, masks, labels, boxes, OUT / "gdino_sam2_overlay.png")

    from transformers import Florence2ForConditionalGeneration

    t0 = time.perf_counter()
    fl_proc = AutoProcessor.from_pretrained(SEG / "Florence-2-large", local_files_only=True)
    fl = Florence2ForConditionalGeneration.from_pretrained(SEG / "Florence-2-large", local_files_only=True,
                                                           torch_dtype=torch.float16).to(dev)
    report["florence_load_s"] = round(time.perf_counter() - t0, 2)
    task = "<REFERRING_EXPRESSION_SEGMENTATION>"
    fl_masks, fl_labels, fl_boxes = [], [], []
    t0 = time.perf_counter()
    for phrase in ("hair", "eyes"):
        with torch.inference_mode():
            enc = fl_proc(text=task + phrase, images=img, return_tensors="pt").to(dev, torch.float16)
            ids = fl.generate(**enc, max_new_tokens=1024, num_beams=3, do_sample=False)
        decoded = fl_proc.batch_decode(ids, skip_special_tokens=False)[0]
        parsed = fl_proc.post_process_generation(decoded, task=task, image_size=img.size)
        polys = parsed[task].get("polygons") or []
        mask = Image.new("L", img.size, 0)
        draw = ImageDraw.Draw(mask)
        for poly_group in polys:
            for poly in poly_group:
                if len(poly) >= 6:
                    draw.polygon(list(zip(poly[0::2], poly[1::2])), fill=255)
        m = np.asarray(mask) > 0
        fl_masks.append(m)
        fl_labels.append(phrase)
        ys, xs = np.nonzero(m)
        fl_boxes.append([float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max())] if len(xs) else [0, 0, 0, 0])
    report["florence_run_s"] = round(time.perf_counter() - t0, 2)
    report["florence_mask_px"] = {l: int(m.sum()) for l, m in zip(fl_labels, fl_masks)}
    overlay(img, fl_masks, fl_labels, fl_boxes, OUT / "florence_overlay.png")
    report["peak_vram_gb"] = round(torch.cuda.max_memory_allocated() / 1e9, 2) if dev == "cuda" else None
    (OUT / "seg_stack_probe.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
