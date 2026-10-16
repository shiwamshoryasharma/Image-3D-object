"""Text-to-image role ABC (part-aware DESIGN.md section 5.2). Torch-free; frozen in slice S1.

Only files and plain dataclasses cross the seam: the provider writes one PNG per candidate into
``request.output_dir`` and returns their paths. Candidates are generated one at a time (batch size 1), each with its
own private ``torch.Generator(device).manual_seed(seed_i)``; the global torch RNG is never seeded.
"""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from inference.base import CancellationToken
from inference.providers.base import ModelProvider, StepProgress


@dataclass(frozen=True, slots=True)
class ImageGenRequest:
    """One generation call. ``seeds[i]`` is the seed of candidate ``i`` (``len(seeds)`` = number of images)."""

    run_id: str
    prompt: str
    """The final (template-expanded) prompt."""
    negative_prompt: str | None
    width: int
    height: int
    steps: int
    guidance: float
    seeds: tuple[int, ...]
    output_dir: Path
    """The provider writes only here."""
    device: str


@dataclass(slots=True)
class ImageGenOutput:
    """Generated candidates in seed order (``paths[i]`` was made with ``seeds[i]``)."""

    paths: list[Path]
    seeds: list[int]
    timings: dict[str, float] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class TextToImageProvider(ModelProvider):
    """A text-to-image model (Z-Image-Turbo in catalog v1)."""

    @abstractmethod
    def generate(self, request: ImageGenRequest, progress: StepProgress, cancel: CancellationToken) -> ImageGenOutput:
        """Generate every candidate on the GPU thread, checking ``cancel`` between candidates (and steps).

        Cancellation raises :class:`~core.exceptions.JobCancelledError`; the weights go back to CPU in ``finally``.
        """
