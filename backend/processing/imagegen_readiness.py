"""3D-readiness check of generated candidate images (part-aware DESIGN.md section 4.4.5). Torch-free.

TRELLIS.2 reconstructs best from one centred subject on a plain background. This check estimates that from the image
alone and only ever produces *warnings* (never a failure):

* background colour = per-channel median (CIE Lab) of a border ring 2 % of the shorter side wide;
* foreground = pixels whose Lab Delta-E (CIE76) from that colour is above 12, cleaned by a 3 px morphological open;
* issues (the same ``t2i_*`` codes are used as run warning codes):

  ``t2i_touches_border``     the foreground bounding box comes within 1 % of an image edge;
  ``t2i_multiple_subjects``  more than one connected component larger than 2 % of the image;
  ``t2i_coverage_low``       foreground below 8 % of the image;
  ``t2i_coverage_high``      foreground above 70 % of the image;
  ``t2i_aspect_mismatch``    a ``character`` candidate whose foreground box is wider than tall.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

import cv2
import numpy as np
from PIL import Image

from processing.image_ops import open_oriented, to_rgb_or_rgba

METHOD: Final = "border_color"
BORDER_RING_FRACTION: Final = 0.02
DELTA_E_THRESHOLD: Final = 12.0
OPEN_KERNEL_PX: Final = 3
EDGE_MARGIN_FRACTION: Final = 0.01
SUBJECT_MIN_AREA_FRACTION: Final = 0.02
COVERAGE_LOW: Final = 0.08
COVERAGE_HIGH: Final = 0.70

TOUCHES_BORDER: Final = "t2i_touches_border"
MULTIPLE_SUBJECTS: Final = "t2i_multiple_subjects"
COVERAGE_LOW_CODE: Final = "t2i_coverage_low"
COVERAGE_HIGH_CODE: Final = "t2i_coverage_high"
ASPECT_MISMATCH: Final = "t2i_aspect_mismatch"

ISSUE_ORDER: Final[tuple[str, ...]] = (
    TOUCHES_BORDER,
    MULTIPLE_SUBJECTS,
    COVERAGE_LOW_CODE,
    COVERAGE_HIGH_CODE,
    ASPECT_MISMATCH,
)
ISSUE_MESSAGES: Final[Mapping[str, str]] = MappingProxyType(
    {
        TOUCHES_BORDER: "the subject touches the image border and may be cut off",
        MULTIPLE_SUBJECTS: "more than one separate subject is visible",
        COVERAGE_LOW_CODE: "the subject covers less than 8 % of the image",
        COVERAGE_HIGH_CODE: "the subject covers more than 70 % of the image",
        ASPECT_MISMATCH: "the character is wider than tall (not a full-body standing pose)",
    }
)
"""Human-readable text of each issue (the run warning reads "Candidate <i>: <text>")."""


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    ok: bool
    issues: tuple[str, ...]
    method: str = METHOD
    metrics: Mapping[str, Any] = field(default_factory=dict)
    """``coverage``, ``bbox`` ``[x0, y0, x1, y1]`` (inclusive, ``None`` without foreground), ``subjects`` (components
    above 2 %), ``background_lab`` and the image ``size``."""


def candidate_warning(index: int, code: str) -> str:
    """Run warning message of one readiness issue."""
    return f"Candidate {index}: {ISSUE_MESSAGES.get(code, code)}"


def _srgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """``uint8`` RGB (H, W, 3) -> float32 CIE Lab (L 0..100), D65 white."""
    return cv2.cvtColor(rgb.astype(np.float32) / 255.0, cv2.COLOR_RGB2LAB)


def _rgb_array(image: Image.Image | np.ndarray | Path | bytes) -> np.ndarray:
    if isinstance(image, np.ndarray):
        array = image
    else:
        pil = image if isinstance(image, Image.Image) else open_oriented(image)
        pil = to_rgb_or_rgba(pil)
        if pil.mode == "RGBA":
            # Transparent pixels count as background: composite onto white before measuring.
            background = Image.new("RGB", pil.size, (255, 255, 255))
            background.paste(pil, mask=pil.getchannel("A"))
            pil = background
        array = np.asarray(pil)
    if array.ndim != 3 or array.shape[2] < 3:
        raise ValueError("Expected an RGB image")
    return np.ascontiguousarray(array[:, :, :3].astype(np.uint8, copy=False))


def foreground_mask(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Boolean foreground mask (after the 3 px open) and the Lab background colour of an RGB ``uint8`` image."""
    height, width = rgb.shape[:2]
    lab = _srgb_to_lab(rgb)
    ring = max(1, round(BORDER_RING_FRACTION * min(height, width)))
    border = np.ones((height, width), dtype=bool)
    border[ring : height - ring, ring : width - ring] = False
    background = np.median(lab[border], axis=0).astype(np.float32)
    delta_e = np.linalg.norm(lab - background, axis=2)
    raw = (delta_e > DELTA_E_THRESHOLD).astype(np.uint8)
    kernel = np.ones((OPEN_KERNEL_PX, OPEN_KERNEL_PX), dtype=np.uint8)
    opened = cv2.morphologyEx(raw, cv2.MORPH_OPEN, kernel)
    return opened.astype(bool), background


def assess_readiness(image: Image.Image | np.ndarray | Path | bytes, *, preset: str) -> ReadinessReport:
    """Readiness of one candidate image for image-to-3D (warnings only)."""
    rgb = _rgb_array(image)
    height, width = rgb.shape[:2]
    area = float(height * width)
    mask, background = foreground_mask(rgb)
    coverage = float(mask.sum()) / area if area else 0.0

    issues: set[str] = set()
    bbox: list[int] | None = None
    subjects = 0
    if mask.any():
        ys, xs = np.nonzero(mask)
        x0, x1, y0, y1 = int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())
        bbox = [x0, y0, x1, y1]
        margin_x = EDGE_MARGIN_FRACTION * width
        margin_y = EDGE_MARGIN_FRACTION * height
        if x0 < margin_x or y0 < margin_y or (width - 1 - x1) < margin_x or (height - 1 - y1) < margin_y:
            issues.add(TOUCHES_BORDER)
        if preset == "character" and (x1 - x0 + 1) > (y1 - y0 + 1):
            issues.add(ASPECT_MISMATCH)
        count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
        component_areas = stats[1:count, cv2.CC_STAT_AREA] if count > 1 else np.zeros(0, dtype=np.int32)
        subjects = int(np.count_nonzero(component_areas > SUBJECT_MIN_AREA_FRACTION * area))
        if subjects > 1:
            issues.add(MULTIPLE_SUBJECTS)
    if coverage < COVERAGE_LOW:
        issues.add(COVERAGE_LOW_CODE)
    elif coverage > COVERAGE_HIGH:
        issues.add(COVERAGE_HIGH_CODE)

    ordered = tuple(code for code in ISSUE_ORDER if code in issues)
    metrics = {
        "coverage": round(coverage, 4),
        "bbox": bbox,
        "subjects": subjects,
        "background_lab": [round(float(v), 2) for v in background],
        "size": [width, height],
    }
    return ReadinessReport(ok=not ordered, issues=ordered, metrics=metrics)
