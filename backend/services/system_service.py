"""Health, system status and engine control. Also broadcasts ``system.updated`` events.

Device information comes from ``engine.device_info()`` executed with :func:`asyncio.to_thread` (the default
thread pool - never the GPU executor) under a short timeout, and is cached for a few seconds.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from config.settings import Settings
from core.events import Event, EventBus
from inference.base import InferenceEngine
from models.enums import SLOT_DISPLAY_ORDER
from processing.image_validation import ALLOWED_MIME_TYPES
from schemas.events import EventType
from schemas.jobs import GenerationSettings
from schemas.system import (
    AppInfo,
    CapabilitiesOut,
    DefaultsInfo,
    DeviceInfo,
    EngineInfo,
    HealthResponse,
    LimitsInfo,
    QueueInfo,
    SystemStatus,
)
from utils.jsonsafe import to_jsonable
from utils.timeutil import utcnow_iso
from workers.queue import JobQueue

if TYPE_CHECKING:  # pragma: no cover
    from workers.generation_worker import GenerationWorker

logger = logging.getLogger(__name__)

_BROADCAST_DEBOUNCE_S = 0.05


def _parse_device(info: Any) -> DeviceInfo | None:
    """Validate the engine's device dict; tolerate unexpected value types instead of failing ``/system``."""
    if not isinstance(info, dict):
        return None
    info = to_jsonable(info)  # plain JSON values; NaN/Infinity -> None
    if not isinstance(info, dict):
        return None
    try:
        return DeviceInfo.model_validate(info)
    except ValidationError:
        logger.warning("Engine device_info() returned unexpected value types; coercing to strings")
        string_fields = {"name", "compute_capability", "torch_version", "cuda_version"}
        coerced = {
            str(k): v
            if v is None or (isinstance(v, (bool, int, float, str)) and str(k) not in string_fields)
            else _device_text(v)
            for k, v in info.items()
        }
        try:
            return DeviceInfo.model_validate(coerced)
        except ValidationError:
            return None


def _device_text(value: Any) -> str:
    """String form of a device value; a ``(major, minor)`` pair becomes ``"major.minor"`` (contract format)."""
    if isinstance(value, list) and value and all(isinstance(part, int) for part in value):
        return ".".join(str(part) for part in value)
    return str(value)


class SystemService:
    def __init__(self, settings: Settings, engine: InferenceEngine, queue: JobQueue, bus: EventBus) -> None:
        self._settings = settings
        self._engine = engine
        self._queue = queue
        self._bus = bus
        self._worker: GenerationWorker | None = None
        self._started = time.monotonic()

        self._device_cache: DeviceInfo | None = None
        self._device_cache_at = -float("inf")
        self._device_task: asyncio.Future[Any] | None = None

        self._broadcast_pending = False
        self._broadcast_timer: asyncio.TimerHandle | None = None
        self._tasks: set[asyncio.Task[Any]] = set()
        self._closed = False

    def bind_worker(self, worker: GenerationWorker) -> None:
        self._worker = worker

    def mark_started(self) -> None:
        self._started = time.monotonic()

    # ------------------------------------------------------------------ health
    def health(self) -> HealthResponse:
        return HealthResponse(
            status="ok",
            app=self._settings.app_name,
            version=self._settings.app_version,
            uptime_seconds=round(time.monotonic() - self._started, 3),
            time=utcnow_iso(),
        )

    # ------------------------------------------------------------------ status
    def engine_info(self) -> EngineInfo:
        status = self._engine.status().to_dict()
        return EngineInfo(
            name=self._engine.name,
            state=status["state"],
            message=status["message"],
            load_progress=status["load_progress"],
            error=status["error"],
            loaded_at=status["loaded_at"],
            details=to_jsonable(status["details"]) or {},
            capabilities=CapabilitiesOut.model_validate(self._engine.capabilities().to_dict()),
        )

    def queue_info(self) -> QueueInfo:
        return QueueInfo(
            pending=self._queue.pending,
            running_job_id=self._worker.running_job_id if self._worker else None,
            max_size=self._queue.max_size,
        )

    def limits(self) -> LimitsInfo:
        return LimitsInfo(
            max_upload_mb=self._settings.max_upload_mb,
            allowed_mime_types=list(ALLOWED_MIME_TYPES),
            max_detail_images=self._settings.max_detail_images,
            min_image_side=self._settings.min_image_side,
            slots=[str(slot) for slot in SLOT_DISPLAY_ORDER],
        )

    def defaults(self) -> DefaultsInfo:
        defaults = GenerationSettings()
        return DefaultsInfo(
            resolution=defaults.resolution,
            seed=defaults.seed,
            randomize_seed=defaults.randomize_seed,
            polygon_target=defaults.polygon_target,
            texture_size=defaults.texture_size,
            remove_background=defaults.remove_background,
            reference_mode=self._settings.reference_mode,
        )

    async def device_info(self) -> DeviceInfo | None:
        """Engine device info with timeout + cache. Never blocks on (or submits to) the GPU worker.

        At most one ``device_info()`` call is in flight. If it outlives the timeout, the cached (possibly
        ``None``) value is served and the result is stored - and broadcast - when the call finishes.
        """
        now = time.monotonic()
        if now - self._device_cache_at < self._settings.device_info_cache_s:
            return self._device_cache
        if self._device_task is None or self._device_task.done():
            self._device_task = asyncio.ensure_future(asyncio.to_thread(self._engine.device_info))
            self._device_task.add_done_callback(self._store_device_result)
        try:
            await asyncio.wait_for(asyncio.shield(self._device_task), self._settings.device_info_timeout_s)
        except TimeoutError:
            logger.debug("device_info() is slow; serving the cached value for now")
        except Exception:  # noqa: BLE001 - logged by _store_device_result
            pass
        return self._device_cache

    def _store_device_result(self, task: asyncio.Future[Any]) -> None:
        """Done-callback of the in-flight ``device_info()`` call (event loop thread)."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.warning("device_info() failed: %s", exc)
        first_result = self._device_cache is None
        self._device_cache = None if exc is not None else _parse_device(task.result())
        self._device_cache_at = time.monotonic()
        if first_result and self._device_cache is not None:
            self.notify_changed()  # let WebSocket clients see the device as soon as it is known

    async def status(self) -> SystemStatus:
        device = await self.device_info()  # may await; everything below is a fresh, synchronous snapshot
        return SystemStatus(
            app=AppInfo(
                name=self._settings.app_name,
                version=self._settings.app_version,
                environment=self._settings.environment,
            ),
            engine=self.engine_info(),
            device=device,
            queue=self.queue_info(),
            limits=self.limits(),
            defaults=self.defaults(),
            auth_required=self._settings.auth_required,
        )

    # ------------------------------------------------------------------ engine control
    async def load_engine(self) -> SystemStatus:
        assert self._worker is not None
        self._worker.request_engine_load()
        return await self.status()

    async def unload_engine(self) -> SystemStatus:
        assert self._worker is not None
        await self._worker.unload_engine()
        self._device_cache_at = -float("inf")
        return await self.status()

    # ------------------------------------------------------------------ broadcasting
    def notify_changed(self) -> None:
        """Schedule a (debounced) ``system.updated`` broadcast. Thread-safe."""
        self._bus.call_soon_threadsafe(self._schedule_broadcast)

    def _schedule_broadcast(self) -> None:
        if self._broadcast_pending or self._closed:
            return
        loop = self._bus.loop
        if loop is None or loop.is_closed():
            return
        self._broadcast_pending = True
        self._broadcast_timer = loop.call_later(_BROADCAST_DEBOUNCE_S, self._start_broadcast)

    def _start_broadcast(self) -> None:
        self._broadcast_timer = None
        if self._closed:
            self._broadcast_pending = False
            return
        task = asyncio.ensure_future(self._broadcast())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _broadcast(self) -> None:
        self._broadcast_pending = False
        try:
            status = await self.status()
        except Exception:  # noqa: BLE001
            logger.exception("Could not build system status for broadcast")
            return
        self._bus.publish(Event(type=str(EventType.SYSTEM_UPDATED), data=status.model_dump(mode="json")))

    async def aclose(self) -> None:
        """Stop broadcasting (shutdown): cancel the pending debounce timer and in-flight broadcasts."""
        self._closed = True
        if self._broadcast_timer is not None:
            self._broadcast_timer.cancel()
            self._broadcast_timer = None
        if self._device_task is not None and not self._device_task.done():
            self._device_task.cancel()  # the worker thread finishes on its own; nothing awaits the result
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
