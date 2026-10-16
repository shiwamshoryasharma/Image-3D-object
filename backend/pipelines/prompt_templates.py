"""Prompt templates of prompt-to-3D image generation (part-aware DESIGN.md section 4.4.3). Torch-free.

:func:`build_prompt` expands the user prompt into the TRELLIS-friendly reference-image prompt of a preset:

* ``character`` (default size 896x1152): one full-body character in a neutral A-pose, front view, plain background;
* ``object`` (1024x1024): one complete product-style object, three-quarter view, plain background;
* ``raw``: the prompt verbatim (no template, no clauses).

The template texts are the ones of ``text_to_image_research.md`` sections 8.1-8.3. Z-Image-Turbo is distilled, so
prompts are positive-only (no negative prompt). The prompt text is local runtime data: logs only ever record
:func:`prompt_log_tag` (length plus the first 12 hex digits of the sha256 of the final prompt).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from parts.taxonomy import match_terms

PRESET_DEFAULT_SIZES: Final[Mapping[str, tuple[int, int]]] = MappingProxyType(
    {"character": (896, 1152), "object": (1024, 1024), "raw": (1024, 1024)}
)
"""``(width, height)`` used when a request leaves ``width``/``height`` empty."""

DEFAULT_BACKGROUND: Final = "light-gray"
CONTRAST_BACKGROUND: Final = "medium-gray"
"""Used when the subject itself is light (white, silver, ...), so it still separates from the background."""

STYLE_CLAUSES: Final[Mapping[str, str]] = MappingProxyType(
    {
        "realistic": "Photorealistic, physically accurate materials and colors.",
        "stylized": "Stylized 3D game-asset render, clean readable shapes, hand-painted textures.",
        "anime": "Anime-style character, clean cel shading, crisp line art.",
    }
)
PART_HINT_CLAUSE: Final = (
    "Hair, clothing layers and accessories are clearly distinct from each other and from the skin; "
    "both eyes clearly visible."
)

CHARACTER_TEMPLATE: Final = (
    "A full-body 3D character reference image of {subject}. Exactly one character, standing upright in a neutral "
    "A-pose: arms straight and angled about 30 degrees away from the torso, hands open and relaxed, feet "
    "shoulder-width apart and flat on the ground. Front view, facing the camera directly, eye-level camera, telephoto "
    "lens with minimal perspective distortion. The whole body is visible from the top of the head to the soles of the "
    "shoes, centered in the frame with empty space on all sides. Neutral expression, eyes open, mouth closed, hair not "
    "covering the face. Plain seamless {background} studio background, no floor line, no props, no text. Soft, even, "
    "diffuse lighting with no cast shadows and no strong highlights. Sharp focus everywhere."
)
OBJECT_TEMPLATE: Final = (
    "A product-style 3D reference image of {subject}. A single complete object, fully visible and centered in the "
    "frame with empty space on all sides, three-quarter front view from slightly above eye level. Plain seamless "
    "{background} studio background, no other objects, no text. Soft, even, diffuse lighting with no cast shadows. "
    "Sharp focus, true-to-life materials."
)
TEMPLATES: Final[Mapping[str, str]] = MappingProxyType({"character": CHARACTER_TEMPLATE, "object": OBJECT_TEMPLATE})

_LIGHT_SUBJECT = re.compile(r"\b(?:white|silver|pale|snow|ivory|light\s+gr[ae]y)\b", re.IGNORECASE)
_WHITESPACE = re.compile(r"\s+")
_LOG_HASH_DIGITS: Final = 12


def background_for(prompt: str) -> str:
    """``medium-gray`` when the prompt names a light colour as a whole word, otherwise ``light-gray``."""
    return CONTRAST_BACKGROUND if _LIGHT_SUBJECT.search(prompt) else DEFAULT_BACKGROUND


def subject_text(prompt: str) -> str:
    """The template ``{subject}``: the trimmed prompt on one line (whitespace runs collapsed) without a trailing full
    stop, so the template sentence that follows it reads correctly."""
    return _WHITESPACE.sub(" ", prompt).strip().rstrip(".").rstrip()


def build_prompt(prompt: str, preset: str, style: str | None, part_hint: bool) -> str:
    """The final prompt sent to the text-to-image model.

    ``raw`` returns ``prompt`` unchanged (only stripped). ``character``/``object`` fill the template and append the
    style clause (``style`` not ``None``) and the part-separation clause (``part_hint``).
    """
    text = prompt.strip()
    if preset == "raw":
        return text
    template = TEMPLATES.get(preset)
    if template is None:
        raise ValueError(f"Unknown prompt preset {preset!r}")
    if style is not None and style not in STYLE_CLAUSES:
        raise ValueError(f"Unknown prompt style {style!r}")
    parts = [template.format(subject=subject_text(text), background=background_for(text))]
    if style is not None:
        parts.append(STYLE_CLAUSES[style])
    if part_hint:
        parts.append(PART_HINT_CLAUSE)
    return " ".join(parts)


def expected_terms(prompt: str) -> list[str]:
    """Taxonomy class ids named by the **user** prompt (``options.expected_terms``; segmentation extends its
    vocabulary with them). The template text is never matched: it names hair, eyes and clothing for every prompt."""
    return match_terms(prompt)


def prompt_sha256(text: str) -> str:
    """Hex sha256 of the UTF-8 prompt (``final_prompt_sha256`` in options and provenance)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def prompt_log_tag(text: str) -> str:
    """What logs may say about a prompt: its length and the first 12 hex digits of its sha256."""
    return f"len={len(text)} sha256={prompt_sha256(text)[:_LOG_HASH_DIGITS]}"
