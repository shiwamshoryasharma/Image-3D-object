"""Eye-area heuristics of the 2D fusion (part-aware DESIGN.md section 4.5.4). numpy/opencv/scipy only.

* **Sides** are character-relative: the eye on the image's right is the character's left eye.
* **Eyebrows** from the detector are kept only when the centre of a mask component lies above an eye and within
  1.5 eye heights of it (a detector "eyebrow" box on a tiara or a hair ornament is rejected); otherwise the dark band
  above the eye (Otsu on L*) is used with a confidence of at most 0.4.
* **Eyelashes**: upper-lid curve = Savitzky-Golay-smoothed per-column top boundary of the eye mask; band from
  -0.35 h to +0.12 h around it, extended 15 % past the outer canthus, intersected with dark pixels (Otsu on L* inside
  the eye bbox), minus the eyebrow and the iris.
* **Eyelids** (classes 26/27): ``dilate(eye, 0.35 h) - eye - eyebrow - eyelash``, intersected with the face mask.

All masks are boolean arrays in the analysis image; ``lightness`` is the L* channel (uint8, OpenCV scaling).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final, Literal

import cv2
import numpy as np
from scipy.signal import savgol_filter

SideName = Literal["left", "right"]

BROW_MAX_DISTANCE: Final = 1.5
"""An eyebrow centre may lie at most this many eye heights above the eye's top edge."""
BROW_X_MARGIN: Final = 0.5
"""... and within the eye's x-range widened by this fraction of the eye width on both sides."""
BROW_FALLBACK_CONFIDENCE: Final = 0.4
LASH_BAND_ABOVE: Final = 0.35
LASH_BAND_BELOW: Final = 0.12
LASH_OUTER_EXTENSION: Final = 0.15
LASH_MAX_CONFIDENCE: Final = 0.6
LID_RING: Final = 0.35
LID_CURVE_POINTS: Final = 16


@dataclass(frozen=True, slots=True)
class EyeInstance:
    side: SideName
    mask: np.ndarray
    score: float
    sources: tuple[str, ...] = ()
    bbox: tuple[int, int, int, int] = field(default=(0, 0, 0, 0))
    """``(x0, y0, x1, y1)``, exclusive end."""

    @property
    def width(self) -> int:
        return max(1, self.bbox[2] - self.bbox[0])

    @property
    def height(self) -> int:
        return max(1, self.bbox[3] - self.bbox[1])

    @property
    def center_x(self) -> float:
        return (self.bbox[0] + self.bbox[2]) / 2


def mask_bbox(mask: np.ndarray) -> tuple[int, int, int, int] | None:
    """``(x0, y0, x1, y1)`` with exclusive end, or ``None`` for an empty mask."""
    ys, xs = np.nonzero(mask)
    if xs.size == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def components(mask: np.ndarray, min_area: int = 1) -> list[np.ndarray]:
    """Connected components (8-connectivity) of a boolean mask, largest first."""
    count, labels, stats, _centroids = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    order = sorted(range(1, count), key=lambda i: -int(stats[i, cv2.CC_STAT_AREA]))
    return [labels == i for i in order if int(stats[i, cv2.CC_STAT_AREA]) >= min_area]


def side_for_x(x: float, reference_x: float) -> SideName:
    """Character-relative side of a point: image-right of the reference is the character's left."""
    return "left" if x > reference_x else "right"


def assign_sides(
    parts: Sequence[np.ndarray], reference_x: float
) -> list[tuple[SideName, np.ndarray]]:
    """Up to two side masks from components: two are ordered by x (image-right = left); a single one is sided by the
    reference x. Further components join the kept component with the nearest centre."""
    if not parts:
        return []
    kept = list(parts[:2])
    centers = [_center_x(part) for part in kept]
    if len(kept) == 2:
        order = np.argsort(centers)
        sides: list[SideName] = ["right", "left"]
        result = [(sides[rank], kept[int(index)]) for rank, index in enumerate(order)]
    else:
        result = [(side_for_x(centers[0], reference_x), kept[0])]
    merged = [mask.copy() for _side, mask in result]
    result_centers = [_center_x(mask) for mask in merged]
    for extra in parts[2:]:
        cx = _center_x(extra)
        nearest = int(np.argmin([abs(cx - c) for c in result_centers]))
        merged[nearest] |= extra
    return [(side, mask) for (side, _old), mask in zip(result, merged, strict=True)]


def _center_x(mask: np.ndarray) -> float:
    xs = np.nonzero(mask)[1]
    return float(xs.mean()) if xs.size else 0.0


def build_eye_instances(
    eye_soft: np.ndarray,
    score: float,
    sources: tuple[str, ...],
    reference_x: float,
    min_area: int,
) -> list[EyeInstance]:
    """Eye instances (at most one per side) from the union soft mask of every eye detection."""
    binary = eye_soft >= 0.5
    sided = assign_sides(components(binary, min_area), reference_x)
    instances: list[EyeInstance] = []
    for side, mask in sided:
        bbox = mask_bbox(mask)
        if bbox is None:
            continue
        instances.append(EyeInstance(side=side, mask=mask, score=score, sources=sources, bbox=bbox))
    return instances


def accept_eyebrow_components(brow: np.ndarray, eyes: Sequence[EyeInstance], min_area: int) -> dict[SideName, np.ndarray]:
    """Components of a detector eyebrow mask whose centre lies above an eye (within 1.5 eye heights), by side."""
    accepted: dict[SideName, np.ndarray] = {}
    for part in components(brow, min_area):
        bbox = mask_bbox(part)
        if bbox is None:
            continue
        cx = (bbox[0] + bbox[2]) / 2
        cy = (bbox[1] + bbox[3]) / 2
        eye = _eye_below(cx, cy, eyes)
        if eye is None:
            continue
        accepted[eye.side] = accepted[eye.side] | part if eye.side in accepted else part
    return accepted


def _eye_below(cx: float, cy: float, eyes: Sequence[EyeInstance]) -> EyeInstance | None:
    best: tuple[float, EyeInstance] | None = None
    for eye in eyes:
        x0, y0, x1, _y1 = eye.bbox
        if cy >= y0:
            continue
        distance = y0 - cy
        if distance > BROW_MAX_DISTANCE * eye.height:
            continue
        margin = BROW_X_MARGIN * eye.width
        if not (x0 - margin <= cx <= x1 + margin):
            continue
        if best is None or distance < best[0]:
            best = (distance, eye)
    return best[1] if best is not None else None


def otsu_dark(values: np.ndarray) -> float | None:
    """Otsu threshold of uint8 values (``None`` when they are constant)."""
    flat = values.reshape(-1, 1).astype(np.uint8)
    if flat.size < 2 or int(flat.min()) == int(flat.max()):
        return None
    threshold, _ = cv2.threshold(flat, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return float(threshold)


def eyebrow_fallback(
    lightness: np.ndarray, eye: EyeInstance, exclude: np.ndarray, min_area: int
) -> np.ndarray | None:
    """The dark band above an eye (Otsu on L*), largest component; ``None`` when nothing dark is there."""
    h, w = lightness.shape
    x0 = max(0, int(eye.bbox[0] - 0.15 * eye.width))
    x1 = min(w, int(np.ceil(eye.bbox[2] + 0.15 * eye.width)))
    y0 = max(0, int(eye.bbox[1] - BROW_MAX_DISTANCE * eye.height))
    y1 = max(0, int(eye.bbox[1] - 0.1 * eye.height))
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    region = lightness[y0:y1, x0:x1]
    allowed = ~exclude[y0:y1, x0:x1]
    if allowed.sum() < 4:
        return None
    threshold = otsu_dark(region[allowed])
    if threshold is None:
        return None
    dark = np.zeros_like(exclude)
    dark[y0:y1, x0:x1] = (region <= threshold) & allowed
    parts = components(dark, min_area)
    return parts[0] if parts else None


def upper_lid_curve(eye_mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per-column top boundary of the eye mask, Savitzky-Golay smoothed: ``(xs, ys)`` as float arrays."""
    columns = np.nonzero(eye_mask.any(axis=0))[0]
    if columns.size == 0:
        return np.empty(0), np.empty(0)
    tops = np.argmax(eye_mask[:, columns], axis=0).astype(np.float64)
    window = min(len(tops) if len(tops) % 2 == 1 else len(tops) - 1, max(5, (len(tops) // 6) * 2 + 1))
    if window >= 5:
        tops = savgol_filter(tops, window_length=window, polyorder=2, mode="interp")
    return columns.astype(np.float64), tops


def eyelash_mask(
    lightness: np.ndarray,
    eye: EyeInstance,
    eyebrow: np.ndarray | None,
    min_area: int,
) -> tuple[np.ndarray, dict[str, object]] | None:
    """Heuristic eyelash mask and its annotation ``{lid_curve, method, confidence}`` for one eye."""
    xs, tops = upper_lid_curve(eye.mask)
    if xs.size < 3:
        return None
    h, w = lightness.shape
    eye_h = float(eye.height)
    extension = int(round(LASH_OUTER_EXTENSION * eye.width))
    # Outer canthus: image-right end for the character's left eye, image-left end for the right eye.
    if eye.side == "left":
        ext_x = np.arange(int(xs[-1]) + 1, min(w, int(xs[-1]) + 1 + extension), dtype=np.float64)
        ext_y = np.full(ext_x.shape, tops[-1])
        curve_x = np.concatenate([xs, ext_x])
        curve_y = np.concatenate([tops, ext_y])
    else:
        ext_x = np.arange(max(0, int(xs[0]) - extension), int(xs[0]), dtype=np.float64)
        ext_y = np.full(ext_x.shape, tops[0])
        curve_x = np.concatenate([ext_x, xs])
        curve_y = np.concatenate([ext_y, tops])
    band = np.zeros((h, w), dtype=bool)
    for x, y in zip(curve_x.astype(int), curve_y, strict=True):
        y0 = max(0, int(np.floor(y - LASH_BAND_ABOVE * eye_h)))
        y1 = min(h, int(np.ceil(y + LASH_BAND_BELOW * eye_h)) + 1)
        if y1 > y0:
            band[y0:y1, x] = True
    bx0, by0 = max(0, int(curve_x.min())), max(0, int(np.floor(curve_y.min() - LASH_BAND_ABOVE * eye_h)))
    bx1 = min(w, int(curve_x.max()) + 1)
    by1 = min(h, max(eye.bbox[3], int(np.ceil(curve_y.max() + LASH_BAND_BELOW * eye_h)) + 1))
    region = lightness[by0:by1, bx0:bx1]
    threshold = otsu_dark(region)
    if threshold is None:
        return None
    dark = np.zeros((h, w), dtype=bool)
    dark[by0:by1, bx0:bx1] = region <= threshold
    lashes = band & dark
    if eyebrow is not None:
        lashes &= ~eyebrow
    lashes &= ~_iris(eye.mask, dark, curve_x, curve_y, eye_h)
    parts = components(lashes, max(1, min_area // 4))
    if not parts:
        return None
    result = np.zeros((h, w), dtype=bool)
    for part in parts:
        result |= part
    coverage = float((band & dark).sum()) / float(max(1, band.sum()))
    confidence = round(min(LASH_MAX_CONFIDENCE, 0.2 + 0.4 * coverage + 0.2 * eye.score), 3)
    step = max(1, len(xs) // LID_CURVE_POINTS)
    lid_curve = [[int(x), int(round(y))] for x, y in zip(xs[::step], tops[::step], strict=True)]
    if lid_curve[-1][0] != int(xs[-1]):
        lid_curve.append([int(xs[-1]), int(round(tops[-1]))])
    return result, {"lid_curve": lid_curve, "method": "heuristic", "confidence": confidence}


def _iris(eye_mask: np.ndarray, dark: np.ndarray, curve_x: np.ndarray, curve_y: np.ndarray, eye_h: float) -> np.ndarray:
    """The iris estimate: the largest dark component inside the eye, below the lid curve."""
    below = np.zeros_like(eye_mask)
    h = eye_mask.shape[0]
    for x, y in zip(curve_x.astype(int), curve_y, strict=True):
        start = min(h, int(np.ceil(y + 0.08 * eye_h)))
        below[start:, x] = True
    candidates = components(eye_mask & dark & below)
    return candidates[0] if candidates else np.zeros_like(eye_mask)


def eyelid_ring(
    eye_mask: np.ndarray,
    eye_height: int,
    eyebrow: np.ndarray | None,
    eyelash: np.ndarray | None,
    face: np.ndarray,
) -> np.ndarray:
    """``dilate(eye, 0.35 h) - eye - eyebrow - eyelash``, intersected with the face mask."""
    radius = max(1, int(round(LID_RING * eye_height)))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1))
    ring = cv2.dilate(eye_mask.astype(np.uint8), kernel).astype(bool) & ~eye_mask
    if eyebrow is not None:
        ring &= ~eyebrow
    if eyelash is not None:
        ring &= ~eyelash
    return ring & face
