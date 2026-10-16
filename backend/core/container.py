"""Composition root: builds and wires every long-lived component of the application."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from config.settings import Settings
from core.events import EventBus
from core.part_aware_container import PartAwareContainer, PartProviderOverrides, build_part_aware
from inference.base import InferenceEngine
from inference.registry import create_engine
from launcher import BlenderProcessRunner
from pipelines.image_to_3d import ImageTo3DPipeline
from services.auth_service import AuthService
from services.blender_runtime_service import BlenderRuntimeService, RuntimeManagerFactory
from services.job_events import JobEventPublisher
from services.job_service import JobService
from services.mesh_tuner_service import MeshTunerService
from services.reference_service import ReferenceService
from services.result_service import ResultService
from services.retention_service import RetentionService
from services.system_service import SystemService
from storage.job_store import JobStore
from storage.paths import RuntimePaths
from storage.reference_store import ReferenceStore
from storage.tuning_store import TuningStore
from workers.generation_worker import GenerationWorker
from workers.queue import JobQueue
from workers.tuning_worker import TuningEventPublisher, TuningWorker

if TYPE_CHECKING:  # pragma: no cover
    from api.websocket_hub import WebSocketHub


@dataclass(eq=False)
class ServiceContainer:
    settings: Settings
    paths: RuntimePaths
    bus: EventBus
    engine: InferenceEngine
    reference_store: ReferenceStore
    job_store: JobStore
    queue: JobQueue
    pipeline: ImageTo3DPipeline
    worker: GenerationWorker
    auth_service: AuthService
    reference_service: ReferenceService
    job_service: JobService
    result_service: ResultService
    system_service: SystemService
    retention_service: RetentionService
    blender_runtime: BlenderRuntimeService
    tuning_store: TuningStore
    tuning_queue: JobQueue
    tuning_worker: TuningWorker
    mesh_tuner_service: MeshTunerService
    ws_hub: WebSocketHub
    background_tasks: set[asyncio.Task[Any]] = field(default_factory=set)
    started: bool = False
    part_aware: PartAwareContainer | None = None


def build_container(
    settings: Settings,
    *,
    engine: InferenceEngine | None = None,
    blender_runtime_factory: RuntimeManagerFactory | None = None,
    blender_runner: BlenderProcessRunner | None = None,
    part_providers: PartProviderOverrides | None = None,
) -> ServiceContainer:
    """Create every component (nothing is executed here: Blender detection runs as a startup background task).

    ``engine``, ``blender_runtime_factory`` (``factory(launcher_settings, on_change) -> BlenderRuntimeManager``),
    ``blender_runner`` (tuning process runner) and ``part_providers`` (part-aware provider/executor doubles) may be
    injected (tests); otherwise the real ones are used.
    """
    from api.websocket_hub import WebSocketHub  # api layer depends on services, not the other way round

    paths = settings.paths
    bus = EventBus()
    engine = engine if engine is not None else create_engine(settings)
    reference_store = ReferenceStore(paths)
    job_store = JobStore(paths)
    queue = JobQueue(settings.max_queue_size)
    events = JobEventPublisher(bus, queue)
    pipeline = ImageTo3DPipeline(engine, job_store, settings)

    system_service = SystemService(settings, engine, queue, bus)
    worker = GenerationWorker(
        engine=engine,
        pipeline=pipeline,
        job_store=job_store,
        queue=queue,
        events=events,
        bus=bus,
        on_system_changed=system_service.notify_changed,
    )
    system_service.bind_worker(worker)

    auth_service = AuthService(settings)
    reference_service = ReferenceService(settings, reference_store)
    job_service = JobService(
        settings=settings,
        engine=engine,
        job_store=job_store,
        references=reference_service,
        queue=queue,
        worker=worker,
        events=events,
        on_system_changed=system_service.notify_changed,
    )
    result_service = ResultService(settings, job_service, job_store)
    retention_service = RetentionService(settings, job_store, reference_store, reference_service)

    # Auto Mesh Tuner: Blender runtime + tuning runs (own queue and single "blender-worker" thread).
    blender_runtime = BlenderRuntimeService(
        settings, bus, manager_factory=blender_runtime_factory, runner=blender_runner
    )

    def job_exists(job_id: str) -> bool:
        return job_id in job_store.ids()

    tuning_store = TuningStore(paths, job_exists=job_exists)
    tuning_queue = JobQueue(settings.blender_max_queue_size)
    tuning_events = TuningEventPublisher(bus)
    tuning_worker = TuningWorker(
        settings=settings,
        paths=paths,
        tuning_store=tuning_store,
        queue=tuning_queue,
        runtime=blender_runtime,
        runner=lambda: blender_runtime.runner,
        events=tuning_events,
        bus=bus,
        job_exists=job_exists,
        job_model_path=job_store.model_path,
        user_resources_dir=blender_runtime.launcher_settings.user_resources_dir,
    )
    mesh_tuner_service = MeshTunerService(
        settings=settings,
        runtime=blender_runtime,
        job_service=job_service,
        result_service=result_service,
        tuning_store=tuning_store,
        queue=tuning_queue,
        worker=tuning_worker,
        events=tuning_events,
    )
    job_service.add_delete_hook(mesh_tuner_service.on_job_deleting)

    ws_hub = WebSocketHub(settings, bus, system_service, auth_service)

    # Part-aware layer: separate run resources that only read finished jobs (part-aware DESIGN.md 1.2 S-b).
    part_aware = build_part_aware(
        settings,
        paths=paths,
        bus=bus,
        engine=engine,
        gpu=worker,
        job_service=job_service,
        job_store=job_store,
        job_queue=queue,
        reference_service=reference_service,
        reference_store=reference_store,
        result_service=result_service,
        overrides=part_providers,
    )

    return ServiceContainer(
        settings=settings,
        paths=paths,
        bus=bus,
        engine=engine,
        reference_store=reference_store,
        job_store=job_store,
        queue=queue,
        pipeline=pipeline,
        worker=worker,
        auth_service=auth_service,
        reference_service=reference_service,
        job_service=job_service,
        result_service=result_service,
        system_service=system_service,
        retention_service=retention_service,
        blender_runtime=blender_runtime,
        tuning_store=tuning_store,
        tuning_queue=tuning_queue,
        tuning_worker=tuning_worker,
        mesh_tuner_service=mesh_tuner_service,
        ws_hub=ws_hub,
        part_aware=part_aware,
    )
