"""TRELLIS.2-4B implementation of :class:`inference.base.InferenceEngine`.

This module is imported lazily by ``inference.registry.create_engine``. It does not import torch or trellis2 at
module level, so it can always be imported; a broken runtime is reported through ``status()`` (``unavailable``)
instead of an import error.

Threading: ``load``/``unload``/``generate`` are serialized by a lifecycle lock (they run on the single GPU worker
thread anyway). ``status``/``capabilities``/``device_info`` never take that lock and are safe from any thread.
"""

from __future__ import annotations

import gc
import logging
import threading
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from core.exceptions import (
    AppError,
    EngineError,
    EngineUnavailableError,
    JobCancelledError,
    ValidationAppError,
)
from inference.base import (
    CancellationToken,
    EngineCapabilities,
    EngineStatus,
    GenerationOutput,
    GenerationRequest,
    InferenceEngine,
    LoadProgress,
    PolygonTargetRange,
    ProgressReporter,
    StatusCallback,
)
from inference.device import DeviceInfoProvider, import_torch, parse_cuda_index
from inference.trellis.environment import RuntimeReport, TrellisSettings
from models.enums import EngineState, ReferenceMode

logger = logging.getLogger(__name__)

TRELLIS_CAPABILITIES = EngineCapabilities(
    resolutions=(512, 1024, 1536),
    texture_sizes=(512, 1024, 2048, 4096),
    polygon_target=PolygonTargetRange(min=5_000, max=2_000_000, default=500_000),
    max_reference_images=1,
    reference_modes=(ReferenceMode.PRIMARY_ONLY,),
    supports_cancel=True,
    supports_texture=True,
)

_MAX_SEED = 2**31 - 1

_OOM_GENERATE_HINT = (
    "The GPU ran out of memory during {stage}. Try a lower resolution (1024 or 512), a smaller texture size or "
    "polygon target, close other GPU applications, or set IMG3D_LOW_VRAM=true."
)
_OOM_LOAD_HINT = (
    "The GPU ran out of memory while loading TRELLIS.2 ({error}). Close other GPU applications or set "
    "IMG3D_LOW_VRAM=true (weights then stay in system RAM and are moved to the GPU per stage)."
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _first_line(text: str) -> str:
    return text.strip().splitlines()[0] if text and text.strip() else ""


def _is_oom(exc: BaseException) -> bool:
    torch = import_torch()
    oom_type = getattr(torch, "OutOfMemoryError", None) if torch is not None else None
    if oom_type is not None and isinstance(exc, oom_type):
        return True
    return isinstance(exc, RuntimeError) and "out of memory" in str(exc).lower() and "cuda" in str(exc).lower()


def _drop_tracebacks(exc: BaseException) -> None:
    """Release the frames (and the GPU tensors they reference) held by ``exc`` and its chained exceptions."""
    seen: set[int] = set()
    current: BaseException | None = exc
    stack: list[BaseException] = []
    while current is not None or stack:
        if current is None:
            current = stack.pop()
        if id(current) in seen:
            current = None
            continue
        seen.add(id(current))
        current.__traceback__ = None
        if current.__cause__ is not None:
            stack.append(current.__cause__)
        current = current.__context__


class TrellisEngine(InferenceEngine):
    """Microsoft TRELLIS.2-4B image-to-3D engine."""

    name = "trellis2"

    def __init__(self, settings: TrellisSettings) -> None:
        self._settings = settings
        self._status_lock = threading.Lock()
        self._lifecycle_lock = threading.RLock()
        self._pipeline: Any = None
        self._runtime: RuntimeReport | None = None
        self._listener: StatusCallback | None = None
        self._device_index = parse_cuda_index(getattr(settings, "device", "cuda"))
        self._device_info = DeviceInfoProvider(getattr(settings, "device", "cuda"))
        self._status = EngineStatus(
            state=EngineState.NOT_LOADED, message="Model not loaded", details=self._details()
        )

    # ------------------------------------------------------------------ status

    def _details(self, extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
        runtime = self._runtime
        details: dict[str, Any] = {
            "attention_backend": (
                runtime.attention_backend if runtime and runtime.attention_backend else self._settings.attention_backend
            ),
            "attention_backend_requested": self._settings.attention_backend,
            "low_vram": bool(self._settings.low_vram),
            "model_dir": str(self._settings.model_dir),
            "device": str(getattr(self._settings, "device", "cuda")),
            "versions": dict(runtime.library_versions) if runtime else {},
        }
        if runtime is not None:
            details["runtime"] = runtime.to_details()
        if extra:
            details.update(extra)
        return details

    def status(self) -> EngineStatus:
        with self._status_lock:
            return self._status

    def _set_status(
        self,
        state: EngineState,
        *,
        message: str | None,
        load_progress: LoadProgress | None = None,
        error: str | None = None,
        loaded_at: datetime | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> EngineStatus:
        status = EngineStatus(
            state=state,
            message=message,
            load_progress=load_progress,
            error=error,
            loaded_at=loaded_at,
            details=dict(details) if details is not None else self._details(),
        )
        with self._status_lock:
            self._status = status
        listener = self._listener
        if listener is not None:
            try:
                listener(status)
            except Exception:  # noqa: BLE001 - a broken listener must not break the engine
                logger.exception("Engine status listener failed")
        return status

    def capabilities(self) -> EngineCapabilities:
        return TRELLIS_CAPABILITIES

    def device_info(self) -> dict[str, Any] | None:
        try:
            return self._device_info.info()
        except Exception:  # noqa: BLE001 - /system must never fail because of the device query
            logger.debug("device_info failed", exc_info=True)
            return None

    @property
    def runtime_report(self) -> RuntimeReport | None:
        return self._runtime

    # ------------------------------------------------------------------ lifecycle

    def load(self, on_status: StatusCallback | None = None) -> None:
        with self._lifecycle_lock:
            if on_status is not None:
                self._listener = on_status
            if self._pipeline is not None and self.status().state is EngineState.READY:
                return
            started = time.perf_counter()
            self._set_status(EngineState.LOADING, message="Preparing TRELLIS.2 runtime")

            # 1) runtime (torch / CUDA / native extensions / attention backend / trellis2 import)
            try:
                from inference.trellis.environment import prepare_runtime

                report = prepare_runtime(self._settings)
            except Exception as exc:  # noqa: BLE001 - prepare_runtime reports expected problems itself
                logger.exception("Unexpected failure while preparing the TRELLIS.2 runtime")
                self._set_status(
                    EngineState.ERROR,
                    message=f"Failed to prepare the TRELLIS.2 runtime: {type(exc).__name__}: {_first_line(str(exc))}",
                    error=f"{type(exc).__name__}: {exc}",
                )
                return
            self._runtime = report
            if not report.ok:
                logger.error("%s\n%s", report.summary(), report.error_text())
                self._set_status(EngineState.UNAVAILABLE, message=report.summary(), error=report.error_text())
                return

            # 2) weights
            try:
                from inference.trellis.assets import AuxModelMissingError
                from inference.trellis.loader import (
                    AuxModelsUnavailableError,
                    ModelFilesMissingError,
                    TrellisPipelineLoader,
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("TRELLIS.2 loader modules failed to import")
                self._set_status(
                    EngineState.UNAVAILABLE,
                    message=f"TRELLIS.2 loader unavailable: {type(exc).__name__}: {_first_line(str(exc))}",
                    error=f"{type(exc).__name__}: {exc}",
                )
                return

            failure: tuple[EngineState, str, str] | None = None
            pipeline: Any = None
            try:
                loader = TrellisPipelineLoader(self._settings, on_progress=self._on_load_progress)
                plan = loader.plan()
                logger.info(
                    "Loading TRELLIS.2-4B from %s (aux: %s, rembg=%s, low_vram=%s, attention=%s)",
                    self._settings.model_dir, self._settings.aux_model_root, plan.aux.rembg_kind,
                    self._settings.low_vram, report.attention_backend,
                )
                pipeline = loader.load(plan)
            except (ModelFilesMissingError, AuxModelsUnavailableError, AuxModelMissingError) as exc:
                text = str(exc)
                logger.error("TRELLIS.2 model files unavailable:\n%s", text)
                missing = [str(m) for m in getattr(exc, "missing", [])]
                if isinstance(exc, ModelFilesMissingError):
                    summary = "TRELLIS.2-4B weights are missing or incomplete"
                else:
                    summary = "Required auxiliary models are missing"
                if missing:
                    shown = ", ".join(missing[:3]) + (f" (+{len(missing) - 3} more)" if len(missing) > 3 else "")
                    summary += f": {shown}"
                failure = (EngineState.UNAVAILABLE, summary + " (see error for how to fix)", text)
            except Exception as exc:  # noqa: BLE001 - every load failure becomes an ERROR status
                if _is_oom(exc):
                    logger.error("Out of GPU memory while loading TRELLIS.2", exc_info=True)
                    message = _OOM_LOAD_HINT.format(error=_first_line(str(exc)))
                else:
                    logger.exception("Failed to load TRELLIS.2")
                    message = f"Failed to load TRELLIS.2: {type(exc).__name__}: {_first_line(str(exc))}"
                failure = (EngineState.ERROR, message, f"{type(exc).__name__}: {exc}")

            if failure is not None:
                pipeline = None
                self._free_gpu()
                state, message, error = failure
                self._set_status(state, message=message, error=error)
                return

            self._pipeline = pipeline
            elapsed = time.perf_counter() - started
            device = report.device_name or "CUDA"
            message = f"TRELLIS.2-4B ready on {device} (attention: {report.attention_backend}" + (
                ", low-VRAM)" if self._settings.low_vram else ")"
            )
            logger.info("%s; loaded in %.1fs", message, elapsed)
            self._set_status(
                EngineState.READY,
                message=message,
                loaded_at=_utcnow(),
                details=self._details({"load_seconds": round(elapsed, 1), "rembg_kind": plan.aux.rembg_kind}),
            )

    def _on_load_progress(self, progress: LoadProgress, message: str) -> None:
        self._set_status(EngineState.LOADING, message=message, load_progress=progress)

    def unload(self) -> None:
        with self._lifecycle_lock:
            had_pipeline = self._pipeline is not None
            self._pipeline = None
            self._free_gpu()
            state = self.status().state
            if state is EngineState.UNAVAILABLE:
                return  # the runtime is still missing; keep the actionable status
            if had_pipeline or state is not EngineState.NOT_LOADED:
                logger.info("TRELLIS.2 unloaded")
                self._set_status(EngineState.NOT_LOADED, message="Model unloaded")

    def _free_gpu(self) -> None:
        gc.collect()
        torch = import_torch()
        if torch is None:
            return
        try:
            if torch.cuda.is_available() and torch.cuda.is_initialized():
                torch.cuda.empty_cache()
        except Exception:  # noqa: BLE001
            logger.debug("empty_cache failed", exc_info=True)

    def _release_after_run(self, pipeline: Any) -> None:
        """Return weights to RAM in low-VRAM mode (also after a cancel/error mid-stage) and free cached blocks."""
        if getattr(pipeline, "low_vram", False):
            try:
                for model in getattr(pipeline, "models", {}).values():
                    model.to("cpu")
                for component in (getattr(pipeline, "image_cond_model", None), getattr(pipeline, "rembg_model", None)):
                    if component is not None:
                        component.cpu()
                decoder = getattr(pipeline, "models", {}).get("shape_slat_decoder")
                if decoder is not None and hasattr(decoder, "low_vram"):
                    decoder.low_vram = False
            except Exception:  # noqa: BLE001 - cleanup must never mask the real outcome
                logger.exception("Failed to move TRELLIS.2 models back to CPU")
        self._free_gpu()

    # ------------------------------------------------------------------ generation

    def _validate(self, request: GenerationRequest) -> None:
        caps = TRELLIS_CAPABILITIES
        params = request.params
        if not request.images:
            raise ValidationAppError("Generation request has no conditioning image")
        if request.reference_mode not in caps.reference_modes:
            raise ValidationAppError(
                f"{request.reference_mode} reference mode is not supported by the current engine"
            )
        if params.resolution not in caps.resolutions:
            raise ValidationAppError(f"Unsupported resolution {params.resolution}; supported: {list(caps.resolutions)}")
        if params.texture_size not in caps.texture_sizes:
            raise ValidationAppError(
                f"Unsupported texture size {params.texture_size}; supported: {list(caps.texture_sizes)}"
            )
        if not caps.polygon_target.min <= params.polygon_target <= caps.polygon_target.max:
            raise ValidationAppError(
                f"polygon_target must be between {caps.polygon_target.min} and {caps.polygon_target.max}"
            )
        if not 0 <= params.seed <= _MAX_SEED:
            raise ValidationAppError(f"seed must be between 0 and {_MAX_SEED}")

    def generate(
        self,
        request: GenerationRequest,
        reporter: ProgressReporter,
        cancel: CancellationToken,
    ) -> GenerationOutput:
        self._validate(request)
        with self._lifecycle_lock:
            pipeline = self._pipeline
            status = self.status()
            if pipeline is None or status.state is not EngineState.READY:
                detail = f": {_first_line(status.error or status.message or '')}" if (status.error or status.message) else ""
                raise EngineUnavailableError(f"TRELLIS.2 engine is not ready (state: {status.state}){detail}")
            cancel.raise_if_cancelled()

            torch = import_torch()
            if torch is not None and torch.cuda.is_available():
                torch.cuda.set_device(self._device_index)
            from inference.trellis.generation import TrellisGenerationRun

            runtime = self._runtime
            run: TrellisGenerationRun | None = TrellisGenerationRun(
                pipeline,
                request,
                reporter,
                cancel,
                attention_backend=runtime.attention_backend if runtime else None,
            )
            params = request.params
            logger.info(
                "[%s] generating: resolution=%d seed=%d texture=%d polygons=%d remove_background=%s",
                request.job_id, params.resolution, params.seed, params.texture_size, params.polygon_target,
                params.remove_background,
            )
            output: GenerationOutput | None = None
            pending: BaseException | None = None
            try:
                output = run.execute()
            except JobCancelledError as exc:
                logger.info("[%s] generation cancelled during %s", request.job_id, run.current_stage)
                pending = exc
            except AppError as exc:
                logger.warning("[%s] generation failed during %s: %s", request.job_id, run.current_stage, exc.message)
                pending = exc
            except Exception as exc:  # noqa: BLE001 - mapped to EngineError below
                stage = str(run.current_stage) if run.current_stage else "generation"
                if _is_oom(exc):
                    logger.error("[%s] out of GPU memory during %s", request.job_id, stage, exc_info=True)
                    pending = EngineError(
                        _OOM_GENERATE_HINT.format(stage=stage),
                        details={"stage": stage, "reason": "out_of_memory", "error": _first_line(str(exc))},
                    )
                else:
                    logger.exception("[%s] generation failed during %s", request.job_id, stage)
                    pending = EngineError(
                        f"Generation failed during {stage}: {type(exc).__name__}: {_first_line(str(exc))}",
                        details={"stage": stage, "type": type(exc).__name__},
                    )
            finally:
                run = None
                if pending is not None:
                    _drop_tracebacks(pending)
                self._release_after_run(pipeline)

            if pending is not None:
                raise pending
            assert output is not None
            logger.info(
                "[%s] generation finished in %.1fs (effective resolution %d)",
                request.job_id, output.timings.get("total", 0.0), output.effective_resolution,
            )
            return output
