"""Segmentation runs (``sg_``): 2D decomposition before reconstruction (part-aware DESIGN.md sections 4.5 and 6.3).

Creation snapshots the source into ``runtime/segmentations/<sg>/source.png`` so that replacing the set image or
deleting the job later never affects the run:

* a reference image goes through ``processing.image_ops.normalize_to_png`` (the same function ``JobService`` applies
  to job inputs), so ``source_pixels_sha256`` (sha256 of the decoded RGBA pixels) can later be compared with a job's
  ``inputs/<primary>.png``;
* a job (must be ``completed``, else 409) contributes its ``preview.png`` (the conditioning image, copied byte for
  byte) and its primary input as ``original.png`` for the full-resolution head pass.

``use_prompt_terms`` resolves the ``expected_terms`` of the ``ig_`` run that produced the image (through the
reference image's provenance) and the prompt words that matched them. Runs are executed by
:class:`workers.run_worker.RunWorker` with ``pipelines.segmentation:SegmentationExecutor``. Torch-free.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

import numpy as np
from PIL import Image

from config.settings import Settings
from core.exceptions import ConflictError, NotFoundError
from core.feature_errors import CapabilityUnavailableError, require_feature
from models.enums import JobStatus
from models.image_generation import ImageGenerationRun
from models.runs import SEGMENTATION_PREFIX
from models.segmentation import (
    LABELS_FILE,
    ORIGINAL_FILE,
    OVERLAY_FILE,
    SOURCE_FILE,
    SegmentationRun,
    SegmentationSource,
)
from pipelines.segmentation import prompt_term_phrases, resolve_roles
from processing.image_ops import normalize_to_png
from schemas.segmentation import (
    JobSegmentationSource,
    ReferenceImageSegmentationSource,
    SegmentationOut,
    SegmentationRequest,
)
from services.feature_service import FeatureService
from services.job_service import JobService
from services.reference_service import ReferenceService
from services.run_events import RunEventPublisher
from storage.run_layout import MASKS_DIR, RunLayout
from storage.run_store import RunStore
from utils.files import ensure_dir, safe_rmtree
from utils.ids import IMAGE_PREFIX, JOB_PREFIX, REFERENCE_SET_PREFIX, is_valid_id, new_id
from utils.timeutil import utcnow
from workers.run_worker import RunWorker

if TYPE_CHECKING:  # pragma: no cover
    from inference.providers.registry import ProviderRegistry

logger = logging.getLogger(__name__)

_CAPABILITY: Final = "segmentation"
_STATIC_FILES: Final[dict[str, str]] = {"source": SOURCE_FILE, "overlay": OVERLAY_FILE, "labels": LABELS_FILE}
_MASK_PREFIX: Final = "masks/"


class SegmentationService:
    def __init__(
        self,
        *,
        settings: Settings,
        store: RunStore[SegmentationRun],
        runs: RunWorker,
        registry: ProviderRegistry | None,
        references: ReferenceService,
        jobs: JobService,
        image_generations: RunStore[ImageGenerationRun],
        events: RunEventPublisher[SegmentationRun],
        layout: RunLayout,
        features: FeatureService,
    ) -> None:
        self._settings = settings
        self._store = store
        self._runs = runs
        self._registry = registry
        self._references = references
        self._jobs = jobs
        self._image_generations = image_generations
        self._events = events
        self._layout = layout
        self._features = features

    def to_out(self, run: SegmentationRun) -> SegmentationOut:
        return SegmentationOut.from_run(run)

    # ------------------------------------------------------------------ create
    async def create(self, request: SegmentationRequest, *, workflow_id: str | None = None) -> SegmentationRun:
        require_feature(self._settings, "segmentation")
        roles = resolve_roles(self._registry)
        if not roles.usable:
            raise CapabilityUnavailableError(
                "No segmentation model is usable (no detector + refiner pair and no parser)",
                capability=_CAPABILITY,
                reasons=roles.reasons(),
            )
        sg_id = new_id(SEGMENTATION_PREFIX)
        run_dir = self._layout.segmentation_dir(sg_id)
        options: dict[str, Any] = {
            "source": request.source.model_dump(mode="json"),
            "options": request.options.model_dump(mode="json"),
            "expected_terms": [],
            "prompt_phrases": {},
        }
        try:
            if isinstance(request.source, ReferenceImageSegmentationSource):
                source = await self._snapshot_reference(request.source, run_dir, options)
            else:
                source = await self._snapshot_job(request.source, run_dir, options)
            pixels_sha = await asyncio.to_thread(_pixels_sha256, run_dir / SOURCE_FILE)
            run = SegmentationRun(
                id=sg_id,
                created_at=utcnow(),
                options=options,
                source=source,
                source_pixels_sha256=pixels_sha,
                workflow_id=workflow_id,
            )
            self._runs.enqueue(run)
        except BaseException:
            await asyncio.to_thread(safe_rmtree, run_dir)
            raise
        logger.info("Segmentation %s queued (%s source)", sg_id, source.kind)
        return run

    async def _snapshot_reference(
        self, source: ReferenceImageSegmentationSource, run_dir: Path, options: dict[str, Any]
    ) -> SegmentationSource:
        if not is_valid_id(source.reference_set_id, REFERENCE_SET_PREFIX):
            raise NotFoundError("Reference set not found", details={"set_id": source.reference_set_id})
        if not is_valid_id(source.image_id, IMAGE_PREFIX):
            raise NotFoundError("Image not found in this reference set", details={"image_id": source.image_id})
        ref_set = self._references.get_set(source.reference_set_id)
        image = ref_set.get_image(source.image_id)
        if image is None:
            raise NotFoundError("Image not found in this reference set", details={"image_id": source.image_id})
        original = self._references.original_path(image)
        if not original.is_file():
            raise NotFoundError("The reference image file is missing", details={"image_id": source.image_id})
        ensure_dir(run_dir)
        await asyncio.to_thread(normalize_to_png, original, run_dir / SOURCE_FILE)
        if _use_prompt_terms(options):
            self._prompt_terms(image, options)
        return SegmentationSource(kind="reference_image", reference_set_id=ref_set.id, image_id=image.id)

    async def _snapshot_job(
        self, source: JobSegmentationSource, run_dir: Path, options: dict[str, Any]
    ) -> SegmentationSource:
        if not is_valid_id(source.job_id, JOB_PREFIX):
            raise NotFoundError("Job not found", details={"job_id": source.job_id})
        job = self._jobs.get_job(source.job_id)
        if job.status is not JobStatus.COMPLETED:
            raise ConflictError(
                f"The job is {job.status}; only completed jobs can be segmented",
                details={"status": str(job.status), "reason": "job_not_completed"},
            )
        paths = self._layout.paths
        preview = paths.job_preview(job.id)
        primary = paths.job_inputs(job.id) / f"{job.primary_image_id}.png"
        if not preview.is_file():
            raise ConflictError("The job has no conditioning image (preview.png)", details={"reason": "preview_missing"})

        def _copy() -> None:
            ensure_dir(run_dir)
            shutil.copyfile(preview, run_dir / SOURCE_FILE)
            if primary.is_file():
                shutil.copyfile(primary, run_dir / ORIGINAL_FILE)

        await asyncio.to_thread(_copy)
        metadata = job.result.metadata if job.result is not None else {}
        options["preprocess"] = metadata.get("preprocess")
        options["remove_background"] = bool(job.settings.get("remove_background", True))
        if _use_prompt_terms(options):
            try:
                image = self._references.get_set(job.reference_set_id).get_image(job.primary_image_id)
            except NotFoundError:
                image = None
            if image is not None:
                self._prompt_terms(image, options)
        return SegmentationSource(kind="job", job_id=job.id)

    def _prompt_terms(self, image: Any, options: dict[str, Any]) -> None:
        """``expected_terms``, the prompt words behind them and the style hint of a prompt-generated image."""
        provenance = getattr(image, "source", None)
        if provenance is None or getattr(provenance, "kind", None) != "t2i":
            return
        ig_id = getattr(provenance, "image_generation_id", None)
        ig = self._image_generations.get(ig_id) if isinstance(ig_id, str) else None
        if ig is None:
            return
        expected = [str(t) for t in ig.options.get("expected_terms") or []]
        options["expected_terms"] = expected
        options["prompt_phrases"] = prompt_term_phrases(str(ig.options.get("prompt") or ""), expected)
        if ig.options.get("style") == "anime":
            options["subject_hint"] = "anime_character"

    # ------------------------------------------------------------------ read
    def get(self, sg_id: str) -> SegmentationRun:
        if not is_valid_id(sg_id, SEGMENTATION_PREFIX):
            raise NotFoundError("Segmentation not found", details={"segmentation_id": sg_id})
        live = self._runs.live_snapshot(sg_id)
        if isinstance(live, SegmentationRun):
            return live
        run = self._store.get(sg_id)
        if run is None:
            raise NotFoundError("Segmentation not found", details={"segmentation_id": sg_id})
        return run

    def list_runs(
        self,
        *,
        limit: int = 20,
        status: JobStatus | None = None,
        reference_set_id: str | None = None,
        job_id: str | None = None,
    ) -> list[SegmentationRun]:
        def matches(run: SegmentationRun) -> bool:
            return reference_set_id is None or run.source.reference_set_id == reference_set_id

        runs = self._store.list_runs(status=status, job_id=job_id, predicate=matches, limit=limit)
        return [self._fresh(run) for run in runs]

    def _fresh(self, run: SegmentationRun) -> SegmentationRun:
        live = self._runs.live_snapshot(run.id)
        return live if isinstance(live, SegmentationRun) else run

    # ------------------------------------------------------------------ cancel / delete
    async def cancel(self, sg_id: str) -> SegmentationRun:
        require_feature(self._settings, "segmentation")
        run = self.get(sg_id)
        if run.is_terminal:
            raise ConflictError(f"Segmentation is already {run.status}", details={"status": str(run.status)})
        if run.status is JobStatus.QUEUED:
            cancelled = self._runs.cancel_queued(sg_id)
            if isinstance(cancelled, SegmentationRun):
                return cancelled
        running = self._runs.cancel_running(sg_id)
        if isinstance(running, SegmentationRun):
            return running
        current = self.get(sg_id)
        if current.is_terminal:
            raise ConflictError(f"Segmentation is already {current.status}", details={"status": str(current.status)})
        return current

    async def delete(self, sg_id: str) -> None:
        require_feature(self._settings, "segmentation")
        run = self.get(sg_id)
        if run.is_active:
            raise ConflictError(
                "Cannot delete a queued or running segmentation; cancel it first", details={"status": str(run.status)}
            )
        await asyncio.to_thread(self._store.delete, sg_id)
        self._events.forget(sg_id)
        logger.info("Segmentation %s deleted", sg_id)

    # ------------------------------------------------------------------ files
    def file(self, sg_id: str, name: str) -> Path:
        """``source``, ``overlay``, ``labels`` or ``masks/<value>`` of a run (whitelisted names only)."""
        run = self.get(sg_id)
        run_dir = self._store.run_dir(run).resolve()
        if name == "source":
            return _existing(run_dir / SOURCE_FILE, run_dir, sg_id, name)
        if name not in _STATIC_FILES and not name.startswith(_MASK_PREFIX):
            raise NotFoundError("No such segmentation file", details={"name": name})
        if run.status is not JobStatus.COMPLETED or run.result is None:
            raise ConflictError(
                f"Segmentation is {run.status}; its files exist once it has completed",
                details={"status": str(run.status)},
            )
        if name in _STATIC_FILES:
            return _existing(run_dir / _STATIC_FILES[name], run_dir, sg_id, name)
        value = name[len(_MASK_PREFIX) :]
        if not value.isdigit() or len(value) > 5 or run.result.label(int(value)) is None:
            raise NotFoundError("No such mask in this segmentation", details={"value": value})
        return _existing(run_dir / MASKS_DIR / f"{int(value)}.png", run_dir, sg_id, name)


def _use_prompt_terms(options: dict[str, Any]) -> bool:
    return bool(options.get("options", {}).get("use_prompt_terms", True))


def _existing(path: Path, run_dir: Path, sg_id: str, name: str) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(run_dir) or not resolved.is_file():
        raise NotFoundError("Segmentation file not found", details={"segmentation_id": sg_id, "name": name})
    return resolved


def _pixels_sha256(path: Path) -> str:
    """sha256 of the decoded RGBA pixel bytes (section 4.5.1)."""
    with Image.open(path) as img:
        pixels = np.asarray(img.convert("RGBA"))
    return hashlib.sha256(pixels.tobytes()).hexdigest()
