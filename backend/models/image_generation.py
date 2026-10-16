"""Image generation run (``ig_``) domain record (part-aware DESIGN.md section 4.4). Frozen in slice S1.

Persisted as ``runtime/imagegen/<ig>/run.json``; the candidates live next to it (``candidates/<n>.png`` plus a
``<n>_thumb.webp`` thumbnail, see :class:`storage.run_layout.RunLayout`). ``options`` holds the effective request:
``prompt``, ``effective_prompt``, ``negative_prompt``, ``model_id``, ``preset``, ``style``, ``part_hint``,
``num_images``, ``seed``, ``width``, ``height``, ``steps``, ``guidance``, ``expected_terms`` and
``final_prompt_sha256``. The prompt text is local runtime data; logs only ever record its length and hash prefix.
"""

from __future__ import annotations

from typing import Final, Literal

from pydantic import Field

from models.runs import RunRecord
from models.types import DomainModel

MAX_CANDIDATES: Final = 4
"""Upper bound of ``num_images`` (and of the candidate index route regex ``^[0-3]$``)."""
READINESS_METHOD: Final = "border_color"


class CandidateReadiness(DomainModel):
    """3D-readiness check of one candidate (warnings only, never failures; section 4.4.5).

    ``issues`` use the same ``t2i_*`` codes as the run warnings.
    """

    ok: bool = True
    issues: list[str] = Field(default_factory=list)
    method: str = READINESS_METHOD


class CandidateUse(DomainModel):
    """A reference image created from the candidate (``POST …/candidates/{n}/use``)."""

    reference_set_id: str
    image_id: str


class ImageCandidate(DomainModel):
    """One generated image. Candidate ``i`` used seed ``(seed + i) mod 2^31``."""

    index: int
    seed: int
    width: int
    height: int
    readiness: CandidateReadiness = Field(default_factory=CandidateReadiness)
    used_as: list[CandidateUse] = Field(default_factory=list)


class ImageGenerationResult(DomainModel):
    candidates: list[ImageCandidate] = Field(default_factory=list)
    timings: dict[str, float] = Field(default_factory=dict)
    """Seconds per step (``load_model``, ``generate``, ``check``, ``save``) plus ``total``."""


class ImageGenerationRun(RunRecord):
    kind: Literal["image_generation"] = "image_generation"
    result: ImageGenerationResult | None = None

    def candidate(self, index: int) -> ImageCandidate | None:
        if self.result is None:
            return None
        return next((c for c in self.result.candidates if c.index == index), None)
