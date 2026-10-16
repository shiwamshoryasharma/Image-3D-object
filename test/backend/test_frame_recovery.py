"""Conditioning frame prediction and recovery (part-aware DESIGN.md section 4.5.2), CPU only.

The bit-exact parity with upstream ``preprocess_image`` is pinned by ``test/engine/test_prepared_conditioning.py``;
here the replica is exercised against job-like previews built the way the engine builds ``preview.png``.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from processing import conditioning as cond


def _subject(size: tuple[int, int], box: tuple[int, int, int, int], *, alpha: bool, seed: int = 0) -> Image.Image:
    """A textured subject (deterministic noise) inside ``box``; transparent outside when ``alpha``."""
    rng = np.random.default_rng(seed)
    w, h = size
    rgb = (rng.random((h, w, 3)) * 60 + 100).astype(np.uint8)
    x0, y0, x1, y1 = box
    rgb[y0:y1, x0:x1] = (rng.random((y1 - y0, x1 - x0, 3)) * 255).astype(np.uint8)
    if not alpha:
        return Image.fromarray(rgb, "RGB")
    a = np.zeros((h, w), dtype=np.uint8)
    matte = Image.new("L", size, 0)
    ImageDraw.Draw(matte).ellipse(box, fill=255)
    a = np.asarray(matte)
    return Image.fromarray(np.dstack([rgb, a]), "RGBA")


def _save(img: Image.Image, path: Path) -> Path:
    img.save(path)
    return path


def _engine_preview(processed: Image.Image) -> Image.Image:
    """``inference/trellis/imaging.make_preview``: RGB, thumbnail to 1024."""
    preview = processed.convert("RGB")
    if max(preview.size) > 1024:
        preview.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
    return preview


@pytest.mark.parametrize(
    ("size", "box"),
    [
        ((301, 257), (40, 30, 260, 240)),  # odd sizes
        ((400, 300), (0, 10, 220, 300)),  # bbox at the left and bottom border
        ((1500, 2100), (200, 300, 1300, 2000)),  # larger than 1024: downscaled first
    ],
)
def test_real_alpha_frame_is_predicted_and_recomputed_exactly(tmp_runtime: Path, size, box):
    source = _save(_subject(size, box, alpha=True), tmp_runtime / "source.png")
    predicted = cond.predict_frame(source, remove_background=True)
    assert predicted is not None
    assert predicted.method == "recompute" and predicted.verified and predicted.residual == 0.0
    assert predicted.transform.mode == "alpha_crop"

    image = cond.load_input(source)
    resized, scale = cond.upstream_resize(image)
    conditioning, _crop, transform = cond.crop_from_alpha(resized, source_size=image.size, scale=scale)
    assert transform == predicted.transform
    assert max(resized.size) <= 1024 and scale == min(1, 1024 / max(size))
    preview = _save(_engine_preview(conditioning), tmp_runtime / "preview.png")
    recovered = cond.recover_frame(source, preview, "alpha_crop", tmp_runtime)
    assert recovered.method == "recompute" and recovered.verified and recovered.residual == 0.0
    assert recovered.transform == predicted.transform
    width = transform.crop_box[2] - transform.crop_box[0]
    assert transform.size == (width, width) and width % 2 == 0


def test_template_match_recovers_a_background_removed_crop(tmp_runtime: Path):
    size, box = (900, 700), (180, 90, 640, 650)
    opaque = _subject(size, box, alpha=False, seed=3)
    source = _save(opaque, tmp_runtime / "source.png")
    # Stand-in for BiRefNet: a known matte on the resized image, then upstream's crop + premultiply.
    resized, scale = cond.upstream_resize(opaque)
    matte = Image.new("L", resized.size, 0)
    ImageDraw.Draw(matte).ellipse(tuple(int(v * scale) for v in box), fill=255)
    rgba = resized.copy()
    rgba.putalpha(matte)
    conditioning, _crop, truth = cond.crop_from_alpha(rgba, source_size=size, scale=scale, mode="background_removed")
    preview = _save(_engine_preview(conditioning), tmp_runtime / "preview.png")

    frame = cond.recover_frame(source, preview, "background_removed", tmp_runtime)
    assert frame.method == "template_match"
    assert frame.verified and frame.residual < cond.TEMPLATE_TOLERANCE
    assert frame.transform.crop_box == truth.crop_box
    assert frame.transform.size == truth.size and frame.transform.mode == "background_removed"
    assert cond.predict_frame(source, remove_background=True) is None


def test_untouched_frame_is_identity(tmp_runtime: Path):
    opaque = _subject((1600, 1200), (300, 200, 1200, 1100), alpha=False)
    source = _save(opaque, tmp_runtime / "source.png")
    preview = _save(_engine_preview(opaque), tmp_runtime / "preview.png")
    frame = cond.recover_frame(source, preview, "untouched", tmp_runtime)
    assert frame.method == "identity" and frame.verified
    assert frame.transform.size == Image.open(preview).size == cond.thumbnail_size((1600, 1200))
    predicted = cond.predict_frame(source, remove_background=False)
    assert predicted is not None and predicted.transform == frame.transform


def test_unverifiable_frames_are_reported_and_persisted_is_never_produced(tmp_runtime: Path):
    rgba = _subject((500, 400), (50, 40, 400, 380), alpha=True, seed=1)
    other = _subject((500, 400), (60, 30, 420, 390), alpha=True, seed=2)
    source = _save(rgba, tmp_runtime / "source.png")
    resized, scale = cond.upstream_resize(other)
    wrong, _crop, _t = cond.crop_from_alpha(resized, source_size=other.size, scale=scale)
    preview = _save(_engine_preview(wrong), tmp_runtime / "preview.png")
    recomputed = cond.recover_frame(source, preview, "alpha_crop", tmp_runtime)
    assert recomputed.method == "recompute" and not recomputed.verified

    noise = Image.fromarray((np.random.default_rng(9).random((300, 300, 3)) * 255).astype(np.uint8))
    preview_noise = _save(noise, tmp_runtime / "noise.png")
    opaque = _save(_subject((800, 600), (100, 100, 500, 500), alpha=False), tmp_runtime / "opaque.png")
    matched = cond.recover_frame(opaque, preview_noise, "background_removed", tmp_runtime)
    assert matched.method == "template_match" and not matched.verified
    summary = matched.summary()
    assert summary["verified"] is False and math.isfinite(summary["residual"])

    methods = {
        cond.recover_frame(source, preview, mode, tmp_runtime).method
        for mode in ("alpha_crop", "background_removed", "untouched", "something_else")
    }
    assert "persisted" not in methods


def test_masks_map_between_source_and_conditioning(tmp_runtime: Path):
    size, box = (1200, 900), (300, 150, 900, 850)
    source = _save(_subject(size, box, alpha=True), tmp_runtime / "source.png")
    frame = cond.predict_frame(source, remove_background=True)
    assert frame is not None
    mask = np.zeros((size[1], size[0]), dtype=bool)
    mask[400:500, 500:700] = True
    in_frame = cond.to_conditioning(mask, frame)
    assert in_frame.shape == (frame.transform.size[1], frame.transform.size[0])
    back = cond.to_source(in_frame, frame, size)
    iou = (back & mask).sum() / (back | mask).sum()
    assert iou > 0.95
    matrix = cond.source_to_frame_matrix(frame.transform)
    x0, y0, x1, y1 = cond.map_box((500, 400, 700, 500), matrix)
    ys, xs = np.nonzero(in_frame)
    assert abs(xs.min() - x0) <= 1.5 and abs(ys.min() - y0) <= 1.5


@pytest.mark.parametrize("size", [(1500, 1000), (1000, 1500), (1025, 1024), (4000, 3), (3, 4000), (800, 600)])
def test_thumbnail_size_matches_pillow(size):
    img = Image.new("L", size)
    img.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
    assert cond.thumbnail_size(size) == img.size


def test_frame_round_trips_through_json():
    transform = cond.CropTransform("alpha_crop", (1500, 2100), 0.4876, (731, 1024), (-10, 4, 700, 714), (710, 710))
    frame = cond.ConditioningFrame(transform, "recompute", True, 0.0)
    assert cond.ConditioningFrame.from_dict(frame.to_dict()) == frame
    assert frame.summary()["crop_box"] == [-10, 4, 700, 714]
