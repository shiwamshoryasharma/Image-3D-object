"""Model registry API: ``GET /models``, ``GET /models/{id}``, ``POST /models/{id}/load|unload`` (section 6.1).

Built on ``inference.providers.registry.ProviderRegistry``. Torch-free.

Warm-up (section 4.8 rule 4): ``POST /models/{id}/load`` answers 202 with state ``loading``; a background task then
runs ``provider.load()`` on a CPU thread (``asyncio.to_thread``, under the provider lock; this pays the disk read
before the user's first prompt) and queues ``warm()`` - one ``to(cuda)``/``to(cpu)`` cycle inside
``registry.guard()`` with the live VRAM check, then ``offload()`` - on the GPU thread through
``gpu.submit_gpu_task``, behind :func:`workers.run_worker.jobs_first_gate`. The model reports ``loading`` until both
finished (then the provider's ``loaded``, or ``error`` with the reason); every transition fires ``features.updated``.

``POST /models/{id}/unload`` answers 202 and drops the weights on the GPU thread (``unload()`` may touch CUDA), in
the background so a running image-to-3D job never blocks the request; a recorded load error is cleared.

Both answer 409 while the model is loading or may be used by the running run (a run of a kind that uses the model's
kind: image generation -> text-to-image, segmentation -> segmentation, part analysis -> segmentation and part
extraction) and 503 ``engine_unavailable`` while the server shuts down. Loading also needs the model's feature flag
and a usable model (503 ``feature_disabled`` / ``model_unavailable``).
"""

from __future__ import annotations

import asyncio
import copy
import logging
from collections.abc import Coroutine, Mapping
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final

from config.settings import Settings
from core.exceptions import ConflictError, EngineUnavailableError, JobCancelledError, NotFoundError
from core.feature_errors import ModelUnavailableError, require_feature
from inference.base import CancellationToken
from inference.providers.registry import KIND_FEATURES
from schemas.model_registry import DependencyOut, InstallOut, LicenseOut, ModelList, ModelOut
from services.run_events import FeaturesEventPublisher
from workers.queue import JobQueue
from workers.run_worker import GpuTaskSubmitter, RunWorker, jobs_first_gate

if TYPE_CHECKING:  # pragma: no cover
    from inference.providers.base import ModelKind, ModelProvider, ModelSpec
    from inference.providers.registry import ProviderRegistry

logger = logging.getLogger(__name__)

RUN_KIND_MODEL_KINDS: Final[Mapping[str, frozenset[str]]] = MappingProxyType(
    {
        "image_generation": frozenset({"text_to_image"}),
        "segmentation": frozenset({"segmentation"}),
        "part_analysis": frozenset({"segmentation", "part_extraction"}),
    }
)
"""Model kinds a running run of each kind may use (409 on load/unload of such a model)."""
SHUTDOWN_MESSAGE: Final = "The server is shutting down"
_RESIDENT_STATES: Final = frozenset({"loading", "loaded"})
_NOT_ON_DISK_CODES: Final = frozenset({"unsupported", "vendored_source_missing", "missing_dependency", "not_installed"})


def _relative_to_backend(path: Path | None, backend_dir: Path) -> str | None:
    """``path`` relative to ``backend/`` (POSIX); ``None`` when it lies elsewhere (never an absolute path)."""
    if path is None:
        return None
    try:
        return Path(path).resolve().relative_to(Path(backend_dir).resolve()).as_posix()
    except ValueError:
        return None


def build_model_out(registry: ProviderRegistry, settings: Settings, model_id: str) -> ModelOut:
    """``ModelOut`` of one catalog entry (``GET /models/{id}`` and every ``/features.models`` entry)."""
    spec: ModelSpec = registry.spec(model_id)
    status = registry.status(model_id)
    local_path = _relative_to_backend(registry.model_dir(model_id), settings.backend_dir)
    if spec.provider is None:
        hint = spec.unsupported_reason
    elif spec.local_subdir is None:
        hint = "No weights: runs on the installed runtime packages"
    else:
        hint = f"Weights are installed manually under backend/{local_path or spec.local_subdir} (DECISIONS.md)"
    return ModelOut(
        id=spec.id,
        kind=spec.kind,
        roles=list(spec.roles),
        display_name=spec.display_name,
        state=status.state,
        reason_code=status.reason_code,
        reason=status.reason,
        approved=registry.is_approved(model_id),
        is_default=model_id in registry.preferred_model_ids(),
        repo_id=spec.repo_id,
        revision=status.revision or spec.revision,
        size_bytes=spec.size_bytes,
        vram_estimate_gb=spec.vram_peak_gb if spec.vram_peak_gb > 0 else None,
        ram_gb=spec.ram_gb if spec.ram_gb > 0 else None,
        cold_load_s=spec.cold_load_s,
        license=LicenseOut(
            id=spec.license.id,
            name=spec.license.name,
            url=spec.license.url,
            commercial=spec.license.commercial,
            gated=spec.license.gated,
            requires_acceptance=spec.license.requires_acceptance,
            # Weights are installed manually: the licence was accepted when they were downloaded.
            accepted=not spec.license.requires_acceptance or status.reason_code not in _NOT_ON_DISK_CODES,
            conditions=list(spec.license.conditions),
            attribution=spec.license.attribution,
        ),
        dependencies=[
            DependencyOut(distribution=dep.distribution, requirement=dep.requirement, installed=dep.installed, ok=dep.ok)
            for dep in registry.dependencies(model_id)
        ],
        vendored_source=spec.vendored_source,
        local_path=local_path,
        install=InstallOut(supported=False, hint=hint),
        capabilities=copy.deepcopy(dict(spec.capabilities)),
        loaded_device=status.loaded_device,
        last_used_at=status.last_used_at,
        hidden_by_default=spec.hidden_by_default,
    )


class ModelRegistryService:
    def __init__(
        self,
        *,
        settings: Settings,
        registry: ProviderRegistry | None,
        gpu: GpuTaskSubmitter,
        job_queue: JobQueue,
        runs: RunWorker,
        events: FeaturesEventPublisher,
    ) -> None:
        self._settings = settings
        self._registry = registry
        self._gpu = gpu
        self._job_queue = job_queue
        self._runs = runs
        self._events = events
        self._tasks: dict[asyncio.Task[None], CancellationToken] = {}
        self._stopping = False

    # ------------------------------------------------------------------ reads
    def list_models(self, kind: ModelKind | None = None, *, include_hidden: bool = False) -> ModelList:
        registry = self._registry
        if registry is None:
            return ModelList(items=[])
        return ModelList(
            items=[
                build_model_out(registry, self._settings, spec.id)
                for spec in registry.specs(kind)
                if include_hidden or not spec.hidden_by_default
            ]
        )

    def get_model(self, model_id: str) -> ModelOut:
        return build_model_out(self._require_registry(model_id), self._settings, model_id)

    # ------------------------------------------------------------------ load / unload
    async def load_model(self, model_id: str) -> ModelOut:
        registry = self._require_registry(model_id)
        spec = registry.spec(model_id)
        self._ensure_accepting()
        feature = KIND_FEATURES.get(spec.kind)
        if feature is not None:
            require_feature(self._settings, feature)
        if registry.is_loading(model_id):
            raise ConflictError(f"{spec.display_name} is already loading", details={"model_id": model_id})
        self._ensure_not_in_use(spec)
        status = registry.status(model_id)
        if not status.usable:
            raise ModelUnavailableError(
                f"{spec.display_name} cannot be loaded: {status.reason or status.state}",
                model_id=model_id,
                state=status.state,
                reason_code=status.reason_code,
            )
        if not registry.begin_load(model_id):
            raise ConflictError(f"{spec.display_name} is already loading", details={"model_id": model_id})
        self._events.notify()
        token = CancellationToken()
        self._spawn(self._load_and_warm(registry, model_id, token), token, name=f"model-load-{model_id}")
        return build_model_out(registry, self._settings, model_id)

    async def unload_model(self, model_id: str) -> ModelOut:
        registry = self._require_registry(model_id)
        spec = registry.spec(model_id)
        self._ensure_accepting()
        if registry.is_loading(model_id):
            raise ConflictError(f"{spec.display_name} is loading", details={"model_id": model_id})
        self._ensure_not_in_use(spec)
        provider = registry.instantiated(model_id)
        if provider is not None and provider.status().state in _RESIDENT_STATES:
            token = CancellationToken()
            self._spawn(self._unload(registry, model_id, provider), token, name=f"model-unload-{model_id}")
        else:  # nothing resident: only clear a recorded error or a substituted stand-in
            registry.after_unload(model_id)
            self._events.notify()
        return build_model_out(registry, self._settings, model_id)

    async def stop(self, timeout: float) -> None:
        """Shutdown hook (before the generation worker stops): cancel warm-ups and wait at most ``timeout``."""
        self._stopping = True
        if not self._tasks:
            return
        for token in self._tasks.values():
            token.cancel(SHUTDOWN_MESSAGE)
        _done, still_running = await asyncio.wait(list(self._tasks), timeout=max(0.0, timeout))
        for task in still_running:
            logger.warning("Model task %s did not finish within %.1f s at shutdown", task.get_name(), timeout)

    # ------------------------------------------------------------------ background work
    def _spawn(self, coro: Coroutine[Any, Any, None], token: CancellationToken, *, name: str) -> None:
        task = asyncio.create_task(coro, name=name)
        self._tasks[task] = token
        task.add_done_callback(self._task_done)

    def _task_done(self, task: asyncio.Task[None]) -> None:
        self._tasks.pop(task, None)
        if not task.cancelled() and task.exception() is not None:
            logger.error("Model task %s failed", task.get_name(), exc_info=task.exception())

    async def _load_and_warm(self, registry: ProviderRegistry, model_id: str, token: CancellationToken) -> None:
        error: str | None = None
        try:
            provider = await asyncio.to_thread(registry.provider, model_id)
            await asyncio.to_thread(self._load_blocking, provider, token)
            token.raise_if_cancelled()
            gave_up = await jobs_first_gate(
                self._gpu, self._job_queue, max_defer_s=self._settings.run_max_defer_s, cancel=token
            )
            if gave_up:
                logger.warning(
                    "Warm-up of %s waited %.0f s behind image-to-3D jobs; warming anyway",
                    model_id,
                    self._settings.run_max_defer_s,
                )
            if not self._gpu.accepting_gpu_tasks:
                raise EngineUnavailableError(SHUTDOWN_MESSAGE)
            await self._gpu.submit_gpu_task(self._warm_blocking, registry, provider, token)
            logger.info("Model %s loaded and warmed", model_id)
        except JobCancelledError:
            logger.info("Warm-up of %s cancelled", model_id)
        except Exception as exc:  # noqa: BLE001 - reported as the model's error state
            error = getattr(exc, "message", None) or f"{type(exc).__name__}: {exc}"
            logger.warning("Loading %s failed: %s", model_id, error)
        finally:
            registry.end_load(model_id, error)
            self._events.notify()

    @staticmethod
    def _load_blocking(provider: ModelProvider, token: CancellationToken) -> None:
        """CPU thread: weights fully into RAM (never touches CUDA)."""
        with provider.lock:
            provider.load(token)

    @staticmethod
    def _warm_blocking(registry: ProviderRegistry, provider: ModelProvider, token: CancellationToken) -> None:
        """GPU thread: one ``to(cuda)``/``to(cpu)`` cycle inside the torch-globals guard, then offload."""
        token.raise_if_cancelled()
        with registry.guard(), provider.lock:
            registry.ensure_vram([provider.model_id])
            try:
                provider.warm(token)
            finally:
                registry.offload([provider.model_id])

    async def _unload(self, registry: ProviderRegistry, model_id: str, provider: ModelProvider) -> None:
        try:
            await self._gpu.submit_gpu_task(self._unload_blocking, registry, provider)
            logger.info("Model %s unloaded", model_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Unloading %s failed: %s: %s", model_id, type(exc).__name__, exc)
        finally:
            registry.after_unload(model_id)
            self._events.notify()

    @staticmethod
    def _unload_blocking(registry: ProviderRegistry, provider: ModelProvider) -> None:
        with registry.guard(), provider.lock:
            provider.unload()

    # ------------------------------------------------------------------ checks
    def _require_registry(self, model_id: str) -> ProviderRegistry:
        registry = self._registry
        if registry is None or not registry.has(model_id):
            raise NotFoundError(f"Model '{model_id}' not found", details={"model_id": model_id})
        return registry

    def _ensure_accepting(self) -> None:
        if self._stopping or self._runs.stopping or not self._gpu.accepting_gpu_tasks:
            raise EngineUnavailableError(SHUTDOWN_MESSAGE)

    def _ensure_not_in_use(self, spec: ModelSpec) -> None:
        running_kind = self._runs.running_kind
        if running_kind is not None and spec.kind in RUN_KIND_MODEL_KINDS.get(running_kind, frozenset()):
            run_id = self._runs.running_id
            raise ConflictError(
                f"{spec.display_name} may be in use by the running {running_kind.replace('_', ' ')} run {run_id}",
                details={"model_id": spec.id, "run_id": run_id},
            )
