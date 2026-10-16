"""Image generation executor (``ig_`` runs): prompt -> candidate images (part-aware DESIGN.md section 4.4.4).

Torch-free: the model work happens behind the :class:`~inference.imagegen.base.TextToImageProvider` seam, resolved
through the model registry. The run worker drives three phases:

========== ======================== ====== ======= =================================================================
step       label                    weight device notes
========== ======================== ====== ======= =================================================================
load_model Loading text-to-image    15     cpu     ``provider.load()`` reads the weights fully into CPU RAM on the
           model                                   ``parts-cpu`` thread (never CUDA); skipped when already resident
generate   Generating images        70     gpu     ``provider.generate()`` on the single ``gpu-worker`` thread;
                                                   candidates one at a time, the registry offloads afterwards
check      Checking 3D readiness    10     cpu     :mod:`processing.imagegen_readiness` (warnings only)
save       Saving candidates        5      cpu     ``candidates/<n>.png`` + ``<n>_thumb.webp`` (max side 384)
========== ======================== ====== ======= =================================================================

``run.options`` is the effective request written by ``ImageGenerationService.create`` (final prompt, size, steps,
guidance, seed, model id); this module never rewrites it. Candidate ``i`` uses seed ``(seed + i) mod 2^31``.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Protocol

from core.exceptions import AppError, JobCancelledError
from inference.imagegen.base import ImageGenOutput, ImageGenRequest, TextToImageProvider
from models.image_generation import (
    MAX_CANDIDATES,
    CandidateReadiness,
    ImageCandidate,
    ImageGenerationResult,
    ImageGenerationRun,
)
from models.runs import IMAGE_GENERATION_PREFIX, RUN_ERROR_CODE, RunKind, StepDef
from pipelines.prompt_templates import prompt_log_tag
from pipelines.runs.context import RunContext, RunProviderRegistry
from pipelines.runs.executor import ExecutorDeps, RunPhase
from processing.image_ops import make_thumbnail_webp
from processing.imagegen_readiness import ReadinessReport, assess_readiness, candidate_warning
from utils.files import atomic_write_bytes, ensure_dir

logger = logging.getLogger(__name__)

SEED_MODULUS: Final = 2**31
GENERATION_FAILED_ERROR_CODE: Final = "generation_failed"
GENERATED_DIR: Final = "generated"
"""Sub-folder of the run's scratch dir the provider writes into."""

STEP_LOAD: Final = "load_model"
STEP_GENERATE: Final = "generate"
STEP_CHECK: Final = "check"
STEP_SAVE: Final = "save"
STEP_IDS: Final[tuple[str, ...]] = (STEP_LOAD, STEP_GENERATE, STEP_CHECK, STEP_SAVE)

PLAN: Final[tuple[StepDef, ...]] = (
    StepDef(STEP_LOAD, "Loading text-to-image model", 15, "cpu"),
    StepDef(STEP_GENERATE, "Generating images", 70, "gpu"),
    StepDef(STEP_CHECK, "Checking 3D readiness", 10, "cpu"),
    StepDef(STEP_SAVE, "Saving candidates", 5, "cpu"),
)
MODEL_RESIDENT_MESSAGE: Final = "Model already in memory"


def image_generation_plan() -> tuple[StepDef, ...]:
    """The step plan of every image generation run (also used for the initial ``steps`` of a new run)."""
    return PLAN


def candidate_seeds(seed: int, count: int) -> tuple[int, ...]:
    """Seeds of ``count`` candidates: ``(seed + i) mod 2^31``."""
    return tuple((int(seed) + i) % SEED_MODULUS for i in range(count))


class ImageGenRegistry(RunProviderRegistry, Protocol):
    """The part of ``inference.providers.registry.ProviderRegistry`` this executor uses."""

    def provider(self, model_id: str) -> Any:
        """The model's provider (lazy import); ``ModelUnavailableError`` when it is not usable."""
        ...


@dataclass(slots=True)
class ImageGenerationState:
    """Phase state of one run (lives only while the run executes)."""

    model_id: str
    prompt: str
    negative_prompt: str | None
    preset: str
    width: int
    height: int
    steps: int
    guidance: float
    seeds: tuple[int, ...]
    output_dir: Path
    provider: TextToImageProvider | None = None
    paths: list[Path] = field(default_factory=list)
    sizes: list[tuple[int, int]] = field(default_factory=list)
    readiness: list[ReadinessReport] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)


def _is_out_of_memory(exc: BaseException) -> bool:
    """CUDA/CPU out-of-memory errors keep their own run error code (``out_of_memory``, set by the run worker)."""
    return type(exc).__name__ in {"OutOfMemoryError", "MemoryError"} or "out of memory" in str(exc).lower()


def _generation_failed(message: str, exc: BaseException) -> AppError:
    return AppError(
        f"{message}: {type(exc).__name__}: {exc}"[:2000],
        code=GENERATION_FAILED_ERROR_CODE,
        status_code=500,
        details={"exception": type(exc).__name__},
    )


class ImageGenerationExecutor:
    """Run executor of ``image_generation`` runs (constructed by the run framework as ``Executor(deps)``)."""

    kind: RunKind = "image_generation"
    prefix: str = IMAGE_GENERATION_PREFIX

    def __init__(self, deps: ExecutorDeps) -> None:
        self._deps = deps
        self._layout = deps.layout

    # ------------------------------------------------------------------ protocol
    def plan(self, run: ImageGenerationRun) -> tuple[StepDef, ...]:
        return image_generation_plan()

    def phases(self, run: ImageGenerationRun) -> tuple[RunPhase[ImageGenerationRun, ImageGenerationState], ...]:
        model_id = str(run.options.get("model_id") or "")
        return (
            RunPhase("load", "cpu", (STEP_LOAD,), self._load),
            RunPhase("generate", "gpu", (STEP_GENERATE,), self._generate, models=(model_id,) if model_id else ()),
            RunPhase("finish", "cpu", (STEP_CHECK, STEP_SAVE), self._check_and_save),
        )

    def prepare(self, run: ImageGenerationRun, work_dir: Path) -> ImageGenerationState:
        options = run.options
        try:
            model_id = str(options["model_id"])
            prompt = str(options["effective_prompt"])
            count = int(options["num_images"])
            state = ImageGenerationState(
                model_id=model_id,
                prompt=prompt,
                negative_prompt=options.get("negative_prompt") or None,
                preset=str(options.get("preset") or "raw"),
                width=int(options["width"]),
                height=int(options["height"]),
                steps=int(options["steps"]),
                guidance=float(options["guidance"]),
                seeds=candidate_seeds(int(options["seed"]), count),
                output_dir=ensure_dir(work_dir / GENERATED_DIR),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise AppError(
                f"The image generation request is incomplete: {type(exc).__name__}: {exc}", code=RUN_ERROR_CODE
            ) from exc
        if not model_id or not prompt or not 1 <= count <= MAX_CANDIDATES:
            raise AppError("The image generation request is incomplete", code=RUN_ERROR_CODE)
        logger.info(
            "Image generation %s: model=%s images=%d size=%dx%d steps=%d prompt %s",
            run.id,
            model_id,
            count,
            state.width,
            state.height,
            state.steps,
            prompt_log_tag(prompt),
        )
        return state

    def finalize(
        self, run: ImageGenerationRun, state: ImageGenerationState, ctx: RunContext
    ) -> ImageGenerationRun:
        candidates = [
            ImageCandidate(
                index=index,
                seed=state.seeds[index],
                width=state.sizes[index][0],
                height=state.sizes[index][1],
                readiness=CandidateReadiness(
                    ok=state.readiness[index].ok,
                    issues=list(state.readiness[index].issues),
                    method=state.readiness[index].method,
                ),
            )
            for index in range(len(state.paths))
        ]
        timings = {step_id: round(state.timings.get(step_id, 0.0), 3) for step_id in STEP_IDS}
        timings["total"] = round(sum(timings.values()), 3)
        result = ImageGenerationResult(candidates=candidates, timings=timings)
        return run.model_copy(update={"result": result}, deep=True)

    # ------------------------------------------------------------------ phases
    def _provider(self, model_id: str) -> TextToImageProvider:
        registry: ImageGenRegistry | None = self._deps.registry  # type: ignore[assignment]
        if registry is None or not callable(getattr(registry, "provider", None)):
            raise AppError(
                "The model registry is not available in this build; images cannot be generated",
                code=RUN_ERROR_CODE,
                status_code=503,
            )
        provider = registry.provider(model_id)
        if not isinstance(provider, TextToImageProvider):
            raise AppError(f"Model {model_id} is not a text-to-image model", code=RUN_ERROR_CODE)
        return provider

    def _load(self, run: ImageGenerationRun, state: ImageGenerationState, ctx: RunContext) -> None:
        """cpu: read the weights into RAM (never touches CUDA)."""
        provider = self._provider(state.model_id)
        state.provider = provider
        if provider.status().state == "loaded":
            ctx.skip(STEP_LOAD, MODEL_RESIDENT_MESSAGE)
            state.timings[STEP_LOAD] = 0.0
            return
        started = time.perf_counter()
        with ctx.step(STEP_LOAD, "Loading text-to-image model") as step:
            try:
                provider.load(ctx.cancel)
            except (JobCancelledError, AppError):
                raise
            except Exception as exc:
                if ctx.cancel.is_cancelled or _is_out_of_memory(exc):
                    raise
                raise _generation_failed(f"Could not load {state.model_id}", exc) from exc
            step.message = "Model loaded into memory"
        state.timings[STEP_LOAD] = time.perf_counter() - started

    def _generate(self, run: ImageGenerationRun, state: ImageGenerationState, ctx: RunContext) -> None:
        """gpu: generate every candidate (the registry checks VRAM before and offloads after this phase)."""
        provider = state.provider or self._provider(state.model_id)
        state.provider = provider
        ctx.use_model(state.model_id)
        request = ImageGenRequest(
            run_id=run.id,
            prompt=state.prompt,
            negative_prompt=state.negative_prompt,
            width=state.width,
            height=state.height,
            steps=state.steps,
            guidance=state.guidance,
            seeds=state.seeds,
            output_dir=state.output_dir,
            device=ctx.device,
        )
        started = time.perf_counter()
        count = len(state.seeds)
        with ctx.step(STEP_GENERATE, "Generating images") as step:
            try:
                output = provider.generate(request, step.progress, ctx.cancel)
            except (JobCancelledError, AppError):
                raise
            except Exception as exc:
                if ctx.cancel.is_cancelled or _is_out_of_memory(exc):
                    raise
                raise _generation_failed("Image generation failed", exc) from exc
            ctx.cancel.raise_if_cancelled()
            state.paths = self._checked_paths(output, state)
            step.details.update({f"provider_{key}": value for key, value in output.timings.items()})
            step.message = f"{count} image{'s' if count != 1 else ''} generated"
        state.timings[STEP_GENERATE] = time.perf_counter() - started

    def _check_and_save(self, run: ImageGenerationRun, state: ImageGenerationState, ctx: RunContext) -> None:
        """cpu: readiness warnings, then the candidate files of the run directory."""
        count = len(state.paths)
        started = time.perf_counter()
        with ctx.step(STEP_CHECK, "Checking 3D readiness") as step:
            for index, path in enumerate(state.paths):
                ctx.cancel.raise_if_cancelled()
                report = assess_readiness(path, preset=state.preset)
                state.readiness.append(report)
                width, height = report.metrics["size"]
                state.sizes.append((int(width), int(height)))
                for code in report.issues:
                    step.warn(code, candidate_warning(index, code))
                step.progress((index + 1) / count)
            step.details["candidates"] = [
                {"index": index, "issues": list(report.issues), **dict(report.metrics)}
                for index, report in enumerate(state.readiness)
            ]
            ready = sum(1 for report in state.readiness if report.ok)
            step.message = f"{ready} of {count} candidate{'s' if count != 1 else ''} look 3D-ready"
        state.timings[STEP_CHECK] = time.perf_counter() - started

        started = time.perf_counter()
        with ctx.step(STEP_SAVE, "Saving candidates") as step:
            ensure_dir(self._layout.candidates_dir(run.id))
            for index, path in enumerate(state.paths):
                ctx.cancel.raise_if_cancelled()
                data = path.read_bytes()
                atomic_write_bytes(self._layout.candidate_path(run.id, index), data)
                atomic_write_bytes(self._layout.candidate_thumb_path(run.id, index), make_thumbnail_webp(data))
                step.progress((index + 1) / count)
            step.message = f"{count} candidate{'s' if count != 1 else ''} saved"
        state.timings[STEP_SAVE] = time.perf_counter() - started

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _checked_paths(output: ImageGenOutput, state: ImageGenerationState) -> list[Path]:
        """The provider's files, in seed order, all inside the run's output dir."""
        expected = list(state.seeds)
        if list(output.seeds) != expected or len(output.paths) != len(expected):
            raise AppError(
                f"The text-to-image provider returned {len(output.paths)} image(s) for {len(expected)} seed(s)",
                code=GENERATION_FAILED_ERROR_CODE,
                status_code=500,
            )
        root = state.output_dir.resolve()
        paths: list[Path] = []
        for path in output.paths:
            resolved = Path(path).resolve()
            if not resolved.is_relative_to(root) or not resolved.is_file():
                raise AppError(
                    "The text-to-image provider did not write its images into the run directory",
                    code=GENERATION_FAILED_ERROR_CODE,
                    status_code=500,
                )
            paths.append(resolved)
        return paths

