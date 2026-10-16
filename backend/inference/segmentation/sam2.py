"""SAM 2.1 hiera-large mask refiner provider (``sam2_1_hiera_large``; part-aware DESIGN.md sections 5.2 and 5.6).

Provider rules (section 5.6; enforced by review): ``Sam2Model`` / ``Sam2Processor`` from transformers 4.57.6 with
``local_files_only=True`` (never ``trust_remote_code``, never a network call); ``load()`` on a CPU thread, the role
method on the ``gpu-worker`` thread under :attr:`lock`; torch/transformers imported inside functions only; masks are
written only into the ``out_dir`` the caller gives.

``refine()`` computes the image embedding once and prompts it with every box. Small boxes (at most
:data:`POINT_MAX_AREA_FRACTION` of the image: eyes, brows, jewelry) also get a positive point at the box centre; for
large boxes (hair, clothes) the centre often lies on another part, so they are prompted with the box alone. Each
mask is written as an 8-bit soft mask (``sigmoid(logit) * 255``) at the input image size. Masks with an empty
foreground or a predicted IoU below :data:`MIN_PREDICTED_IOU` are dropped.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

from inference.base import CancellationToken
from inference.segmentation.base import Box2D, Mask2D, MaskRefiner
from inference.segmentation.gdino import TransformersSegmentationProvider, open_rgb

logger = logging.getLogger(__name__)

POINT_MAX_AREA_FRACTION: Final = 0.05
MIN_PREDICTED_IOU: Final = 0.3
PROMPT_CHUNK: Final = 32


class Sam2Provider(TransformersSegmentationProvider, MaskRefiner):
    """Box (+ point) prompted masks from ``facebook/sam2.1-hiera-large``."""

    def _load_components(self) -> tuple[Any, Any]:
        from transformers import Sam2Model, Sam2Processor

        processor = Sam2Processor.from_pretrained(self.model_dir, local_files_only=True)
        model = Sam2Model.from_pretrained(self.model_dir, local_files_only=True)
        return processor, model

    def refine(self, image: Path, boxes: Sequence[Box2D], out_dir: Path, cancel: CancellationToken) -> list[Mask2D]:
        import numpy as np
        import torch
        from PIL import Image

        if not boxes:
            return []
        with self.lock:
            model = self._model_on_device(cancel)
            processor = self._processor
            assert processor is not None
            img = open_rgb(image)
            width, height = img.size
            out_dir.mkdir(parents=True, exist_ok=True)
            device = next(model.parameters()).device
            encoded = processor(images=img, return_tensors="pt")
            original_sizes = encoded["original_sizes"]
            with torch.inference_mode():
                embeddings = model.get_image_embeddings(encoded["pixel_values"].to(device))
            image_area = float(width * height)
            indexed = list(enumerate(boxes))
            small = [(i, b) for i, b in indexed if _area(b) <= POINT_MAX_AREA_FRACTION * image_area]
            large = [(i, b) for i, b in indexed if _area(b) > POINT_MAX_AREA_FRACTION * image_area]
            results: dict[int, Mask2D] = {}
            for group, with_points in ((small, True), (large, False)):
                for start in range(0, len(group), PROMPT_CHUNK):
                    cancel.raise_if_cancelled()
                    chunk = group[start : start + PROMPT_CHUNK]
                    kwargs: dict[str, Any] = {
                        "input_boxes": [[[float(v) for v in b.xyxy] for _i, b in chunk]],
                        "original_sizes": original_sizes,
                        "return_tensors": "pt",
                    }
                    if with_points:
                        kwargs["input_points"] = [[[_center(b)] for _i, b in chunk]]
                        kwargs["input_labels"] = [[[1] for _chunk in chunk]]
                    prompts = processor(**kwargs)
                    inputs = {k: v.to(device) for k, v in prompts.items() if k != "original_sizes"}
                    with torch.inference_mode():
                        outputs = model(image_embeddings=embeddings, multimask_output=False, **inputs)
                    logits = processor.post_process_masks(
                        outputs.pred_masks.float().cpu(), original_sizes.cpu(), binarize=False
                    )[0]
                    ious = outputs.iou_scores.float().cpu().reshape(len(chunk), -1)[:, 0].tolist()
                    for (index, box), mask_logits, iou in zip(chunk, logits, ious, strict=True):
                        soft = torch.sigmoid(mask_logits.reshape(height, width)).numpy()
                        binary = soft >= 0.5
                        if not binary.any() or iou < MIN_PREDICTED_IOU:
                            continue
                        ys, xs = np.nonzero(binary)
                        path = out_dir / f"{index:03d}_{_slug(box.class_id)}.png"
                        Image.fromarray(np.clip(np.round(soft * 255), 0, 255).astype(np.uint8), "L").save(path)
                        results[index] = Mask2D(
                            class_id=box.class_id,
                            path=path,
                            score=float(box.score),
                            bbox=(int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1),
                            source=self.model_id,
                            instance=index,
                        )
            logger.info("%s: %d box(es) -> %d mask(s)", self.model_id, len(boxes), len(results))
            return [results[i] for i in sorted(results)]


def _area(box: Box2D) -> float:
    x0, y0, x1, y1 = box.xyxy
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


def _center(box: Box2D) -> list[float]:
    x0, y0, x1, y1 = box.xyxy
    return [(x0 + x1) / 2, (y0 + y1) / 2]


def _slug(class_id: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in class_id)
