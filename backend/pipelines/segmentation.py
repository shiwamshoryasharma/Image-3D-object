"""Segmentation runs (``sg_``): 2D decomposition before reconstruction (part-aware DESIGN.md section 4.5).

Steps (section 4.5.5)::

    prepare  5  cpu   load the snapshot taken at creation (source.png), upstream downscale
    frame    5  cpu   conditioning frame: predicted for reference images, recovered for job sources (section 4.5.2)
    parse   25  gpu   dense parser of the profile (skipped without one)
    detect  25  gpu   Grounding DINO (batched vocabulary) + Florence-2 grounding, full image then head crop
    refine  30  gpu   SAM 2.1 per box (+ a positive point for small boxes)
    fuse    10  cpu   section 4.5.4 fusion, eye heuristics, artifacts

Phases ``cpu[prepare, frame]`` (also loads the providers into CPU RAM), ``gpu[parse, detect, refine]``,
``cpu[fuse]``. Providers come from the registry (``registry.provider(id)``); every role whose model is not usable is
skipped with a warning (degradation, section 4.5.3).

:func:`segment_image` runs the model and fusion stages in one call for reuse by part analyses (internal pass and
extra views); it must be called on the GPU thread inside a GPU phase of the caller.

Artifacts (section 4.5.6), moved into ``runtime/segmentations/<sg>/`` by ``finalize``: ``frame.json``,
``labels.png`` (16-bit, value = taxonomy value), ``overlay.png`` (RGBA, <= 1024 px), ``masks/<value>.png`` (8-bit soft
masks) and ``segmentation.json`` (the result payload).
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import time
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, cast

import cv2
import numpy as np
from PIL import Image

from core.exceptions import AppError
from inference.providers.base import USABLE_STATES
from inference.segmentation.base import Box2D, Detector2D, Grounder2D, Mask2D, MaskRefiner, Parser2D
from models.runs import RUN_ERROR_CODE, SOURCE_MISSING_ERROR_CODE, StepDef
from models.segmentation import (
    FRAME_FILE,
    LABELS_FILE,
    ORIGINAL_FILE,
    OVERLAY_FILE,
    RESULT_FILE,
    SOURCE_FILE,
    SegmentationFrame,
    SegmentationLabel,
    SegmentationResult,
    SegmentationRun,
)
from parts.taxonomy import CLASSES, TAXONOMY, classes_for_term
from pipelines.runs.executor import ExecutorDeps, RunPhase
from processing import conditioning as cond
from processing.seg2d import fusion
from processing.seg2d.eyes import mask_bbox
from schemas.segmentation import SegmentationOptions
from storage.run_layout import MASKS_DIR
from utils.files import ensure_dir, retry_os, safe_rmtree

if TYPE_CHECKING:  # pragma: no cover
    from pipelines.runs.context import RunContext, StepHandle

logger = logging.getLogger(__name__)

DETECTOR_ID: Final = "grounding_dino_base"
REFINER_ID: Final = "sam2_1_hiera_large"
GROUNDER_ID: Final = "florence2_large"
PARSER_IDS: Final[Mapping[str, str]] = {"anime": "see_through_l2d", "realistic": "sapiens2_seg_0_4b"}
"""Profile -> its dense parser (section 4.5.3)."""
PROFILE_FOR_SUBJECT: Final[Mapping[str, str]] = {
    "anime_character": "anime",
    "realistic_human": "realistic",
    "object": "baseline",
}
ANIME_WORDS: Final = re.compile(r"\b(anime|animated|manga|cartoon|chibi|illustration|drawing)\b")
"""Section 4.5.3 list plus ``animated`` (Florence-2 ``<DETAILED_CAPTION>`` says "an animated girl" for anime art)."""
HUMAN_WORDS: Final = re.compile(r"\b(person|man|woman|boy|girl|photo)\b")

TEXT_THRESHOLD: Final = 0.25
HEAD_PASS_SIZE: Final = 1008
HEAD_CLASS_IDS: Final[tuple[str, ...]] = (
    "eye.left",
    "eyebrow.left",
    "eyelash.left",
    "body.head.mouth",
    "body.head.nose",
    "body.head.ear.left",
    "accessory.earwear",
    "accessory.eyewear",
    "accessory.headwear",
)
"""Small classes of the head pass (sided families by their ``.left`` id)."""
MAX_BOXES_PER_CLASS: Final = 6
STEP_RANGES: Final[Mapping[str, tuple[float, float]]] = {
    "parse": (0.0, 0.25),
    "detect": (0.25, 0.5),
    "refine": (0.5, 0.85),
    "fuse": (0.85, 1.0),
}
"""Share of each stage when :func:`segment_image` reports into one caller step."""

WORK_OUT_DIR: Final = "out"
RAW_DIR: Final = "raw"
ANALYSIS_FILE: Final = "analysis.png"
RESULT_FILES: Final = (FRAME_FILE, LABELS_FILE, OVERLAY_FILE, RESULT_FILE)


# --------------------------------------------------------------------------------------------------
# Roles and profiles
# --------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RoleIssue:
    model_id: str
    role: str
    state: str
    reason_code: str
    reason: str | None

    def to_reason(self) -> dict[str, Any]:
        return {"model_id": self.model_id, "state": self.state, "reason_code": self.reason_code, "reason": self.reason}


@dataclass(frozen=True, slots=True)
class SegmentationRoles:
    """Which role models are usable right now (registry states, section 5.3)."""

    detector: str | None
    refiner: str | None
    grounder: str | None
    parsers: Mapping[str, str]
    """Profile -> usable parser id."""
    issues: tuple[RoleIssue, ...]

    @property
    def has_pair(self) -> bool:
        return self.detector is not None and self.refiner is not None

    @property
    def usable(self) -> bool:
        """At least one detector + refiner pair or one parser (else creation is 503 ``capability_unavailable``)."""
        return self.has_pair or bool(self.parsers)

    def issue(self, model_id: str) -> RoleIssue | None:
        return next((i for i in self.issues if i.model_id == model_id), None)

    def reasons(self) -> list[dict[str, Any]]:
        return [issue.to_reason() for issue in self.issues]

    def model_ids(self, profiles: Iterable[str] = ()) -> tuple[str, ...]:
        ids = [m for m in (self.grounder, self.detector, self.refiner) if m is not None]
        ids.extend(self.parsers[p] for p in profiles if p in self.parsers)
        return tuple(dict.fromkeys(ids))


def _status(registry: Any, model_id: str) -> Any | None:
    try:
        return registry.status(model_id)
    except Exception:  # noqa: BLE001 - an unknown id is simply unusable
        logger.warning("No registry status for %s", model_id, exc_info=True)
        return None


def model_usable(registry: Any, model_id: str) -> bool:
    if registry is None:
        return False
    usable = getattr(registry, "usable", None)
    if callable(usable):
        try:
            return bool(usable(model_id))
        except Exception:  # noqa: BLE001
            return False
    status = _status(registry, model_id)
    return status is not None and getattr(status, "state", None) in USABLE_STATES


def resolve_roles(registry: Any) -> SegmentationRoles:
    issues: list[RoleIssue] = []

    def pick(model_id: str, role: str) -> str | None:
        if model_usable(registry, model_id):
            return model_id
        status = _status(registry, model_id) if registry is not None else None
        issues.append(
            RoleIssue(
                model_id=model_id,
                role=role,
                state=str(getattr(status, "state", "unavailable")),
                reason_code=str(getattr(status, "reason_code", "error" if registry is not None else "not_implemented")),
                reason=getattr(status, "reason", None) if status is not None else "The model registry is not available",
            )
        )
        return None

    detector = pick(DETECTOR_ID, "detector")
    refiner = pick(REFINER_ID, "refiner")
    grounder = pick(GROUNDER_ID, "grounder")
    parsers = {profile: model_id for profile, model_id in PARSER_IDS.items() if pick(model_id, "parser")}
    return SegmentationRoles(detector, refiner, grounder, parsers, tuple(issues))


def subject_from_caption(caption: str) -> str:
    """Deterministic keyword rules of ``subject: auto`` (section 4.5.3)."""
    text = caption.lower()
    if ANIME_WORDS.search(text):
        return "anime_character"
    if HUMAN_WORDS.search(text):
        return "realistic_human"
    return "object"


def resolve_profile(
    requested: str, subject: str, roles: SegmentationRoles, default_profile: str = "auto"
) -> tuple[str, list[str]]:
    """Effective profile and the degradation warnings (``profile_degraded``)."""
    warnings: list[str] = []
    profile = requested if requested != "auto" else default_profile
    if profile == "auto":
        profile = PROFILE_FOR_SUBJECT.get(subject, "baseline")
    if profile in PARSER_IDS and profile not in roles.parsers:
        parser_id = PARSER_IDS[profile]
        issue = roles.issue(parser_id)
        code = issue.reason_code if issue is not None else "unavailable"
        if roles.has_pair:
            warnings.append(f"{parser_id} unavailable ({code}); using baseline")
            profile = "baseline"
        elif roles.parsers:
            fallback = next(iter(roles.parsers))
            warnings.append(f"{parser_id} unavailable ({code}); using {fallback}")
            profile = fallback
    if profile == "baseline" and not roles.has_pair and roles.parsers:
        fallback = next(iter(roles.parsers))
        warnings.append(f"No detector + refiner pair is usable; using the {fallback} parser")
        profile = fallback
    if profile == "baseline" and not roles.has_pair:
        raise AppError(
            "No segmentation model is usable (no detector + refiner pair and no parser)",
            code="segmentation_failed",
            status_code=503,
            details={"reasons": roles.reasons()},
        )
    return profile, warnings


# --------------------------------------------------------------------------------------------------
# Vocabulary
# --------------------------------------------------------------------------------------------------


def _family_id(class_id: str) -> str:
    """The id that stands for a sided family in detector prompts (``eye.right`` -> ``eye.left``)."""
    return class_id[: -len(".right")] + ".left" if class_id.endswith(".right") else class_id


def requested_classes(options: SegmentationOptions) -> frozenset[str] | None:
    return None if options.classes is None else frozenset(options.classes)


def detection_vocabulary(
    classes: frozenset[str] | None,
    *,
    only: Iterable[str] | None = None,
    skip_groups: Iterable[str] = (),
    extra_terms: Sequence[str] = (),
) -> dict[str, tuple[str, ...]]:
    """Class id -> phrases for the detector: every aliased taxonomy class (one entry per sided family), restricted to
    the requested classes. Eyes and the face are kept when eyebrows, eyelashes or eyelids are requested (their
    geometry drives those rules); ``extra_terms`` are added under their class."""
    wanted: set[str] | None = None
    if classes is not None:
        wanted = {_family_id(c) for c in classes}
        if wanted & {"eyebrow.left", "eyelash.left", "body.head.eyelid.left"}:
            wanted |= {"eye.left", "body.head.face"}
    restrict = {_family_id(c) for c in only} if only is not None else None
    skip = set(skip_groups)
    vocab: dict[str, list[str]] = {}
    for cls in CLASSES:
        if not cls.aliases or cls.id.endswith(".right"):
            continue
        if wanted is not None and cls.id not in wanted:
            continue
        if restrict is not None and cls.id not in restrict:
            continue
        if cls.group in skip:
            continue
        vocab[cls.id] = list(cls.aliases)
    for term in extra_terms:
        for class_id in classes_for_term(term):
            key = _family_id(class_id)
            vocab.setdefault(key, [])
            if term not in vocab[key]:
                vocab[key].insert(0, term)
    return {k: tuple(v) for k, v in vocab.items()}


def prompt_term_phrases(prompt: str, class_ids: Iterable[str]) -> dict[str, list[str]]:
    """The words of ``prompt`` that matched each expected class (aliases as whole words, optional plural)."""
    text = " ".join(re.sub(r"[-_]+", " ", prompt.lower()).split())
    result: dict[str, list[str]] = {}
    for class_id in class_ids:
        cls = TAXONOMY.get(class_id)
        if cls is None:
            continue
        found = [
            alias
            for alias in cls.aliases
            if re.search(r"(?<![a-z0-9])" + re.escape(alias) + r"(?:es|s)?(?![a-z0-9])", text)
        ]
        if found:
            result[_family_id(class_id)] = list(dict.fromkeys([*result.get(_family_id(class_id), []), *found]))
    return result


def grounding_phrases(
    options: SegmentationOptions, prompt_phrases: Mapping[str, Sequence[str]]
) -> dict[str, tuple[str, ...]]:
    """Florence-2 phrases: the prompt's own words for its expected terms plus the extra terms."""
    classes = requested_classes(options)
    phrases: dict[str, list[str]] = {}
    for class_id, items in prompt_phrases.items():
        if classes is None or class_id in classes or _family_id(class_id) in {_family_id(c) for c in classes}:
            phrases.setdefault(class_id, []).extend(items)
    for term in options.extra_terms:
        for class_id in classes_for_term(term):
            phrases.setdefault(_family_id(class_id), []).append(term)
    return {k: tuple(dict.fromkeys(v)) for k, v in phrases.items()}


def select_boxes(boxes: Sequence[Box2D]) -> list[Box2D]:
    """Class-wise non-maximum suppression, at most :data:`MAX_BOXES_PER_CLASS` boxes per class, best first."""
    by_class: dict[str, list[Box2D]] = {}
    for box in boxes:
        by_class.setdefault(box.class_id, []).append(box)
    selected: list[Box2D] = []
    for items in by_class.values():
        kept = fusion.nms([b.xyxy for b in items], [b.score for b in items])
        selected.extend(items[i] for i in kept[:MAX_BOXES_PER_CLASS])
    return selected


# --------------------------------------------------------------------------------------------------
# Raw model output
# --------------------------------------------------------------------------------------------------


@dataclass(slots=True)
class RawCandidate:
    class_id: str
    path: Path
    score: float
    role: fusion.CandidateRole
    sources: tuple[str, ...]
    box: tuple[float, float, float, float] | None
    head_pass: bool = False
    to_image: np.ndarray | None = None
    """2x3 matrix from the mask's pixel grid to the analysis image (``None``: same grid)."""


@dataclass(slots=True)
class RawSegmentation:
    subject: str
    profile: str
    caption: str | None
    candidates: list[RawCandidate] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SegmentationInput:
    """What the model stages need. ``image`` is the analysis image (results use its pixel grid)."""

    image: Path
    options: SegmentationOptions
    original: Path | None = None
    source_to_image: np.ndarray | None = None
    """2x3 matrix original pixel -> analysis pixel; ``None``: the head pass crops ``image`` itself."""
    expected_terms: tuple[str, ...] = ()
    prompt_phrases: Mapping[str, Sequence[str]] = field(default_factory=dict)
    subject_hint: str | None = None
    default_profile: str = "auto"


class _Stage:
    __slots__ = ("details", "progress_fn")

    def __init__(self, progress_fn: Any, details: dict[str, Any]) -> None:
        self.progress_fn = progress_fn
        self.details = details

    def progress(self, fraction: float, message: str | None = None) -> None:
        self.progress_fn(max(0.0, min(1.0, fraction)), message)


class _Stages:
    """Reports the stages as the run's own steps, or as fractions of one caller step (``segment_image``)."""

    def __init__(self, ctx: RunContext, step: StepHandle | None) -> None:
        self._ctx = ctx
        self._step = step

    @contextmanager
    def stage(self, name: str, message: str) -> Iterator[_Stage]:
        if self._step is None:
            with self._ctx.step(name, message) as handle:
                yield _Stage(handle.progress, handle.details)
            return
        low, high = STEP_RANGES[name]
        parent = self._step
        parent.progress(low, message)
        details = parent.details.setdefault(name, {})
        yield _Stage(lambda f, m=None: parent.progress(low + (high - low) * f, m), details)
        parent.progress(high)

    def skip(self, name: str, reason: str) -> None:
        if self._step is None:
            self._ctx.skip(name, reason)
        else:
            self._step.details.setdefault(name, {})["skipped"] = reason

    def warn(self, code: str, message: str, name: str) -> None:
        self._ctx.warn(code, message, name if self._step is None else self._step.step_id)


def _provider(registry: Any, model_id: str) -> Any:
    getter = getattr(registry, "provider", None)
    if not callable(getter):
        raise AppError("The model registry cannot provide models in this build", code=RUN_ERROR_CODE, status_code=503)
    return getter(model_id)


def load_providers(registry: Any, model_ids: Sequence[str], cancel: Any) -> None:
    """``load()`` the providers into CPU RAM (CPU thread, before the GPU phase)."""
    for model_id in model_ids:
        cancel.raise_if_cancelled()
        _provider(registry, model_id).load(cancel)


def run_models(inp: SegmentationInput, ctx: RunContext, raw_dir: Path, stages: _Stages) -> RawSegmentation:
    """GPU thread: subject, parser, detector + grounder (full image and head crop) and refiner."""
    registry = ctx.registry
    if registry is None:
        raise AppError("The model registry is not available in this build", code=RUN_ERROR_CODE, status_code=503)
    cancel = ctx.cancel
    roles = resolve_roles(registry)
    options = inp.options
    classes = requested_classes(options)
    ensure_dir(raw_dir)
    image_size = Image.open(inp.image).size

    # Subject (section 4.5.3): explicit, from the prompt style, else the Florence-2 caption.
    caption: str | None = None
    subject = options.subject
    started = time.perf_counter()
    if subject == "auto":
        if inp.subject_hint is not None:
            subject = inp.subject_hint
        elif roles.grounder is not None:
            ctx.message("Identifying the subject")
            grounder = cast(Grounder2D, _provider(registry, roles.grounder))
            caption = grounder.caption(inp.image, cancel)
            ctx.use_model(roles.grounder)
            subject = subject_from_caption(caption)
        else:
            subject = "object"
    caption_s = time.perf_counter() - started
    profile, degraded = resolve_profile(options.profile, subject, roles, inp.default_profile)
    raw = RawSegmentation(subject=subject, profile=profile, caption=caption)
    raw.timings["caption"] = round(caption_s, 3)
    for message in degraded:
        stages.warn("profile_degraded", message, "parse")
    if roles.grounder is None:
        issue = roles.issue(GROUNDER_ID)
        code = issue.reason_code if issue is not None else "unavailable"
        stages.warn("profile_degraded", f"{GROUNDER_ID} unavailable ({code}); captions and phrase grounding skipped", "detect")

    # Parse.
    parser_id = roles.parsers.get(profile)
    started = time.perf_counter()
    if parser_id is None:
        stages.skip("parse", "No parser for the baseline profile")
    else:
        with stages.stage("parse", "Parsing parts") as stage:
            parser = cast(Parser2D, _provider(registry, parser_id))
            masks = parser.parse(inp.image, raw_dir / "parse", cancel)
            ctx.use_model(parser_id)
            raw.candidates.extend(_raw_from_masks(masks, "parser"))
            stage.details.update({"parser": parser_id, "masks": len(masks)})
    raw.timings["parse"] = round(time.perf_counter() - started, 3)

    # Detect: full image, then the head crop.
    full_boxes: list[Box2D] = []
    head_boxes: list[Box2D] = []
    head: tuple[Path, np.ndarray] | None = None
    started = time.perf_counter()
    if roles.has_pair or roles.grounder is not None:
        with stages.stage("detect", "Detecting parts") as stage:
            skip_groups = fusion.DENSE_GROUPS if parser_id is not None else ()
            boxes: list[Box2D] = []
            if roles.has_pair:
                assert roles.detector is not None
                detector = cast(Detector2D, _provider(registry, roles.detector))
                vocab = detection_vocabulary(classes, skip_groups=skip_groups, extra_terms=options.extra_terms)
                boxes.extend(
                    detector.detect(
                        inp.image, vocab, box_threshold=options.min_confidence, text_threshold=TEXT_THRESHOLD,
                        cancel=cancel,
                    )
                )
                ctx.use_model(roles.detector)
                stage.details["gdino_batches"] = int(getattr(detector, "last_batch_count", 0))
                stage.details["gdino_classes"] = len(vocab)
            stage.progress(0.4)
            phrases = grounding_phrases(options, inp.prompt_phrases)
            if roles.grounder is not None and phrases and roles.refiner is not None:
                grounder = cast(Grounder2D, _provider(registry, roles.grounder))
                boxes.extend(grounder.ground(inp.image, phrases, cancel))
                ctx.use_model(roles.grounder)
                stage.details["grounded_phrases"] = sum(len(v) for v in phrases.values())
            full_boxes = select_boxes(boxes)
            stage.details["boxes"] = len(full_boxes)
            stage.progress(0.6)
            if options.head_pass and roles.has_pair:
                box = _head_box(full_boxes, raw.candidates, image_size)
                if box is not None:
                    head = _head_crop(inp, box, raw_dir)
                    head_vocab = detection_vocabulary(classes, only=HEAD_CLASS_IDS)
                    if head_vocab:
                        assert roles.detector is not None
                        detector = cast(Detector2D, _provider(registry, roles.detector))
                        head_boxes = select_boxes(
                            detector.detect(
                                head[0], head_vocab, box_threshold=options.min_confidence,
                                text_threshold=TEXT_THRESHOLD, cancel=cancel,
                            )
                        )
                        stage.details["head_batches"] = int(getattr(detector, "last_batch_count", 0))
                    stage.details["head_box"] = [round(v, 1) for v in box]
                    stage.details["head_boxes"] = len(head_boxes)
                    stage.details["head_full_resolution"] = inp.source_to_image is not None
                else:
                    stage.details["head_box"] = None
            raw.details["boxes"] = [_box_detail(b) for b in full_boxes]
            raw.details["head_boxes"] = [_box_detail(b) for b in head_boxes]
    else:
        stages.skip("detect", "No detector + refiner pair or grounder is usable")
    raw.timings["detect"] = round(time.perf_counter() - started, 3)

    # Refine.
    started = time.perf_counter()
    if roles.refiner is not None and (full_boxes or head_boxes):
        with stages.stage("refine", "Refining masks") as stage:
            refiner = cast(MaskRefiner, _provider(registry, roles.refiner))
            full_masks = refiner.refine(inp.image, full_boxes, raw_dir / "full", cancel)
            raw.candidates.extend(_raw_from_masks(full_masks, "detector", full_boxes))
            stage.progress(0.6)
            if head is not None and head_boxes:
                head_masks = refiner.refine(head[0], head_boxes, raw_dir / "head", cancel)
                raw.candidates.extend(_raw_from_masks(head_masks, "detector", head_boxes, head_pass=True, to_image=head[1]))
                stage.details["head_masks"] = len(head_masks)
            ctx.use_model(roles.refiner)
            stage.details["masks"] = len(full_masks)
    else:
        stages.skip("refine", "No boxes to refine" if roles.refiner is not None else "No refiner is usable")
    raw.timings["refine"] = round(time.perf_counter() - started, 3)
    return raw


def _box_detail(box: Box2D) -> dict[str, Any]:
    return {
        "class_id": box.class_id,
        "phrase": box.phrase,
        "score": round(box.score, 3),
        "xyxy": [round(v, 1) for v in box.xyxy],
        "source": box.source,
    }


def _raw_from_masks(
    masks: Sequence[Mask2D],
    role: fusion.CandidateRole,
    boxes: Sequence[Box2D] | None = None,
    *,
    head_pass: bool = False,
    to_image: np.ndarray | None = None,
) -> list[RawCandidate]:
    result: list[RawCandidate] = []
    for mask in masks:
        box = boxes[mask.instance] if boxes is not None and 0 <= mask.instance < len(boxes) else None
        xyxy: tuple[float, float, float, float] = box.xyxy if box is not None else tuple(float(v) for v in mask.bbox)  # type: ignore[assignment]
        sources = (box.source, mask.source) if box is not None and box.source != mask.source else (mask.source,)
        result.append(
            RawCandidate(
                class_id=mask.class_id,
                path=mask.path,
                score=mask.score,
                role=role,
                sources=tuple(dict.fromkeys(sources)),
                box=cond.map_box(xyxy, to_image) if to_image is not None else xyxy,
                head_pass=head_pass,
                to_image=to_image,
            )
        )
    return result


def _head_box(
    boxes: Sequence[Box2D], parsed: Sequence[RawCandidate], size: tuple[int, int]
) -> tuple[float, float, float, float] | None:
    def best(class_ids: set[str]) -> Box2D | None:
        items = [b for b in boxes if b.class_id in class_ids]
        return max(items, key=lambda b: b.score) if items else None

    face = best({"body.head.face"})
    hair = best({"hair"})
    eyes = sorted((b for b in boxes if b.class_id.startswith("eye.")), key=lambda b: -b.score)[:2]
    face_xyxy = face.xyxy if face is not None else None
    hair_xyxy = hair.xyxy if hair is not None else None
    for c in parsed:
        if face_xyxy is None and c.class_id == "body.head.face" and c.box is not None:
            face_xyxy = c.box
        if hair_xyxy is None and c.class_id == "hair" and c.box is not None:
            hair_xyxy = c.box
    return fusion.head_box(face_xyxy, [e.xyxy for e in eyes], hair_xyxy, size)


def _head_crop(inp: SegmentationInput, box: tuple[float, float, float, float], raw_dir: Path) -> tuple[Path, np.ndarray]:
    """Crop the head box at full resolution (the original when the frame is known) and resize it to 1008 px.

    Returns the crop file and the 2x3 matrix crop pixel -> analysis pixel.
    """
    if inp.source_to_image is not None and inp.original is not None:
        source_path = inp.original
        to_image = inp.source_to_image
    else:
        source_path = inp.image
        to_image = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    to_source = cv2.invertAffineTransform(to_image)
    x0, y0, x1, y1 = cond.map_box(box, to_source)
    side = max(x1 - x0, y1 - y0)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    left, top = int(math.floor(cx - side / 2)), int(math.floor(cy - side / 2))
    span = max(1, int(math.ceil(side)))
    with Image.open(source_path) as src:
        crop = src.convert("RGB").crop((left, top, left + span, top + span))
    crop = crop.resize((HEAD_PASS_SIZE, HEAD_PASS_SIZE), Image.Resampling.LANCZOS)
    path = raw_dir / "head.png"
    crop.save(path)
    k = span / HEAD_PASS_SIZE
    crop_to_source = np.array([[k, 0.0, left + 0.5 * k - 0.5], [0.0, k, top + 0.5 * k - 0.5]])
    return path, _compose(to_image, crop_to_source)


def _compose(outer: np.ndarray, inner: np.ndarray) -> np.ndarray:
    """2x3 affine ``outer`` after ``inner``."""
    a = np.vstack([outer, [0.0, 0.0, 1.0]])
    b = np.vstack([inner, [0.0, 0.0, 1.0]])
    return (a @ b)[:2]


# --------------------------------------------------------------------------------------------------
# Fusion and artifacts
# --------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OutputSpace:
    space: str
    """``conditioning`` or ``source``."""
    frame: cond.ConditioningFrame | None
    size: tuple[int, int]
    """``(width, height)`` of the result pixel grid."""
    analysis_to_result: np.ndarray | None = None
    """2x3 matrix analysis pixel -> result pixel (``None``: same grid)."""


def fuse_raw(
    raw: RawSegmentation,
    inp: SegmentationInput,
    out: OutputSpace,
    out_dir: Path,
    stages: _Stages,
) -> SegmentationResult:
    """CPU: load the raw masks, fuse them (section 4.5.4) and write the artifacts into ``out_dir``."""
    started = time.perf_counter()
    with stages.stage("fuse", "Fusing labels") as stage:
        image_rgb = np.asarray(Image.open(inp.image).convert("RGB"))
        height, width = image_rgb.shape[:2]
        candidates: list[fusion.MaskCandidate] = []
        for raw_candidate in raw.candidates:
            mask = np.asarray(Image.open(raw_candidate.path).convert("L"), dtype=np.float32) / 255.0
            if raw_candidate.to_image is not None:
                mask = cond.warp_mask(mask, raw_candidate.to_image, (width, height), nearest=False)
            elif mask.shape != (height, width):
                mask = cv2.resize(mask, (width, height), interpolation=cv2.INTER_LINEAR)
            candidates.append(
                fusion.MaskCandidate(
                    class_id=raw_candidate.class_id,
                    mask=mask,
                    score=raw_candidate.score,
                    role=raw_candidate.role,
                    sources=raw_candidate.sources,
                    box=raw_candidate.box,
                    head_pass=raw_candidate.head_pass,
                )
            )
        stage.progress(0.3)
        fused = fusion.fuse(
            candidates, image_rgb, min_confidence=inp.options.min_confidence, classes=requested_classes(inp.options)
        )
        stage.progress(0.7)
        labels, soft, stats, annotations = _to_result_space(fused, out)
        ensure_dir(out_dir / MASKS_DIR)
        Image.fromarray(labels.astype(np.uint16)).save(out_dir / LABELS_FILE)
        for value, mask in soft.items():
            Image.fromarray(mask, "L").save(out_dir / MASKS_DIR / f"{value}.png")
        overlay_base = image_rgb
        if out.analysis_to_result is not None:
            overlay_base = np.asarray(Image.open(inp.original or inp.image).convert("RGB"))
            if overlay_base.shape[:2] != labels.shape:
                overlay_base = cv2.resize(overlay_base, (labels.shape[1], labels.shape[0]))
        Image.fromarray(fusion.overlay_image(overlay_base, labels), "RGBA").save(out_dir / OVERLAY_FILE)
        found = {s.category for s in stats}
        expected = list(dict.fromkeys([*inp.expected_terms, *_extra_term_classes(inp.options)]))
        missing = [class_id for class_id in _expand_sides(expected) if class_id not in found]
        stage.details.update(
            {
                "labels": len(stats),
                "rejected": fused.rejected[:50],
                "candidates": len(candidates),
            }
        )
    raw.timings["fuse"] = round(time.perf_counter() - started, 3)
    timings = {key: raw.timings.get(key, 0.0) for key in ("parse", "detect", "refine", "fuse")}
    timings["caption"] = raw.timings.get("caption", 0.0)
    timings["total"] = round(sum(timings.values()), 3)
    result = SegmentationResult(
        space=cast(Any, out.space),
        frame=SegmentationFrame.model_validate(out.frame.summary()) if out.frame is not None else None,
        subject=cast(Any, raw.subject),
        profile=cast(Any, raw.profile),
        labels=[SegmentationLabel.model_validate(s.to_dict()) for s in stats],
        annotations=annotations,
        expected_terms_missing=missing,
        timings=timings,
    )
    (out_dir / RESULT_FILE).write_text(
        json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def _to_result_space(
    fused: fusion.FusionOutput, out: OutputSpace
) -> tuple[np.ndarray, dict[int, np.ndarray], list[fusion.LabelStat], dict[str, dict[str, Any]]]:
    if out.analysis_to_result is None:
        return fused.labels, fused.soft, fused.stats, fused.annotations
    matrix = out.analysis_to_result
    labels = cond.warp_mask(fused.labels, matrix, out.size, nearest=True)
    soft = {v: cond.warp_mask(m, matrix, out.size, nearest=False) for v, m in fused.soft.items()}
    stats: list[fusion.LabelStat] = []
    for stat in fused.stats:
        region = labels == stat.value
        bbox = mask_bbox(region)
        if bbox is None:
            continue
        soft[stat.value][~region] = 0
        stats.append(
            fusion.LabelStat(
                value=stat.value,
                category=stat.category,
                group=stat.group,
                area_px=int(region.sum()),
                confidence=stat.confidence,
                bbox=bbox,
                side=stat.side,
                sources=stat.sources,
            )
        )
    kept = {s.value for s in stats}
    soft = {v: m for v, m in soft.items() if v in kept}
    annotations: dict[str, dict[str, Any]] = {}
    for key, value in fused.annotations.items():
        curve = value.get("lid_curve")
        if isinstance(curve, list) and curve:
            points = np.hstack([np.asarray(curve, dtype=np.float64), np.ones((len(curve), 1))]) @ matrix.T
            value = {**value, "lid_curve": [[int(round(x)), int(round(y))] for x, y in points]}
        annotations[key] = value
    return labels, soft, stats, annotations


def _extra_term_classes(options: SegmentationOptions) -> list[str]:
    return [class_id for term in options.extra_terms for class_id in classes_for_term(term)]


def _expand_sides(class_ids: Iterable[str]) -> list[str]:
    result: list[str] = []
    for class_id in class_ids:
        family = fusion.family_of(class_id)
        pair = fusion.sided_ids(family)
        result.extend(pair if pair is not None else (class_id,))
    return list(dict.fromkeys(result))


def segment_image(
    image: Path,
    original: Path | None,
    frame: cond.ConditioningFrame | None,
    options: SegmentationOptions,
    ctx: RunContext,
    out_dir: Path,
    *,
    step: StepHandle | None = None,
    expected_terms: Sequence[str] = (),
    prompt_phrases: Mapping[str, Sequence[str]] | None = None,
    subject_hint: str | None = None,
    default_profile: str = "auto",
) -> SegmentationResult:
    """Segment a conditioning image in one call (part analyses: internal pass and extra views).

    GPU thread, inside a GPU phase of the caller. ``frame`` maps ``original`` to ``image``; with a verified frame the
    head pass crops ``original`` at full resolution, otherwise ``image`` itself. Results are in conditioning space.
    Progress goes to ``step`` when given (the stages share it), else to the ``parse``/``detect``/``refine``/``fuse``
    steps of the run.
    """
    source_to_image = None
    if frame is not None and frame.verified and original is not None:
        source_to_image = cond.source_to_frame_matrix(frame.transform)
    inp = SegmentationInput(
        image=image,
        options=options,
        original=original,
        source_to_image=source_to_image,
        expected_terms=tuple(expected_terms),
        prompt_phrases=prompt_phrases or {},
        subject_hint=subject_hint,
        default_profile=default_profile,
    )
    stages = _Stages(ctx, step)
    ensure_dir(out_dir)
    size = Image.open(image).size
    raw = run_models(inp, ctx, out_dir / RAW_DIR, stages)
    out = OutputSpace(space="conditioning", frame=frame, size=size)
    result = fuse_raw(raw, inp, out, out_dir, stages)
    safe_rmtree(out_dir / RAW_DIR)
    return result


# --------------------------------------------------------------------------------------------------
# Executor
# --------------------------------------------------------------------------------------------------


@dataclass(slots=True)
class SegmentationState:
    run_dir: Path
    work_dir: Path
    source_kind: str
    options: SegmentationOptions
    expected_terms: tuple[str, ...]
    prompt_phrases: dict[str, list[str]]
    subject_hint: str | None
    preprocess: str | None
    default_profile: str
    roles: SegmentationRoles | None = None
    inp: SegmentationInput | None = None
    out: OutputSpace | None = None
    raw: RawSegmentation | None = None
    result: SegmentationResult | None = None

    @property
    def out_dir(self) -> Path:
        return self.work_dir / WORK_OUT_DIR


class SegmentationExecutor:
    """``RunExecutor`` of ``sg_`` runs (built by the container as ``SegmentationExecutor(deps)``)."""

    kind = "segmentation"
    prefix = "sg"

    def __init__(self, deps: ExecutorDeps) -> None:
        self._deps = deps

    # ------------------------------------------------------------------ plan
    def plan(self, run: SegmentationRun) -> tuple[StepDef, ...]:
        roles = resolve_roles(self._deps.registry)
        options = _options_of(run)
        parse_skip = self._parse_skip_reason(roles, options, run)
        pair_skip = None if roles.has_pair or roles.grounder is not None else "No detector + refiner pair is usable"
        refine_skip = None if roles.refiner is not None else "No refiner is usable"
        return (
            StepDef("prepare", "Preparing the image", 5, "cpu"),
            StepDef("frame", "Conditioning frame", 5, "cpu"),
            StepDef("parse", "Parsing parts", 25, "gpu", parse_skip),
            StepDef("detect", "Detecting parts", 25, "gpu", pair_skip),
            StepDef("refine", "Refining masks", 30, "gpu", refine_skip),
            StepDef("fuse", "Fusing labels", 10, "cpu"),
        )

    def _parse_skip_reason(self, roles: SegmentationRoles, options: SegmentationOptions, run: SegmentationRun) -> str | None:
        if not roles.parsers:
            return "No parser is usable; using baseline"
        profile = options.profile if options.profile != "auto" else self._deps.settings.segmentation_default_profile
        if profile == "baseline":
            return "No parser for the baseline profile"
        if profile in PARSER_IDS and profile not in roles.parsers and roles.has_pair:
            return f"{PARSER_IDS[profile]} is unavailable; using baseline"
        subject = options.subject if options.subject != "auto" else run.options.get("subject_hint")
        if profile == "auto" and subject is not None and PROFILE_FOR_SUBJECT.get(str(subject)) not in roles.parsers:
            return "No parser for this subject; using baseline"
        return None

    def phases(self, run: SegmentationRun) -> tuple[RunPhase[SegmentationRun, SegmentationState], ...]:
        roles = resolve_roles(self._deps.registry)
        models = roles.model_ids(PARSER_IDS)
        return (
            RunPhase("inputs", "cpu", ("prepare", "frame"), self._inputs_phase),
            RunPhase("models", "gpu", ("parse", "detect", "refine"), self._models_phase, models),
            RunPhase("fuse", "cpu", ("fuse",), self._fuse_phase),
        )

    # ------------------------------------------------------------------ prepare / finalize
    def prepare(self, run: SegmentationRun, work_dir: Path) -> SegmentationState:
        ensure_dir(work_dir)
        run_dir = self._deps.layout.segmentation_dir(run.id)
        if not (run_dir / SOURCE_FILE).is_file():
            raise AppError(
                "The segmentation source snapshot is missing", code=SOURCE_MISSING_ERROR_CODE,
                details={"run_id": run.id},
            )
        phrases = run.options.get("prompt_phrases") or {}
        return SegmentationState(
            run_dir=run_dir,
            work_dir=work_dir,
            source_kind=run.source.kind,
            options=_options_of(run),
            expected_terms=tuple(str(t) for t in run.options.get("expected_terms") or ()),
            prompt_phrases={str(k): [str(p) for p in v] for k, v in dict(phrases).items()},
            subject_hint=run.options.get("subject_hint"),
            preprocess=run.options.get("preprocess"),
            default_profile=self._deps.settings.segmentation_default_profile,
        )

    def finalize(self, run: SegmentationRun, state: SegmentationState, ctx: RunContext) -> SegmentationRun:
        if state.result is None:
            raise AppError("The segmentation produced no result", code="segmentation_failed")
        out_dir = state.out_dir
        masks_target = state.run_dir / MASKS_DIR
        safe_rmtree(masks_target)
        retry_os(lambda: os.replace(out_dir / MASKS_DIR, masks_target))
        for name in RESULT_FILES:
            source = out_dir / name
            if source.is_file():
                retry_os(lambda s=source, n=name: os.replace(s, state.run_dir / n))
        return run.model_copy(update={"result": state.result}, deep=True)

    # ------------------------------------------------------------------ phases
    def _inputs_phase(self, run: SegmentationRun, state: SegmentationState, ctx: RunContext) -> None:
        source_path = state.run_dir / SOURCE_FILE
        ensure_dir(state.out_dir)
        with ctx.step("prepare", "Preparing the image") as step:
            source = cond.load_input(source_path)
            step.details.update({"source_size": list(source.size), "real_alpha": cond.has_real_alpha(source)})
        with ctx.step("frame", "Recovering the conditioning frame") as step:
            state.inp, state.out = self._frame(state, source, source_path, step)
            step.details.update({"space": state.out.space})
            frame_doc = {
                "space": state.out.space,
                "frame": state.out.frame.to_dict() if state.out.frame is not None else None,
                "head_pass_full_resolution": state.inp.source_to_image is not None,
            }
            (state.out_dir / FRAME_FILE).write_text(json.dumps(frame_doc, indent=2), encoding="utf-8")
        roles = resolve_roles(self._deps.registry)
        state.roles = roles
        if self._deps.registry is not None:
            ctx.message("Loading models")
            load_providers(self._deps.registry, roles.model_ids(_likely_profiles(state, roles)), ctx.cancel)

    def _frame(
        self, state: SegmentationState, source: Image.Image, source_path: Path, step: Any
    ) -> tuple[SegmentationInput, OutputSpace]:
        options = state.options
        common: dict[str, Any] = {
            "options": options,
            "expected_terms": state.expected_terms,
            "prompt_phrases": {k: tuple(v) for k, v in state.prompt_phrases.items()},
            "subject_hint": state.subject_hint,
            "default_profile": state.default_profile,
        }
        analysis = state.work_dir / ANALYSIS_FILE
        if state.source_kind == "job":
            original = state.run_dir / ORIGINAL_FILE
            frame: cond.ConditioningFrame | None = None
            if original.is_file() and state.preprocess:
                frame = cond.recover_frame(original, source_path, state.preprocess, state.run_dir)
                step.details["frame"] = frame.summary()
            verified = frame is not None and frame.verified
            if options.head_pass and not verified:
                step.warn(
                    "head_pass_low_res",
                    "The job's conditioning frame could not be verified; the head pass uses the conditioning image",
                )
            inp = SegmentationInput(
                image=source_path,
                original=original if verified else None,
                source_to_image=cond.source_to_frame_matrix(frame.transform) if verified and frame else None,
                **common,
            )
            return inp, OutputSpace(space="conditioning", frame=frame, size=source.size)
        frame = cond.predict_frame(source_path, remove_background=True)
        if frame is not None and frame.transform.mode == "alpha_crop":
            conditioning, _transform = cond.conditioning_from_source(source)
            conditioning.save(analysis)
            step.details["frame"] = frame.summary()
            inp = SegmentationInput(
                image=analysis,
                original=source_path,
                source_to_image=cond.source_to_frame_matrix(frame.transform),
                **common,
            )
            return inp, OutputSpace(space="conditioning", frame=frame, size=frame.transform.size)
        step.warn(
            "frame_unverified",
            "The conditioning crop of an opaque image is decided by background removal at generation time; "
            "masks are in source space",
        )
        resized, _scale = cond.upstream_resize(source.convert("RGB"))
        resized.save(analysis)
        sx = resized.width / source.width
        sy = resized.height / source.height
        to_image = np.array([[sx, 0.0, 0.5 * sx - 0.5], [0.0, sy, 0.5 * sy - 0.5]])
        inp = SegmentationInput(image=analysis, original=source_path, source_to_image=to_image, **common)
        to_result = None if resized.size == source.size else cv2.invertAffineTransform(to_image)
        return inp, OutputSpace(space="source", frame=None, size=source.size, analysis_to_result=to_result)

    def _models_phase(self, run: SegmentationRun, state: SegmentationState, ctx: RunContext) -> None:
        assert state.inp is not None
        state.raw = run_models(state.inp, ctx, state.work_dir / RAW_DIR, _Stages(ctx, None))

    def _fuse_phase(self, run: SegmentationRun, state: SegmentationState, ctx: RunContext) -> None:
        assert state.inp is not None and state.out is not None and state.raw is not None
        state.result = fuse_raw(state.raw, state.inp, state.out, state.out_dir, _Stages(ctx, None))
        if state.raw.caption is not None:
            ctx.message(f"Subject: {state.raw.subject}")


def _likely_profiles(state: SegmentationState, roles: SegmentationRoles) -> tuple[str, ...]:
    options = state.options
    profile = options.profile if options.profile != "auto" else state.default_profile
    if profile in PARSER_IDS:
        return (profile,)
    if profile == "baseline":
        return () if roles.has_pair else tuple(roles.parsers)[:1]
    subject = options.subject if options.subject != "auto" else state.subject_hint
    if subject is not None:
        mapped = PROFILE_FOR_SUBJECT.get(subject)
        return (mapped,) if mapped in PARSER_IDS else ()
    return tuple(roles.parsers)


def _options_of(run: SegmentationRun) -> SegmentationOptions:
    return SegmentationOptions.model_validate(run.options.get("options") or {})
