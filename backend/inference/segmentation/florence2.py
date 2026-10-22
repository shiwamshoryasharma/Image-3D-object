"""Florence-2-large grounder provider (``florence2_large``; part-aware DESIGN.md sections 5.2 and 5.6).

Provider rules (section 5.6; enforced by review): the transformers-native ``Florence2ForConditionalGeneration``
(4.57.6) in fp16 on CUDA (fp32 on a CPU device), ``local_files_only=True``, never ``trust_remote_code``, never a
network call; ``load()`` on a CPU thread, role methods on the ``gpu-worker`` thread under :attr:`lock`;
torch/transformers imported inside functions only.

* ``caption()`` runs the ``<DETAILED_CAPTION>`` task (used by ``subject: auto``). Measured on the ALICE and
  ANIME_CHARACTER_01 samples (2026-10-22): ``<CAPTION>`` never names the drawing style ("A woman in a blue and white
  dress ..."), so the section 4.5.3 keyword rules classified every anime sample as ``realistic_human``;
  ``<DETAILED_CAPTION>`` says "an animated girl ..." on all of them.
* ``ground()`` runs ``<CAPTION_TO_PHRASE_GROUNDING>`` once per phrase. Florence-2 has no box scores, so the score of
  every box of a phrase is the beam's length-normalised sequence probability (``exp(sequences_scores)``).
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

from inference.base import CancellationToken
from inference.segmentation.base import Box2D, Grounder2D
from inference.segmentation.gdino import TransformersSegmentationProvider, open_rgb, phrase_entries

logger = logging.getLogger(__name__)

CAPTION_TASK: Final = "<DETAILED_CAPTION>"
GROUNDING_TASK: Final = "<CAPTION_TO_PHRASE_GROUNDING>"
NUM_BEAMS: Final = 3
CAPTION_MAX_TOKENS: Final = 96
GROUNDING_MAX_TOKENS: Final = 256


class Florence2Provider(TransformersSegmentationProvider, Grounder2D):
    """Captions and phrase grounding from ``florence-community/Florence-2-large``."""

    def _load_components(self) -> tuple[Any, Any]:
        import torch
        from transformers import AutoProcessor, Florence2ForConditionalGeneration

        dtype = torch.float16 if self.uses_cuda else torch.float32
        processor = AutoProcessor.from_pretrained(self.model_dir, local_files_only=True)
        model = Florence2ForConditionalGeneration.from_pretrained(
            self.model_dir, local_files_only=True, torch_dtype=dtype
        )
        return processor, model

    def _generate(self, model: Any, img: Any, prompt: str, max_new_tokens: int) -> tuple[Any, float]:
        import torch

        assert self._processor is not None
        device = next(model.parameters()).device
        dtype = next(model.parameters()).dtype
        encoded = self._processor(text=prompt, images=img, return_tensors="pt")
        inputs = {k: (v.to(device, dtype) if v.is_floating_point() else v.to(device)) for k, v in encoded.items()}
        with torch.inference_mode():
            out = model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                num_beams=NUM_BEAMS,
                do_sample=False,
                return_dict_in_generate=True,
                output_scores=True,
            )
        decoded = self._processor.batch_decode(out.sequences, skip_special_tokens=False)[0]
        scores = getattr(out, "sequences_scores", None)
        probability = math.exp(float(scores[0])) if scores is not None else 0.0
        return decoded, probability

    def caption(self, image: Path, cancel: CancellationToken) -> str:
        with self.lock:
            model = self._model_on_device(cancel)
            img = open_rgb(image)
            decoded, _probability = self._generate(model, img, CAPTION_TASK, CAPTION_MAX_TOKENS)
            assert self._processor is not None
            parsed = self._processor.post_process_generation(decoded, task=CAPTION_TASK, image_size=img.size)
            caption = str(parsed.get(CAPTION_TASK, "")).strip()
            logger.info("%s caption: %s", self.model_id, caption)
            return caption

    def ground(self, image: Path, phrases: Mapping[str, tuple[str, ...]], cancel: CancellationToken) -> list[Box2D]:
        with self.lock:
            model = self._model_on_device(cancel)
            img = open_rgb(image)
            width, height = img.size
            boxes: list[Box2D] = []
            assert self._processor is not None
            for class_id, phrase in phrase_entries(phrases):
                cancel.raise_if_cancelled()
                decoded, probability = self._generate(model, img, GROUNDING_TASK + phrase, GROUNDING_MAX_TOKENS)
                parsed = self._processor.post_process_generation(decoded, task=GROUNDING_TASK, image_size=img.size)
                result = parsed.get(GROUNDING_TASK) or {}
                for bbox in result.get("bboxes") or []:
                    x0, y0, x1, y1 = (float(v) for v in bbox)
                    x0, y0 = max(0.0, x0), max(0.0, y0)
                    x1, y1 = min(float(width), x1), min(float(height), y1)
                    if x1 - x0 < 1 or y1 - y0 < 1:
                        continue
                    boxes.append(
                        Box2D(
                            class_id=class_id,
                            xyxy=(x0, y0, x1, y1),
                            score=probability,
                            source=self.model_id,
                            phrase=phrase,
                        )
                    )
            return boxes
