"""2D fusion, eye heuristics, vocabulary packing and profile rules (part-aware DESIGN.md sections 4.5.3-4.5.4)."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest
from fakes_segmentation import EYEBROW_Y, TIARA_BOX, character_image

from core.exceptions import AppError
from inference.segmentation.gdino import MAX_PROMPT_TOKENS, pack_phrases, phrase_entries, prompt_text
from parts.taxonomy import CLASSES, TAXONOMY
from pipelines.segmentation import (
    SegmentationRoles,
    detection_vocabulary,
    prompt_term_phrases,
    resolve_profile,
    resolve_roles,
    select_boxes,
    subject_from_caption,
)
from inference.segmentation.base import Box2D
from processing.seg2d import fusion
from processing.seg2d.fusion import MaskCandidate, fuse

H = W = 400


def _ellipse(cx: float, cy: float, rx: float, ry: float) -> np.ndarray:
    yy, xx = np.mgrid[0:H, 0:W]
    return (((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2 <= 1).astype(np.float32)


def _rect(x0: int, y0: int, x1: int, y1: int) -> np.ndarray:
    m = np.zeros((H, W), np.float32)
    m[y0:y1, x0:x1] = 1
    return m


def _cand(class_id: str, mask: np.ndarray, score: float = 0.8, role: str = "detector", **kw) -> MaskCandidate:
    ys, xs = np.nonzero(mask >= 0.5)
    box = (float(xs.min()), float(ys.min()), float(xs.max() + 1), float(ys.max() + 1)) if xs.size else None
    return MaskCandidate(class_id, mask, score, role, (f"src_{role}",), kw.pop("box", box), **kw)  # type: ignore[arg-type]


def _image() -> np.ndarray:
    return np.asarray(character_image())


def _face_and_eyes() -> list[MaskCandidate]:
    return [
        _cand("body.head.face", _ellipse(200, 182, 80, 92)),
        _cand("eye.left", _ellipse(165, 180, 14, 9)),
        _cand("eye.left", _ellipse(235, 180, 14, 9)),
    ]


def _value(class_id: str) -> int:
    return TAXONOMY[class_id].value


def test_z_priority_sides_and_label_stats():
    out = fuse(
        [*_face_and_eyes(), _cand("body.head.mouth", _ellipse(200, 235, 18, 6)), _cand("hair", _rect(95, 30, 305, 95))],
        _image(),
        min_confidence=0.35,
    )
    labels = out.labels
    assert labels[180, 165] == _value("eye.right")  # image-left eye = character's right eye
    assert labels[180, 235] == _value("eye.left")
    assert labels[235, 200] == _value("body.head.mouth")  # equal z: the smaller (more specific) mask wins
    assert labels[60, 200] == _value("hair")  # hair (z 70) in front of the face
    assert labels[200, 200] == _value("body.head.face")
    stats = {s.category: s for s in out.stats}
    assert stats["eye.left"].side == "left" and stats["eye.left"].group == "eyes"
    assert stats["eye.left"].bbox[0] > stats["eye.right"].bbox[0]
    assert set(out.soft) == {s.value for s in out.stats}
    eye_soft = out.soft[_value("eye.left")]
    assert eye_soft.dtype == np.uint8 and eye_soft[180, 235] > 0 and eye_soft[180, 165] == 0


def test_tiara_eyebrow_is_rejected_and_real_brows_are_kept():
    tiara = _rect(*TIARA_BOX)
    brows = _rect(148, EYEBROW_Y[0], 182, EYEBROW_Y[1] + 1) + _rect(218, EYEBROW_Y[0], 252, EYEBROW_Y[1] + 1)
    out = fuse(
        [*_face_and_eyes(), _cand("eyebrow.left", tiara), _cand("eyebrow.left", brows)], _image(), min_confidence=0.35
    )
    stats = {s.category: s for s in out.stats}
    assert {"eyebrow.left", "eyebrow.right"} <= set(stats)
    for side in ("left", "right"):
        assert EYEBROW_Y[0] <= stats[f"eyebrow.{side}"].bbox[1] <= EYEBROW_Y[1]
        assert stats[f"eyebrow.{side}"].confidence == pytest.approx(0.8)
    assert not np.isin(out.labels[TIARA_BOX[1] : TIARA_BOX[3], TIARA_BOX[0] : TIARA_BOX[2]],
                       [_value("eyebrow.left"), _value("eyebrow.right")]).any()
    assert any(r["reason"] == "eyebrow_not_above_eye" for r in out.rejected)


def test_eyebrow_fallback_uses_the_dark_band_with_low_confidence():
    out = fuse(_face_and_eyes(), _image(), min_confidence=0.35)
    stats = {s.category: s for s in out.stats}
    assert "eyebrow.left" in stats and "eyebrow.right" in stats
    for side in ("left", "right"):
        assert stats[f"eyebrow.{side}"].confidence <= 0.4
        assert stats[f"eyebrow.{side}"].sources == ("heuristic2d",)
        assert abs(stats[f"eyebrow.{side}"].bbox[1] - EYEBROW_Y[0]) <= 3


def test_eyelashes_and_eyelids():
    image = _image().copy()
    image[168:172, 150:182] = (10, 10, 10)  # a dark lash line on the upper lid of the image-left eye
    candidates = _face_and_eyes()
    candidates[1] = _cand("eye.left", _ellipse(165, 180, 14, 9))
    out = fuse(candidates, image, min_confidence=0.35)
    assert "eyelash.right" in out.annotations
    annotation = out.annotations["eyelash.right"]
    assert annotation["method"] == "heuristic" and 0 < annotation["confidence"] <= 0.6
    curve = np.asarray(annotation["lid_curve"])
    assert curve.shape[1] == 2 and curve[:, 0].min() >= 150 and curve[:, 0].max() <= 180
    lash = out.labels == _value("eyelash.right")
    assert lash.any() and np.nonzero(lash)[0].max() <= 182
    lid = out.labels == _value("body.head.eyelid.left")
    face = _ellipse(200, 182, 80, 92) >= 0.5
    assert lid.any() and not (lid & ~face).any()
    assert not (lid & (_ellipse(235, 180, 14, 9) >= 0.5)).any()


def test_gates_preferences_and_class_filter():
    tiny = _cand("accessory.earwear", _rect(10, 10, 12, 12))
    weak = _cand("clothing.dress", _rect(130, 280, 270, 400), score=0.2)
    parser_hair = _cand("hair", _rect(95, 30, 305, 95), score=0.9, role="parser")
    detector_hair = _cand("hair", _rect(0, 0, 400, 30), score=0.95)
    parser_eye = _cand("eye.left", _ellipse(165, 180, 30, 20), role="parser")
    out = fuse(
        [*_face_and_eyes(), tiny, weak, parser_hair, detector_hair, parser_eye], _image(), min_confidence=0.35
    )
    categories = {s.category for s in out.stats}
    assert "accessory.earwear" not in categories  # below 0.02 % of the frame
    assert "clothing.dress" not in categories  # below min_confidence
    assert out.labels[10, 200] == 0  # dense hair: the parser wins over the detector
    assert out.labels[60, 200] == _value("hair")
    assert out.labels[180, 190] != _value("eye.right")  # small eyes: detector + refiner win over the parser
    filtered = fuse(_face_and_eyes(), _image(), min_confidence=0.35, classes=["eye.left"])
    assert {s.category for s in filtered.stats} == {"eye.left"}


def test_head_pass_replaces_overlapping_full_image_detections():
    full = _cand("body.head.mouth", _ellipse(200, 235, 25, 10))
    head = _cand("body.head.mouth", _ellipse(200, 235, 18, 6), head_pass=True)
    out = fuse([*_face_and_eyes(), full, head], _image(), min_confidence=0.35)
    mouth = out.labels == _value("body.head.mouth")
    assert mouth.sum() == pytest.approx((_ellipse(200, 235, 18, 6) >= 0.5).sum(), rel=0.05)


def test_overlay_and_colours_are_deterministic():
    labels = np.zeros((1500, 900), np.uint16)
    labels[100:200, 100:200] = _value("hair")
    overlay = fusion.overlay_image(np.zeros((1500, 900, 3), np.uint8), labels)
    assert overlay.shape[2] == 4 and max(overlay.shape[:2]) == 1024
    assert fusion.label_color(30) == fusion.label_color(30) != fusion.label_color(31)


# ==================================================================================================
# Vocabulary and packing
# ==================================================================================================


def test_pack_phrases_never_drops_or_splits_a_phrase():
    vocab = detection_vocabulary(None)
    entries = phrase_entries(vocab)
    aliased = {c.id for c in CLASSES if c.aliases and not c.id.endswith(".right")}
    assert set(vocab) == aliased
    count = lambda text: len(text.replace(".", " . ").split()) + 2  # noqa: E731 - word-level stand-in tokenizer
    batches = pack_phrases(entries, count, MAX_PROMPT_TOKENS)
    assert len(batches) > 1
    assert [e for batch in batches for e in batch] == entries
    for batch in batches:
        text, spans = prompt_text(batch)
        assert count(text) <= MAX_PROMPT_TOKENS
        for _cid, phrase, start, end in spans:
            assert text[start:end] == phrase
    text, _spans = prompt_text([("hair", "hair"), ("eye.left", "eye")])
    assert text == "hair. eye."


def test_vocabulary_keeps_eye_geometry_for_brows_and_adds_extra_terms():
    vocab = detection_vocabulary(frozenset({"eyebrow.right"}), extra_terms=["magic wand", "tiara"])
    assert set(vocab) >= {"eyebrow.left", "eye.left", "body.head.face", "object.held", "accessory.headwear"}
    assert vocab["object.held"][0] == "magic wand"
    head = detection_vocabulary(None, only=["eye.left", "accessory.headwear"])
    assert set(head) == {"eye.left", "accessory.headwear"} and "tiara" in head["accessory.headwear"]
    dense_skipped = detection_vocabulary(None, skip_groups=fusion.DENSE_GROUPS)
    assert "hair" not in dense_skipped and "eye.left" in dense_skipped


def test_prompt_term_phrases_and_box_selection():
    phrases = prompt_term_phrases("A knight with a red Cape, two swords and blue eyes", ["clothing.outer", "object.held", "eye.left"])
    assert phrases == {"clothing.outer": ["cape"], "object.held": ["sword"], "eye.left": ["eye"]}
    boxes = [Box2D("hair", (0, 0, 10, 10), 0.9, "m", "hair"), Box2D("hair", (1, 1, 10, 10), 0.8, "m", "bangs"),
             Box2D("eye.left", (1, 1, 10, 10), 0.7, "m", "eye")]
    assert [(b.class_id, b.score) for b in select_boxes(boxes)] == [("hair", 0.9), ("eye.left", 0.7)]


# ==================================================================================================
# Subject and profile rules
# ==================================================================================================


@pytest.mark.parametrize(
    ("caption", "subject"),
    [
        ("An anime girl with long blonde hair", "anime_character"),
        ("a cartoon drawing of a knight", "anime_character"),
        ("A photo of a woman in a red dress", "realistic_human"),
        ("a man standing", "realistic_human"),
        ("a wooden chair", "object"),
        ("a mannequin", "object"),
    ],
)
def test_subject_keyword_rules(caption, subject):
    assert subject_from_caption(caption) == subject


def _roles(pair: bool = True, parsers: dict[str, str] | None = None) -> SegmentationRoles:
    return SegmentationRoles(
        detector="grounding_dino_base" if pair else None,
        refiner="sam2_1_hiera_large" if pair else None,
        grounder=None,
        parsers=parsers or {},
        issues=(),
    )


def test_profile_resolution_and_degradation():
    assert resolve_profile("auto", "object", _roles()) == ("baseline", [])
    profile, warnings = resolve_profile("auto", "anime_character", _roles())
    assert profile == "baseline" and "see_through_l2d unavailable" in warnings[0]
    assert resolve_profile("auto", "anime_character", _roles(parsers={"anime": "see_through_l2d"}))[0] == "anime"
    assert resolve_profile("auto", "object", _roles(), default_profile="realistic")[0] == "baseline"
    profile, warnings = resolve_profile("baseline", "object", _roles(pair=False, parsers={"realistic": "sapiens2_seg_0_4b"}))
    assert profile == "realistic" and warnings
    with pytest.raises(AppError) as info:
        resolve_profile("auto", "object", _roles(pair=False))
    assert info.value.code == "segmentation_failed"


def test_resolve_roles_reports_every_unusable_model():
    states = {"grounding_dino_base": "installed", "sam2_1_hiera_large": "loaded"}
    registry = SimpleNamespace(
        status=lambda mid: SimpleNamespace(
            state=states.get(mid, "unavailable"), reason_code="ok" if mid in states else "not_approved", reason=None
        )
    )
    roles = resolve_roles(registry)
    assert roles.has_pair and roles.grounder is None and roles.parsers == {}
    assert {i.model_id for i in roles.issues} == {"florence2_large", "see_through_l2d", "sapiens2_seg_0_4b"}
    none = resolve_roles(None)
    assert not none.usable and len(none.reasons()) == 5
