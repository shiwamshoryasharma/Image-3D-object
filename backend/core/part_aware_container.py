"""Composition of the part-aware layer (part-aware DESIGN.md sections 1.2 S-b, 4.3 and 11).

Written once in slice S1 and **not edited by later slices**: whatever a later slice adds is resolved lazily by
dotted path, so an unmerged module degrades honestly instead of breaking startup:

* run executors (:data:`EXECUTOR_PATHS`, built as ``Executor(ExecutorDeps)``): a missing module makes that run kind
  report ``not_implemented`` (:class:`~pipelines.runs.executor.ExecutorTable`);
* the model registry (:data:`REGISTRY_PATH`, :data:`CATALOG_PATH`; S2): built as
  ``ProviderRegistry(settings, catalog, engine=, overrides=, on_change=)``; without it every model is unavailable and
  GPU phases fail with a clear error;
* the run retention service (:data:`RETENTION_PATH`; S8): built as
  ``RunRetentionService(settings=, layout=, stores=, workflow_store=)`` and started with
  ``run_forever(settings.retention_interval_s)``.

The services are S1 classes with frozen constructors (stubs until their slice merges). Lifecycle:

* :meth:`PartAwareContainer.start` (after the tuning worker starts): loads the run and workflow stores and marks runs
  left ``queued``/``running`` as ``interrupted``; with ``part_aware_enabled=true`` it then creates the global run
  folders and starts the :class:`~workers.run_worker.RunWorker`, the workflow service (subscription + sweep), the
  retention task and the engine-change watcher of ``features.updated``. With ``part_aware_enabled=false`` only the
  stores load, so history stays readable and nothing new is written.
* :meth:`PartAwareContainer.stop` (**before** the generation worker stops): stops the workflow service, cancels the
  active run, awaits its GPU task (at most ``timeout``) and fails queued runs as ``interrupted``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final

from config.settings import Settings
from core.events import Event, EventBus, Subscription
from inference.base import InferenceEngine
from models.image_generation import ImageGenerationRun
from models.part_analysis import PartAnalysisRun
from models.runs import RunKind
from models.segmentation import SegmentationRun
from models.workflow import WorkflowRecord
from pipelines.runs.executor import DottedPathMissing, ExecutorDeps, ExecutorTable, import_dotted
from schemas.events import EventType
from schemas.image_generation import ImageGenerationOut
from schemas.parts import PartAnalysisOut
from schemas.segmentation import SegmentationOut
from services.feature_service import FeatureService
from services.image_generation_service import ImageGenerationService
from services.job_service import JobService
from services.model_registry_service import ModelRegistryService
from services.part_analysis_service import PartAnalysisService
from services.part_files import PartFiles
from services.reference_service import ReferenceService
from services.result_service import ResultService
from services.run_events import WORKFLOW_PROGRESS_INTERVAL_S, FeaturesEventPublisher, RunEventPublisher
from services.segmentation_service import SegmentationService
from services.workflow_service import WorkflowService
from storage.job_store import JobStore
from storage.paths import RuntimePaths
from storage.reference_store import ReferenceStore
from storage.run_layout import RunLayout
from storage.run_store import RunStore
from storage.workflow_store import WorkflowStore
from workers.queue import JobQueue
from workers.run_worker import GpuTaskSubmitter, RunWorker

if TYPE_CHECKING:  # pragma: no cover
    from inference.providers.base import ModelProvider, ModelSpec

logger = logging.getLogger("img3d.part_aware")

EXECUTOR_PATHS: Final[Mapping[RunKind, str]] = MappingProxyType(
    {
        "image_generation": "pipelines.image_generation:ImageGenerationExecutor",
        "segmentation": "pipelines.segmentation:SegmentationExecutor",
        "part_analysis": "pipelines.part_analysis:PartAnalysisExecutor",
    }
)
REGISTRY_PATH: Final = "inference.providers.registry:ProviderRegistry"
CATALOG_PATH: Final = "inference.providers.catalog:CATALOG"
RETENTION_PATH: Final = "services.run_retention_service:RunRetentionService"


@dataclass(frozen=True, slots=True)
class PartProviderOverrides:
    """Test doubles injected through ``create_app(..., part_providers=...)`` (the application never sets them).

    ``providers`` and ``catalog`` are handed to the registry (fake providers by model id, a replacement catalog);
    ``registry`` replaces the registry as a whole; ``executors`` replace run executors per kind.
    """

    providers: Mapping[str, ModelProvider] = field(default_factory=dict)
    catalog: Sequence[ModelSpec] | None = None
    registry: Any | None = None
    executors: Mapping[RunKind, Any] = field(default_factory=dict)


@dataclass(eq=False)
class PartAwareContainer:
    settings: Settings
    layout: RunLayout
    registry: Any | None
    """``inference.providers.registry.ProviderRegistry`` (``None`` when that module is not part of the build)."""
    executors: ExecutorTable
    stores: Mapping[RunKind, RunStore[Any]]
    workflow_store: WorkflowStore
    run_queue: JobQueue
    run_worker: RunWorker
    publishers: Mapping[RunKind, RunEventPublisher[Any]]
    workflow_events: RunEventPublisher[WorkflowRecord]
    features_events: FeaturesEventPublisher
    feature_service: FeatureService
    model_registry_service: ModelRegistryService
    image_generation_service: ImageGenerationService
    segmentation_service: SegmentationService
    part_analysis_service: PartAnalysisService
    part_files: PartFiles
    workflow_service: WorkflowService
    retention: Any | None
    """``services.run_retention_service.RunRetentionService`` (``None`` until S8 merges)."""
    bus: EventBus
    background_tasks: set[asyncio.Task[Any]] = field(default_factory=set)
    started: bool = False
    _engine_watch: Subscription | None = field(default=None, init=False, repr=False)

    @property
    def enabled(self) -> bool:
        return self.settings.part_aware_enabled

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        if self.started:
            return
        settings = self.settings
        logger.info("Part-aware configuration: %s", settings.part_aware_summary())
        for kind, store in self.stores.items():
            store.load()
            recovered = store.recover_interrupted()
            if recovered:
                logger.warning("Marked %d interrupted %s run(s) as failed", len(recovered), kind.replace("_", " "))
        self.workflow_store.load()
        interrupted = self.workflow_store.recover_interrupted()
        if interrupted:
            logger.warning("Marked %d interrupted workflow(s) as failed", len(interrupted))
        self.started = True
        if not self.enabled:
            logger.info("Part-aware layer disabled (IMG3D_PART_AWARE_ENABLED=false): history is readable, nothing runs")
            return

        self.layout.ensure()
        self.run_worker.start()
        await self.workflow_service.start()
        background: list[tuple[Coroutine[Any, Any, Any], str]] = [
            (self._watch_engine_changes(), "features-engine-watch"),
        ]
        if self.retention is not None:
            background.append((self.retention.run_forever(settings.retention_interval_s), "run-retention"))
        for coro, name in background:
            task = asyncio.create_task(coro, name=name)
            self.background_tasks.add(task)
            task.add_done_callback(self.background_tasks.discard)
        logger.info(
            "Part-aware layer ready (executors: %s; registry: %s)",
            ", ".join(f"{kind}={self.executors.availability(kind).code}" for kind in EXECUTOR_PATHS),
            "available" if self.registry is not None else "not part of this build",
        )

    async def stop(self, timeout: float) -> None:
        """Shut the layer down; must run before ``GenerationWorker.stop`` (no orphaned auxiliary GPU task)."""
        if self._engine_watch is not None:
            self._engine_watch.close()
        for task in list(self.background_tasks):
            task.cancel()
        if self.background_tasks:
            await asyncio.gather(*self.background_tasks, return_exceptions=True)
        steps: tuple[tuple[str, Callable[[], Coroutine[Any, Any, None]]], ...] = (
            ("workflow service", lambda: self.workflow_service.stop(timeout)),
            ("model registry service", lambda: self.model_registry_service.stop(timeout)),
            ("run worker", lambda: self.run_worker.stop(timeout)),
        )
        for label, stop in steps:
            try:
                await stop()
            except Exception:  # noqa: BLE001 - keep shutting down
                logger.exception("Error while stopping the %s", label)
        self.started = False

    async def _watch_engine_changes(self) -> None:
        """``system.updated`` (engine state, queue) -> debounced ``features.updated``."""
        subscription = self.bus.subscribe(lambda event: event.type == str(EventType.SYSTEM_UPDATED))
        self._engine_watch = subscription
        try:
            while True:
                event: Event | None = await subscription.get()
                if event is None:
                    return
                self.features_events.notify()
        finally:
            subscription.close()


def build_part_aware(
    settings: Settings,
    *,
    paths: RuntimePaths,
    bus: EventBus,
    engine: InferenceEngine,
    gpu: GpuTaskSubmitter,
    job_service: JobService,
    job_store: JobStore,
    job_queue: JobQueue,
    reference_service: ReferenceService,
    reference_store: ReferenceStore,
    result_service: ResultService,
    overrides: PartProviderOverrides | None = None,
) -> PartAwareContainer:
    """Create every part-aware component (nothing runs here; :meth:`PartAwareContainer.start` starts them)."""
    overrides = overrides or PartProviderOverrides()
    layout = RunLayout(paths)
    features_events = FeaturesEventPublisher(bus)
    registry = _build_registry(settings, engine, overrides, on_change=features_events.notify)

    def job_exists(job_id: str) -> bool:
        return job_id in job_store.ids()

    image_generation_store: RunStore[ImageGenerationRun] = RunStore(
        layout, kind="image_generation", record_type=ImageGenerationRun
    )
    segmentation_store: RunStore[SegmentationRun] = RunStore(layout, kind="segmentation", record_type=SegmentationRun)
    part_analysis_store: RunStore[PartAnalysisRun] = RunStore(
        layout, kind="part_analysis", record_type=PartAnalysisRun, job_exists=job_exists
    )
    stores: dict[RunKind, RunStore[Any]] = {
        "image_generation": image_generation_store,
        "segmentation": segmentation_store,
        "part_analysis": part_analysis_store,
    }
    workflow_store = WorkflowStore(paths)

    publishers: dict[RunKind, RunEventPublisher[Any]] = {
        "image_generation": RunEventPublisher(
            bus, event_type=EventType.IMAGE_GENERATION_UPDATED, serialize=ImageGenerationOut.from_run
        ),
        "segmentation": RunEventPublisher(
            bus, event_type=EventType.SEGMENTATION_UPDATED, serialize=SegmentationOut.from_run
        ),
        "part_analysis": RunEventPublisher(
            bus, event_type=EventType.PART_ANALYSIS_UPDATED, serialize=PartAnalysisOut.from_run
        ),
    }

    executors = ExecutorTable(
        EXECUTOR_PATHS,
        ExecutorDeps(
            settings=settings,
            layout=layout,
            registry=registry,
            job_store=job_store,
            reference_store=reference_store,
            stores=MappingProxyType(stores),
        ),
        overrides=overrides.executors,
    )
    run_queue = JobQueue(settings.run_queue_max_size)
    run_worker = RunWorker(
        settings=settings,
        gpu=gpu,
        queue=run_queue,
        job_queue=job_queue,
        executors=executors,
        stores=stores,
        publishers=publishers,
        registry=registry,
        bus=bus,
        paths=paths,
        on_queue_changed=features_events.notify,
    )

    feature_service = FeatureService(
        settings=settings,
        engine=engine,
        registry=registry,
        executors=executors,
        runs=run_worker,
        bus=bus,
        events=features_events,
    )
    features_events.bind(feature_service.features)
    model_registry_service = ModelRegistryService(
        settings=settings, registry=registry, gpu=gpu, job_queue=job_queue, runs=run_worker, events=features_events
    )
    image_generation_service = ImageGenerationService(
        settings=settings,
        store=image_generation_store,
        runs=run_worker,
        registry=registry,
        references=reference_service,
        events=publishers["image_generation"],
        layout=layout,
        features=feature_service,
    )
    segmentation_service = SegmentationService(
        settings=settings,
        store=segmentation_store,
        runs=run_worker,
        registry=registry,
        references=reference_service,
        jobs=job_service,
        image_generations=image_generation_store,
        events=publishers["segmentation"],
        layout=layout,
        features=feature_service,
    )
    part_files = PartFiles(settings=settings, layout=layout, store=part_analysis_store)
    part_analysis_service = PartAnalysisService(
        settings=settings,
        store=part_analysis_store,
        runs=run_worker,
        registry=registry,
        jobs=job_service,
        results=result_service,
        segmentations=segmentation_store,
        events=publishers["part_analysis"],
        layout=layout,
        files=part_files,
        features=feature_service,
    )

    workflow_holder: list[WorkflowService] = []

    def serialize_workflow(record: WorkflowRecord) -> Any:
        return workflow_holder[0].to_out(record)

    workflow_events: RunEventPublisher[WorkflowRecord] = RunEventPublisher(
        bus,
        event_type=EventType.WORKFLOW_UPDATED,
        serialize=serialize_workflow,
        job_id_of=lambda _record: None,
        min_interval=WORKFLOW_PROGRESS_INTERVAL_S,
    )
    workflow_service = WorkflowService(
        settings=settings,
        store=workflow_store,
        bus=bus,
        events=workflow_events,
        features=feature_service,
        jobs=job_service,
        references=reference_service,
        image_generations=image_generation_service,
        segmentations=segmentation_service,
        part_analyses=part_analysis_service,
        runs=run_worker,
        job_queue=job_queue,
        layout=layout,
    )
    workflow_holder.append(workflow_service)

    job_service.add_delete_hook(part_analysis_service.on_job_deleting)
    retention = _build_retention(settings, layout, stores, workflow_store)

    return PartAwareContainer(
        settings=settings,
        layout=layout,
        registry=registry,
        executors=executors,
        stores=MappingProxyType(stores),
        workflow_store=workflow_store,
        run_queue=run_queue,
        run_worker=run_worker,
        publishers=MappingProxyType(publishers),
        workflow_events=workflow_events,
        features_events=features_events,
        feature_service=feature_service,
        model_registry_service=model_registry_service,
        image_generation_service=image_generation_service,
        segmentation_service=segmentation_service,
        part_analysis_service=part_analysis_service,
        part_files=part_files,
        workflow_service=workflow_service,
        retention=retention,
        bus=bus,
    )


def _build_registry(
    settings: Settings,
    engine: InferenceEngine,
    overrides: PartProviderOverrides,
    *,
    on_change: Callable[[], None],
) -> Any | None:
    if overrides.registry is not None:
        return overrides.registry
    try:
        registry_cls = import_dotted(REGISTRY_PATH)
        catalog = overrides.catalog if overrides.catalog is not None else import_dotted(CATALOG_PATH)
    except DottedPathMissing as exc:
        logger.info("Model registry is not part of this build (%s missing); every model reports unavailable", exc)
        return None
    except Exception:  # noqa: BLE001 - a broken registry must not prevent image-to-3D from starting
        logger.exception("Model registry is not importable; every model reports unavailable")
        return None
    try:
        return registry_cls(settings, tuple(catalog), engine=engine, overrides=overrides, on_change=on_change)
    except Exception:  # noqa: BLE001
        logger.exception("Model registry could not be created; every model reports unavailable")
        return None


def _build_retention(
    settings: Settings,
    layout: RunLayout,
    stores: Mapping[RunKind, RunStore[Any]],
    workflow_store: WorkflowStore,
) -> Any | None:
    try:
        retention_cls = import_dotted(RETENTION_PATH)
    except DottedPathMissing:
        logger.info("Run retention is not part of this build; part-aware runs are kept until deleted")
        return None
    except Exception:  # noqa: BLE001
        logger.exception("Run retention service is not importable")
        return None
    try:
        return retention_cls(
            settings=settings, layout=layout, stores=MappingProxyType(dict(stores)), workflow_store=workflow_store
        )
    except Exception:  # noqa: BLE001
        logger.exception("Run retention service could not be created")
        return None
