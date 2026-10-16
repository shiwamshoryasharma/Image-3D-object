"""Image generation runs (``ig_``): prompt -> candidate images (part-aware DESIGN.md sections 4.4 and 6.2). Torch-free.

``create`` validates the request against the chosen text-to-image model (sizes, steps, guidance, negative prompt,
image count, ``settings.prompt_max_chars``), expands the prompt template, fixes the seed and queues the run on the
shared :class:`~workers.run_worker.RunWorker`, which executes it with
:class:`pipelines.image_generation.ImageGenerationExecutor`. ``use_candidate`` imports a finished candidate through
the regular :class:`~services.reference_service.ReferenceService` upload path (validation, thumbnail, slot rules) with
:class:`~models.reference.ImageSource` provenance.

Errors: 503 ``feature_disabled`` (flag off), ``capability_unavailable`` (no usable model or static VRAM check),
``model_unavailable`` (explicit model not usable), ``engine_unavailable`` (shutting down); 429 ``queue_full``; 422
``validation_error`` with per-field ``unsupported_by_model`` / ``conflicting_fields`` / ``string_too_long``. Logs only
ever carry the prompt length and the first 12 hex digits of its sha256.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from config.settings import Settings
from core.exceptions import ConflictError, EngineUnavailableError, NotFoundError, ValidationAppError
from core.feature_errors import CapabilityUnavailableError, ModelUnavailableError, require_feature
from inference.providers.base import ModelSpec
from models.enums import JobStatus
from models.image_generation import CandidateUse, ImageGenerationRun
from models.reference import ImageSource
from models.runs import IMAGE_GENERATION_PREFIX, RunProgress, steps_from_plan
from pipelines.image_generation import image_generation_plan
from pipelines.prompt_templates import (
    PRESET_DEFAULT_SIZES,
    build_prompt,
    expected_terms,
    prompt_log_tag,
    prompt_sha256,
)
from schemas.image_generation import ImageGenerationOut, ImageGenerationRequest, UseCandidateRequest
from schemas.jobs import MAX_SEED
from schemas.references import MAX_LABEL_LENGTH, ReferenceImageOut, ReferenceSetOut, UploadImageResponse
from services.feature_service import FeatureService
from services.reference_service import ReferenceService
from services.run_events import RunEventPublisher
from storage.run_layout import RunLayout
from storage.run_store import RunStore
from utils.ids import is_valid_id, new_id
from utils.timeutil import utcnow
from workers.run_worker import RunWorker

if TYPE_CHECKING:  # pragma: no cover
    from inference.providers.registry import ProviderRegistry

logger = logging.getLogger(__name__)

_CAPABILITY = "image_generation"
_MODEL_KIND = "text_to_image"
_SET_NAME_PROMPT_CHARS = 60
_UNKNOWN_LICENSE = "unknown"

DeleteGuard = Callable[[ImageGenerationRun], str | None]
"""Returns a reason (409) when the run must not be deleted, e.g. an active workflow still uses it."""


class ImageGenerationService:
    def __init__(
        self,
        *,
        settings: Settings,
        store: RunStore[ImageGenerationRun],
        runs: RunWorker,
        registry: ProviderRegistry | None,
        references: ReferenceService,
        events: RunEventPublisher[ImageGenerationRun],
        layout: RunLayout,
        features: FeatureService,
    ) -> None:
        self._settings = settings
        self._store = store
        self._runs = runs
        self._registry = registry
        self._references = references
        self._events = events
        self._layout = layout
        self._features = features
        self._mutation_lock = asyncio.Lock()
        self._delete_guards: list[DeleteGuard] = []

    def to_out(self, run: ImageGenerationRun) -> ImageGenerationOut:
        return ImageGenerationOut.from_run(run)

    def add_delete_guard(self, guard: DeleteGuard) -> None:
        """Register a check run before every delete (the workflow service refuses runs its active workflows use)."""
        self._delete_guards.append(guard)

    # ------------------------------------------------------------------ create
    async def create(self, request: ImageGenerationRequest, *, workflow_id: str | None = None) -> ImageGenerationRun:
        require_feature(self._settings, "image_generation")
        if self._runs.stopping:
            raise EngineUnavailableError("The server is shutting down")
        model_id, spec = self._resolve_model(request.model_id)
        options = self._effective_options(request, model_id, spec)
        now = utcnow()
        plan = image_generation_plan()
        run = ImageGenerationRun(
            id=new_id(IMAGE_GENERATION_PREFIX),
            created_at=now,
            options=options,
            steps=steps_from_plan(plan),
            progress=RunProgress(step_count=len(plan), message="Queued", updated_at=now),
            workflow_id=workflow_id,
        )
        self._runs.enqueue(run)
        logger.info(
            "Image generation %s queued: model=%s preset=%s images=%d prompt %s",
            run.id,
            model_id,
            options["preset"],
            options["num_images"],
            prompt_log_tag(options["effective_prompt"]),
        )
        return run

    def _resolve_model(self, requested: str | None) -> tuple[str, ModelSpec]:
        registry = self._registry
        if registry is None:
            raise CapabilityUnavailableError(
                "Text-to-image generation is not available in this build",
                capability=_CAPABILITY,
                reasons=[
                    {
                        "model_id": None,
                        "state": "unavailable",
                        "reason_code": "not_implemented",
                        "reason": "The model registry is not part of this build",
                    }
                ],
            )
        specs = {spec.id: spec for spec in registry.specs(_MODEL_KIND)}
        if requested is not None:
            spec = specs.get(requested)
            if spec is None:
                raise ValidationAppError(
                    f"Model {requested!r} is not a text-to-image model",
                    details=[
                        {
                            "loc": ["body", "model_id"],
                            "msg": f"Unknown text-to-image model {requested!r}",
                            "type": "unsupported_by_model",
                            "ctx": {"allowed": sorted(specs)},
                        }
                    ],
                )
            status = registry.status(requested)
            if not status.usable:
                raise ModelUnavailableError(
                    f"Model {requested} is not available: {status.reason or status.reason_code}",
                    model_id=requested,
                    state=status.state,
                    reason_code=status.reason_code,
                )
            model_id = requested
        else:
            default = registry.default_model(_MODEL_KIND)
            default_spec = specs.get(default) if default is not None else None
            if default is None or default_spec is None:
                reasons = [
                    {
                        "model_id": status.model_id,
                        "state": status.state,
                        "reason_code": status.reason_code,
                        "reason": status.reason,
                    }
                    for status in registry.statuses(_MODEL_KIND)
                ]
                raise CapabilityUnavailableError(
                    "No text-to-image model is available", capability=_CAPABILITY, reasons=reasons
                )
            model_id, spec = default, default_spec
        registry.check_vram_static(model_id)
        return model_id, spec

    def _effective_options(self, request: ImageGenerationRequest, model_id: str, spec: ModelSpec) -> dict[str, Any]:
        """The normalized request stored as ``run.options`` (422 listing every unsupported value)."""
        caps: Mapping[str, Any] = spec.capabilities or {}
        issues: list[dict[str, Any]] = []

        def issue(field: str, msg: str, *, kind: str = "unsupported_by_model", **ctx: Any) -> None:
            issues.append({"loc": ["body", field], "msg": msg, "type": kind, "ctx": ctx})

        max_chars = self._settings.prompt_max_chars
        if len(request.prompt) > max_chars:
            issue(
                "prompt",
                f"String should have at most {max_chars} characters",
                kind="string_too_long",
                max_length=max_chars,
            )
        if request.negative_prompt and not bool(caps.get("supports_negative_prompt", False)):
            issue("negative_prompt", f"Model {model_id} does not use a negative prompt", allowed=None)
        max_images = int(caps.get("max_images", request.num_images))
        if request.num_images > max_images:
            issue("num_images", f"Model {model_id} generates at most {max_images} images", max=max_images)

        sizes = [(int(size[0]), int(size[1])) for size in caps.get("sizes", ())]
        allowed_sizes = [list(size) for size in sizes]
        width, height = request.width, request.height
        if width is None and height is None:
            width, height = PRESET_DEFAULT_SIZES[request.preset]
            if sizes and (width, height) not in sizes:
                width, height = sizes[0]
        elif width is None or height is None:
            missing = "height" if height is None else "width"
            issue(missing, "width and height must be given together", kind="conflicting_fields")
        elif sizes and (width, height) not in sizes:
            issue(
                "width",
                f"Size {width}x{height} is not supported by {model_id}; allowed: {allowed_sizes}",
                allowed=allowed_sizes,
            )

        steps = _bounded(request.steps, caps.get("steps"), "steps", model_id, issue)
        guidance = _bounded(request.guidance, caps.get("guidance"), "guidance", model_id, issue)
        if issues:
            raise ValidationAppError(f"The request is not supported by model {model_id}", details=issues)

        seed = secrets.randbelow(MAX_SEED + 1) if request.randomize_seed else request.seed
        effective = build_prompt(request.prompt, request.preset, request.style, request.part_hint)
        return {
            "prompt": request.prompt,
            "effective_prompt": effective,
            "negative_prompt": request.negative_prompt,
            "model_id": model_id,
            "preset": request.preset,
            "style": request.style,
            "part_hint": request.part_hint,
            "num_images": request.num_images,
            "seed": seed,
            "width": width,
            "height": height,
            "steps": int(steps) if steps is not None else None,
            "guidance": float(guidance) if guidance is not None else None,
            "expected_terms": expected_terms(request.prompt),
            "final_prompt_sha256": prompt_sha256(effective),
        }

    # ------------------------------------------------------------------ read
    def get(self, ig_id: str) -> ImageGenerationRun:
        if not is_valid_id(ig_id, IMAGE_GENERATION_PREFIX):
            raise NotFoundError("Image generation not found", details={"image_generation_id": ig_id})
        live = self._runs.live_snapshot(ig_id)
        if isinstance(live, ImageGenerationRun):
            return live
        run = self._store.get(ig_id)
        if run is None:
            raise NotFoundError("Image generation not found", details={"image_generation_id": ig_id})
        return run

    def list_runs(self, *, limit: int = 20, status: JobStatus | None = None) -> list[ImageGenerationRun]:
        runs = self._store.list_runs(status=status, limit=limit)
        running_id = self._runs.running_id
        live = self._runs.live_snapshot(running_id) if running_id is not None else None
        if not isinstance(live, ImageGenerationRun):
            return runs
        return [live if run.id == live.id and (status is None or live.status is status) else run for run in runs]

    def list_for_workflow(self, workflow_id: str) -> list[ImageGenerationRun]:
        """Runs created by one workflow, newest first."""
        return self._store.list_runs(workflow_id=workflow_id)

    # ------------------------------------------------------------------ cancel / delete
    async def cancel(self, ig_id: str) -> ImageGenerationRun:
        require_feature(self._settings, "image_generation")
        run = self.get(ig_id)
        if run.is_terminal:
            raise _terminal_conflict(run)
        cancelled = self._runs.cancel_queued(ig_id) or self._runs.cancel_running(ig_id)
        if isinstance(cancelled, ImageGenerationRun):
            return cancelled
        # It left the queue between the two calls: it is running now or has already finished.
        cancelled = self._runs.cancel_running(ig_id)
        if isinstance(cancelled, ImageGenerationRun):
            return cancelled
        current = self.get(ig_id)
        if current.is_terminal:
            raise _terminal_conflict(current)
        return current

    async def delete(self, ig_id: str) -> None:
        require_feature(self._settings, "image_generation")
        run = self.get(ig_id)
        if run.is_active:
            raise ConflictError(
                "The image generation is still active; cancel it first",
                details={"image_generation_id": ig_id, "status": str(run.status), "reason": "active"},
            )
        for guard in self._delete_guards:
            reason = guard(run)
            if reason:
                raise ConflictError(reason, details={"image_generation_id": ig_id, "reason": "in_use"})
        async with self._mutation_lock:
            await asyncio.to_thread(self._store.delete, ig_id)
        self._events.forget(ig_id)
        logger.info("Image generation %s deleted", ig_id)

    # ------------------------------------------------------------------ candidates
    def candidate_file(self, ig_id: str, index: int, *, thumbnail: bool = False) -> Path:
        run = self._completed(ig_id)
        if run.candidate(index) is None:
            raise NotFoundError("Candidate not found", details={"image_generation_id": ig_id, "index": index})
        layout = self._layout
        path = layout.candidate_thumb_path(ig_id, index) if thumbnail else layout.candidate_path(ig_id, index)
        if not path.is_file():
            raise NotFoundError("Candidate file is missing", details={"image_generation_id": ig_id, "index": index})
        return path

    def _completed(self, ig_id: str) -> ImageGenerationRun:
        run = self.get(ig_id)
        if run.status is not JobStatus.COMPLETED:
            raise ConflictError(
                "The image generation has not completed",
                details={"image_generation_id": ig_id, "status": str(run.status), "reason": "not_completed"},
            )
        return run

    async def use_candidate(self, ig_id: str, index: int, request: UseCandidateRequest) -> UploadImageResponse:
        """Import candidate ``index`` as a reference image (into a new set when ``reference_set_id`` is null)."""
        require_feature(self._settings, "image_generation")
        run = self._completed(ig_id)
        candidate = run.candidate(index)
        if candidate is None:
            raise NotFoundError("Candidate not found", details={"image_generation_id": ig_id, "index": index})
        path = self.candidate_file(ig_id, index)
        if request.reference_set_id is not None:
            self._references.get_set(request.reference_set_id)  # 404 before any work
        data = await asyncio.to_thread(path.read_bytes)
        prompt = str(run.options.get("prompt") or "")
        source = _image_source(run, index, candidate.seed)

        created_set_id: str | None = None
        set_id = request.reference_set_id
        if set_id is None:
            set_id = created_set_id = self._references.create_set(
                name="Prompt: " + prompt[:_SET_NAME_PROMPT_CHARS]
            ).id
        label = request.label if request.label is not None else ("Prompt: " + prompt)[:MAX_LABEL_LENGTH]
        try:
            image, ref_set = await self._references.upload_image(
                set_id,
                data=data,
                filename=f"{ig_id}_{index}.png",
                slot=request.slot,
                label=label,
                make_primary=request.make_primary,
                source=source,
            )
        except BaseException:
            if created_set_id is not None:
                await self._discard_set(created_set_id)
            raise
        await self._record_use(ig_id, index, CandidateUse(reference_set_id=ref_set.id, image_id=image.id))
        logger.info("Candidate %d of %s imported as image %s of set %s", index, ig_id, image.id, ref_set.id)
        return UploadImageResponse(
            image=ReferenceImageOut.from_domain(image),
            set=ReferenceSetOut.from_domain(ref_set, max_detail_images=self._settings.max_detail_images),
        )

    async def _record_use(self, ig_id: str, index: int, use: CandidateUse) -> None:
        async with self._mutation_lock:
            run = self._store.get(ig_id)
            candidate = run.candidate(index) if run is not None else None
            if run is None or candidate is None:
                logger.info("Image generation %s was deleted while candidate %d was imported", ig_id, index)
                return
            candidate.used_as.append(use)
            saved = await asyncio.to_thread(self._store.save, run)
        if saved:
            self._events.publish(run)

    async def _discard_set(self, set_id: str) -> None:
        try:
            await self._references.delete_set(set_id)
        except Exception:  # noqa: BLE001 - the original error is the one to report
            logger.warning("Could not remove the reference set %s created for a failed import", set_id, exc_info=True)


def _bounded(
    value: float | None,
    bounds: Any,
    field: str,
    model_id: str,
    issue: Callable[..., None],
) -> float | None:
    """``value`` (or the model default) checked against the model's ``{default, min, max}``."""
    limits: Mapping[str, Any] = bounds if isinstance(bounds, Mapping) else {}
    if value is None:
        default = limits.get("default")
        if default is None:
            issue(field, f"Model {model_id} needs an explicit {field} value")
        return default
    low, high = limits.get("min"), limits.get("max")
    if (low is not None and value < low) or (high is not None and value > high):
        issue(field, f"{field} must be between {low} and {high} for {model_id}", min=low, max=high)
    return value


def _terminal_conflict(run: ImageGenerationRun) -> ConflictError:
    return ConflictError(
        f"The image generation has already finished ({run.status})",
        details={"image_generation_id": run.id, "status": str(run.status), "reason": "terminal"},
    )


def _image_source(run: ImageGenerationRun, index: int, seed: int) -> ImageSource:
    """Provenance of candidate ``index`` (model revision and licence from the run's recorded model use)."""
    model_id = str(run.options.get("model_id") or "")
    use = next((m for m in run.models if m.id == model_id), None)
    digest = run.options.get("final_prompt_sha256")
    if not isinstance(digest, str) or not digest:
        digest = prompt_sha256(str(run.options.get("effective_prompt") or ""))
    return ImageSource(
        kind="t2i",
        image_generation_id=run.id,
        candidate_index=index,
        model_id=model_id,
        model_revision=use.revision if use is not None else None,
        license_id=use.license_id if use is not None and use.license_id else _UNKNOWN_LICENSE,
        seed=seed,
        final_prompt_sha256=digest,
    )
