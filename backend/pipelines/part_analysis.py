"""Part analysis runs (``pa_``): decomposition after reconstruction (part-aware DESIGN.md section 4.6).

Steps and phases (section 4.6.4)::

    prepare           4  cpu  read_base_glb + sha256, weld, adjacency, components, shells, base colour per face;
                              semantic: opposite-sheet partners, segmentation snapshot, conditioning frame, providers
                              loaded into CPU RAM
    segmentation_2d  12  gpu  internal pass on the job's preview.png (section 4.6.3), skipped when a qualifying
                              segmentation is reused
    camera_fit        8  gpu  silhouette grid fit of the lifter (front/back near-ties are logged)
    label_lift       22  gpu  face-ID votes: input view (weight 1.0) + ``views`` textured extra views (segmented with the
                              same profile, weight 0.5)
    assign_labels     8  cpu  parts.labels.assign_labels (steps 5.1-5.8)
    partition        16  cpu  parts.policy.plan_parts (section 2.4, partner ownership)
    export           22  cpu  parts.export.export_part_layer into ``runtime/temp/parts/<pa>/out/`` (A4 check, validate)
    previews          8  gpu  front/back/left/right label-colour renders (lifter usable and export.previews only)

Phases ``cpu[prepare]`` -> ``gpu[segmentation_2d, camera_fit, label_lift]`` -> ``cpu[assign_labels, partition,
export]`` -> ``gpu[previews]``; the ``components`` strategy has no lifting phase. ``finalize`` re-validates the staging
directory, publishes it atomically to ``jobs/<job>/parts/<pa>/asset/`` (``parts.publish.validate_and_publish``) and
builds the result. The job's ``model.glb``, ``preview.png`` and ``job.json`` are only read (I2).

Error codes: ``UnsupportedBaseMeshError`` -> ``unsupported_base_mesh``, ``BaseMeshChangedError`` / missing inputs ->
``source_missing``, ``ReassemblyError`` -> ``partition_failed``, lifter failures -> ``camera_fit_failed`` /
``lifting_failed``, ``StagingInvalidError``/``PublishError`` -> ``export_failed``, ``PublishConflictError`` ->
``publish_conflict``, ``JobDeletedError`` -> ``job_deleted``. Torch-free: the lifter and segmentation models are
reached through the registry only.
"""

from __future__ import annotations

import json
import logging
import math
import shutil
import time
import traceback
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, cast

import cv2
import numpy as np
from PIL import Image
from scipy import ndimage

from core.exceptions import AppError
from core.feature_errors import JobDeletedError
from inference.parts.base import CameraFit, CameraGrid, CameraSpec, LabelLifter, MeshArrays, MeshHandle
from models.enums import JobStatus
from models.job import Job, MeshStats
from models.part_analysis import (
    PREVIEW_VIEWS,
    PartAnalysisResult,
    PartAnalysisRun,
    PartRefinementSummary,
    PartSummary,
)
from models.runs import RUN_ERROR_CODE, SOURCE_MISSING_ERROR_CODE, WARNING_CODES, StepDef
from models.segmentation import (
    FRAME_FILE,
    LABELS_FILE,
    OVERLAY_FILE,
    RESULT_FILE,
    SOURCE_FILE,
    SegmentationResult,
    SegmentationRun,
)
from parts.components import MeshTopology, build_topology
from parts.export import ExportOptions, ExportResult, LabelSource, export_part_layer, write_manifest
from parts.frames import ViewCameraSpec, extra_view_specs, extra_views, input_view
from parts.gltf_io import (
    BaseMesh,
    BaseMeshChangedError,
    UnsupportedBaseMeshError,
    base_color_texture,
    face_base_color_lab,
    read_base_glb,
)
from parts.labels import LabelAssignment, VoteTable, assign_labels, unlabelled_assignment
from parts.lineage import find_earlier_analyses
from parts.manifest import (
    ConditioningFrameInfo,
    GeneratorInfo,
    Guide2D,
    ImageOrigin,
    ManifestProvenance,
    ManifestWarning,
    ModelRef,
    PartsManifest,
    PromptOrigin,
    ProvenanceStep,
    SegmentationArtifacts,
    StrategyInfo,
    StrategySegmentation,
)
from parts.policy import PartPlan, effective_min_part_faces, plan_parts
from parts.publish import validate_and_publish
from parts.reassembly import ReassemblyError
from parts.shells import ShellReport, classify_shells, global_shell_eps, pair_opposite_faces
from parts.taxonomy import TAXONOMY
from pipelines.runs.context import model_use
from pipelines.runs.executor import ExecutorDeps, RunPhase
from pipelines.segmentation import (
    PARSER_IDS,
    PROFILE_FOR_SUBJECT,
    load_providers,
    model_usable,
    prompt_term_phrases,
    resolve_roles,
    segment_image,
)
from processing import conditioning as cond
from processing.seg2d.fusion import label_color
from schemas.parts import PartAnalysisOptions
from schemas.segmentation import SegmentationOptions
from storage.run_layout import MASKS_DIR
from utils.files import ensure_dir, safe_rmtree
from utils.timeutil import isoformat_z

if TYPE_CHECKING:  # pragma: no cover
    from pipelines.runs.context import RunContext, StepHandle

logger = logging.getLogger(__name__)

LIFTER_ID: Final = "raster_lifter"
SEMANTIC_STRATEGIES: Final = frozenset({"semantic_regions", "semantic_parts"})
STAGING_DIR: Final = "out"
SEG_DIR: Final = "segmentation"
PREVIEWS_DIR: Final = "previews"
INPUT_DIR: Final = "inputs"
PREVIEW_COPY: Final = "conditioning.png"
ORIGINAL_COPY: Final = "original.png"
REUSED_DIR: Final = "reused_segmentation"
VIEWS_DIR: Final = "views"
EXTRA_VIEW_SIZE: Final = 1024
PREVIEW_SIZE: Final = 512
PREVIEW_FOV_DEG: Final = 30.0
DISTANCE_MARGIN: Final = 1.05
FOREGROUND_LUMA: Final = 8
FOREGROUND_CLOSE_PX: Final = 5
LOW_IOU_WARNING: Final = "camera_fit_low_iou"
RERUN_WARNING: Final = "segmentation_rerun_for_alignment"
PARTIAL_WARNINGS: Final = frozenset({LOW_IOU_WARNING, "profile_degraded", "frame_unverified"})
"""Warnings that make the result ``partial`` (fit rejected, segmentation degraded, frame unverified)."""

PREVIEW_YAWS: Final[Mapping[str, float]] = {"front": 0.0, "back": 180.0, "left": 90.0, "right": 270.0}
"""Asset frame (+Z front): the character's left side faces +X, i.e. the camera at yaw 90."""


def camera_grid() -> CameraGrid:
    """Section 4.6.5 step 3: yaw 0-350 in 10 degree steps x pitch -20..40 in 10 degree steps x FOV {10, 30, 50}."""
    return CameraGrid(
        yaws=tuple(float(y) for y in range(0, 360, 10)),
        pitches=tuple(float(p) for p in range(-20, 41, 10)),
        fovs=(10.0, 30.0, 50.0),
    )


def bounding_radius(positions: np.ndarray) -> float:
    """Bounding-sphere radius around the AABB centre of the base positions."""
    pts = np.asarray(positions, dtype=np.float64)
    if pts.size == 0:
        return 1.0
    center = (pts.min(axis=0) + pts.max(axis=0)) / 2.0
    return float(max(np.sqrt(((pts - center) ** 2).sum(axis=1)).max(), 1e-6))


def camera_distance(fov_deg: float, radius: float) -> float:
    return DISTANCE_MARGIN * radius / math.sin(math.radians(fov_deg) / 2.0)


# --------------------------------------------------------------------------------------------------
# Effective options and strategy resolution (shared with the service)
# --------------------------------------------------------------------------------------------------


def options_of(run: PartAnalysisRun) -> PartAnalysisOptions:
    """The effective request stored in ``run.options`` (extra bookkeeping keys are ignored)."""
    fields = PartAnalysisOptions.model_fields
    return PartAnalysisOptions.model_validate({k: v for k, v in run.options.items() if k in fields})


def semantic_usable(registry: Any) -> tuple[bool, list[dict[str, Any]]]:
    """Semantic strategies need the baseline detector + refiner pair and a usable lifter (as ``/features``)."""
    if registry is None:
        return False, [{"model_id": None, "reason_code": "not_implemented", "reason": "No model registry"}]
    roles = resolve_roles(registry)
    reasons = [issue.to_reason() for issue in roles.issues if issue.role in ("detector", "refiner")]
    lifter_ok = model_usable(registry, LIFTER_ID)
    if not lifter_ok:
        status = None
        try:
            status = registry.status(LIFTER_ID)
        except Exception:  # noqa: BLE001
            status = None
        reasons.append(
            {
                "model_id": LIFTER_ID,
                "state": getattr(status, "state", "unavailable"),
                "reason_code": getattr(status, "reason_code", "error"),
                "reason": getattr(status, "reason", None),
            }
        )
    return roles.has_pair and lifter_ok, reasons


def lifter_usable(registry: Any) -> bool:
    return registry is not None and model_usable(registry, LIFTER_ID)


# --------------------------------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------------------------------


@dataclass(slots=True)
class _WarnTap:
    """Forwards to the run context and remembers warning codes / models (segment_image reports through it)."""

    ctx: Any
    codes: set[str] = field(default_factory=set)
    seen: set[tuple[str, str]] = field(default_factory=set)
    models: list[str] = field(default_factory=list)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.ctx, name)

    def warn(self, code: str, message: str, step_id: str | None = None) -> None:
        self.codes.add(code)
        if (code, message) in self.seen:
            return
        self.seen.add((code, message))
        self.ctx.warn(code, message, step_id)

    def use_model(self, model_id: str) -> Any:
        if model_id not in self.models:
            self.models.append(model_id)
        return self.ctx.use_model(model_id)


@dataclass(slots=True, eq=False)
class PartAnalysisState:
    work_dir: Path
    job: Job
    job_dir: Path
    model_path: Path
    preview_path: Path
    original_path: Path | None
    options: PartAnalysisOptions
    strategy: str
    seg_source: str
    """``internal`` | ``reused`` | ``none``."""
    seg_options: SegmentationOptions
    expected_terms: tuple[str, ...]
    prompt_phrases: dict[str, list[str]]
    subject_hint: str | None
    origin: ImageOrigin | PromptOrigin | None
    previews_enabled: bool
    started: float = field(default_factory=time.perf_counter)
    timings: dict[str, float] = field(default_factory=dict)
    warnings: list[ManifestWarning] = field(default_factory=list)
    warning_codes: set[str] = field(default_factory=set)
    models: list[str] = field(default_factory=list)
    step_models: dict[str, list[str]] = field(default_factory=dict)
    base: BaseMesh | None = None
    topology: MeshTopology | None = None
    shells: ShellReport | None = None
    partners: np.ndarray | None = None
    face_lab: np.ndarray | None = None
    shell_eps: float | None = None
    effective_resolution: int = 0
    min_part_faces: int = 0
    radius: float = 1.0
    conditioning_rgb: np.ndarray | None = None
    frame: cond.ConditioningFrame | None = None
    seg_dir: Path | None = None
    seg_result: SegmentationResult | None = None
    label_map: np.ndarray | None = None
    fit: CameraFit | None = None
    fit_details: dict[str, Any] = field(default_factory=dict)
    view_specs: list[ViewCameraSpec] = field(default_factory=list)
    votes: VoteTable | None = None
    input_face_id: np.ndarray | None = None
    input_depth: np.ndarray | None = None
    assignment: LabelAssignment | None = None
    plan: PartPlan | None = None
    export: ExportResult | None = None
    previews: list[str] = field(default_factory=list)

    @property
    def staging(self) -> Path:
        return self.work_dir / STAGING_DIR

    @property
    def semantic(self) -> bool:
        return self.strategy in SEMANTIC_STRATEGIES

    def warn(self, ctx: Any, code: str, message: str, step_id: str | None = None) -> None:
        self.warning_codes.add(code)
        if all(w.code != code or w.message != message for w in self.warnings):
            self.warnings.append(ManifestWarning(code=code, message=message))
        ctx.warn(code, message, step_id)

    def log(self, ctx: Any, text: str) -> None:
        try:
            with open(ctx.log_path, "a", encoding="utf-8") as fh:
                fh.write(f"{time.strftime('%H:%M:%S')} {text}\n")
        except OSError:
            logger.debug("Could not write the run log", exc_info=True)


# --------------------------------------------------------------------------------------------------
# Executor
# --------------------------------------------------------------------------------------------------


class PartAnalysisExecutor:
    """``RunExecutor`` of ``pa_`` runs (built by the container as ``PartAnalysisExecutor(deps)``)."""

    kind = "part_analysis"
    prefix = "pa"

    def __init__(self, deps: ExecutorDeps) -> None:
        self._deps = deps

    # ------------------------------------------------------------------ plan
    def plan(self, run: PartAnalysisRun) -> tuple[StepDef, ...]:
        options = options_of(run)
        semantic = options.strategy in SEMANTIC_STRATEGIES
        reused = run.options.get("segmentation_source") == "reused"
        geometry_only = None if semantic else "Not used by the components strategy"
        seg_skip = geometry_only or (f"Reusing segmentation {options.segmentation_id}" if reused else None)
        return (
            StepDef("prepare", "Preparing inputs", 4, "cpu"),
            StepDef("segmentation_2d", "Segmenting image", 12, "gpu", seg_skip),
            StepDef("camera_fit", "Aligning camera", 8, "gpu", geometry_only),
            StepDef("label_lift", "Lifting labels to 3D", 22, "gpu", geometry_only),
            StepDef("assign_labels", "Assigning labels", 8, "cpu", geometry_only),
            StepDef("partition", "Separating parts", 16, "cpu"),
            StepDef("export", "Exporting parts", 22, "cpu"),
            StepDef("previews", "Rendering previews", 8, "gpu", self._previews_skip(options)),
        )

    def _previews_skip(self, options: PartAnalysisOptions) -> str | None:
        if not options.export.previews:
            return "Previews are off (export.previews = false)"
        if not lifter_usable(self._deps.registry):
            return "The label lifter is not usable"
        return None

    def phases(self, run: PartAnalysisRun) -> tuple[RunPhase[PartAnalysisRun, PartAnalysisState], ...]:
        options = options_of(run)
        phases: list[RunPhase[PartAnalysisRun, PartAnalysisState]] = [
            RunPhase("prepare", "cpu", ("prepare",), self._prepare_phase)
        ]
        if options.strategy in SEMANTIC_STRATEGIES:
            phases.append(
                RunPhase(
                    "lift",
                    "gpu",
                    ("segmentation_2d", "camera_fit", "label_lift"),
                    self._lift_phase,
                    self._lift_models(run, options),
                )
            )
            phases.append(RunPhase("parts", "cpu", ("assign_labels", "partition", "export"), self._parts_phase))
        else:
            phases.append(RunPhase("parts", "cpu", ("partition", "export"), self._parts_phase))
        if self._previews_skip(options) is None:
            phases.append(RunPhase("previews", "gpu", ("previews",), self._previews_phase, (LIFTER_ID,)))
        return tuple(phases)

    def _lift_models(self, run: PartAnalysisRun, options: PartAnalysisOptions) -> tuple[str, ...]:
        registry = self._deps.registry
        if registry is None:
            return (LIFTER_ID,)
        roles = resolve_roles(registry)
        seg = options.segmentation or SegmentationOptions()
        profiles = _likely_profiles(seg, run.options.get("subject_hint"), self._deps.settings.segmentation_default_profile)
        return (*roles.model_ids(profiles), LIFTER_ID)

    # ------------------------------------------------------------------ prepare / finalize
    def prepare(self, run: PartAnalysisRun, work_dir: Path) -> PartAnalysisState:
        ensure_dir(work_dir)
        deps = self._deps
        job = deps.job_store.get(run.job_id)
        if job is None or not deps.job_store.paths.job_metadata(run.job_id).is_file():
            raise JobDeletedError(job_id=run.job_id)
        if job.status is not JobStatus.COMPLETED:
            raise AppError("The job is no longer completed", code=SOURCE_MISSING_ERROR_CODE, details={"job_id": job.id})
        paths = deps.job_store.paths
        model_path = paths.job_model(job.id)
        preview = paths.job_preview(job.id)
        if not model_path.is_file():
            raise AppError("The job's model.glb is missing", code=SOURCE_MISSING_ERROR_CODE, details={"job_id": job.id})
        options = options_of(run)
        strategy = str(options.strategy or "components")
        semantic = strategy in SEMANTIC_STRATEGIES
        inputs = ensure_dir(work_dir / INPUT_DIR)
        preview_copy = inputs / PREVIEW_COPY
        original_copy: Path | None = None
        if preview.is_file():
            shutil.copyfile(preview, preview_copy)
        elif semantic:
            raise AppError("The job has no preview.png", code=SOURCE_MISSING_ERROR_CODE, details={"job_id": job.id})
        primary = paths.job_inputs(job.id) / f"{job.primary_image_id}.png"
        if primary.is_file():
            original_copy = inputs / ORIGINAL_COPY
            shutil.copyfile(primary, original_copy)
        provenance = self._provenance(job)
        state = PartAnalysisState(
            work_dir=work_dir,
            job=job,
            job_dir=paths.job_dir(job.id),
            model_path=model_path,
            preview_path=preview_copy,
            original_path=original_copy,
            options=options,
            strategy=strategy,
            seg_source=str(run.options.get("segmentation_source") or ("internal" if semantic else "none")),
            seg_options=options.segmentation or SegmentationOptions(),
            expected_terms=provenance.expected_terms,
            prompt_phrases=provenance.prompt_phrases,
            subject_hint=provenance.subject_hint,
            origin=provenance.origin,
            previews_enabled=self._previews_skip(options) is None,
        )
        if semantic and state.seg_source == "reused":
            self._snapshot_segmentation(options.segmentation_id, state)
        return state

    def _provenance(self, job: Job) -> _Provenance:
        """Origin of the job's primary image; for a prompt image also its expected terms and style hint."""
        try:
            ref_set = self._deps.reference_store.get(job.reference_set_id)
        except Exception:  # noqa: BLE001 - provenance never fails a run
            ref_set = None
        image = ref_set.get_image(job.primary_image_id) if ref_set is not None else None
        if image is None:
            return _Provenance(origin=None)
        source = getattr(image, "source", None)
        if source is None or getattr(source, "kind", None) != "t2i":
            return _Provenance(origin=ImageOrigin())
        origin = PromptOrigin(
            image_generation_id=source.image_generation_id, model_id=source.model_id,
            model_revision=source.model_revision, license_id=source.license_id, seed=source.seed,
            final_prompt_sha256=source.final_prompt_sha256,
        )
        store = self._deps.stores.get("image_generation")
        ig = store.get(source.image_generation_id) if store is not None else None
        if ig is None:
            return _Provenance(origin=origin)
        expected = tuple(str(t) for t in ig.options.get("expected_terms") or ())
        return _Provenance(
            origin=origin,
            expected_terms=expected,
            prompt_phrases=prompt_term_phrases(str(ig.options.get("prompt") or ""), expected),
            subject_hint="anime_character" if ig.options.get("style") == "anime" else None,
        )

    def _snapshot_segmentation(self, sg_id: str | None, state: PartAnalysisState) -> None:
        store = self._deps.stores.get("segmentation")
        sg = store.get(sg_id) if store is not None and sg_id else None
        if not isinstance(sg, SegmentationRun) or sg.result is None or sg.status is not JobStatus.COMPLETED:
            raise AppError(
                "The reused segmentation is gone", code=SOURCE_MISSING_ERROR_CODE, details={"segmentation_id": sg_id}
            )
        source = store.run_dir(sg)
        target = ensure_dir(state.work_dir / REUSED_DIR)
        for name in (LABELS_FILE, OVERLAY_FILE, FRAME_FILE, RESULT_FILE, SOURCE_FILE):
            if (source / name).is_file():
                shutil.copyfile(source / name, target / name)
        if (source / MASKS_DIR).is_dir():
            shutil.copytree(source / MASKS_DIR, target / MASKS_DIR, dirs_exist_ok=True)
        if not (target / LABELS_FILE).is_file():
            raise AppError(
                "The reused segmentation has no labels.png", code=SOURCE_MISSING_ERROR_CODE,
                details={"segmentation_id": sg_id},
            )
        state.seg_dir = target
        state.seg_result = sg.result

    def finalize(self, run: PartAnalysisRun, state: PartAnalysisState, ctx: RunContext) -> PartAnalysisRun:
        if state.export is None or state.base is None:
            raise AppError("The part analysis produced no part layer", code="export_failed")
        manifest = state.export.manifest
        if state.previews:
            artifacts = manifest.artifacts.model_copy(update={"previews": [f"{PREVIEWS_DIR}/{v}.png" for v in state.previews]})
            manifest = manifest.model_copy(update={"artifacts": artifacts})
            write_manifest(state.staging, manifest)
        target = self._deps.layout.part_analysis_asset_dir(run.job_id, run.id)
        store = self._deps.stores["part_analysis"]
        self._require_job(state)
        try:
            validate_and_publish(
                state.staging, target, manifest, state.model_path, lambda: store.job_exists(run.job_id)
            )
        except AppError:
            self._require_job(state)
            raise
        state.timings["total"] = round(time.perf_counter() - state.started, 3)
        state.log(ctx, f"published {target.name} ({len(manifest.parts)} parts, {state.timings['total']:.1f} s)")
        result = _result(state, manifest, target)
        return run.model_copy(update={"result": result}, deep=True)

    def _require_job(self, state: PartAnalysisState) -> None:
        """``job_deleted`` when retention removed the job folder underneath the run (never recreated)."""
        if not self._deps.job_store.paths.job_metadata(state.job.id).is_file():
            raise JobDeletedError(job_id=state.job.id)

    # ------------------------------------------------------------------ phases
    @contextmanager
    def _step(self, state: PartAnalysisState, ctx: RunContext, step_id: str, message: str) -> Iterator[StepHandle]:
        started = time.perf_counter()
        state.log(ctx, f"[{step_id}] start")
        try:
            with ctx.step(step_id, message) as handle:
                yield handle
        except BaseException as exc:
            state.log(ctx, f"[{step_id}] failed: {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
            raise
        finally:
            state.timings[step_id] = round(time.perf_counter() - started, 3)
        state.log(ctx, f"[{step_id}] done in {state.timings[step_id]:.2f} s")

    def _prepare_phase(self, run: PartAnalysisRun, state: PartAnalysisState, ctx: RunContext) -> None:
        with self._step(state, ctx, "prepare", "Reading the generated mesh") as step:
            if run.options.get("segmentation_rerun"):
                state.warn(
                    ctx, RERUN_WARNING,
                    f"Segmentation {state.options.segmentation_id} is not pixel-aligned with this job's conditioning "
                    "image; the internal pass was used for lifting", "prepare",
                )
            try:
                base = read_base_glb(state.model_path)
            except UnsupportedBaseMeshError as exc:
                raise AppError(str(exc), code="unsupported_base_mesh", status_code=422) from exc
            except (FileNotFoundError, BaseMeshChangedError) as exc:
                raise AppError(str(exc), code=SOURCE_MISSING_ERROR_CODE) from exc
            step.progress(0.2, "Building the mesh topology")
            result = state.job.result
            effective = int(result.effective_resolution) if result is not None else 1024
            state.effective_resolution = max(effective, 1)
            topology = build_topology(base.positions, base.faces)
            ctx.cancel.raise_if_cancelled()
            step.progress(0.5, "Classifying shells")
            shells = classify_shells(topology, state.effective_resolution)
            state.base, state.topology, state.shells = base, topology, shells
            state.shell_eps = global_shell_eps(topology, state.effective_resolution)
            state.min_part_faces = effective_min_part_faces(state.options.min_part_faces, topology.face_count)
            state.radius = bounding_radius(base.positions)
            details: dict[str, Any] = {
                "faces": topology.face_count,
                "vertices": base.vertex_count,
                "components": int(topology.components.face_counts.shape[0]),
                "min_part_faces": state.min_part_faces,
                "model_sha256": base.sha256,
            }
            if state.semantic:
                step.progress(0.7, "Pairing opposite sheets")
                state.face_lab = face_base_color_lab(base)
                state.partners = pair_opposite_faces(
                    topology.geometry.centroids, topology.geometry.normals, state.shell_eps
                )
                details["partner_coverage"] = round(float((state.partners >= 0).mean()), 4)
                self._prepare_conditioning(state, step)
            step.details.update(details)
        if state.semantic and self._deps.registry is not None:
            ctx.message("Loading models")
            roles = resolve_roles(self._deps.registry)
            profiles = _likely_profiles(state.seg_options, state.subject_hint, self._deps.settings.segmentation_default_profile)
            ids = list(roles.model_ids(profiles)) if state.seg_source == "internal" or state.options.views else []
            load_providers(self._deps.registry, [*ids, LIFTER_ID], ctx.cancel)

    def _prepare_conditioning(self, state: PartAnalysisState, step: StepHandle) -> None:
        with Image.open(state.preview_path) as img:
            state.conditioning_rgb = np.asarray(img.convert("RGB")).copy()
        metadata = state.job.result.metadata if state.job.result is not None else {}
        preprocess = metadata.get("preprocess")
        if state.original_path is not None and isinstance(preprocess, str):
            try:
                state.frame = cond.recover_frame(state.original_path, state.preview_path, preprocess, state.job_dir)
                step.details["frame"] = state.frame.summary()
            except Exception as exc:  # noqa: BLE001 - the frame only steers the head pass
                logger.warning("Conditioning frame recovery failed: %s", exc)
                state.frame = None

    def _lift_phase(self, run: PartAnalysisRun, state: PartAnalysisState, ctx: RunContext) -> None:
        registry = ctx.registry
        if registry is None:
            raise AppError("The model registry is not available", code=RUN_ERROR_CODE, status_code=503)
        assert state.base is not None and state.topology is not None and state.conditioning_rgb is not None
        tap = _WarnTap(ctx)
        if state.seg_source == "internal":
            with self._step(state, ctx, "segmentation_2d", "Segmenting the conditioning image") as step:
                out_dir = state.work_dir / "seg_input"
                try:
                    state.seg_result = segment_image(
                        state.preview_path,
                        state.original_path,
                        state.frame,
                        state.seg_options,
                        cast("RunContext", tap),
                        out_dir,
                        step=step,
                        expected_terms=state.expected_terms,
                        prompt_phrases=state.prompt_phrases,
                        subject_hint=state.subject_hint,
                        default_profile=self._deps.settings.segmentation_default_profile,
                    )
                except AppError:
                    raise
                except Exception as exc:
                    raise AppError(f"Segmentation failed: {exc}", code="segmentation_failed") from exc
                state.seg_dir = out_dir
                step.details.update({"subject": state.seg_result.subject, "profile": state.seg_result.profile,
                                     "labels": len(state.seg_result.labels)})
            state.step_models["segmentation_2d"] = list(tap.models)
        assert state.seg_result is not None and state.seg_dir is not None
        state.label_map = _load_label_map(state.seg_dir / LABELS_FILE, state.conditioning_rgb.shape[:2])
        lifter = cast(LabelLifter, registry.provider(LIFTER_ID))  # type: ignore[attr-defined]
        ctx.use_model(LIFTER_ID)
        handle = _upload(lifter, state.base)
        try:
            with self._step(state, ctx, "camera_fit", "Aligning the camera to the silhouette") as step:
                foreground = conditioning_foreground(
                    state.conditioning_rgb, state.label_map, _preprocess_of(state.job)
                )
                try:
                    state.fit = lifter.fit_camera(handle, foreground, camera_grid(), ctx.cancel)
                except AppError:
                    raise
                except Exception as exc:
                    if ctx.cancel.is_cancelled:
                        raise
                    raise AppError(f"Camera fit failed: {exc}", code="camera_fit_failed") from exc
                state.fit_details = _fit_details(state.fit, getattr(lifter, "last_fit", None))
                step.details.update(state.fit_details)
                if not state.fit.accepted:
                    state.warn(ctx, LOW_IOU_WARNING,
                               f"The camera fit IoU {state.fit.iou:.3f} is below the threshold; using the front view",
                               "camera_fit")
                step.message = f"IoU {state.fit.iou:.3f}"
            with self._step(state, ctx, "label_lift", "Lifting labels to 3D") as step:
                try:
                    self._lift(state, ctx, tap, lifter, handle, step)
                except AppError:
                    raise
                except Exception as exc:
                    if ctx.cancel.is_cancelled:
                        raise
                    raise AppError(f"Label lifting failed: {exc}", code="lifting_failed") from exc
            state.step_models["label_lift"] = list(dict.fromkeys([LIFTER_ID, *tap.models]))
        finally:
            _release(lifter, handle)
        state.warning_codes |= tap.codes
        for code, message in sorted(tap.seen):
            if all(w.code != code or w.message != message for w in state.warnings):
                state.warnings.append(ManifestWarning(code=code, message=message))

    def _lift(
        self,
        state: PartAnalysisState,
        ctx: RunContext,
        tap: _WarnTap,
        lifter: LabelLifter,
        handle: MeshHandle,
        step: StepHandle,
    ) -> None:
        assert state.fit is not None and state.label_map is not None and state.seg_result is not None
        assert state.topology is not None
        fit = state.fit
        votes = VoteTable(state.topology.face_count)
        height, width = state.label_map.shape
        camera = CameraSpec(fit.yaw_deg, fit.pitch_deg, fit.fov_deg, fit.distance)
        raster = lifter.raster(handle, camera, (width, height), fit.crop)
        confidence = _confidence_map(state.label_map, state.seg_result)
        votes.add_view(raster.face_id, raster.cos, state.label_map, weight=1.0, confidence=confidence)
        state.input_face_id, state.input_depth = raster.face_id, raster.depth
        per_view: list[dict[str, Any]] = [
            {"id": "input", "pixels": int((state.label_map > 0).sum()), "voted": int(votes.pixels)}
        ]
        fit_spec = ViewCameraSpec(fit.yaw_deg, fit.pitch_deg, fit.fov_deg, fit.distance)
        state.view_specs = extra_view_specs(fit_spec, state.options.views)
        views_dir = ensure_dir(state.work_dir / VIEWS_DIR)
        subject = state.seg_result.subject
        total = max(len(state.view_specs), 1)
        for index, spec in enumerate(state.view_specs, start=1):
            ctx.cancel.raise_if_cancelled()
            step.progress((index - 1) / total, f"View {index} of {len(state.view_specs)}")
            view_camera = CameraSpec(spec.yaw_deg, spec.pitch_deg, spec.fov_deg, spec.distance)
            size = (EXTRA_VIEW_SIZE, EXTRA_VIEW_SIZE)
            image = lifter.render_textured(handle, view_camera, size)
            image_path = views_dir / f"view_{index}.png"
            Image.fromarray(image).save(image_path)
            out_dir = views_dir / f"view_{index}"
            before = votes.pixels
            result = segment_image(
                image_path, None, None, state.seg_options, cast("RunContext", tap), out_dir,
                step=cast("StepHandle", _SubStep(step, (index - 1) / total, index / total)),
                expected_terms=state.expected_terms,
                prompt_phrases=state.prompt_phrases,
                subject_hint=subject,
                default_profile=self._deps.settings.segmentation_default_profile,
            )
            labels = _load_label_map(out_dir / LABELS_FILE, (EXTRA_VIEW_SIZE, EXTRA_VIEW_SIZE))
            view_raster = lifter.raster(handle, view_camera, size, None)
            votes.add_view(view_raster.face_id, view_raster.cos, labels, weight=0.5,
                           confidence=_confidence_map(labels, result))
            per_view.append({"id": f"view_{index}", "yaw": round(spec.yaw_deg, 1), "pitch": spec.pitch_deg,
                             "pixels": int((labels > 0).sum()), "voted": int(votes.pixels - before),
                             "labels": len(result.labels)})
            safe_rmtree(out_dir / MASKS_DIR)
        state.votes = votes
        step.details.update({"views": per_view, "vote_pixels": int(votes.pixels)})
        step.message = f"{1 + len(state.view_specs)} views"

    def _parts_phase(self, run: PartAnalysisRun, state: PartAnalysisState, ctx: RunContext) -> None:
        assert state.base is not None and state.topology is not None and state.shells is not None
        topology = state.topology
        if state.semantic:
            assert state.votes is not None and state.fit is not None
            with self._step(state, ctx, "assign_labels", "Assigning labels") as step:
                state.assignment = assign_labels(
                    state.votes.summary(),
                    topology,
                    partners=cast(np.ndarray, state.partners),
                    shells=state.shells,
                    effective_resolution=state.effective_resolution,
                    fit_accepted=state.fit.accepted,
                    face_lab=state.face_lab,
                    shell_eps=state.shell_eps,
                )
                step.details.update({k: round(float(v), 4) for k, v in state.assignment.stats.items()})
                step.details["unlabelled_face_ratio"] = round(state.assignment.unlabelled_face_ratio, 4)
        else:
            state.assignment = unlabelled_assignment(topology.face_count)
        assignment = state.assignment
        with self._step(state, ctx, "partition", "Separating parts") as step:
            state.plan = plan_parts(
                topology,
                state.shells,
                assignment.labels,
                strategy=state.strategy,  # type: ignore[arg-type]
                separate=state.options.separate,
                min_part_faces=state.min_part_faces,
                max_parts=state.options.max_parts,
                split_shells=state.options.split_shells,
                face_flags=assignment.flags,
                donor=assignment.donor,
                partners=state.partners,
            )
            step.details.update({"parts": len(state.plan.parts), "nodes": state.plan.node_count})
        with self._step(state, ctx, "export", "Exporting parts") as step:
            self._require_job(state)
            if state.semantic:
                self._write_segmentation_artifacts(state)
            options = self._export_options(run, state, ctx)
            try:
                state.export = export_part_layer(
                    state.staging, state.base, topology, state.shells, assignment, state.plan, options,
                    partners=state.partners,
                )
            except AppError:
                self._require_job(state)
                raise
            except ReassemblyError as exc:
                raise AppError(f"Reassembly check failed: {exc}", code="partition_failed") from exc
            except ValueError as exc:
                raise AppError(f"The partition could not be exported: {exc}", code="partition_failed") from exc
            manifest = state.export.manifest
            for warning in manifest.warnings:
                if warning.code in WARNING_CODES and warning.code not in state.warning_codes:
                    state.warning_codes.add(warning.code)
                    ctx.warn(warning.code, warning.message, "export")
            step.details.update({k: round(v, 3) for k, v in state.export.timings.items()})
            step.details["summary"] = manifest.summary.model_dump(mode="json")

    def _write_segmentation_artifacts(self, state: PartAnalysisState) -> None:
        assert state.seg_dir is not None and state.seg_result is not None
        target = ensure_dir(state.staging / SEG_DIR)
        shutil.copyfile(state.preview_path, target / SOURCE_FILE)
        for name in (LABELS_FILE, OVERLAY_FILE):
            source = state.seg_dir / name
            if source.is_file():
                shutil.copyfile(source, target / name)
        frame_doc = {
            "space": "conditioning",
            "source": state.seg_source,
            "frame": state.frame.to_dict() if state.frame is not None else None,
        }
        (target / FRAME_FILE).write_text(json.dumps(frame_doc, indent=2), encoding="utf-8")
        (target / RESULT_FILE).write_text(
            json.dumps(state.seg_result.model_dump(mode="json"), indent=2), encoding="utf-8"
        )

    def _export_options(self, run: PartAnalysisRun, state: PartAnalysisState, ctx: RunContext) -> ExportOptions:
        assert state.base is not None
        job = state.job
        result = job.result
        metadata = result.metadata if result is not None else {}
        generator = GeneratorInfo(
            app_version=self._deps.settings.app_version,
            engine=str(metadata.get("engine") or job.engine),
            model=str(metadata.get("model") or job.engine),
            pipeline_type=metadata.get("pipeline_type"),
            seed=int(result.seed if result is not None else job.seed),
            resolution=int(result.resolution if result is not None else state.effective_resolution),
            effective_resolution=state.effective_resolution,
            texture_size=int(result.texture_size if result is not None else 0),
            polygon_target=int(result.polygon_target if result is not None else 0),
        )
        seg = state.seg_result
        strategy = StrategyInfo(
            id=state.strategy,  # type: ignore[arg-type]
            segmentation=StrategySegmentation(
                source=state.seg_source,  # type: ignore[arg-type]
                segmentation_id=state.options.segmentation_id,
                profile=seg.profile if seg is not None else None,
                subject=seg.subject if seg is not None else None,
            ),
            separate=list(state.options.separate),
            min_part_faces=state.min_part_faces,
            max_parts=state.options.max_parts,
            split_shells=state.options.split_shells,
            views=state.options.views if state.semantic else 0,
            models=[_model_ref(ctx, model_id) for model_id in _all_models(state)],
        )
        views = []
        if state.semantic and state.fit is not None:
            fit = state.fit
            views = [
                input_view(
                    ViewCameraSpec(fit.yaw_deg, fit.pitch_deg, fit.fov_deg, fit.distance),
                    method=fit.method, iou=fit.iou, accepted=fit.accepted, candidates_tested=fit.candidates_tested,
                ),
                *extra_views(state.view_specs),
            ]
        class_masks = None
        if state.semantic and state.label_map is not None:
            class_masks = {int(v): state.label_map == v for v in np.unique(state.label_map) if int(v) > 0}
        steps = [
            ProvenanceStep(
                id=step_id,
                models=[_model_ref(ctx, m) for m in state.step_models.get(step_id, [])],
                duration_s=duration,
            )
            for step_id, duration in state.timings.items()
        ]
        refs = [_model_ref(ctx, m) for m in _all_models(state)]
        provenance = ManifestProvenance(steps=steps, licences=sorted({r.license_id for r in refs if r.license_id}))
        return ExportOptions(
            analysis_id=run.id,
            job_id=job.id,
            generator=generator,
            strategy=strategy,
            effective_resolution=state.effective_resolution,
            created_at=isoformat_z(run.created_at),
            primary_image_id=job.primary_image_id,
            origin=state.origin,
            views=views,
            conditioning=_conditioning_info(state) if state.semantic else None,
            label_source=_label_source(seg) if state.semantic else None,
            class_masks=class_masks,
            conditioning_image=state.conditioning_rgb if state.semantic else None,
            segmentation=SegmentationArtifacts(
                labels=f"{SEG_DIR}/{LABELS_FILE}", overlay=f"{SEG_DIR}/{OVERLAY_FILE}",
                frame=f"{SEG_DIR}/{FRAME_FILE}", source=f"{SEG_DIR}/{SOURCE_FILE}",
            ) if state.semantic else None,
            previews=(),
            per_part_glbs=state.options.export.per_part_glbs,
            expected_classes=[t for t in state.expected_terms if t in TAXONOMY],
            expectation_source="prompt" if state.expected_terms else None,
            provenance=provenance,
            warnings=list(state.warnings),
            annotations=_annotations(seg) if seg is not None else {},
            earlier=find_earlier_analyses(state.job_dir / "parts", model_sha256=state.base.sha256, exclude=run.id),
            input_face_id=state.input_face_id,
            input_depth=state.input_depth,
        )

    def _previews_phase(self, run: PartAnalysisRun, state: PartAnalysisState, ctx: RunContext) -> None:
        registry = ctx.registry
        assert state.base is not None and state.export is not None and state.plan is not None
        with self._step(state, ctx, "previews", "Rendering previews") as step:
            if registry is None:
                raise AppError("The model registry is not available", code=RUN_ERROR_CODE, status_code=503)
            lifter = cast(LabelLifter, registry.provider(LIFTER_ID))  # type: ignore[attr-defined]
            ctx.use_model(LIFTER_ID)
            colors = preview_face_colors(state.plan.face_labels, state.plan.face_owner)
            target = ensure_dir(state.staging / PREVIEWS_DIR)
            handle = _upload(lifter, state.base)
            try:
                distance = camera_distance(PREVIEW_FOV_DEG, state.radius)
                for index, view in enumerate(PREVIEW_VIEWS):
                    ctx.cancel.raise_if_cancelled()
                    camera = CameraSpec(PREVIEW_YAWS[view], 0.0, PREVIEW_FOV_DEG, distance)
                    image = lifter.render_labels(handle, camera, (PREVIEW_SIZE, PREVIEW_SIZE), colors)
                    Image.fromarray(image).save(target / f"{view}.png")
                    state.previews.append(view)
                    step.progress((index + 1) / len(PREVIEW_VIEWS))
            finally:
                _release(lifter, handle)
            state.step_models["previews"] = [LIFTER_ID]
            step.details["views"] = list(state.previews)


# --------------------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Provenance:
    origin: ImageOrigin | PromptOrigin | None
    expected_terms: tuple[str, ...] = ()
    prompt_phrases: dict[str, list[str]] = field(default_factory=dict)
    subject_hint: str | None = None


class _SubStep:
    """A slice ``[low, high]`` of a parent step's progress (segment_image reports its stages into it)."""

    __slots__ = ("_high", "_low", "_parent", "details", "message", "step_id")

    def __init__(self, parent: StepHandle, low: float, high: float) -> None:
        self._parent, self._low, self._high = parent, low, high
        self.step_id = parent.step_id
        self.details: dict[str, Any] = {}
        self.message: str | None = None

    def progress(self, fraction: float, message: str | None = None) -> None:
        self._parent.progress(self._low + (self._high - self._low) * max(0.0, min(1.0, fraction)), message)

    def warn(self, code: str, message: str) -> None:
        self._parent.warn(code, message)


def _likely_profiles(options: SegmentationOptions, subject_hint: Any, default_profile: str) -> tuple[str, ...]:
    profile = options.profile if options.profile != "auto" else default_profile
    if profile in PARSER_IDS:
        return (profile,)
    if profile == "baseline":
        return ()
    subject = options.subject if options.subject != "auto" else subject_hint
    if subject is not None:
        mapped = PROFILE_FOR_SUBJECT.get(str(subject))
        return (mapped,) if mapped in PARSER_IDS else ()
    return tuple(PARSER_IDS)


def _preprocess_of(job: Job) -> str | None:
    metadata = job.result.metadata if job.result is not None else {}
    value = metadata.get("preprocess")
    return value if isinstance(value, str) else None


def conditioning_foreground(rgb: np.ndarray, labels: np.ndarray, preprocess: str | None) -> np.ndarray:
    """Section 4.6.3: ``(luma > 8) | (labels > 0)`` with a 5 px close + hole fill; ``labels > 0`` for ``untouched``."""
    if preprocess == "untouched":
        mask = labels > 0
    else:
        luma = cv2.cvtColor(np.ascontiguousarray(rgb, dtype=np.uint8), cv2.COLOR_RGB2GRAY)
        mask = (luma > FOREGROUND_LUMA) | (labels > 0)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (FOREGROUND_CLOSE_PX, FOREGROUND_CLOSE_PX))
    closed = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_CLOSE, kernel) > 0
    filled = ndimage.binary_fill_holes(closed)
    if not filled.any():
        raise AppError("The conditioning image has no foreground", code="camera_fit_failed")
    return np.asarray(filled, dtype=bool)


def _load_label_map(path: Path, shape: tuple[int, int]) -> np.ndarray:
    """16-bit ``labels.png`` (taxonomy value per pixel) resized (nearest) to ``shape`` = (height, width)."""
    with Image.open(path) as img:
        labels = np.asarray(img).astype(np.uint16)
    if labels.ndim == 3:
        labels = labels[..., 0]
    if labels.shape != tuple(shape):
        labels = cv2.resize(labels, (shape[1], shape[0]), interpolation=cv2.INTER_NEAREST)
    return labels


def _confidence_map(labels: np.ndarray, result: SegmentationResult) -> np.ndarray:
    lookup = np.zeros(int(labels.max()) + 1 if labels.size else 1, dtype=np.float32)
    for label in result.labels:
        if 0 < label.value < lookup.size:
            lookup[label.value] = float(min(max(label.confidence, 0.0), 1.0))
    return lookup[labels]


def _upload(lifter: LabelLifter, base: BaseMesh) -> MeshHandle:
    atlas = base_color_texture(base) if base.uv is not None else None
    return lifter.upload(MeshArrays(positions=base.positions, faces=base.faces, uv=base.uv, atlas_rgb=atlas))


def _release(lifter: LabelLifter, handle: MeshHandle) -> None:
    try:
        lifter.release(handle)
    except Exception:  # noqa: BLE001 - release is best effort; offload follows
        logger.warning("Releasing the lifter mesh failed", exc_info=True)


def _fit_details(fit: CameraFit, diag: Any) -> dict[str, Any]:
    details: dict[str, Any] = {
        "yaw": round(fit.yaw_deg, 2), "pitch": round(fit.pitch_deg, 2), "fov": round(fit.fov_deg, 2),
        "distance": round(fit.distance, 4), "iou": round(fit.iou, 4), "accepted": fit.accepted,
        "candidates_tested": fit.candidates_tested,
    }
    if diag is not None:
        details["search"] = {
            "best": list(diag.best) if diag.best is not None else None,
            "mirror": list(diag.mirror) if diag.mirror is not None else None,
            "mirror_tie_break": bool(diag.mirror_tie_break),
            "timings": {k: round(float(v), 3) for k, v in dict(diag.timings).items()},
        }
        if diag.mirror_tie_break:
            logger.info("Camera fit front/back near-tie: best %s, mirror %s (front kept)", diag.best, diag.mirror)
    return details


def preview_face_colors(face_labels: np.ndarray, face_owner: np.ndarray) -> np.ndarray:
    """Label colour per face (the overlay palette); unlabelled faces get a colour per mesh owner."""
    labels = np.asarray(face_labels, dtype=np.int64)
    owners = np.asarray(face_owner, dtype=np.int64)
    colors = np.zeros((labels.shape[0], 3), dtype=np.uint8)
    for value in np.unique(labels):
        if value > 0:
            colors[labels == value] = label_color(int(value))
    unlabelled = labels == 0
    if unlabelled.any():
        for owner in np.unique(owners[unlabelled]):
            selection = unlabelled & (owners == owner)
            colors[selection] = (160, 160, 160) if owner == 0 else label_color(1000 + int(owner))
    return colors


def _all_models(state: PartAnalysisState) -> list[str]:
    return list(dict.fromkeys(m for models in state.step_models.values() for m in models))


def _model_ref(ctx: Any, model_id: str) -> ModelRef:
    use = model_use(getattr(ctx, "registry", None), model_id)
    return ModelRef(id=use.id, revision=use.revision, license_id=use.license_id or "unknown")


def _label_source(result: SegmentationResult | None) -> LabelSource | None:
    if result is None:
        return None
    primary = PARSER_IDS.get(result.profile) if result.profile in PARSER_IDS else "sam2_1_hiera_large"
    return LabelSource(provider=result.profile, model=primary)


def _conditioning_info(state: PartAnalysisState) -> ConditioningFrameInfo | None:
    frame = state.frame
    if frame is None:
        return None
    transform = frame.transform
    return ConditioningFrameInfo(
        file=f"{SEG_DIR}/{SOURCE_FILE}",
        width=int(transform.size[0]),
        height=int(transform.size[1]),
        source_image_id=state.job.primary_image_id,
        source_size=[int(v) for v in transform.source_size],
        scale=float(transform.scale),
        crop_box=[int(v) for v in transform.crop_box] if transform.crop_box is not None else None,
        alpha_threshold=float(transform.alpha_threshold),
        mode=transform.mode,
        method=frame.method,
        verified=bool(frame.verified),
        residual=float(frame.residual) if math.isfinite(frame.residual) else 1e9,
    )


def _annotations(result: SegmentationResult) -> dict[int, dict[str, Guide2D]]:
    out: dict[int, dict[str, Guide2D]] = {}
    for class_id, guides in result.annotations.items():
        cls = TAXONOMY.get(class_id)
        if cls is None or not isinstance(guides, Mapping):
            continue
        method = str(guides.get("method") or "heuristic")
        confidence = guides.get("confidence")
        for name, points in guides.items():
            if name in ("method", "confidence") or not isinstance(points, Sequence):
                continue
            try:
                out.setdefault(cls.value, {})[name] = Guide2D(
                    points=[[float(p[0]), float(p[1])] for p in points], method=method,
                    confidence=float(confidence) if confidence is not None else None,
                )
            except (TypeError, ValueError, IndexError):
                logger.debug("Skipping annotation %s.%s", class_id, name)
    return out


def _mesh_stats(state: PartAnalysisState, manifest: PartsManifest, assembled_path: Path) -> MeshStats:
    assert state.base is not None and state.export is not None
    base = state.base
    positions = np.asarray(base.positions, dtype=np.float64)
    bbox_min = positions.min(axis=0).tolist() if positions.size else [0.0, 0.0, 0.0]
    bbox_max = positions.max(axis=0).tolist() if positions.size else [0.0, 0.0, 0.0]
    result = state.job.result
    return MeshStats(
        vertices=int(sum(node.vmap.shape[0] for node in state.export.nodes)),
        faces=int(base.face_count),
        materials=int(base.material_count),
        textures=int(base.texture_count),
        texture_size=int(result.texture_size) if result is not None else None,
        has_uv=base.uv is not None,
        bbox_min=[round(v, 6) for v in bbox_min],
        bbox_max=[round(v, 6) for v in bbox_max],
        dimensions=[round(b - a, 6) for a, b in zip(bbox_min, bbox_max, strict=True)],
        file_size_bytes=assembled_path.stat().st_size if assembled_path.is_file() else 0,
    )


def _result(state: PartAnalysisState, manifest: PartsManifest, asset_dir: Path) -> PartAnalysisResult:
    summary = manifest.summary
    parts = []
    for record in manifest.parts:
        thumbnail = record.files.thumbnail
        parts.append(
            PartSummary(
                id=record.id,
                label=record.label,
                category=record.category,
                group=record.group,
                kind=record.kind,
                node_name=record.node_name if record.kind in ("base", "physical") else None,
                region_value=record.region.value,
                faces=int(record.stats.faces),
                confidence=record.confidence,
                side=record.side,
                has_mask=record.files.mask is not None,
                thumbnail=thumbnail.rsplit("/", 1)[-1] if thumbnail else None,
                refinement=PartRefinementSummary(
                    status=record.refinement.status,
                    locked=record.refinement.locked,
                    eligible=record.refinement.eligible,
                    needs=list(record.refinement.needs),
                ),
            )
        )
    partial = bool(state.warning_codes & PARTIAL_WARNINGS)
    timings = {k: v for k, v in state.timings.items() if k in _TIMING_KEYS}
    return PartAnalysisResult(
        status="partial" if partial else "completed",
        part_count=summary.part_count,
        physical_count=summary.physical_count,
        deferred_count=summary.deferred_count,
        region_count=summary.region_count,
        camera_fit_iou=summary.camera_fit_iou if state.semantic else None,
        unlabelled_face_ratio=summary.unlabelled_face_ratio,
        partner_filled_ratio=summary.partner_filled_ratio,
        cut_partner_consistency=summary.cut_partner_consistency,
        has_overlay=(asset_dir / SEG_DIR / OVERLAY_FILE).is_file(),
        previews=[v for v in state.previews if (asset_dir / PREVIEWS_DIR / f"{v}.png").is_file()],
        parts=parts,
        stats=_mesh_stats(state, manifest, asset_dir / manifest.artifacts.assembled_glb.file),
        timings=timings,
    )


_TIMING_KEYS: Final = frozenset(
    {"prepare", "segmentation_2d", "camera_fit", "label_lift", "assign_labels", "partition", "export", "previews",
     "total"}
)

__all__ = [
    "LIFTER_ID",
    "SEMANTIC_STRATEGIES",
    "PartAnalysisExecutor",
    "PartAnalysisState",
    "bounding_radius",
    "camera_distance",
    "camera_grid",
    "conditioning_foreground",
    "lifter_usable",
    "options_of",
    "preview_face_colors",
    "semantic_usable",
]
