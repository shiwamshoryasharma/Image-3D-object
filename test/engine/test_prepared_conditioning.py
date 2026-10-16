"""Parity of the torch-free conditioning replica with upstream ``preprocess_image`` (part-aware DESIGN.md 4.5.2).

``processing.conditioning.crop_from_alpha`` must produce exactly the pixels of
``Trellis2ImageTo3DPipeline.preprocess_image`` (Pillow's float-box rounding, out-of-range black, float32
premultiplication) for RGBA inputs of odd and even sizes, bboxes touching the border and inputs larger than 1024, and
for the background-removal path with a deterministic stand-in for BiRefNet. No weights are loaded: the pipeline object
is created without ``__init__`` and only ``preprocess_image`` runs.

Run::

    backend\\python\\python.exe -s -m pytest test\\engine\\test_prepared_conditioning.py -p no:cacheprovider
"""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image, ImageDraw

pytest.importorskip("torch")
try:
    from trellis2.pipelines.trellis2_image_to_3d import Trellis2ImageTo3DPipeline
except Exception as exc:  # noqa: BLE001 - a missing/broken TRELLIS checkout skips the parity test
    pytest.skip(f"trellis2 is not importable: {type(exc).__name__}: {exc}", allow_module_level=True)

from processing.conditioning import crop_from_alpha, upstream_resize  # noqa: E402


def _matte(size: tuple[int, int]) -> Image.Image:
    """Deterministic soft matte: an off-centre ellipse with a feathered edge."""
    w, h = size
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    d = ((xx - 0.47 * w) / (0.33 * w)) ** 2 + ((yy - 0.55 * h) / (0.41 * h)) ** 2
    alpha = np.clip((1.15 - d) / 0.3, 0, 1) * 255
    return Image.fromarray(alpha.astype(np.uint8), "L")


class StubRembg:
    """Stand-in for BiRefNet: RGB in, RGBA with :func:`_matte` out (deterministic)."""

    def __call__(self, image: Image.Image) -> Image.Image:
        out = image.convert("RGB").copy()
        out.putalpha(_matte(image.size))
        return out


def _pipeline() -> Trellis2ImageTo3DPipeline:
    pipe = Trellis2ImageTo3DPipeline.__new__(Trellis2ImageTo3DPipeline)
    pipe.low_vram = False
    pipe.rembg_model = StubRembg()
    pipe._device = "cpu"
    return pipe


def _rgba(size: tuple[int, int], box: tuple[int, int, int, int], seed: int) -> Image.Image:
    rng = np.random.default_rng(seed)
    w, h = size
    rgb = (rng.random((h, w, 3)) * 255).astype(np.uint8)
    alpha = Image.new("L", size, 0)
    draw = ImageDraw.Draw(alpha)
    draw.ellipse(box, fill=255)
    draw.rectangle((box[0], box[1], box[0] + (box[2] - box[0]) // 3, box[1] + 4), fill=180)  # partial alpha
    return Image.fromarray(np.dstack([rgb, np.asarray(alpha)]), "RGBA")


CASES = [
    ((300, 200), (20, 10, 260, 190)),  # even
    ((301, 257), (40, 33, 258, 250)),  # odd
    ((333, 401), (0, 0, 200, 400)),  # bbox at the top-left and bottom border
    ((512, 512), (311, 100, 511, 300)),  # bbox at the right border
    ((1500, 2101), (100, 200, 1400, 2050)),  # larger than 1024
    ((2048, 999), (900, 50, 2047, 990)),  # wide, larger than 1024, border
]


@pytest.mark.parametrize(("size", "box"), CASES)
def test_alpha_crop_matches_upstream(size, box):
    image = _rgba(size, box, seed=size[0] + size[1])
    expected = np.asarray(_pipeline().preprocess_image(image.copy()))
    resized, _scale = upstream_resize(image)
    replica, _crop, transform = crop_from_alpha(resized, source_size=image.size)
    assert np.array_equal(np.asarray(replica), expected)
    assert transform.size == (expected.shape[1], expected.shape[0])


@pytest.mark.parametrize("size", [(640, 480), (1203, 1601), (999, 1000)])
def test_background_removal_path_matches_upstream(size):
    rng = np.random.default_rng(size[0])
    image = Image.fromarray((rng.random((size[1], size[0], 3)) * 255).astype(np.uint8), "RGB")
    expected = np.asarray(_pipeline().preprocess_image(image.copy()))
    resized, _scale = upstream_resize(image)
    replica, _crop, _transform = crop_from_alpha(StubRembg()(resized.convert("RGB")), mode="background_removed")
    assert np.array_equal(np.asarray(replica), expected)
