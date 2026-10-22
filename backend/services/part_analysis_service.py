"""Part analysis runs (``pa_``): decomposition after reconstruction (part-aware DESIGN.md sections 4.6 and 6.4).

Creation (``POST /jobs/{job_id}/part-analyses``):

* 503 ``feature_disabled`` without the part-analysis flag; 404 for an unknown job; 409 when the job is not
  ``completed`` or its ``model.glb`` is missing;
* the effective options: fields the body does not set fall back to the server settings (``parts_default_views``,
  ``parts_max_parts``, ``parts_min_part_faces``); ``strategy = null`` resolves like ``/features.default_strategy``
  (``semantic_parts`` when the baseline segmentation pair and the lifter are usable, else ``components``); a reserved
  strategy or a semantic one without its models is 503 ``capability_unavailable``;
* ``segmentation_id`` (404 unknown, 409 not completed) is reused only when it qualifies (section 4.6.3 rule 1: a
  job-source ``sg_`` of the same job, or a reference-image ``sg_`` with a verified ``recompute`` frame whose
  ``source_pixels_sha256`` equals the sha256 of the job's decoded primary input); otherwise the run does its internal
  pass and records ``segmentation_rerun_for_alignment``.

Runs are executed by :class:`workers.run_worker.RunWorker` with ``pipelines.part_analysis:PartAnalysisExecutor`` and
stored inside their job (``jobs/<job>/parts/<pa>/run.json``). Files of published layers are served from
``asset/`` (whitelisted names only) or built on demand by :class:`services.part_files.PartFiles`. Torch-free.

:meth:`PartAnalysisService.on_job_deleting` is registered by the container as a job-delete hook: deleting a job
cancels its active analyses (waiting at most 30 s), forgets them and purges their export cache before the job folder
(and ``parts/`` with it) is removed.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

import numpy as np
from PIL import Image

from config.settings import Settings
from core.exceptions import ConflictError, NotFoundError, ValidationAppError
from core.feature_errors import CapabilityUnavailableError, require_feature
from models.enums import JobStatus
from models.part_analysis import PREVIEW_VIEWS, PartAnalysisRun
from models.runs import PART_ANALYSIS_PREFIX, SEGMENTATION_PREFIX
from models.segmentation import SegmentationRun
from parts.manifest import BufferRecord, PartsManifest
from pipelines.part_analysis import SEMANTIC_STRATEGIES, semantic_usable
from schemas.parts import RESERVED_STRATEGIES, PartAnalysisOptions, PartAnalysisOut
from services.feature_service import FeatureService
from services.job_service import JobService
from services.part_files import PartFiles
from services.result_service import ResultService
from services.run_events import RunEventPublisher
from storage.run_layout import RUN_LOG_FILE, RunLayout
from storage.run_store import RunStore
from utils.ids import JOB_PREFIX, is_valid_id, new_id
from utils.timeutil import utcnow
from workers.run_worker import RunWorker

if TYPE_CHECKING:  # pragma: no cover
    from inference.providers.registry import ProviderRegistry

logger = logging.getLogger(__name__)

JOB_DELETE_WAIT_S: Final = 30.0
JOB_DELETED_REASON: Final = "The job was deleted"
LOG_TAIL_BYTES: Final = 256 * 1024
SEGMENTATION_FILES: Final = frozenset({"source.png", "labels.png", "overlay.png"})
THUMB_NAME: Final = re.compile(r"^[a-z0-9_.]{1,90}\.webp$")
PREVIEW_NAME: Final = re.compile(r"^(front|back|left|right)\.png$")
_CAPABILITY = "part_analysis"
_SETTINGS_DEFAULTS: Final = {
    "views": "parts_default_views",
    "max_parts": "parts_max_parts",
    "min_part_faces": "parts_min_part_faces",
}


class PartAnalysisService:
    def __init__(
        self,
        *,
        settings: Settings,
        store: RunStore[PartAnalysisRun],
        runs: RunWorker,
        registry: ProviderRegistry | None,
        jobs: JobService,
        results: ResultService,
        segmentations: RunStore[SegmentationRun],
        events: RunEventPublisher[PartAnalysisRun],
        layout: RunLayout,
        files: PartFiles,
        features: FeatureService,
    ) -> None:
        self._settings = settings
        self._store = store
        self._runs = runs
        self._registry = registry
        self._jobs = jobs
        self._results = results
        self._segmentations = segmentations
        self._events = events
        self._layout = layout
        self._files = files
        self._features = features

    def to_out(self, run: PartAnalysisRun) -> PartAnalysisOut:
        return PartAnalysisOut.from_run(run)

    # ------------------------------------------------------------------ create
    async def create(
        self, job_id: str, options: PartAnalysisOptions | None = None, *, workflow_id: str | None = None
    ) -> PartAnalysisRun:
        require_feature(self._settings, "part_analysis")
        job = self._job(job_id)
        if job.status is not JobStatus.COMPLETED:
            raise ConflictError(
                f"The job is {job.status}; only completed jobs can be analysed",
                details={"status": str(job.status), "reason": "job_not_completed"},
            )
        if not self._layout.paths.job_model(job.id).is_file():
            raise ConflictError("The job has no model.glb", details={"reason": "model_missing"})
        request = self._effective(options or PartAnalysisOptions())
        if workflow_id is not None and request.segmentation_id is not None:
            raise ValidationAppError(
                "segmentation_id must be null inside workflows",
                details=[{"loc": ["body", "parts", "segmentation_id"], "msg": "must be null", "type": "value_error"}],
            )
        extra: dict[str, Any] = {"segmentation_source": "internal" if request.strategy in SEMANTIC_STRATEGIES
                                 else "none", "segmentation_rerun": False}
        if request.segmentation_id is not None:
            sg = self._segmentation(request.segmentation_id)
            if request.strategy in SEMANTIC_STRATEGIES:
                reused = await asyncio.to_thread(self._qualifies, sg, job)
                extra["segmentation_source"] = "reused" if reused else "internal"
                extra["segmentation_rerun"] = not reused
        run = PartAnalysisRun(
            id=new_id(PART_ANALYSIS_PREFIX),
            created_at=utcnow(),
            options={**request.model_dump(mode="json"), **extra},
            job_id=job.id,
            workflow_id=workflow_id,
        )
        self._runs.enqueue(run)
        logger.info("Part analysis %s queued for job %s (%s)", run.id, job.id, request.strategy)
        return run

    def _effective(self, options: PartAnalysisOptions) -> PartAnalysisOptions:
        update: dict[str, Any] = {
            name: getattr(self._settings, setting)
            for name, setting in _SETTINGS_DEFAULTS.items()
            if name not in options.model_fields_set
        }
        strategy = options.strategy
        usable, reasons = semantic_usable(self._registry)
        if strategy is None:
            strategy = "semantic_parts" if usable else "components"
        elif strategy in RESERVED_STRATEGIES:
            raise CapabilityUnavailableError(
                f"The {strategy} strategy is reserved and not implemented",
                capability=_CAPABILITY,
                reasons=[{"strategy": strategy, "reason_code": "not_implemented"}],
            )
        elif strategy in SEMANTIC_STRATEGIES and not usable:
            raise CapabilityUnavailableError(
                f"The {strategy} strategy needs the baseline segmentation models and the label lifter",
                capability=_CAPABILITY,
                reasons=reasons,
            )
        update["strategy"] = strategy
        return PartAnalysisOptions.model_validate({**options.model_dump(mode="json"), **update})

    def _qualifies(self, sg: SegmentationRun, job: Any) -> bool:
        """Section 4.6.3 rule 1: may the masks of ``sg`` be lifted onto ``job``'s mesh as they are?"""
        result = sg.result
        if result is None or result.space != "conditioning":
            return False
        if sg.source.kind == "job":
            return sg.source.job_id == job.id
        frame = result.frame
        if frame is None or not frame.verified or frame.method != "recompute" or not sg.source_pixels_sha256:
            return False
        primary = self._layout.paths.job_inputs(job.id) / f"{job.primary_image_id}.png"
        if not primary.is_file():
            return False
        return _pixels_sha256(primary) == sg.source_pixels_sha256

    def _segmentation(self, sg_id: str) -> SegmentationRun:
        sg = self._segmentations.get(sg_id) if is_valid_id(sg_id, SEGMENTATION_PREFIX) else None
        if sg is None:
            raise NotFoundError("Segmentation not found", details={"segmentation_id": sg_id})
        if sg.status is not JobStatus.COMPLETED or sg.result is None:
            raise ConflictError(
                f"Segmentation is {sg.status}; only a completed segmentation can be reused",
                details={"status": str(sg.status), "segmentation_id": sg_id},
            )
        return sg

    def _job(self, job_id: str) -> Any:
        if not is_valid_id(job_id, JOB_PREFIX):
            raise NotFoundError("Job not found", details={"job_id": job_id})
        return self._jobs.get_job(job_id)

    # ------------------------------------------------------------------ read
    def get(self, pa_id: str) -> PartAnalysisRun:
        if not is_valid_id(pa_id, PART_ANALYSIS_PREFIX):
            raise NotFoundError("Part analysis not found", details={"analysis_id": pa_id})
        live = self._runs.live_snapshot(pa_id)
        if isinstance(live, PartAnalysisRun):
            return live
        run = self._store.get(pa_id)
        if run is None:
            raise NotFoundError("Part analysis not found", details={"analysis_id": pa_id})
        return run

    def list_for_job(self, job_id: str) -> list[PartAnalysisRun]:
        job = self._job(job_id)
        return [self._fresh(run) for run in self._store.list_for_job(job.id)]

    def latest(self, job_id: str) -> PartAnalysisRun:
        job = self._job(job_id)
        for run in self._store.list_runs(job_id=job.id, status=JobStatus.COMPLETED):
            if run.result is not None:
                return run
        raise NotFoundError("The job has no completed part analysis", details={"job_id": job.id, "reason": "none"})

    def _fresh(self, run: PartAnalysisRun) -> PartAnalysisRun:
        live = self._runs.live_snapshot(run.id)
        return live if isinstance(live, PartAnalysisRun) else run

    # ------------------------------------------------------------------ cancel / delete
    async def cancel(self, pa_id: str) -> PartAnalysisRun:
        require_feature(self._settings, "part_analysis")
        run = self.get(pa_id)
        if run.is_terminal:
            raise ConflictError(f"Part analysis is already {run.status}", details={"status": str(run.status)})
        if run.status is JobStatus.QUEUED:
            cancelled = self._runs.cancel_queued(pa_id)
            if isinstance(cancelled, PartAnalysisRun):
                return cancelled
        running = self._runs.cancel_running(pa_id)
        if isinstance(running, PartAnalysisRun):
            return running
        current = self.get(pa_id)
        if current.is_terminal:
            raise ConflictError(f"Part analysis is already {current.status}", details={"status": str(current.status)})
        return current

    async def delete(self, pa_id: str) -> None:
        require_feature(self._settings, "part_analysis")
        run = self.get(pa_id)
        if run.is_active:
            raise ConflictError(
                "Cannot delete a queued or running part analysis; cancel it first",
                details={"status": str(run.status)},
            )
        await asyncio.to_thread(self._store.delete, pa_id)
        await asyncio.to_thread(self._files.purge, pa_id)
        self._events.forget(pa_id)
        logger.info("Part analysis %s deleted", pa_id)

    # ------------------------------------------------------------------ files
    def asset_file(self, pa_id: str, relative: str) -> Path:
        """A whitelisted file of a published asset (manifest, ``model_parts.glb``, masks, thumbs, ...)."""
        run = self._files.completed_run(pa_id)
        asset = self._files.asset_dir(run).resolve()
        path = (asset / relative).resolve()
        if not path.is_relative_to(asset) or not path.is_file():
            raise NotFoundError("Part analysis file not found", details={"analysis_id": pa_id, "name": relative})
        return path

    def manifest(self, pa_id: str) -> tuple[PartAnalysisRun, PartsManifest]:
        run = self._files.completed_run(pa_id)
        return run, self._files.manifest(run)

    def mask_file(self, pa_id: str, value: str) -> Path:
        _run, manifest = self.manifest(pa_id)
        number = int(value)
        if all(entry.value != number for entry in manifest.label_table):
            raise NotFoundError("No such label in this analysis", details={"value": value})
        return self.asset_file(pa_id, f"masks/{number}.png")

    def thumbnail_file(self, pa_id: str, name: str) -> Path:
        _run, manifest = self.manifest(pa_id)
        relative = f"thumbs/{name}"
        if not THUMB_NAME.match(name) or all(p.files.thumbnail != relative for p in manifest.parts):
            raise NotFoundError("No such thumbnail in this analysis", details={"name": name})
        return self.asset_file(pa_id, relative)

    def segmentation_file(self, pa_id: str, name: str) -> Path:
        if name not in SEGMENTATION_FILES:
            raise NotFoundError("No such segmentation file", details={"name": name})
        self._files.completed_run(pa_id)
        return self.asset_file(pa_id, f"segmentation/{name}")

    def preview_file(self, pa_id: str, name: str) -> Path:
        if not PREVIEW_NAME.match(name) or name.removesuffix(".png") not in PREVIEW_VIEWS:
            raise NotFoundError("No such preview", details={"name": name})
        self._files.completed_run(pa_id)
        return self.asset_file(pa_id, f"previews/{name}")

    def buffer_file(self, pa_id: str, buffer_id: str) -> tuple[Path, BufferRecord]:
        _run, manifest = self.manifest(pa_id)
        record = next((b for b in manifest.buffers if b.id == buffer_id), None)
        if record is None:
            raise NotFoundError("No such buffer in this analysis", details={"buffer_id": buffer_id})
        return self.asset_file(pa_id, record.file), record

    def log_tail(self, pa_id: str) -> str:
        run = self.get(pa_id)
        path = self._layout.part_analysis_log(run.job_id, run.id)
        if not path.is_file():
            raise NotFoundError("This part analysis has no log", details={"analysis_id": pa_id, "file": RUN_LOG_FILE})
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - LOG_TAIL_BYTES))
            return fh.read().decode("utf-8", errors="replace")

    async def part_glb(self, pa_id: str, part_id: str) -> tuple[PartAnalysisRun, Path]:
        path = await self._files.part_glb(pa_id, part_id)
        return self._files.completed_run(pa_id), path

    async def bundle_zip(self, pa_id: str, *, include_base: bool) -> tuple[PartAnalysisRun, Path]:
        path = await self._files.bundle_zip(pa_id, include_base=include_base)
        return self._files.completed_run(pa_id), path

    # ------------------------------------------------------------------ job-delete hook
    async def on_job_deleting(self, job_id: str) -> None:
        """Cancel the job's active analyses (wait <= 30 s), forget them and purge their export cache."""
        for run in self._store.list_active(job_id):
            if self._runs.cancel_queued(run.id, JOB_DELETED_REASON) is not None:
                continue
            if self._runs.cancel_running(run.id, JOB_DELETED_REASON) is None:
                continue
            if not await self._runs.wait_finished(run.id, JOB_DELETE_WAIT_S):
                logger.warning(
                    "Part analysis %s did not stop within %.0f s before deleting job %s",
                    run.id,
                    JOB_DELETE_WAIT_S,
                    job_id,
                )
        forgotten = self._store.forget_job(job_id)
        for pa_id in forgotten:
            self._files.purge(pa_id)
        if forgotten:
            suffix = "is" if len(forgotten) == 1 else "es"
            logger.info("Job %s deleted with %d part analys%s", job_id, len(forgotten), suffix)


def _pixels_sha256(path: Path) -> str:
    """sha256 of the decoded RGBA pixel bytes (section 4.5.1; compared with ``sg.source_pixels_sha256``)."""
    with Image.open(path) as img:
        pixels = np.asarray(img.convert("RGBA"))
    return hashlib.sha256(pixels.tobytes()).hexdigest()
