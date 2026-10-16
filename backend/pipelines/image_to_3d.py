"""Image-to-3D pipeline: job inputs -> reference strategy -> engine.generate -> GLB stats -> JobResultData."""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path
from typing import Any

from config.settings import Settings
from core.exceptions import EngineError, StorageError
from inference.base import (
    CancellationToken,
    ConditioningImage,
    GenerationParams,
    GenerationRequest,
    InferenceEngine,
    ProgressReporter,
    SamplerOverrides,
)
from models.enums import JobStage, ReferenceMode
from models.job import Job, JobResultData
from pipelines.base import GenerationPipeline
from pipelines.reference_strategy import get_strategy
from processing.mesh_stats import compute_glb_stats
from storage.job_store import JobStore
from utils.files import replace_file
from utils.jsonsafe import finite_or, to_jsonable

logger = logging.getLogger(__name__)


def _sampler(advanced: dict[str, Any] | None, key: str) -> SamplerOverrides:
    values = (advanced or {}).get(key) or {}
    return SamplerOverrides(
        steps=values.get("steps"),
        guidance_strength=values.get("guidance_strength"),
        guidance_rescale=values.get("guidance_rescale"),
        rescale_t=values.get("rescale_t"),
    )


def _jsonable(value: dict[str, Any]) -> dict[str, Any]:
    """Engine metadata may hold numpy scalars/paths/NaN; make it strict JSON for job.json."""
    result = to_jsonable(value)
    return result if isinstance(result, dict) else {}


def build_generation_params(job: Job, *, glb_webp_textures: bool) -> GenerationParams:
    """Map a job's normalized settings (+ resolved seed) onto engine parameters."""
    s = job.settings
    advanced = s.get("advanced")
    return GenerationParams(
        resolution=int(s["resolution"]),
        seed=int(job.seed),
        polygon_target=int(s["polygon_target"]),
        texture_size=int(s["texture_size"]),
        remove_background=bool(s["remove_background"]),
        sparse_structure=_sampler(advanced, "sparse_structure"),
        shape=_sampler(advanced, "shape"),
        texture=_sampler(advanced, "texture"),
        glb_webp_textures=glb_webp_textures,
    )


class ImageTo3DPipeline(GenerationPipeline):
    def __init__(self, engine: InferenceEngine, job_store: JobStore, settings: Settings) -> None:
        self._engine = engine
        self._jobs = job_store
        self._settings = settings

    def resolve_references(self, job: Job) -> list[ConditioningImage]:
        """Job-local normalized PNG inputs for every reference snapshot (primary first)."""
        inputs = self._jobs.inputs_dir(job.id)
        references: list[ConditioningImage] = []
        for ref in job.references:
            path = inputs / ref.input_file_name
            if not path.is_file():
                raise StorageError(
                    f"Input image {ref.image_id} is missing from the job directory",
                    details={"image_id": ref.image_id},
                )
            references.append(
                ConditioningImage(image_id=ref.image_id, slot=ref.slot, path=path, is_primary=ref.is_primary)
            )
        references.sort(key=lambda r: not r.is_primary)
        return references

    def run(self, job: Job, reporter: ProgressReporter, cancel: CancellationToken) -> JobResultData:
        started = time.perf_counter()
        cancel.raise_if_cancelled()

        all_references = self.resolve_references(job)
        capabilities = self._engine.capabilities()
        mode = ReferenceMode(job.settings.get("reference_mode") or self._settings.reference_mode)
        strategy = get_strategy(mode)
        strategy.validate(capabilities)
        selected = strategy.select(all_references, capabilities)
        params = build_generation_params(job, glb_webp_textures=self._settings.glb_webp_textures)
        output_dir = self._jobs.job_dir(job.id)

        request = GenerationRequest(
            job_id=job.id,
            images=tuple(selected),
            all_references=tuple(all_references),
            params=params,
            output_dir=output_dir,
            reference_mode=mode,
        )
        logger.info(
            "Generating: resolution=%d seed=%d polygon_target=%d texture_size=%d images=%s",
            params.resolution,
            params.seed,
            params.polygon_target,
            params.texture_size,
            [img.image_id for img in selected],
        )
        output = self._engine.generate(request, reporter, cancel)

        finalize_started = time.perf_counter()
        reporter.stage(JobStage.FINALIZING, "Computing model statistics")
        model_path = self._place(output.glb_path, self._jobs.model_path(job.id))
        if model_path is None:
            raise EngineError("The engine did not produce a GLB file")
        preview_path = self._place(output.preview_path, self._jobs.preview_path(job.id))

        try:
            stats = compute_glb_stats(model_path, fallback_texture_size=params.texture_size)
        except Exception as exc:  # noqa: BLE001 - surface as an engine failure with context
            raise EngineError(
                f"The exported GLB could not be read: {exc}", details={"exception": type(exc).__name__}
            ) from exc

        timings = {
            str(k): round(finite_or(v), 3) for k, v in (output.timings or {}).items() if k != "total"
        }
        timings[str(JobStage.FINALIZING)] = round(time.perf_counter() - finalize_started, 3)
        timings["total"] = round(time.perf_counter() - started, 3)

        return JobResultData(
            stats=stats,
            timings=timings,
            seed=params.seed,
            resolution=params.resolution,
            effective_resolution=int(output.effective_resolution or params.resolution),
            texture_size=params.texture_size,
            polygon_target=params.polygon_target,
            has_preview=preview_path is not None,
            metadata=_jsonable({**(output.metadata or {}), "engine_mesh_stats": dict(output.mesh_stats or {})}),
        )

    @staticmethod
    def _place(source: Path | None, destination: Path) -> Path | None:
        """Ensure an engine output file lives at its canonical job path."""
        if source is None:
            return destination if destination.is_file() else None
        source = Path(source)
        if not source.is_file():
            return destination if destination.is_file() else None
        if source.resolve() == destination.resolve():
            return destination
        try:
            replace_file(source, destination)
        except OSError:
            shutil.copy2(source, destination)
        return destination
