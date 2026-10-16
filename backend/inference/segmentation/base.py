"""2D segmentation role ABCs (part-aware DESIGN.md section 5.2). Torch-free; frozen in slice S1.

Roles: a :class:`Detector2D` turns phrases into boxes (Grounding DINO), a :class:`Grounder2D` captions and grounds
phrases (Florence-2), a :class:`MaskRefiner` turns boxes into masks (SAM 2.1) and a :class:`Parser2D` produces dense
closed-set masks (See-through for anime characters, Sapiens2 for realistic humans).

Only files and plain dataclasses cross the seam: masks are 8-bit soft-mask PNGs written into the given ``out_dir``.
Every ``class_id`` is a taxonomy class id (``parts.taxonomy``); pixel coordinates are in the space of the input image.
"""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from inference.base import CancellationToken
from inference.providers.base import ModelProvider


@dataclass(frozen=True, slots=True)
class Box2D:
    """A detection. ``xyxy`` in pixels; ``source`` is the model id; ``phrase`` the prompt phrase that produced it."""

    class_id: str
    xyxy: tuple[float, float, float, float]
    score: float
    source: str
    phrase: str


@dataclass(frozen=True, slots=True)
class Mask2D:
    """A soft mask (8-bit PNG at ``path``) of one class instance; ``bbox`` is ``(x0, y0, x1, y1)`` in pixels."""

    class_id: str
    path: Path
    score: float
    bbox: tuple[int, int, int, int]
    source: str
    instance: int


class Detector2D(ModelProvider):
    """Open-vocabulary detector (Grounding DINO): phrases → boxes."""

    @abstractmethod
    def detect(
        self,
        image: Path,
        phrases: Mapping[str, tuple[str, ...]],
        *,
        box_threshold: float,
        text_threshold: float,
        cancel: CancellationToken,
    ) -> list[Box2D]:
        """Boxes for ``phrases`` (class id → its phrases). Implementations pack the vocabulary into tokenizer-sized
        batches so that no class is silently dropped by text truncation."""


class Grounder2D(ModelProvider):
    """Captioning and phrase grounding (Florence-2)."""

    @abstractmethod
    def caption(self, image: Path, cancel: CancellationToken) -> str:
        """A short caption of the whole image (used by ``subject: auto``)."""

    @abstractmethod
    def ground(self, image: Path, phrases: Mapping[str, tuple[str, ...]], cancel: CancellationToken) -> list[Box2D]:
        """Boxes for ``phrases`` (class id → its phrases)."""


class MaskRefiner(ModelProvider):
    """Box (+ point) prompted mask refinement (SAM 2.1)."""

    @abstractmethod
    def refine(self, image: Path, boxes: Sequence[Box2D], out_dir: Path, cancel: CancellationToken) -> list[Mask2D]:
        """One mask per box, written into ``out_dir``."""


class Parser2D(ModelProvider):
    """Closed-set dense parser (See-through l2d for anime, Sapiens2-seg for realistic humans)."""

    label_map: Mapping[str, str]
    """Source class name → taxonomy class id (segmentation research section 7.2)."""

    @abstractmethod
    def parse(self, image: Path, out_dir: Path, cancel: CancellationToken) -> list[Mask2D]:
        """Dense masks of every recognised class, written into ``out_dir``, already mapped to taxonomy ids."""
