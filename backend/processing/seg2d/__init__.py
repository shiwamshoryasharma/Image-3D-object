"""2D segmentation post-processing (part-aware DESIGN.md section 4.5.4): label fusion and eye-area heuristics.

numpy/opencv/scipy only (torch-free). :mod:`processing.seg2d.fusion` turns parser and detector + refiner mask
candidates into one taxonomy label map; :mod:`processing.seg2d.eyes` holds the eye, eyebrow, eyelash and eyelid rules.
"""

from __future__ import annotations

from processing.seg2d.fusion import FusionOutput, LabelStat, MaskCandidate, fuse, nms, overlay_image

__all__ = ["FusionOutput", "LabelStat", "MaskCandidate", "fuse", "nms", "overlay_image"]
