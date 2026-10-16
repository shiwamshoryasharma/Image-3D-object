"""Conditioning frame of TRELLIS.2 (part-aware DESIGN.md section 4.5.2). Torch-free; exact replica of upstream.

``Trellis2ImageTo3DPipeline.preprocess_image`` (``third_party/TRELLIS.2/trellis2/pipelines/trellis2_image_to_3d.py``)
turns the input image into the conditioning image and throws the crop box and the alpha away:

1. note whether the input is RGBA with any alpha < 255;
2. downscale with ``scale = min(1, 1024 / max(w, h))`` to ``(int(w * scale), int(h * scale))`` (LANCZOS);
3. without alpha, remove the background (BiRefNet);
4. bbox of ``alpha > 0.8 * 255``, float ``center``, ``size = int(max(x1 - x0, y1 - y0))`` and
   ``box = (cx - size // 2, cy - size // 2, cx + size // 2, cy + size // 2)``;
5. ``PIL.Image.crop`` (float box rounded by Pillow; out-of-range pixels become black);
6. premultiply in float32.

This module replays that arithmetic bit for bit (:func:`crop_from_alpha`, pinned by
``test/engine/test_prepared_conditioning.py``), predicts the frame of an input before reconstruction
(:func:`predict_frame`) and recovers it afterwards from the job's ``preview.png`` (:func:`recover_frame`). Masks move
between the source image and the conditioning image with :func:`to_conditioning` / :func:`to_source`.

Coordinates use the OpenCV convention (integer pixel centres). A source pixel ``x`` maps to the resized pixel
``(x + 0.5) * sx - 0.5`` with ``sx = resized_w / source_w``, then to the conditioning pixel by subtracting the crop
origin. The job's ``preview.png`` is exactly the conditioning image for ``alpha_crop`` and ``background_removed``
inputs (the crop is never larger than 1024 px) and a thumbnail of the input for ``untouched`` ones.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Final, Literal

import cv2
import numpy as np
from PIL import Image

from processing.image_ops import open_oriented, to_rgb_or_rgba

ConditioningModeName = Literal["alpha_crop", "background_removed", "untouched"]
FrameMethod = Literal["recompute", "persisted", "template_match", "identity"]

MAX_CONDITIONING_SIDE: Final = 1024
"""Upstream downscales the input so that its longest side is at most 1024 px."""
ALPHA_THRESHOLD: Final = 0.8
"""Upstream crops to the bbox of ``alpha > 0.8 * 255``."""
PREVIEW_MAX_SIDE: Final = 1024
"""``inference/trellis/imaging.py`` stores ``preview.png`` with at most 1024 px per side."""
RECOMPUTE_TOLERANCE: Final = 2.0
"""``recompute`` frames are verified when the mean |difference| over the foreground is below this (uint8 units)."""
TEMPLATE_TOLERANCE: Final = 6.0
"""``template_match`` frames are verified below this residual (the BiRefNet matte is not persisted)."""
FOREGROUND_LUMA: Final = 8
"""Preview pixels brighter than this are foreground (the premultiplied background is black)."""
TEMPLATE_COARSE_FACTOR: Final = 8
TEMPLATE_CANDIDATES: Final = 3
TEMPLATE_REFINE_RADIUS: Final = 8


@dataclass(frozen=True, slots=True)
class CropTransform:
    """How the conditioning image was cut out of the source image."""

    mode: ConditioningModeName
    source_size: tuple[int, int]
    """``(width, height)`` of the source image."""
    scale: float
    """Upstream ``min(1, 1024 / max(w, h))`` (for ``untouched``: preview width / source width)."""
    resized_size: tuple[int, int]
    crop_box: tuple[int, int, int, int] | None
    """Pillow-rounded box in resized pixels; may exceed the resized bounds. ``None`` for ``untouched``."""
    size: tuple[int, int]
    """``(width, height)`` of the conditioning image."""
    alpha_threshold: float = ALPHA_THRESHOLD
    premultiplied: bool = True

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["source_size"] = list(self.source_size)
        data["resized_size"] = list(self.resized_size)
        data["crop_box"] = list(self.crop_box) if self.crop_box is not None else None
        data["size"] = list(self.size)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CropTransform:
        crop = data.get("crop_box")
        return cls(
            mode=data["mode"],
            source_size=(int(data["source_size"][0]), int(data["source_size"][1])),
            scale=float(data["scale"]),
            resized_size=(int(data["resized_size"][0]), int(data["resized_size"][1])),
            crop_box=tuple(int(v) for v in crop) if crop is not None else None,  # type: ignore[arg-type]
            size=(int(data["size"][0]), int(data["size"][1])),
            alpha_threshold=float(data.get("alpha_threshold", ALPHA_THRESHOLD)),
            premultiplied=bool(data.get("premultiplied", True)),
        )


@dataclass(frozen=True, slots=True)
class ConditioningFrame:
    transform: CropTransform
    method: FrameMethod
    """``persisted`` is reserved for future generation intermediates (no code path produces it)."""
    verified: bool
    residual: float
    """Mean |difference| over foreground pixels, uint8 units (0 when the frame is exact by construction)."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "transform": self.transform.to_dict(),
            "method": self.method,
            "verified": self.verified,
            "residual": self.residual,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConditioningFrame:
        return cls(
            transform=CropTransform.from_dict(data["transform"]),
            method=data["method"],
            verified=bool(data["verified"]),
            residual=float(data["residual"]),
        )

    def summary(self) -> dict[str, Any]:
        """The ``SegmentationResult.frame`` shape (section 4.5.6)."""
        t = self.transform
        return {
            "mode": t.mode,
            "method": self.method,
            "scale": t.scale,
            "crop_box": list(t.crop_box) if t.crop_box is not None else None,
            "size": list(t.size),
            "verified": self.verified,
            "residual": round(float(self.residual), 3) if math.isfinite(self.residual) else 1e9,
        }


# --------------------------------------------------------------------------------------------------
# Upstream replica
# --------------------------------------------------------------------------------------------------


def load_input(path: Path) -> Image.Image:
    """Open an input like the engine does: EXIF orientation applied, 8-bit ``RGB`` or ``RGBA``."""
    return to_rgb_or_rgba(open_oriented(path))


def has_real_alpha(image: Image.Image) -> bool:
    """Upstream's test: an ``RGBA`` image with any alpha below 255."""
    if image.mode != "RGBA":
        return False
    return not bool(np.all(np.array(image)[:, :, 3] == 255))


def upstream_resize(img: Image.Image) -> tuple[Image.Image, float]:
    """Step 2: LANCZOS downscale to at most 1024 px per side. Returns ``(image, scale)``; never upscales."""
    max_size = max(img.size)
    scale = min(1, MAX_CONDITIONING_SIDE / max_size)
    if scale < 1:
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.Resampling.LANCZOS)
    return img, float(scale)


def crop_from_alpha(
    rgba_resized: Image.Image,
    *,
    source_size: tuple[int, int] | None = None,
    scale: float = 1.0,
    mode: ConditioningModeName = "alpha_crop",
) -> tuple[Image.Image, Image.Image, CropTransform]:
    """Steps 4-6 on an already resized RGBA image (input alpha or background-removal matte).

    Returns ``(RGB premultiplied conditioning image, RGBA crop, transform)``. Raises :class:`ValueError` when no pixel
    is above the alpha threshold (upstream fails with a zero-size array there).
    """
    if rgba_resized.mode != "RGBA":
        raise ValueError(f"crop_from_alpha needs an RGBA image, got {rgba_resized.mode}")
    output_np = np.array(rgba_resized)
    alpha = output_np[:, :, 3]
    found = np.argwhere(alpha > ALPHA_THRESHOLD * 255)
    if found.size == 0:
        raise ValueError("The alpha channel has no pixel above the 80% opacity threshold")
    bbox = np.min(found[:, 1]), np.min(found[:, 0]), np.max(found[:, 1]), np.max(found[:, 0])
    center = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    size = max(bbox[2] - bbox[0], bbox[3] - bbox[1])
    size = int(size * 1)
    box = center[0] - size // 2, center[1] - size // 2, center[0] + size // 2, center[1] + size // 2
    crop = rgba_resized.crop(box)  # type: ignore[arg-type]
    output = np.array(crop).astype(np.float32) / 255
    output = output[:, :, :3] * output[:, :, 3:4]
    conditioning = Image.fromarray((output * 255).astype(np.uint8))
    x0, y0, x1, y1 = (int(round(v)) for v in box)
    transform = CropTransform(
        mode=mode,
        source_size=source_size or rgba_resized.size,
        scale=float(scale),
        resized_size=rgba_resized.size,
        crop_box=(x0, y0, x1, y1),
        size=conditioning.size,
    )
    return conditioning, crop, transform


def thumbnail_size(size: tuple[int, int], max_side: int = PREVIEW_MAX_SIDE) -> tuple[int, int]:
    """Size ``Image.thumbnail((max_side, max_side))`` produces (Pillow's aspect-preserving rounding)."""
    width, height = size
    if width <= max_side and height <= max_side:
        return width, height
    aspect = width / height
    x, y = max_side, max_side

    def round_aspect(number: float, key: Any) -> int:
        return max(min(math.floor(number), math.ceil(number), key=key), 1)

    if x / y >= aspect:
        x = round_aspect(y * aspect, key=lambda n: abs(aspect - n / y))
    else:
        y = round_aspect(x / aspect, key=lambda n: 0 if n == 0 else abs(aspect - x / n))
    return x, y


def untouched_transform(source_size: tuple[int, int], preview_size: tuple[int, int] | None = None) -> CropTransform:
    """``untouched`` inputs: the stored preview is a thumbnail of the source (no crop)."""
    size = preview_size or thumbnail_size(source_size)
    return CropTransform(
        mode="untouched",
        source_size=source_size,
        scale=size[0] / source_size[0],
        resized_size=size,
        crop_box=None,
        size=size,
    )


def conditioning_from_source(source: Image.Image) -> tuple[Image.Image, CropTransform]:
    """The exact conditioning image of a real-alpha input (steps 1-6 without background removal)."""
    if not has_real_alpha(source):
        raise ValueError("Only real-alpha inputs have a predictable conditioning image")
    resized, scale = upstream_resize(source)
    conditioning, _crop, transform = crop_from_alpha(resized, source_size=source.size, scale=scale)
    return conditioning, transform


# --------------------------------------------------------------------------------------------------
# Frame prediction and recovery
# --------------------------------------------------------------------------------------------------


def predict_frame(source: Path, remove_background: bool) -> ConditioningFrame | None:
    """The frame of an input before reconstruction: exact for real-alpha inputs and for opaque inputs without
    background removal (``untouched``); ``None`` when BiRefNet would decide the crop."""
    image = load_input(source)
    if has_real_alpha(image):
        try:
            _conditioning, transform = conditioning_from_source(image)
        except ValueError:
            return None
        return ConditioningFrame(transform=transform, method="recompute", verified=True, residual=0.0)
    if not remove_background:
        return ConditioningFrame(
            transform=untouched_transform(image.size), method="identity", verified=True, residual=0.0
        )
    return None


def recover_frame(source: Path, preview: Path, preprocess_mode: str, job_dir: Path) -> ConditioningFrame:
    """The frame of a finished job, recovered from its input and ``preview.png``.

    ``preprocess_mode`` is the engine's ``metadata.preprocess``. ``job_dir`` is where persisted generation
    intermediates would live (method ``persisted``, reserved and never produced in this milestone).
    """
    del job_dir  # persisted intermediates are deferred (DESIGN D1): no code path reads them
    image = load_input(source)
    preview_img = Image.open(preview)
    preview_img.load()
    preview_rgb = np.asarray(preview_img.convert("RGB"))
    if preprocess_mode == "alpha_crop" and has_real_alpha(image):
        return _recompute(image, preview_rgb)
    if preprocess_mode == "untouched":
        transform = untouched_transform(image.size, (preview_rgb.shape[1], preview_rgb.shape[0]))
        return ConditioningFrame(transform=transform, method="identity", verified=True, residual=0.0)
    return _template_match(image, preview_rgb)


def _recompute(image: Image.Image, preview_rgb: np.ndarray) -> ConditioningFrame:
    resized, scale = upstream_resize(image)
    conditioning, crop, transform = crop_from_alpha(resized, source_size=image.size, scale=scale)
    replica = np.asarray(conditioning)
    if replica.shape != preview_rgb.shape:
        return ConditioningFrame(transform=transform, method="recompute", verified=False, residual=math.inf)
    foreground = np.asarray(crop)[:, :, 3] > 0
    residual = _mean_abs_diff(replica, preview_rgb, foreground)
    return ConditioningFrame(
        transform=transform, method="recompute", verified=residual < RECOMPUTE_TOLERANCE, residual=residual
    )


def _template_match(image: Image.Image, preview_rgb: np.ndarray) -> ConditioningFrame:
    """Locate the (square, premultiplied) preview inside the resized source.

    Pyramid search: masked TM_SQDIFF on 1/8-scale images over the resized source padded by the crop size on every
    side (crops may leave the image; Pillow fills with black), keeping the top candidates; then a +-8 px masked SSD
    at full resolution over the foreground ``preview_luma > 8``.
    """
    rgb = image.convert("RGB")
    resized, scale = upstream_resize(rgb)
    source = np.asarray(resized)
    height, width = preview_rgb.shape[:2]
    pad = max(width, height)
    padded = cv2.copyMakeBorder(source, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=(0, 0, 0))
    luma = cv2.cvtColor(preview_rgb, cv2.COLOR_RGB2GRAY)
    foreground = luma > FOREGROUND_LUMA

    def failed(residual: float = math.inf) -> ConditioningFrame:
        transform = CropTransform(
            mode="background_removed",
            source_size=image.size,
            scale=scale,
            resized_size=resized.size,
            crop_box=None,
            size=(width, height),
        )
        return ConditioningFrame(transform=transform, method="template_match", verified=False, residual=residual)

    if not foreground.any():
        return failed()
    factor = TEMPLATE_COARSE_FACTOR
    small_source = cv2.resize(
        padded, (max(1, padded.shape[1] // factor), max(1, padded.shape[0] // factor)), interpolation=cv2.INTER_AREA
    )
    small_template = cv2.resize(
        preview_rgb, (max(1, width // factor), max(1, height // factor)), interpolation=cv2.INTER_AREA
    )
    small_mask = cv2.resize(
        foreground.astype(np.uint8) * 255, (small_template.shape[1], small_template.shape[0]),
        interpolation=cv2.INTER_NEAREST,
    )
    if not small_mask.any():
        small_mask = np.full(small_template.shape[:2], 255, np.uint8)
    coarse = cv2.matchTemplate(
        small_source.astype(np.float32), small_template.astype(np.float32), cv2.TM_SQDIFF,
        mask=cv2.merge([small_mask.astype(np.float32)] * 3),
    )
    coarse = np.nan_to_num(coarse, nan=np.inf, posinf=np.inf)
    candidates: list[tuple[int, int]] = []
    work = coarse.copy()
    for _ in range(TEMPLATE_CANDIDATES):
        _min_val, _max_val, min_loc, _max_loc = cv2.minMaxLoc(work)
        cx, cy = min_loc
        if not math.isfinite(float(work[cy, cx])):
            break
        candidates.append((cx * factor, cy * factor))
        work[max(0, cy - 2) : cy + 3, max(0, cx - 2) : cx + 3] = np.inf
    template = preview_rgb.astype(np.float32)
    mask3 = cv2.merge([foreground.astype(np.float32)] * 3)
    radius = TEMPLATE_REFINE_RADIUS
    best: tuple[float, int, int] | None = None
    for x, y in candidates:
        x0 = min(max(0, x - radius), padded.shape[1] - width)
        y0 = min(max(0, y - radius), padded.shape[0] - height)
        x1 = min(padded.shape[1], x + width + radius)
        y1 = min(padded.shape[0], y + height + radius)
        window = padded[y0:y1, x0:x1].astype(np.float32)
        if window.shape[0] < height or window.shape[1] < width:
            continue
        fine = cv2.matchTemplate(window, template, cv2.TM_SQDIFF, mask=mask3)
        fine = np.nan_to_num(fine, nan=np.inf, posinf=np.inf)
        min_val, _max_val, min_loc, _max_loc = cv2.minMaxLoc(fine)
        if best is None or min_val < best[0]:
            best = (float(min_val), x0 + min_loc[0], y0 + min_loc[1])
    if best is None:
        return failed()
    _ssd, px, py = best
    crop = padded[py : py + height, px : px + width]
    residual = _mean_abs_diff(crop, preview_rgb, foreground)
    transform = CropTransform(
        mode="background_removed",
        source_size=image.size,
        scale=scale,
        resized_size=resized.size,
        crop_box=(px - pad, py - pad, px - pad + width, py - pad + height),
        size=(width, height),
    )
    return ConditioningFrame(
        transform=transform, method="template_match", verified=residual < TEMPLATE_TOLERANCE, residual=residual
    )


def _mean_abs_diff(a: np.ndarray, b: np.ndarray, mask: np.ndarray) -> float:
    if not mask.any():
        return math.inf
    diff = np.abs(a.astype(np.int16) - b.astype(np.int16))
    return float(diff[mask].mean())


# --------------------------------------------------------------------------------------------------
# Mapping between the source and the conditioning image
# --------------------------------------------------------------------------------------------------


def source_to_frame_matrix(transform: CropTransform) -> np.ndarray:
    """2x3 affine matrix: source pixel -> conditioning pixel (pixel-centre convention)."""
    sx = transform.resized_size[0] / transform.source_size[0]
    sy = transform.resized_size[1] / transform.source_size[1]
    ox, oy = (transform.crop_box[0], transform.crop_box[1]) if transform.crop_box is not None else (0, 0)
    return np.array([[sx, 0.0, 0.5 * sx - 0.5 - ox], [0.0, sy, 0.5 * sy - 0.5 - oy]], dtype=np.float64)


def frame_to_source_matrix(transform: CropTransform) -> np.ndarray:
    """2x3 affine matrix: conditioning pixel -> source pixel."""
    return cv2.invertAffineTransform(source_to_frame_matrix(transform))


def warp_mask(
    mask: np.ndarray, matrix: np.ndarray, size: tuple[int, int], *, nearest: bool = True
) -> np.ndarray:
    """Warp ``mask`` with a forward 2x3 ``matrix`` onto a ``(width, height)`` canvas (zeros outside)."""
    is_bool = mask.dtype == np.bool_
    data = mask.astype(np.uint8) if is_bool else mask
    if data.dtype not in (np.uint8, np.uint16, np.float32):
        data = data.astype(np.float32)
    flags = cv2.INTER_NEAREST if nearest else cv2.INTER_LINEAR
    out = cv2.warpAffine(data, matrix, size, flags=flags, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    return out.astype(bool) if is_bool else out


def to_conditioning(mask: np.ndarray, frame: ConditioningFrame) -> np.ndarray:
    """A source-space mask in conditioning space (nearest neighbour)."""
    return warp_mask(mask, source_to_frame_matrix(frame.transform), frame.transform.size)


def to_source(mask: np.ndarray, frame: ConditioningFrame, source_size: tuple[int, int]) -> np.ndarray:
    """A conditioning-space mask in source space (nearest neighbour)."""
    return warp_mask(mask, frame_to_source_matrix(frame.transform), source_size)


def map_box(box: tuple[float, float, float, float], matrix: np.ndarray) -> tuple[float, float, float, float]:
    """Axis-aligned box through a 2x3 affine matrix (scale + translation)."""
    x0, y0, x1, y1 = box
    corners = np.array([[x0, y0, 1.0], [x1, y1, 1.0]]) @ matrix.T
    return (
        float(min(corners[:, 0])),
        float(min(corners[:, 1])),
        float(max(corners[:, 0])),
        float(max(corners[:, 1])),
    )
