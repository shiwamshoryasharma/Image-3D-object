"""Input-image handling for the TRELLIS.2 engine (pure Pillow, no torch).

Semantics (contract section 7.4, ``remove_background``):

* ``remove_background=true``: an RGBA image with a *real* alpha channel (some pixel < 255) is cropped/centred
  using that alpha; otherwise the background-removal model runs, then crop + centre (upstream
  ``Trellis2ImageTo3DPipeline.preprocess_image``).
* ``remove_background=false``: a real alpha channel is still used for the crop (upstream ``preprocess_image``);
  otherwise the image is used untouched (RGB).
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageOps

PREVIEW_MAX_SIDE = 1024
"""Upper bound for the stored preview of an *untouched* image (the conditioning encoder resizes to ≤1024 anyway)."""

ALPHA_FOREGROUND_THRESHOLD = int(0.8 * 255)
"""Upstream crops to pixels with ``alpha > 0.8 * 255``; an image without such pixels has no usable foreground."""

_ALPHA_MODES = frozenset({"RGBA", "LA", "PA", "RGBa", "La"})
_HIGH_BIT_MODES = frozenset({"I", "I;16", "I;16B", "I;16L", "I;16N", "F"})


def normalize_mode(image: Image.Image) -> Image.Image:
    """Convert any Pillow mode to ``RGBA`` (if it carries transparency) or ``RGB``."""
    if image.mode in _HIGH_BIT_MODES:
        # 16/32-bit grayscale: scale to 8 bit instead of clipping everything above 255 to white.
        image = image.convert("I").point(lambda value: value * (1 / 256)).convert("L")
    if image.mode in _ALPHA_MODES or "transparency" in image.info:
        return image.convert("RGBA")
    return image.convert("RGB")


def load_conditioning_image(path: Path) -> Image.Image:
    """Open an input image, apply its EXIF orientation and normalize the mode (RGB/RGBA)."""
    with Image.open(path) as source:
        source.load()
        oriented = ImageOps.exif_transpose(source)
        image = normalize_mode(oriented)
    return image


def has_real_alpha(image: Image.Image) -> bool:
    """``True`` for RGBA images whose alpha channel is not fully opaque (same test as upstream)."""
    if image.mode != "RGBA":
        return False
    low, _high = image.getchannel("A").getextrema()
    return low < 255


def has_alpha_foreground(image: Image.Image) -> bool:
    """``True`` if some pixel is opaque enough for upstream's crop (``alpha > 0.8 * 255``)."""
    if image.mode != "RGBA":
        return True
    _low, high = image.getchannel("A").getextrema()
    return high > ALPHA_FOREGROUND_THRESHOLD


def needs_upstream_preprocess(image: Image.Image, remove_background: bool) -> bool:
    """Whether ``pipeline.preprocess_image`` (alpha crop and/or background removal) must run."""
    return remove_background or has_real_alpha(image)


def make_preview(image: Image.Image, max_side: int = PREVIEW_MAX_SIDE) -> Image.Image:
    """RGB copy of the conditioning image, downscaled to ``max_side`` if larger."""
    preview = image.convert("RGB")
    if max(preview.size) > max_side:
        preview.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    return preview
