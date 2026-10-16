"""Grounding DINO base detector provider (``grounding_dino_base``; part-aware DESIGN.md sections 5.2 and 5.6).

Provider rules (section 5.6; enforced by review):

* ``from_pretrained(local_dir, local_files_only=True)`` only: never ``trust_remote_code``, never a network call.
* ``load()`` runs on a CPU thread and materialises the weights in CPU RAM; ``detect()`` / ``offload()`` /
  ``unload()`` run on the single ``gpu-worker`` thread. :attr:`lock` serialises all of them.
* ``device = settings.device``; the registry's ``offload()`` after the GPU phase moves the weights back to the CPU.
* Only files and plain dataclasses cross the seam; this module never writes outside the directory it is given.
* torch/transformers are imported inside functions only (``test_import_boundaries.py``).

``detect()`` packs the phrase vocabulary into batches of at most :data:`MAX_PROMPT_TOKENS` tokenizer tokens (the
``". "`` separators and the special tokens counted; grounding-dino-base truncates at ``max_text_len = 256``) and runs
one forward pass per batch, so no class is silently dropped. The batch count and the class ids sent are kept in
:attr:`GroundingDinoProvider.last_batch_count` / :attr:`GroundingDinoProvider.last_class_ids` for the smoke test.

Each query is assigned the phrase whose tokens score highest (token spans from the tokenizer's offset mapping), so
a box is never attributed to a neighbouring phrase of the same batch.

:class:`TransformersSegmentationProvider` is the shared lifecycle of the three transformers-native segmentation
providers (Grounding DINO, SAM 2.1, Florence-2).
"""

from __future__ import annotations

import gc
import logging
import threading
from abc import abstractmethod
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from config.settings import Settings
from inference.base import CancellationToken
from inference.device import is_cuda_device
from inference.providers.base import LoadedDevice, ModelProvider, ModelSpec, ModelUiState, ProviderStatus, ReasonCode
from inference.segmentation.base import Box2D, Detector2D
from utils.timeutil import utcnow

logger = logging.getLogger(__name__)

MAX_PROMPT_TOKENS: Final = 200
"""Token budget of one Grounding DINO prompt (``max_text_len`` is 256)."""
PHRASE_SEPARATOR: Final = ". "


def model_dir_for(spec: ModelSpec, settings: Settings) -> Path:
    """``settings.model_paths[id]`` or ``aux_model_root / local_subdir`` (registry rule 7)."""
    override = settings.model_paths.get(spec.id)
    if override is not None:
        return Path(override)
    if spec.local_subdir is None:
        raise ValueError(f"{spec.id} has no local folder")
    return Path(settings.aux_model_root) / spec.local_subdir


def open_rgb(path: Path) -> Any:
    """The image at ``path`` as a detached RGB ``PIL.Image``."""
    from PIL import Image

    with Image.open(path) as img:
        img.load()
        return img.convert("RGB")


class TransformersSegmentationProvider(ModelProvider):
    """Lifecycle shared by the transformers-native segmentation providers.

    Subclasses implement :meth:`_load_components` (CPU, ``local_files_only=True``) and call :meth:`_model_on_device`
    inside their role methods (GPU thread, holding :attr:`lock`).
    """

    def __init__(self, spec: ModelSpec, settings: Settings, model_dir: Path | None = None) -> None:
        super().__init__(spec)
        self._settings = settings
        self.model_dir = Path(model_dir) if model_dir is not None else model_dir_for(spec, settings)
        self.device = settings.device
        self._processor: Any | None = None
        self._model: Any | None = None
        self._state_lock = threading.Lock()
        self._state: ModelUiState = "installed"
        self._reason_code: ReasonCode = "ok"
        self._reason: str | None = None
        self._loaded_device: LoadedDevice | None = None
        self._last_used: datetime | None = None

    # ------------------------------------------------------------------ state
    @property
    def uses_cuda(self) -> bool:
        return is_cuda_device(self.device)

    def status(self) -> ProviderStatus:
        with self._state_lock:
            return ProviderStatus(
                model_id=self.model_id,
                state=self._state,
                reason_code=self._reason_code,
                reason=self._reason,
                revision=self.spec.revision,
                loaded_device=self._loaded_device,
                last_used_at=self._last_used,
            )

    def _set_state(
        self,
        state: ModelUiState,
        *,
        device: LoadedDevice | None = None,
        reason: str | None = None,
    ) -> None:
        with self._state_lock:
            self._state = state
            self._reason_code = "error" if state == "error" else "ok"
            self._reason = reason
            self._loaded_device = device

    def _touch(self) -> None:
        with self._state_lock:
            self._last_used = utcnow()

    # ------------------------------------------------------------------ lifecycle
    @abstractmethod
    def _load_components(self) -> tuple[Any, Any]:
        """``(processor, model)`` loaded on the CPU from :attr:`model_dir` with ``local_files_only=True``."""

    def load(self, cancel: CancellationToken) -> None:
        with self.lock:
            if self._model is not None:
                return
            cancel.raise_if_cancelled()
            self._set_state("loading")
            try:
                processor, model = self._load_components()
                model.eval()
            except Exception as exc:
                self._set_state("error", reason=f"{type(exc).__name__}: {exc}")
                logger.exception("Could not load %s from %s", self.model_id, self.model_dir)
                raise
            self._processor, self._model = processor, model
            self._set_state("loaded", device="cpu")
            logger.info("%s loaded into CPU RAM from %s", self.model_id, self.model_dir)

    def _model_on_device(self, cancel: CancellationToken) -> Any:
        """The model on :attr:`device` (loads first if needed). GPU thread, inside :attr:`lock`."""
        self.load(cancel)
        assert self._model is not None
        if self.uses_cuda and self.status().loaded_device != "cuda":
            self._model.to(self.device)
            self._set_state("loaded", device="cuda")
        self._touch()
        return self._model

    def offload(self) -> None:
        with self.lock:
            if self._model is None or self.status().loaded_device != "cuda":
                return
            self._model.to("cpu")
            self._set_state("loaded", device="cpu")
            _empty_cuda_cache()

    def unload(self) -> None:
        with self.lock:
            self._model = None
            self._processor = None
            self._set_state("installed")
            gc.collect()
            _empty_cuda_cache()


def _empty_cuda_cache() -> None:
    import torch

    if torch.cuda.is_available():
        torch.cuda.empty_cache()


# --------------------------------------------------------------------------------------------------
# Prompt packing (torch-free helpers, also used by the tests)
# --------------------------------------------------------------------------------------------------


def prompt_text(entries: Sequence[tuple[str, str]]) -> tuple[str, list[tuple[str, str, int, int]]]:
    """``"hair. eye. skirt."`` and the character span of every phrase: ``(class_id, phrase, start, end)``."""
    parts: list[str] = []
    spans: list[tuple[str, str, int, int]] = []
    position = 0
    for index, (class_id, phrase) in enumerate(entries):
        if index:
            parts.append(PHRASE_SEPARATOR)
            position += len(PHRASE_SEPARATOR)
        spans.append((class_id, phrase, position, position + len(phrase)))
        parts.append(phrase)
        position += len(phrase)
    parts.append(".")
    return "".join(parts), spans


def pack_phrases(
    entries: Sequence[tuple[str, str]], count_tokens: Callable[[str], int], limit: int = MAX_PROMPT_TOKENS
) -> list[list[tuple[str, str]]]:
    """Greedy batches whose prompt text stays within ``limit`` tokens; a phrase is never split or dropped."""
    batches: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]] = []
    for entry in entries:
        trial = [*current, entry]
        if current and count_tokens(prompt_text(trial)[0]) > limit:
            batches.append(current)
            current = [entry]
        else:
            current = trial
    if current:
        batches.append(current)
    return batches


def phrase_entries(phrases: Mapping[str, tuple[str, ...]]) -> list[tuple[str, str]]:
    """Unique, normalised ``(class_id, phrase)`` pairs; a phrase already claimed by a class is not repeated."""
    seen: set[str] = set()
    entries: list[tuple[str, str]] = []
    for class_id, items in phrases.items():
        for raw in items:
            phrase = " ".join(raw.replace(".", " ").lower().split())
            if phrase and phrase not in seen:
                seen.add(phrase)
                entries.append((class_id, phrase))
    return entries


# --------------------------------------------------------------------------------------------------
# Grounding DINO
# --------------------------------------------------------------------------------------------------


class GroundingDinoProvider(TransformersSegmentationProvider, Detector2D):
    """Open-vocabulary boxes from ``IDEA-Research/grounding-dino-base`` (transformers-native classes)."""

    def __init__(self, spec: ModelSpec, settings: Settings, model_dir: Path | None = None) -> None:
        super().__init__(spec, settings, model_dir)
        self.last_batch_count: int = 0
        self.last_class_ids: frozenset[str] = frozenset()

    def _load_components(self) -> tuple[Any, Any]:
        from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor

        processor = AutoProcessor.from_pretrained(self.model_dir, local_files_only=True)
        model = AutoModelForZeroShotObjectDetection.from_pretrained(self.model_dir, local_files_only=True)
        return processor, model

    def detect(
        self,
        image: Path,
        phrases: Mapping[str, tuple[str, ...]],
        *,
        box_threshold: float,
        text_threshold: float,
        cancel: CancellationToken,
    ) -> list[Box2D]:
        import torch

        with self.lock:
            model = self._model_on_device(cancel)
            processor = self._processor
            assert processor is not None
            tokenizer = processor.tokenizer
            entries = phrase_entries(phrases)
            batches = pack_phrases(entries, lambda text: len(tokenizer(text)["input_ids"]))
            self.last_batch_count = len(batches)
            self.last_class_ids = frozenset(class_id for class_id, _phrase in entries)
            img = open_rgb(image)
            width, height = img.size
            device = next(model.parameters()).device
            boxes: list[Box2D] = []
            for batch in batches:
                cancel.raise_if_cancelled()
                text, spans = prompt_text(batch)
                encoded = processor(images=img, text=text, return_tensors="pt")
                offsets = tokenizer(text, return_offsets_mapping=True)["offset_mapping"]
                token_sets = [
                    [i for i, (start, end) in enumerate(offsets) if end > start and start < s_end and end > s_start]
                    for _cid, _phrase, s_start, s_end in spans
                ]
                with torch.inference_mode():
                    outputs = model(**{k: v.to(device) for k, v in encoded.items()})
                probs = outputs.logits[0].sigmoid().float().cpu()
                pred = outputs.pred_boxes[0].float().cpu()
                columns = [probs[:, idx].max(dim=1).values if idx else torch.zeros(probs.shape[0]) for idx in token_sets]
                if not columns:
                    continue
                phrase_scores = torch.stack(columns, dim=1)
                best, which = phrase_scores.max(dim=1)
                threshold = max(box_threshold, text_threshold)
                for query in torch.nonzero(best >= threshold).flatten().tolist():
                    class_id, phrase, _s, _e = spans[int(which[query])]
                    cx, cy, bw, bh = (float(v) for v in pred[query])
                    x0 = max(0.0, (cx - bw / 2) * width)
                    y0 = max(0.0, (cy - bh / 2) * height)
                    x1 = min(float(width), (cx + bw / 2) * width)
                    y1 = min(float(height), (cy + bh / 2) * height)
                    if x1 - x0 < 1 or y1 - y0 < 1:
                        continue
                    boxes.append(
                        Box2D(
                            class_id=class_id,
                            xyxy=(x0, y0, x1, y1),
                            score=float(best[query]),
                            source=self.model_id,
                            phrase=phrase,
                        )
                    )
            logger.info(
                "%s: %d phrase(s) of %d class(es) in %d batch(es) -> %d box(es)",
                self.model_id, len(entries), len(self.last_class_ids), len(batches), len(boxes),
            )
            return boxes
