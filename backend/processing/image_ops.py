"""Image normalization helpers (EXIF orientation, mode conversion, thumbnails, normalized PNG copies)."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from processing.image_validation import PILLOW_FORMATS
from utils.files import atomic_write_bytes

THUMBNAIL_MAX_SIDE = 384


@dataclass(frozen=True, slots=True)
class NormalizedImage:
    path: Path
    width: int
    height: int
    has_alpha: bool


def open_oriented(source: bytes | Path) -> Image.Image:
    """Open an image, fully decode it and apply its EXIF orientation. Returns a detached image."""
    stream = BytesIO(source) if isinstance(source, bytes) else source
    with Image.open(stream, formats=PILLOW_FORMATS) as img:
        img.load()
        oriented = ImageOps.exif_transpose(img)
    # exif_transpose returns a new image (or a copy); make sure nothing keeps the source file handle open.
    oriented.load()
    return oriented


def to_rgb_or_rgba(img: Image.Image) -> Image.Image:
    """Convert any Pillow mode to 8-bit ``RGB`` or ``RGBA`` (alpha kept iff the image has transparency).

    Handles palette (P/PA), grayscale (1/L/LA), 16/32-bit integer and float grayscale (scaled, not clipped),
    CMYK and YCbCr.
    """
    mode = img.mode
    if mode in ("RGB", "RGBA"):
        return img
    if mode in ("LA", "La", "PA", "RGBa"):
        return img.convert("RGBA")
    if mode.startswith("I") or mode == "F":
        arr = np.asarray(img, dtype=np.float64)
        if mode.startswith("I;16") or (arr.size and float(arr.max()) > 255.0):
            arr = arr / 257.0
        gray = Image.fromarray(np.clip(np.rint(arr), 0, 255).astype(np.uint8), mode="L")
        return gray.convert("RGB")
    has_alpha = bool(img.has_transparency_data)
    if mode in ("P", "L", "1"):
        return img.convert("RGBA" if has_alpha else "RGB")
    # CMYK, YCbCr, LAB, HSV, ...
    return img.convert("RGBA" if has_alpha and "A" in mode else "RGB")


def make_thumbnail_webp(source: bytes | Path, *, max_side: int = THUMBNAIL_MAX_SIDE, quality: int = 82) -> bytes:
    """EXIF-transposed WEBP thumbnail whose longest side is at most ``max_side`` (alpha preserved)."""
    img = to_rgb_or_rgba(open_oriented(source))
    img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    out = BytesIO()
    img.save(out, format="WEBP", quality=quality, method=4)
    return out.getvalue()


def normalize_to_png(source: bytes | Path, destination: Path) -> NormalizedImage:
    """Write a normalized PNG copy (EXIF orientation applied; 8-bit RGB or RGBA) atomically."""
    img = to_rgb_or_rgba(open_oriented(source))
    out = BytesIO()
    img.save(out, format="PNG", compress_level=3)
    atomic_write_bytes(destination, out.getvalue())
    return NormalizedImage(path=destination, width=img.width, height=img.height, has_alpha=img.mode == "RGBA")
