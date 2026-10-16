"""Image generation schemas: ``/image-generations`` (part-aware DESIGN.md sections 4.4 and 6.2). Frozen in slice S1.

Request hygiene that needs no server state lives here: the prompt is stripped and control characters other than
``\\n``/``\\t`` are a 422 with details type ``invalid_characters``. Limits that depend on settings or on the model
(``settings.prompt_max_chars``, sizes, step and guidance bounds, negative-prompt support) are checked by the service
and reported as ``unsupported_by_model``.
"""

from __future__ import annotations

import unicodedata
from typing import Final, Literal, get_args

from pydantic import ConfigDict, Field, field_validator
from pydantic_core import PydanticCustomError

from inference.providers.base import MODEL_ID_PATTERN
from models.enums import ReferenceSlot
from models.image_generation import (
    MAX_CANDIDATES,
    CandidateReadiness,
    CandidateUse,
    ImageCandidate,
    ImageGenerationResult,
    ImageGenerationRun,
)
from schemas.common import API_V1_PREFIX, ApiModel, RequestModel
from schemas.jobs import MAX_SEED
from schemas.references import MAX_LABEL_LENGTH, UploadImageResponse
from schemas.runs import RunOutBase, run_out_fields

PromptPreset = Literal["character", "object", "raw"]
PromptStyle = Literal["realistic", "stylized", "anime"]

PROMPT_PRESETS: Final[tuple[str, ...]] = get_args(PromptPreset)
PROMPT_STYLES: Final[tuple[str, ...]] = get_args(PromptStyle)
PROMPT_HARD_MAX_CHARS: Final = 8000
"""Schema cap; the effective limit is ``settings.prompt_max_chars`` (default 2000)."""
NEGATIVE_PROMPT_MAX_CHARS: Final = 1000
MAX_IMAGES: Final = MAX_CANDIDATES

_ALLOWED_CONTROL_CHARACTERS: Final = frozenset({"\n", "\t"})


def control_characters(text: str) -> list[str]:
    """Control characters of ``text`` that a prompt may not contain (everything in category Cc except ``\\n``,
    ``\\t``)."""
    return [ch for ch in text if unicodedata.category(ch) == "Cc" and ch not in _ALLOWED_CONTROL_CHARACTERS]


def clean_prompt_text(value: str, *, field: str) -> str:
    """Strip ``value`` and reject control characters (pydantic error type ``invalid_characters``)."""
    stripped = value.strip()
    bad = control_characters(stripped)
    if bad:
        raise PydanticCustomError(
            "invalid_characters",
            "{field} contains {count} control character(s); only line breaks and tabs are allowed",
            {"field": field, "count": len(bad)},
        )
    return stripped


# --------------------------------------------------------------------------------------------------
# Requests
# --------------------------------------------------------------------------------------------------


class ImageGenerationRequest(RequestModel):
    """``POST /image-generations``. Candidate ``i`` uses seed ``(seed + i) mod 2^31``; ``null`` sizes, steps and
    guidance take the model's (or the preset's) defaults."""

    prompt: str = Field(min_length=1, max_length=PROMPT_HARD_MAX_CHARS)
    negative_prompt: str | None = Field(default=None, max_length=NEGATIVE_PROMPT_MAX_CHARS)
    model_id: str | None = Field(default=None, pattern=MODEL_ID_PATTERN)
    """``None`` → ``registry.default_model("text_to_image")``."""
    preset: PromptPreset = "character"
    style: PromptStyle | None = None
    part_hint: bool = True
    """Append the part-separation clause to the template (section 4.4.3)."""
    num_images: int = Field(default=1, ge=1, le=MAX_IMAGES)
    seed: int = Field(default=0, ge=0, le=MAX_SEED)
    randomize_seed: bool = True
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    """``width``/``height``: ``None`` → preset default; the pair must be one of the model's ``capabilities.sizes``."""
    steps: int | None = Field(default=None, ge=1)
    guidance: float | None = Field(default=None, ge=0.0)

    @field_validator("prompt")
    @classmethod
    def _clean_prompt(cls, value: str) -> str:
        cleaned = clean_prompt_text(value, field="prompt")
        if not cleaned:
            raise PydanticCustomError("string_too_short", "String should have at least 1 character", {"min_length": 1})
        return cleaned

    @field_validator("negative_prompt")
    @classmethod
    def _clean_negative_prompt(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return clean_prompt_text(value, field="negative_prompt") or None


class WorkflowImageGenerationRequest(ImageGenerationRequest):
    """``ImageGenerationRequest`` inside a workflow (prompt source, ``prompt`` node): exactly one candidate, which the
    workflow imports automatically (``selection: auto_first``).

    ``from_attributes`` lets server code pass a plain :class:`ImageGenerationRequest` where this model is expected; it
    is re-validated, so ``num_images > 1`` is still refused. JSON bodies are unaffected (unknown keys stay 422).
    """

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    num_images: int = Field(default=1, ge=1, le=1)


class UseCandidateRequest(RequestModel):
    """``POST /image-generations/{ig}/candidates/{index}/use``. ``reference_set_id = null`` creates a new set."""

    reference_set_id: str | None = None
    slot: ReferenceSlot = ReferenceSlot.FRONT
    make_primary: bool = True
    label: str | None = Field(default=None, max_length=MAX_LABEL_LENGTH)


UseCandidateResponse = UploadImageResponse
"""``201 {"image": ReferenceImageOut, "set": ReferenceSetOut}``: the same body as an upload (contract 7.3)."""


# --------------------------------------------------------------------------------------------------
# Responses
# --------------------------------------------------------------------------------------------------


def image_generation_url(ig_id: str) -> str:
    return f"{API_V1_PREFIX}/image-generations/{ig_id}"


class CandidateReadinessOut(ApiModel):
    ok: bool
    issues: list[str]
    method: str

    @classmethod
    def from_domain(cls, readiness: CandidateReadiness) -> CandidateReadinessOut:
        return cls(ok=readiness.ok, issues=list(readiness.issues), method=readiness.method)


class CandidateUseOut(ApiModel):
    reference_set_id: str
    image_id: str

    @classmethod
    def from_domain(cls, use: CandidateUse) -> CandidateUseOut:
        return cls(reference_set_id=use.reference_set_id, image_id=use.image_id)


class ImageCandidateOut(ApiModel):
    index: int
    seed: int
    width: int
    height: int
    url: str
    thumbnail_url: str
    readiness: CandidateReadinessOut
    used_as: list[CandidateUseOut]

    @classmethod
    def from_domain(cls, ig_id: str, candidate: ImageCandidate) -> ImageCandidateOut:
        base = f"{image_generation_url(ig_id)}/candidates/{candidate.index}"
        return cls(
            index=candidate.index,
            seed=candidate.seed,
            width=candidate.width,
            height=candidate.height,
            url=base,
            thumbnail_url=f"{base}/thumbnail",
            readiness=CandidateReadinessOut.from_domain(candidate.readiness),
            used_as=[CandidateUseOut.from_domain(use) for use in candidate.used_as],
        )


class ImageGenerationResultOut(ApiModel):
    candidates: list[ImageCandidateOut]
    timings: dict[str, float]

    @classmethod
    def from_domain(cls, ig_id: str, result: ImageGenerationResult) -> ImageGenerationResultOut:
        return cls(
            candidates=[ImageCandidateOut.from_domain(ig_id, c) for c in result.candidates],
            timings=dict(result.timings),
        )


class ImageGenerationOut(RunOutBase):
    kind: Literal["image_generation"]
    result: ImageGenerationResultOut | None

    @classmethod
    def from_run(cls, run: ImageGenerationRun) -> ImageGenerationOut:
        return cls(
            **run_out_fields(run),
            result=ImageGenerationResultOut.from_domain(run.id, run.result) if run.result is not None else None,
        )


class ImageGenerationList(ApiModel):
    items: list[ImageGenerationOut]
