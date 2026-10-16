"""Slice S4: prompt templates and prompt hygiene helpers (part-aware DESIGN.md sections 4.4.2 and 4.4.3)."""

from __future__ import annotations

import hashlib

import pytest

from pipelines.prompt_templates import (
    CHARACTER_TEMPLATE,
    OBJECT_TEMPLATE,
    PART_HINT_CLAUSE,
    PRESET_DEFAULT_SIZES,
    STYLE_CLAUSES,
    background_for,
    build_prompt,
    expected_terms,
    prompt_log_tag,
    prompt_sha256,
)

CHARACTER_TEXT = (
    "A full-body 3D character reference image of a knight with a red cape and a sword. Exactly one character, "
    "standing upright in a neutral A-pose: arms straight and angled about 30 degrees away from the torso, hands open "
    "and relaxed, feet shoulder-width apart and flat on the ground. Front view, facing the camera directly, eye-level "
    "camera, telephoto lens with minimal perspective distortion. The whole body is visible from the top of the head to "
    "the soles of the shoes, centered in the frame with empty space on all sides. Neutral expression, eyes open, mouth "
    "closed, hair not covering the face. Plain seamless light-gray studio background, no floor line, no props, no "
    "text. Soft, even, diffuse lighting with no cast shadows and no strong highlights. Sharp focus everywhere."
)
OBJECT_TEXT = (
    "A product-style 3D reference image of a brass lantern. A single complete object, fully visible and centered in "
    "the frame with empty space on all sides, three-quarter front view from slightly above eye level. Plain seamless "
    "light-gray studio background, no other objects, no text. Soft, even, diffuse lighting with no cast shadows. Sharp "
    "focus, true-to-life materials."
)


def test_character_template_text_is_exact():
    assert build_prompt("a knight with a red cape and a sword", "character", None, False) == CHARACTER_TEXT


def test_object_template_text_is_exact():
    assert build_prompt("a brass lantern", "object", None, False) == OBJECT_TEXT


def test_style_and_part_hint_clauses_are_appended_in_order():
    prompt = build_prompt("a brass lantern", "object", "stylized", True)
    assert prompt == f"{OBJECT_TEXT} {STYLE_CLAUSES['stylized']} {PART_HINT_CLAUSE}"
    assert build_prompt("a knight", "character", "anime", False).endswith(
        "Sharp focus everywhere. Anime-style character, clean cel shading, crisp line art."
    )
    assert build_prompt("a knight", "character", None, True).endswith(
        "Sharp focus everywhere. Hair, clothing layers and accessories are clearly distinct from each other and from "
        "the skin; both eyes clearly visible."
    )
    assert STYLE_CLAUSES["realistic"] == "Photorealistic, physically accurate materials and colors."


def test_raw_preset_passes_the_prompt_through():
    text = "masterpiece, 1girl, silver hair\nfull body"
    assert build_prompt(f"  {text}  ", "raw", "anime", True) == text


def test_subject_is_one_line_without_a_trailing_full_stop():
    prompt = build_prompt("  a knight\n with a cape.  ", "character", None, False)
    assert prompt.startswith("A full-body 3D character reference image of a knight with a cape. Exactly one")


@pytest.mark.parametrize(
    ("prompt", "background"),
    [
        ("a knight in white armor", "medium-gray"),
        ("SILVER robot", "medium-gray"),
        ("a pale vampire", "medium-gray"),
        ("a dress of light  grey silk", "medium-gray"),
        ("a light gray cat", "medium-gray"),
        ("an ivory chess piece", "medium-gray"),
        ("a snow leopard", "medium-gray"),
        ("a whitewashed fence", "light-gray"),
        ("a snowman", "light-gray"),
        ("a red dragon", "light-gray"),
    ],
)
def test_background_switches_to_medium_gray_for_light_subjects(prompt, background):
    assert background_for(prompt) == background
    assert f"Plain seamless {background} studio background" in build_prompt(prompt, "character", None, False)


def test_unknown_preset_or_style_is_rejected():
    with pytest.raises(ValueError):
        build_prompt("x", "portrait", None, False)
    with pytest.raises(ValueError):
        build_prompt("x", "character", "cubist", False)


def test_templates_contain_only_known_placeholders():
    for template in (CHARACTER_TEMPLATE, OBJECT_TEMPLATE):
        assert template.format(subject="S", background="B").count("S") >= 1
    assert PRESET_DEFAULT_SIZES == {"character": (896, 1152), "object": (1024, 1024), "raw": (1024, 1024)}


def test_expected_terms_come_from_the_user_prompt_only():
    assert expected_terms("a knight with a red cape and a sword") == ["object.held", "clothing.outer"]
    assert expected_terms("a smooth stone") == []
    assert expected_terms(build_prompt("a smooth stone", "character", None, True)) != []  # why the template is skipped


def test_hash_and_log_tag_never_contain_the_prompt():
    text = "a secret prompt"
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert prompt_sha256(text) == digest
    tag = prompt_log_tag(text)
    assert tag == f"len={len(text)} sha256={digest[:12]}"
    assert "secret" not in tag
