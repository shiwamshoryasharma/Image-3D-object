"""Application startup/shutdown (FastAPI lifespan, contract section 9)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

from fastapi import FastAPI

from core.container import ServiceContainer
from core.logging import flush_logging
from processing.image_validation import configure_pillow

logger = logging.getLogger("img3d.lifecycle")


async def startup(container: ServiceContainer) -> None:
    settings = container.settings
    logger.info("Starting %s %s (%s)", settings.app_name, settings.app_version, settings.environment)
    logger.info("Configuration: %s", settings.safe_summary())

    container.paths.ensure()
    purged = container.paths.purge_temp()
    if purged:
        logger.info("Purged %d temp entr%s", purged, "y" if purged == 1 else "ies")
    configure_pillow(settings.max_image_pixels)

    container.reference_store.load()
    container.job_store.load()
    recovered = container.job_store.recover_interrupted()
    if recovered:
        logger.warning("Marked %d interrupted job(s) as failed", len(recovered))
    container.tuning_store.load()
    interrupted_tunings = container.tuning_store.recover_interrupted()
    if interrupted_tunings:
        logger.warning("Marked %d interrupted tuning run(s) as failed", len(interrupted_tunings))

    container.bus.bind_loop(asyncio.get_running_loop())
    container.system_service.mark_started()
    container.worker.start()
    container.tuning_worker.start()
    if container.part_aware:
        await container.part_aware.start()

    engine_status = container.engine.status()
    logger.info("Inference engine '%s' state: %s", container.engine.name, engine_status.state)
    if engine_status.error:
        logger.error("Inference engine error: %s", engine_status.error)
    if settings.preload_model:
        container.worker.request_engine_load()
    else:
        logger.info("Model preload disabled: the model loads on the first job or POST /api/v1/system/engine/load")

    background: list[tuple[Coroutine[Any, Any, Any], str]] = [
        (container.retention_service.run_forever(settings.retention_interval_s), "retention-cleanup"),
        # Warm the device-info cache (the first call may import torch) so /system answers quickly.
        (container.system_service.device_info(), "device-info-prefetch"),
    ]
    if settings.blender_enabled and settings.blender_detect_on_startup:
        # Executes every Blender candidate (`--version`); never blocks the API.
        background.append((container.blender_runtime.startup_detect(), "blender-detect-startup"))
    elif not settings.blender_enabled:
        logger.info("Auto Mesh Tuner disabled (IMG3D_BLENDER_ENABLED=false)")
    for coro, name in background:
        task = asyncio.create_task(coro, name=name)
        container.background_tasks.add(task)
        task.add_done_callback(container.background_tasks.discard)
    container.started = True
    logger.info("%s is ready (API available while the model loads)", settings.app_name)


async def shutdown(container: ServiceContainer) -> None:
    settings = container.settings
    logger.info("Shutting down %s", settings.app_name)
    container.started = False

    for task in list(container.background_tasks):
        task.cancel()
    if container.background_tasks:
        await asyncio.gather(*container.background_tasks, return_exceptions=True)

    if container.part_aware:
        try:
            # Before the generation worker stops: cancels the active run and awaits its GPU task.
            await container.part_aware.stop(timeout=settings.shutdown_timeout_s)
        except Exception:  # noqa: BLE001 - keep shutting down
            logger.exception("Error while stopping the part-aware layer")

    try:
        await container.worker.stop(timeout=settings.shutdown_timeout_s)
    except Exception:  # noqa: BLE001 - keep shutting down
        logger.exception("Error while stopping the generation worker")

    try:
        # Kills a running Blender process tree (Job Object) and fails queued tuning runs as interrupted.
        await container.tuning_worker.stop(timeout=settings.shutdown_timeout_s)
    except Exception:  # noqa: BLE001
        logger.exception("Error while stopping the tuning worker")

    try:
        await container.blender_runtime.aclose()  # cancels a portable Blender download (resumable .part kept)
    except Exception:  # noqa: BLE001
        logger.exception("Error while closing the Blender runtime")

    try:
        await container.ws_hub.close_all()
    except Exception:  # noqa: BLE001
        logger.exception("Error while closing WebSocket connections")

    try:
        await container.worker.shutdown_engine(timeout=settings.shutdown_timeout_s)
    except Exception:  # noqa: BLE001
        logger.exception("Error while unloading the inference engine")

    await container.system_service.aclose()
    container.bus.close()
    logger.info("Shutdown complete")
    flush_logging()


def build_lifespan(container: ServiceContainer) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await startup(container)
        try:
            yield
        finally:
            await shutdown(container)

    return lifespan
