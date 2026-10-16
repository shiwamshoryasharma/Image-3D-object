"""2D label fusion (part-aware DESIGN.md section 4.5.4). numpy/opencv only; torch-free.

Input: mask candidates in the *analysis image* (the image that was segmented) from a dense parser, from detector +
refiner pairs (Grounding DINO boxes refined by SAM 2.1, Florence-2 grounding) and from both passes (full image and
head crop, already warped into the analysis image). Output: one 16-bit label map (value = taxonomy value), one soft
mask per class, label statistics and 2D annotations.

Rules:

* **Mapping.** Candidates carry taxonomy class ids. A sided class (``eye.left``) stands for its family: the final
  side is decided here (character-relative: image-right = character left).
* **Source preference.** Dense classes (hair, face, body, clothes) prefer the parser; small or rare classes (eyes,
  brows, lashes, accessories, objects) prefer detector + refiner. The other role is used only when the preferred one
  found nothing for that class.
* **Gates.** ``min_confidence`` per candidate and a minimum area of 0.02 % of the frame per class; pixels that fail
  become ``unknown`` (0).
* **Per-pixel argmax ordered by ``z_priority``.** Classes are painted from back to front (ascending
  ``z_priority``; at equal priority the larger mask first, so the more specific class wins) wherever their soft mask
  is at least 0.5.
* **Eyes, eyebrows, eyelashes, eyelids** follow :mod:`processing.seg2d.eyes`.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Literal

import cv2
import numpy as np

from parts.taxonomy import BY_VALUE, TAXONOMY, UNKNOWN, PartClass
from processing.seg2d import eyes as eye_rules

CandidateRole = Literal["parser", "detector", "heuristic"]

MIN_AREA_FRACTION: Final = 0.0002
"""Minimum class area: 0.02 % of the frame."""
SOFT_THRESHOLD: Final = 0.5
NMS_IOU: Final = 0.5
DENSE_GROUPS: Final = frozenset({"hair", "face", "body", "clothes"})
"""Groups whose classes prefer the parser; every other group prefers detector + refiner."""
FACE_FEATURE_FAMILIES: Final = frozenset({"eye", "eyebrow", "eyelash", "body.head.ear", "body.head.eyelid"})
"""Sided families whose side is decided relative to the face (the rest relative to the subject)."""
HEURISTIC_SOURCE: Final = "heuristic2d"


def family_of(class_id: str) -> str:
    """``eye.left`` -> ``eye``; unsided ids are their own family."""
    if class_id.endswith(".left"):
        return class_id[: -len(".left")]
    if class_id.endswith(".right"):
        return class_id[: -len(".right")]
    return class_id


def sided_ids(family: str) -> tuple[str, str] | None:
    left, right = f"{family}.left", f"{family}.right"
    if left in TAXONOMY and right in TAXONOMY:
        return left, right
    return None


@dataclass(slots=True)
class MaskCandidate:
    """One soft mask of one class instance in the analysis image."""

    class_id: str
    mask: np.ndarray
    """float32 in 0..1, shape ``(height, width)`` of the analysis image."""
    score: float
    role: CandidateRole
    sources: tuple[str, ...]
    box: tuple[float, float, float, float] | None = None
    head_pass: bool = False

    @property
    def family(self) -> str:
        return family_of(self.class_id)


@dataclass(frozen=True, slots=True)
class LabelStat:
    value: int
    category: str
    group: str
    area_px: int
    confidence: float
    bbox: tuple[int, int, int, int]
    side: str | None
    sources: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "category": self.category,
            "group": self.group,
            "area_px": self.area_px,
            "confidence": round(float(self.confidence), 4),
            "bbox": list(self.bbox),
            "side": self.side,
            "sources": list(self.sources),
        }


@dataclass(slots=True)
class FusionOutput:
    labels: np.ndarray
    """uint16 label map (value = taxonomy value; 0 = unknown/background)."""
    soft: dict[int, np.ndarray]
    """value -> uint8 soft mask (0 outside the class's final pixels)."""
    stats: list[LabelStat]
    annotations: dict[str, dict[str, Any]] = field(default_factory=dict)
    rejected: list[dict[str, Any]] = field(default_factory=list)
    """Candidates dropped by a rule (e.g. an eyebrow box away from the eyes), for the run details."""


@dataclass(slots=True)
class _ClassLayer:
    cls: PartClass
    soft: np.ndarray
    score: float
    sources: set[str]
    heuristic: bool = False


# --------------------------------------------------------------------------------------------------
# Boxes
# --------------------------------------------------------------------------------------------------


def box_iou(a: Sequence[float], b: Sequence[float]) -> float:
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def nms(boxes: Sequence[Sequence[float]], scores: Sequence[float], iou: float = NMS_IOU) -> list[int]:
    """Indices kept by greedy non-maximum suppression, best first."""
    order = sorted(range(len(boxes)), key=lambda i: -scores[i])
    kept: list[int] = []
    for i in order:
        if all(box_iou(boxes[i], boxes[j]) <= iou for j in kept):
            kept.append(i)
    return kept


def head_box(
    face: Sequence[float] | None,
    eyes: Iterable[Sequence[float]],
    hair: Sequence[float] | None,
    size: tuple[int, int],
    margin: float = 0.25,
) -> tuple[float, float, float, float] | None:
    """Union of the face, eye and hair-top boxes plus a ``margin``, squared, in ``size = (width, height)``.

    The hair box only contributes its top edge (long hair would otherwise pull the box down to the waist), limited
    to one face height above the face. ``None`` without a face or an eye box.
    """
    boxes = [tuple(b) for b in eyes]
    if face is not None:
        boxes.append(tuple(face))
    if not boxes:
        return None
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[2] for b in boxes)
    y1 = max(b[3] for b in boxes)
    if hair is not None:
        reach = (face[3] - face[1]) if face is not None else (y1 - y0) * 2
        y0 = min(y0, max(hair[1], y0 - reach))
    w, h = x1 - x0, y1 - y0
    side = max(w, h) * (1 + 2 * margin)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    width, height = size
    side = min(side, float(max(width, height)))
    return cx - side / 2, cy - side / 2, cx + side / 2, cy + side / 2


# --------------------------------------------------------------------------------------------------
# Fusion
# --------------------------------------------------------------------------------------------------


def fuse(
    candidates: Sequence[MaskCandidate],
    image_rgb: np.ndarray,
    *,
    min_confidence: float,
    classes: Iterable[str] | None = None,
) -> FusionOutput:
    """Fuse mask candidates into one label map (section 4.5.4)."""
    height, width = image_rgb.shape[:2]
    min_area = max(1, int(round(MIN_AREA_FRACTION * height * width)))
    allowed = None if classes is None else frozenset(classes)
    lightness = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2LAB)[:, :, 0]
    rejected: list[dict[str, Any]] = []

    kept = [c for c in candidates if c.score >= min_confidence and c.class_id in TAXONOMY and c.class_id != UNKNOWN.id]
    for c in candidates:
        if c.score < min_confidence:
            rejected.append({"class_id": c.class_id, "reason": "low_confidence", "score": round(c.score, 3)})
    kept = _prefer_sources(kept)
    by_family: dict[str, list[MaskCandidate]] = {}
    for c in kept:
        by_family.setdefault(c.family, []).append(c)

    layers: dict[str, _ClassLayer] = {}
    face_layer = _union_layer(by_family.get("body.head.face", []))
    face_mask = face_layer[0] >= SOFT_THRESHOLD if face_layer is not None else None
    face_x = _center_x(face_mask) if face_mask is not None and face_mask.any() else None
    subject_x = _subject_center_x(kept, width)

    # Eyes first: their geometry validates the eyebrows and drives the lashes and lids.
    eye_instances: list[eye_rules.EyeInstance] = []
    eye_union = _union_layer(by_family.pop("eye", []))
    if eye_union is not None:
        soft, score, sources = eye_union
        eye_instances = eye_rules.build_eye_instances(
            soft, score, sources, face_x if face_x is not None else subject_x, min_area
        )
        for inst in eye_instances:
            class_id = f"eye.{inst.side}"
            layers[class_id] = _ClassLayer(TAXONOMY[class_id], np.where(inst.mask, soft, 0.0), score, set(sources))

    brow_masks = _eyebrows(by_family.pop("eyebrow", []), eye_instances, lightness, min_area, layers, rejected)
    by_family.pop("eyelash", None)  # eyelashes are always the heuristic of section 4.5.4
    by_family.pop("body.head.eyelid", None)  # eyelids are always the ring heuristic

    for family, items in by_family.items():
        pair = sided_ids(family)
        if pair is None:
            union = _union_layer(items)
            if union is not None:
                soft, score, sources = union
                layers[family] = _ClassLayer(TAXONOMY[family], soft, score, set(sources))
            continue
        reference = face_x if family in FACE_FEATURE_FAMILIES and face_x is not None else subject_x
        for side, side_items in _split_instances_by_side(items, reference).items():
            union = _union_layer(side_items)
            if union is None:
                continue
            soft, score, sources = union
            class_id = f"{family}.{side}"
            layers[class_id] = _ClassLayer(TAXONOMY[class_id], soft, score, set(sources))

    labels = np.zeros((height, width), dtype=np.uint16)
    areas = {cid: int((layer.soft >= SOFT_THRESHOLD).sum()) for cid, layer in layers.items()}
    order = sorted(layers, key=lambda cid: (layers[cid].cls.z_priority, -areas[cid], layers[cid].cls.value))
    for class_id in order:
        if allowed is not None and class_id not in allowed:
            continue
        labels[layers[class_id].soft >= SOFT_THRESHOLD] = layers[class_id].cls.value

    annotations: dict[str, dict[str, Any]] = {}
    lash_masks: dict[str, np.ndarray] = {}
    for inst in eye_instances:
        eye_value = TAXONOMY[f"eye.{inst.side}"].value
        final_eye = labels == eye_value if (allowed is None or f"eye.{inst.side}" in allowed) else inst.mask
        if not final_eye.any():
            continue
        current = eye_rules.EyeInstance(
            side=inst.side,
            mask=final_eye,
            score=inst.score,
            sources=inst.sources,
            bbox=eye_rules.mask_bbox(final_eye) or inst.bbox,
        )
        lash_id = f"eyelash.{inst.side}"
        lashes = eye_rules.eyelash_mask(lightness, current, brow_masks.get(inst.side), min_area)
        if lashes is not None:
            mask, annotation = lashes
            confidence = float(annotation["confidence"])  # type: ignore[arg-type]
            lash_masks[inst.side] = mask
            annotations[lash_id] = annotation
            layers[lash_id] = _ClassLayer(
                TAXONOMY[lash_id], mask.astype(np.float32) * confidence, confidence, {HEURISTIC_SOURCE}, True
            )
            if allowed is None or lash_id in allowed:
                paintable = _below_priority(labels, TAXONOMY[lash_id].z_priority)
                labels[mask & paintable] = TAXONOMY[lash_id].value
        lid_id = f"body.head.eyelid.{inst.side}"
        if face_mask is not None and (allowed is None or lid_id in allowed):
            ring = eye_rules.eyelid_ring(
                current.mask, current.height, brow_masks.get(inst.side), lash_masks.get(inst.side), face_mask
            )
            face_value = TAXONOMY["body.head.face"].value
            ring &= (labels == 0) | (labels == face_value)
            if ring.any():
                confidence = round(min(inst.score, eye_rules.BROW_FALLBACK_CONFIDENCE + 0.1), 3)
                layers[lid_id] = _ClassLayer(
                    TAXONOMY[lid_id], ring.astype(np.float32) * confidence, confidence, {HEURISTIC_SOURCE}, True
                )
                labels[ring] = TAXONOMY[lid_id].value

    stats: list[LabelStat] = []
    soft_out: dict[int, np.ndarray] = {}
    for value in sorted(int(v) for v in np.unique(labels) if v != 0):
        region = labels == value
        area = int(region.sum())
        cls = BY_VALUE[value]
        if area < min_area:
            labels[region] = 0
            rejected.append({"class_id": cls.id, "reason": "too_small", "area_px": area})
            continue
        layer = layers.get(cls.id)
        if layer is None:  # pragma: no cover - every painted value has a layer
            continue
        soft_u8 = np.zeros((height, width), dtype=np.uint8)
        soft_u8[region] = np.clip(np.round(layer.soft[region] * 255), 1, 255).astype(np.uint8)
        soft_out[value] = soft_u8
        bbox = eye_rules.mask_bbox(region)
        assert bbox is not None
        stats.append(
            LabelStat(
                value=value,
                category=cls.id,
                group=cls.group,
                area_px=area,
                confidence=layer.score,
                bbox=bbox,
                side=cls.side,
                sources=tuple(sorted(layer.sources)),
            )
        )
    for key in list(annotations):
        if TAXONOMY[key].value not in soft_out:
            del annotations[key]
    return FusionOutput(labels=labels, soft=soft_out, stats=stats, annotations=annotations, rejected=rejected)


def _prefer_sources(candidates: list[MaskCandidate]) -> list[MaskCandidate]:
    by_family: dict[str, list[MaskCandidate]] = {}
    for c in candidates:
        by_family.setdefault(c.family, []).append(c)
    result: list[MaskCandidate] = []
    for items in by_family.values():
        roles = {c.role for c in items}
        if {"parser", "detector"} <= roles:
            group = TAXONOMY[items[0].class_id].group
            preferred = "parser" if group in DENSE_GROUPS else "detector"
            items = [c for c in items if c.role == preferred or c.role == "heuristic"]
        head = [c.box for c in items if c.head_pass and c.box is not None]
        if head:
            # The full-resolution head pass is more precise: drop full-image detections it found again.
            items = [c for c in items if c.head_pass or c.role != "detector" or not _covered(c.box, head)]
        result.extend(items)
    return result


def _covered(box: tuple[float, float, float, float] | None, others: Sequence[tuple[float, float, float, float]]) -> bool:
    """``True`` when ``box`` and one of ``others`` overlap by more than half of the smaller box (the same part)."""
    if box is None:
        return False
    area = max(1e-6, (box[2] - box[0]) * (box[3] - box[1]))
    for o in others:
        inter = max(0.0, min(box[2], o[2]) - max(box[0], o[0])) * max(0.0, min(box[3], o[3]) - max(box[1], o[1]))
        other_area = max(1e-6, (o[2] - o[0]) * (o[3] - o[1]))
        if inter / min(area, other_area) > 0.5:
            return True
    return False


def _union_layer(items: Sequence[MaskCandidate]) -> tuple[np.ndarray, float, tuple[str, ...]] | None:
    if not items:
        return None
    soft = items[0].mask.astype(np.float32, copy=True)
    for c in items[1:]:
        np.maximum(soft, c.mask, out=soft)
    score = max(c.score for c in items)
    sources = tuple(sorted({s for c in items for s in c.sources}))
    return soft, float(score), sources


def _split_instances_by_side(items: Sequence[MaskCandidate], reference_x: float) -> dict[str, list[MaskCandidate]]:
    centers = [_candidate_center_x(c) for c in items]
    result: dict[str, list[MaskCandidate]] = {}
    if len(items) >= 2:
        ranked = sorted(range(len(items)), key=lambda i: -items[i].score)[:2]
        a, b = sorted(ranked, key=lambda i: centers[i])
        if abs(centers[a] - centers[b]) > 1.0:
            anchors = {"right": centers[a], "left": centers[b]}
            for c, x in zip(items, centers, strict=True):
                side = min(anchors, key=lambda s: abs(anchors[s] - x))
                result.setdefault(side, []).append(c)
            return result
    for c, x in zip(items, centers, strict=True):
        result.setdefault(eye_rules.side_for_x(x, reference_x), []).append(c)
    return result


def _eyebrows(
    items: Sequence[MaskCandidate],
    eye_instances: Sequence[eye_rules.EyeInstance],
    lightness: np.ndarray,
    min_area: int,
    layers: dict[str, _ClassLayer],
    rejected: list[dict[str, Any]],
) -> dict[str, np.ndarray]:
    """Validated detector eyebrows per side, else the dark-band fallback (confidence <= 0.4)."""
    found: dict[str, tuple[np.ndarray, np.ndarray, float, set[str]]] = {}
    for c in items:
        binary = c.mask >= SOFT_THRESHOLD
        accepted = eye_rules.accept_eyebrow_components(binary, eye_instances, min_area)
        if not accepted:
            rejected.append(
                {
                    "class_id": c.class_id,
                    "reason": "eyebrow_not_above_eye",
                    "box": [round(v, 1) for v in c.box] if c.box is not None else None,
                    "head_pass": c.head_pass,
                }
            )
            continue
        for side, part in accepted.items():
            soft = np.where(part, c.mask, 0.0).astype(np.float32)
            if side in found:
                mask, prev_soft, score, sources = found[side]
                found[side] = (mask | part, np.maximum(prev_soft, soft), max(score, c.score), sources | set(c.sources))
            else:
                found[side] = (part, soft, c.score, set(c.sources))
    result: dict[str, np.ndarray] = {}
    for side, (mask, soft, score, sources) in found.items():
        class_id = f"eyebrow.{side}"
        layers[class_id] = _ClassLayer(TAXONOMY[class_id], soft, score, sources)
        result[side] = mask
    for inst in eye_instances:
        if inst.side in result:
            continue
        exclude = np.zeros_like(inst.mask)
        for other in eye_instances:
            exclude |= other.mask
        fallback = eye_rules.eyebrow_fallback(lightness, inst, exclude, min_area)
        if fallback is None:
            continue
        confidence = round(min(eye_rules.BROW_FALLBACK_CONFIDENCE, inst.score), 3)
        class_id = f"eyebrow.{inst.side}"
        layers[class_id] = _ClassLayer(
            TAXONOMY[class_id], fallback.astype(np.float32) * max(confidence, SOFT_THRESHOLD), confidence,
            {HEURISTIC_SOURCE}, True,
        )
        result[inst.side] = fallback
    return result


def _below_priority(labels: np.ndarray, z_priority: int) -> np.ndarray:
    """Pixels whose current class is behind ``z_priority`` (or unknown)."""
    lookup = np.zeros(int(labels.max()) + 1, dtype=bool)
    for value in range(lookup.size):
        cls = BY_VALUE.get(value)
        lookup[value] = cls is None or cls.z_priority <= z_priority
    return lookup[labels]


def _center_x(mask: np.ndarray) -> float:
    xs = np.nonzero(mask)[1]
    return float(xs.mean()) if xs.size else 0.0


def _candidate_center_x(c: MaskCandidate) -> float:
    if c.box is not None:
        return (c.box[0] + c.box[2]) / 2
    return _center_x(c.mask >= SOFT_THRESHOLD)


def _subject_center_x(candidates: Sequence[MaskCandidate], width: int) -> float:
    union: np.ndarray | None = None
    for c in candidates:
        binary = c.mask >= SOFT_THRESHOLD
        union = binary if union is None else union | binary
    if union is None or not union.any():
        return width / 2
    return _center_x(union)


def overlay_image(image_rgb: np.ndarray, labels: np.ndarray, *, alpha: float = 0.55, max_side: int = 1024) -> np.ndarray:
    """RGBA preview: every label tinted with a stable colour over the image, at most ``max_side`` px."""
    rgb = image_rgb.astype(np.float32)
    out = rgb.copy()
    for value in (int(v) for v in np.unique(labels) if v != 0):
        color = np.array(label_color(value), dtype=np.float32)
        region = labels == value
        out[region] = rgb[region] * (1 - alpha) + color * alpha
    rgba = np.dstack([np.clip(out, 0, 255).astype(np.uint8), np.full(labels.shape, 255, np.uint8)])
    h, w = labels.shape
    if max(h, w) > max_side:
        scale = max_side / max(h, w)
        rgba = cv2.resize(rgba, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
    return rgba


def label_color(value: int) -> tuple[int, int, int]:
    """Deterministic, well-spread colour of a taxonomy value (golden-angle hue)."""
    hue = int((value * 137.508) % 180)
    hsv = np.uint8([[[hue, 200, 255]]])
    r, g, b = cv2.cvtColor(hsv, cv2.COLOR_HSV2RGB)[0, 0]
    return int(r), int(g), int(b)
