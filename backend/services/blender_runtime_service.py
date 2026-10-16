"""Blender runtime service: async facade over :class:`launcher.BlenderRuntimeManager` (DESIGN.md sections 2 and 6).

* Every blocking manager call (detection executes ``blender --version``, selection probes unknown executables,
  release resolution hits the network) runs in a worker thread, never on the event loop.
* Launcher errors are mapped to :class:`~core.exceptions.AppError` subclasses carrying the DESIGN error codes and
  HTTP status codes (404 not found, 422 incompatible, 503 unavailable, 403 downloads disabled, 409 download in
  progress, 502 release/download failures).
* Every manager state change is broadcast as ``blender.updated`` via :meth:`EventBus.publish_threadsafe`
  (download-progress frames are droppable; state changes are not).
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from typing import Any, Final

from config.settings import Settings
from core.events import Event, EventBus
from core.exceptions import AppError, ValidationAppError
from launcher import (
    BlenderIncompatibleError,
    BlenderInstallation,
    BlenderNotFoundError,
    BlenderProcessRunner,
    BlenderRuntimeManager,
    BlenderUnavailableError,
    DownloadCancelledError,
    DownloadError,
    DownloadInProgressError,
    DownloadsDisabledError,
    LauncherError,
    LauncherSettings,
    PathTooLongError,
    ReleaseResolveError,
    RuntimeMode,
    RuntimeStatus,
)
from schemas.events import EventType
from schemas.mesh_tuner import BlenderReleaseOut, BlenderRuntimeOut

logger = logging.getLogger(__name__)

DISABLED_MESSAGE: Final = "Auto Mesh Tuner is disabled by configuration (IMG3D_BLENDER_ENABLED=false)."
CLOSE_TIMEOUT_S: Final = 5.0

RuntimeManagerFactory = Callable[[LauncherSettings, Callable[[RuntimeStatus], None]], BlenderRuntimeManager]
"""``factory(launcher_settings, on_change) -> manager`` (tests inject managers with fake detectors/downloaders)."""


# --------------------------------------------------------------------------------------------------
# Errors (HTTP mapping of launcher errors)
# --------------------------------------------------------------------------------------------------


class BlenderRuntimeAppError(AppError):
    status_code = 500
    code = "blender_error"


class BlenderNotFoundAppError(BlenderRuntimeAppError):
    status_code = 404
    code = "blender_not_found"


class BlenderIncompatibleAppError(BlenderRuntimeAppError):
    status_code = 422
    code = "blender_incompatible"


class BlenderUnavailableAppError(BlenderRuntimeAppError):
    """No usable Blender runtime; ``message`` is the user prompt (e.g. offer a download)."""

    status_code = 503
    code = "blender_unavailable"


class BlenderDownloadDisabledAppError(BlenderRuntimeAppError):
    status_code = 403
    code = "blender_download_disabled"


class BlenderDownloadInProgressAppError(BlenderRuntimeAppError):
    status_code = 409
    code = "blender_download_in_progress"


class BlenderDownloadCancelledAppError(BlenderRuntimeAppError):
    status_code = 409
    code = "blender_download_cancelled"


class BlenderReleaseUnresolvedAppError(BlenderRuntimeAppError):
    status_code = 502
    code = "blender_release_unresolved"


class BlenderDownloadFailedAppError(BlenderRuntimeAppError):
    status_code = 502
    code = "blender_download_failed"


class BlenderPathTooLongAppError(BlenderRuntimeAppError):
    status_code = 422
    code = "blender_path_too_long"


_ERROR_MAP: Final[tuple[tuple[type[LauncherError], type[BlenderRuntimeAppError]], ...]] = (
    # most specific first (ReleaseResolveError is a DownloadError)
    (BlenderNotFoundError, BlenderNotFoundAppError),
    (BlenderIncompatibleError, BlenderIncompatibleAppError),
    (BlenderUnavailableError, BlenderUnavailableAppError),
    (DownloadsDisabledError, BlenderDownloadDisabledAppError),
    (DownloadInProgressError, BlenderDownloadInProgressAppError),
    (DownloadCancelledError, BlenderDownloadCancelledAppError),
    (ReleaseResolveError, BlenderReleaseUnresolvedAppError),
    (DownloadError, BlenderDownloadFailedAppError),
    (PathTooLongError, BlenderPathTooLongAppError),
)


def map_launcher_error(exc: LauncherError) -> AppError:
    """The :class:`AppError` for a launcher error (keeps the launcher's own ``code``, e.g. checksum mismatch)."""
    for launcher_type, app_type in _ERROR_MAP:
        if isinstance(exc, launcher_type):
            return app_type(exc.message, code=exc.code, details=exc.details)
    return ValidationAppError(exc.message, code=exc.code, details=exc.details)  # e.g. blender_invalid_mode


# --------------------------------------------------------------------------------------------------
# Construction helpers
# --------------------------------------------------------------------------------------------------


def build_launcher_settings(settings: Settings) -> LauncherSettings:
    """Launcher configuration derived from the ``IMG3D_BLENDER_*`` settings."""
    return LauncherSettings(
        runtime_root=settings.blender_runtime_dir,
        temp_root=settings.blender_temp_dir,
        min_version=settings.blender_min_version,  # type: ignore[arg-type] - parsed in __post_init__
        override_executable=settings.blender_executable,
        extra_search_dirs=list(settings.blender_search_dirs),
        allow_download=settings.blender_allow_download,
        download_base_url=settings.blender_download_base_url,
        download_channel=settings.blender_download_channel,
        detect_timeout_s=settings.blender_detect_timeout_s,
    )


def default_manager_factory(
    launcher_settings: LauncherSettings, on_change: Callable[[RuntimeStatus], None]
) -> BlenderRuntimeManager:
    return BlenderRuntimeManager(launcher_settings, on_change=on_change)


def runtime_out(status: RuntimeStatus) -> dict[str, Any]:
    """``BlenderRuntimeOut`` JSON (identical for REST and ``blender.updated``)."""
    return BlenderRuntimeOut.model_validate(status.to_dict()).model_dump(mode="json")


# --------------------------------------------------------------------------------------------------
# Service
# --------------------------------------------------------------------------------------------------


class BlenderRuntimeService:
    def __init__(
        self,
        settings: Settings,
        bus: EventBus,
        *,
        manager_factory: RuntimeManagerFactory | None = None,
        runner: BlenderProcessRunner | None = None,
    ) -> None:
        self._settings = settings
        self._bus = bus
        self._launcher_settings = build_launcher_settings(settings)
        self._publish_lock = threading.Lock()
        self._last_published_key: tuple[Any, ...] | None = None
        factory = manager_factory or default_manager_factory
        self._manager = factory(self._launcher_settings, self._on_change)
        self._runner = runner
        self._scan_task: asyncio.Task[None] | None = None
        self._closed = False

    # ------------------------------------------------------------------ accessors
    @property
    def enabled(self) -> bool:
        return self._settings.blender_enabled

    @property
    def manager(self) -> BlenderRuntimeManager:
        return self._manager

    @property
    def launcher_settings(self) -> LauncherSettings:
        return self._launcher_settings

    @property
    def runner(self) -> BlenderProcessRunner:
        """The process runner used for tuning runs (injected for tests, else the manager's)."""
        return self._runner if self._runner is not None else self._manager.runner

    # ------------------------------------------------------------------ status
    def snapshot(self) -> dict[str, Any]:
        """Current ``BlenderRuntimeOut`` (cheap; no detection)."""
        return self._to_out(self._manager.status())

    async def status(self) -> dict[str, Any]:
        """Current status. The first call after startup without a scan starts detection in the background."""
        if self.enabled and not self._closed:
            current = self._manager.status()
            if current.last_scan_at is None and current.state == "detecting" and not self._scan_running():
                self._start_background_scan()
        return self.snapshot()

    async def detect(self, *, force: bool = True) -> dict[str, Any]:
        """Re-scan installations (blocking detection in a thread)."""
        self._require_enabled()
        status = await self._call(self._manager.scan, force=force)
        return self._to_out(status)

    async def startup_detect(self) -> None:
        """Background detection at startup (never raises; the API is available meanwhile)."""
        if not self.enabled:
            return
        try:
            task = self._start_background_scan()
            await asyncio.shield(task)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - detection failure is reported through the status
            logger.exception("Blender detection at startup failed")

    async def select(self, mode: RuntimeMode, executable: str | None = None) -> dict[str, Any]:
        self._require_enabled()
        exe = executable.strip() if executable and executable.strip() else None
        status = await self._call(self._manager.select, mode, exe)
        return self._to_out(status)

    async def resolve_release(self, *, force: bool = True) -> dict[str, Any]:
        """Official latest release for this machine (network)."""
        self._require_enabled()
        release = await self._call(self._manager.resolve_release, force=force)
        return BlenderReleaseOut.model_validate(release.to_dict()).model_dump(mode="json")

    async def start_download(self, *, confirm: bool, version: str | None = None) -> dict[str, Any]:
        """Start the confirmed portable Blender download (background thread)."""
        self._require_enabled()
        if confirm is not True:
            raise ValidationAppError(
                "Downloading Blender requires explicit confirmation (confirm: true)",
                code="confirmation_required",
            )
        status = await self._call(self._manager.start_download, version=version)
        return self._to_out(status)

    async def cancel_download(self) -> dict[str, Any]:
        self._require_enabled()
        status = await self._call(self._manager.cancel_download)
        return self._to_out(status)

    async def require_runtime(self) -> BlenderInstallation:
        """The selected compatible installation or 503 ``blender_unavailable`` carrying the user prompt."""
        self._require_enabled()
        return await self._call(self._manager.require_runtime)

    # ------------------------------------------------------------------ lifecycle
    async def aclose(self) -> None:
        """Cancel a running download and wait (bounded) for its thread."""
        self._closed = True
        task = self._scan_task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        try:
            await asyncio.to_thread(self._manager.close, CLOSE_TIMEOUT_S)
        except Exception:  # noqa: BLE001
            logger.exception("Error while closing the Blender runtime manager")

    # ------------------------------------------------------------------ internals
    def _require_enabled(self) -> None:
        if not self.enabled:
            raise BlenderUnavailableAppError(DISABLED_MESSAGE)

    async def _call(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        try:
            return await asyncio.to_thread(fn, *args, **kwargs)
        except LauncherError as exc:
            raise map_launcher_error(exc) from exc

    def _scan_running(self) -> bool:
        return self._scan_task is not None and not self._scan_task.done()

    def _start_background_scan(self) -> asyncio.Task[None]:
        if self._scan_task is not None and not self._scan_task.done():
            return self._scan_task

        async def _scan() -> None:
            try:
                await asyncio.to_thread(self._manager.scan)
            except Exception:  # noqa: BLE001 - the manager reports failures through its status
                logger.exception("Background Blender detection failed")

        self._scan_task = asyncio.create_task(_scan(), name="blender-detect")
        return self._scan_task

    def _to_out(self, status: RuntimeStatus) -> dict[str, Any]:
        data = runtime_out(status)
        if not self.enabled:
            data.update(state="error", error=DISABLED_MESSAGE, message=DISABLED_MESSAGE, can_download=False)
        return data

    def _on_change(self, status: RuntimeStatus) -> None:
        """Manager listener (any thread): broadcast ``blender.updated``."""
        try:
            data = self._to_out(status)
        except Exception:  # noqa: BLE001 - a listener must never break the manager
            logger.exception("Could not serialize the Blender runtime status")
            return
        download = data.get("download") or {}
        key = (data["state"], download.get("state"), (data.get("selected") or {}).get("id"), data.get("mode"))
        active = download.get("state") in ("resolving", "downloading", "verifying", "extracting", "validating")
        with self._publish_lock:
            droppable = active and key == self._last_published_key
            self._last_published_key = key
        self._bus.publish_threadsafe(Event(type=str(EventType.BLENDER_UPDATED), data=data, droppable=droppable))
