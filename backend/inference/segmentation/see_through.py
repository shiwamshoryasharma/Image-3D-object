"""See-through ``l2d_sam_iter2`` anime part parser (``see_through_l2d``; part-aware DESIGN.md sections 4.5.3, 5.2, 5.6).

Model code: the pinned submodule ``backend/third_party/see-through`` (shitagaki-lab/see-through, Apache-2.0),
imported only through :class:`~inference.providers.vendor_import.VendoredPackage` (``img3d_vendor.see_through.*``;
never ``sys.path``). Only ``common/modules/semanticsam.py`` and what it imports (the SAM-HQ ViT-H modelling code,
``common/utils/{torch_utils,io_utils}.py``) are executed: model definitions, no network access, no downloads. Their
``requirements*.txt`` are never installed; everything they import (torch, torchvision, einops, accelerate,
huggingface_hub, safetensors, PyYAML, opencv) is already part of the backend.

Weights: ``backend/.model/seg/l2d_sam_iter2/checkpoint-18000.pt`` (``24yearsold/l2d_sam_iter2``), read with
``torch.load(weights_only=True)`` into a ``SemanticSam(class_num=19)`` built on empty (meta) parameters, then
``load_state_dict(strict=True, assign=True)``: the checkpoint is bfloat16 and every key matches. The model runs
closed-set (prompts are zeroed upstream) at its native 1024 px long side and returns 19 logit planes; upstream uses
``logits > 0`` as the mask.

Output contract (S5, ``Parser2D.parse``): one 8-bit soft PNG (``sigmoid(logit) * 255``, input image size) per
taxonomy class in ``out_dir``; ``score`` = mean probability inside the mask (0..1); ``source`` = the model id.
Sides are character-relative (image-right = character left): ``eyes`` and ``ears`` are split into connected
components and each component's side is decided against the face mask centre; an ``ears`` component whose centre lies
above the face is ``appendage.animal_ears`` (research section 7.2). Classes with an empty mask are omitted.

Provider rules (section 5.6): ``load()`` on a CPU thread, :meth:`parse` on the ``gpu-worker`` thread under
:attr:`lock`; torch is imported inside functions only; files are written only into the given ``out_dir``.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import MappingProxyType
from typing import Any, ClassVar, Final

import numpy as np

from config.settings import Settings
from inference.base import CancellationToken
from inference.providers.base import ModelSpec
from inference.providers.vendor_import import VendoredPackage
from inference.segmentation.base import Mask2D, Parser2D
from inference.segmentation.gdino import TransformersSegmentationProvider, open_rgb

logger = logging.getLogger(__name__)

CHECKPOINT_FILE: Final = "checkpoint-18000.pt"
CLASS_NAMES: Final[tuple[str, ...]] = (
    "hair", "headwear", "face", "eyes", "eyewear", "ears", "earwear", "nose", "mouth",
    "neck", "neckwear", "topwear", "handwear", "bottomwear", "legwear", "footwear",
    "tail", "wings", "objects",
)  # fmt: skip
"""Output order of the 19 class decoders (upstream ``VALID_BODY_PARTS_V2``, used by ``sam_infer_l2d``)."""

LABEL_MAP: Final[Mapping[str, str]] = MappingProxyType(
    {
        "hair": "hair",
        "headwear": "accessory.headwear",
        "face": "body.head.face",
        "eyes": "eye",  # sided family: split by connected components
        "eyewear": "accessory.eyewear",
        "ears": "body.head.ear",  # sided family (or appendage.animal_ears above the face)
        "earwear": "accessory.earwear",
        "nose": "body.head.nose",
        "mouth": "body.head.mouth",
        "neck": "body.neck",
        "neckwear": "clothing.neckwear",
        "topwear": "clothing.top",
        "handwear": "clothing.handwear",
        "bottomwear": "clothing.bottom",
        "legwear": "clothing.legwear",
        "footwear": "clothing.footwear",
        "tail": "appendage.tail",
        "wings": "appendage.wings",
        "objects": "object.held",
    }
)
"""Source class -> taxonomy id (segmentation research section 7.2); a family id (``eye``) means "split by side"."""

SIDED_FAMILIES: Final = frozenset({"eye", "body.head.ear"})
"""Label-map families whose ``.left``/``.right`` side the parser decides from the mask geometry (face features)."""
FACE_ID: Final = "body.head.face"
EARS_FAMILY: Final = "body.head.ear"
ANIMAL_EARS_ID: Final = "appendage.animal_ears"
MIN_COMPONENT_PX: Final = 16
MIN_COMPONENT_FRACTION: Final = 0.05
"""Connected components smaller than this share of the family's largest component (or 16 px) are dropped."""


# --------------------------------------------------------------------------------------------------
# Shared parser machinery (also used by inference/segmentation/sapiens2.py)
# --------------------------------------------------------------------------------------------------

Plane = tuple[str, np.ndarray, np.ndarray]
"""``(source class, soft probability float32 HxW in 0..1, binary bool HxW)`` at the input image size."""


class VendoredParserProvider(TransformersSegmentationProvider, Parser2D):
    """Lifecycle of a closed-set parser whose model code is vendored (``VendoredPackage``), plus ``parse()``."""

    vendored_name: ClassVar[str]
    label_map: Mapping[str, str]

    def __init__(self, spec: ModelSpec, settings: Settings, model_dir: Path | None = None) -> None:
        super().__init__(spec, settings, model_dir)
        if spec.vendored_source is None:
            raise ValueError(f"{spec.id} has no vendored_source")
        self.vendored = VendoredPackage(self.vendored_name, Path(settings.backend_dir) / spec.vendored_source)
        self.last_timings: dict[str, float] = {}

    def _predict(self, model: Any, rgb: np.ndarray) -> list[Plane]:  # pragma: no cover - abstract
        raise NotImplementedError

    def parse(self, image: Path, out_dir: Path, cancel: CancellationToken) -> list[Mask2D]:
        with self.lock:
            model = self._model_on_device(cancel)
            rgb = np.asarray(open_rgb(image))
            cancel.raise_if_cancelled()
            started = time.perf_counter()
            planes = self._predict(model, rgb)
            forward_s = time.perf_counter() - started
            cancel.raise_if_cancelled()
            masks = masks_from_planes(planes, self.label_map, out_dir, source=self.model_id)
            self.last_timings = {"forward_s": round(forward_s, 3), "total_s": round(time.perf_counter() - started, 3)}
            logger.info("%s: %d mask(s) in %.2f s", self.model_id, len(masks), self.last_timings["total_s"])
            return masks


def masks_from_planes(
    planes: Sequence[Plane], label_map: Mapping[str, str], out_dir: Path, *, source: str
) -> list[Mask2D]:
    """Map source planes to taxonomy masks (merge same-target planes, split sided families) and write them."""
    merged: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for label, soft, binary in planes:
        target = label_map.get(label)
        if target is None or not binary.any():
            continue
        if target in merged:
            prev_soft, prev_bin = merged[target]
            merged[target] = (np.minimum(prev_soft + soft, 1.0), prev_bin | binary)  # union of exclusive classes
        else:
            merged[target] = (soft.astype(np.float32, copy=False), binary.astype(bool, copy=False))

    face = merged.get(FACE_ID)
    face_box = _bbox(face[1]) if face is not None else None
    face_x = (face_box[0] + face_box[2]) / 2 if face_box is not None else None
    outputs: list[tuple[str, np.ndarray, np.ndarray]] = []
    for target, (soft, binary) in merged.items():
        if target not in SIDED_FAMILIES:
            outputs.append((target, soft, binary))
            continue
        for class_id, part in split_sides(binary, target, face_x=face_x, face_top=face_box[1] if face_box else None):
            outputs.append((class_id, np.where(part, soft, 0.0).astype(np.float32), part))

    out_dir.mkdir(parents=True, exist_ok=True)
    masks: list[Mask2D] = []
    for index, (class_id, soft, binary) in enumerate(outputs):
        box = _bbox(binary)
        if box is None:
            continue
        path = out_dir / f"{index:03d}_{_slug(class_id)}.png"
        _write_png(path, soft)
        masks.append(
            Mask2D(
                class_id=class_id,
                path=path,
                score=float(np.clip(soft[binary].mean(), 0.0, 1.0)),
                bbox=box,
                source=source,
                instance=index,
            )
        )
    return masks


def split_sides(
    binary: np.ndarray, family: str, *, face_x: float | None, face_top: float | None
) -> list[tuple[str, np.ndarray]]:
    """``[(class_id, mask), ...]``: components grouped by character-relative side (image-right = left).

    The reference is the face centre for face features (eyes, ears), else the midpoint of the components. An ears
    component whose centre lies above the face top is ``appendage.animal_ears``.
    """
    import cv2

    count, labels, stats, centroids = cv2.connectedComponentsWithStats(binary.astype(np.uint8), connectivity=8)
    if count <= 1:
        return []
    areas = stats[1:, cv2.CC_STAT_AREA]
    keep_min = max(MIN_COMPONENT_PX, MIN_COMPONENT_FRACTION * float(areas.max()))
    kept = [i + 1 for i, area in enumerate(areas) if area >= keep_min]
    if not kept:
        return []
    face_feature = family in {"eye", EARS_FAMILY}
    if face_feature and face_x is not None:
        reference = face_x
    else:
        xs = [float(centroids[i][0]) for i in kept]
        reference = (min(xs) + max(xs)) / 2 if len(xs) > 1 else float(binary.shape[1]) / 2
    groups: dict[str, np.ndarray] = {}
    for comp in kept:
        cx, cy = float(centroids[comp][0]), float(centroids[comp][1])
        if family == EARS_FAMILY and face_top is not None and cy < face_top:
            class_id = ANIMAL_EARS_ID
        else:
            class_id = f"{family}.{'left' if cx > reference else 'right'}"
        part = labels == comp
        groups[class_id] = groups[class_id] | part if class_id in groups else part
    return sorted(groups.items())


def _bbox(binary: np.ndarray) -> tuple[int, int, int, int] | None:
    ys, xs = np.nonzero(binary)
    if xs.size == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def _write_png(path: Path, soft: np.ndarray) -> None:
    from PIL import Image

    Image.fromarray(np.clip(np.round(soft * 255.0), 0, 255).astype(np.uint8), "L").save(path)


def _slug(class_id: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in class_id)


# --------------------------------------------------------------------------------------------------
# See-through
# --------------------------------------------------------------------------------------------------


class SeeThroughProvider(VendoredParserProvider):
    """Closed-set anime part masks from the vendored See-through ``SemanticSam`` (SAM-HQ ViT-H, 19 decoders)."""

    vendored_name = "see_through"
    label_map = LABEL_MAP

    def _load_components(self) -> tuple[Any, Any]:
        import accelerate
        import torch

        semanticsam = self.vendored.import_module("modules.semanticsam")
        with accelerate.init_empty_weights(include_buffers=False):
            model = semanticsam.SemanticSam(class_num=len(CLASS_NAMES))
        state = torch.load(self.model_dir / CHECKPOINT_FILE, map_location="cpu", weights_only=True)
        if isinstance(state, dict) and "state_dict" in state:
            state = state["state_dict"]
        model.load_state_dict(state, strict=True, assign=True)
        return None, model

    def _predict(self, model: Any, rgb: np.ndarray) -> list[Plane]:
        import torch

        with torch.inference_mode():
            logits = model.inference(rgb)[0].float()  # (19, H, W) at the input size
            probs = torch.sigmoid(logits).cpu().numpy()
            binary = (logits > 0).cpu().numpy()
        return [(name, probs[i], binary[i]) for i, name in enumerate(CLASS_NAMES)]
