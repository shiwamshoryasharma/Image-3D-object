"""Capability discovery: ``GET /features`` and the ``features.updated`` payload (part-aware DESIGN.md section 6.1).

Torch-free. Every capability entry (pipeline, profile, strategy, node) carries ``available``, ``code`` (a
``ReasonCode``) and ``reason`` (``None`` when available); the first blocking condition decides the code:

* ``image_to_3d``: the engine state is not ``error``/``unavailable`` (else ``engine_unavailable``);
* ``prompt_to_3d``: the image-generation flag, the image-generation executor (``not_implemented`` until it merges), a
  usable text-to-image model (registry rules, static VRAM check included) and ``image_to_3d``. It does not depend on
  the workflows flag (the review-first flow needs no workflow);
* ``segmentation``: the flag, the executor and at least one usable profile. ``baseline`` needs a usable detector and
  refiner; ``anime``/``realistic`` need their parser plus the baseline detector and refiner;
* ``part_extraction``: the part-analysis flag and the executor. ``components`` needs nothing else; the semantic
  strategies also need the baseline segmentation models and a usable lifter; ``per_part_regeneration`` is reserved;
* ``workflow``: the workflows flag and the workflow compiler module (``not_implemented`` until it merges);
* ``nodes``: :func:`services.workflow_catalog.resolve_node_states` over the four pipeline capabilities (refine nodes
  are locked and ``hidden_by_default``), and ``workflow.catalog_etag`` is the ETag of the catalog built from the same
  states.

The part-aware container binds :meth:`FeatureService.features` to the debounced ``features.updated`` publisher; it
fires on registry state changes, run-queue changes and engine changes.
"""

from __future__ import annotations

import importlib.util
import logging
from typing import TYPE_CHECKING, Final

from config.settings import Settings
from core.events import EventBus
from core.feature_errors import FeatureDisabledError, FeatureName, feature_enabled, require_feature
from inference.base import InferenceEngine
from models.enums import EngineState
from pipelines.runs.executor import ExecutorTable
from schemas.features import (
    PAUSED_REASON,
    CapabilityOut,
    FeatureFlagsOut,
    FeatureLimitsOut,
    FeaturesApiOut,
    FeaturesOut,
    PartExtractionCapabilityOut,
    PartGroupOut,
    PipelineOut,
    PromptToThreeDOut,
    QueuesOut,
    SegmentationCapabilityOut,
    SegmentationProfileOut,
    StrategyOut,
    TaxonomyOut,
    ToggleOut,
    WorkflowCapabilityOut,
)
from schemas.image_generation import MAX_IMAGES, NEGATIVE_PROMPT_MAX_CHARS, PROMPT_PRESETS, PROMPT_STYLES
from schemas.model_registry import ModelOut
from schemas.parts import (
    MAX_PARTS_LIMIT,
    MAX_VIEWS,
    RESERVED_STRATEGIES,
    STRATEGIES_REQUIRING_SEGMENTATION,
    STRATEGY_LABELS,
)
from schemas.runs import RunQueueOut
from schemas.segmentation import MAX_EXTRA_TERMS, SEGMENTATION_SUBJECT_OPTIONS
from schemas.workflows import MAX_GRAPH_EDGES, MAX_GRAPH_NODES
from services.model_registry_service import build_model_out
from services.run_events import FeaturesEventPublisher
from services.workflow_catalog import LEGACY_JOB_NODE_MAP, build_catalog, feature_nodes, resolve_node_states
from workers.run_worker import RunWorker

if TYPE_CHECKING:  # pragma: no cover
    from inference.providers.base import ProviderStatus
    from inference.providers.registry import ProviderRegistry

logger = logging.getLogger(__name__)

API_PREFIX: Final = "/api/v1"
WORKFLOW_COMPILER_MODULE: Final = "services.workflow_compiler"
"""S11 module; its presence marks the workflow API as implemented in this build."""
SELECTION_MODES: Final[tuple[str, ...]] = ("auto_first",)
DEFAULT_PROFILE: Final = "auto"
REGISTRY_MISSING_REASON: Final = "The model registry is not part of this build"
PER_PART_REGENERATION_REASON: Final = "Per-part regeneration is not implemented in this milestone."
_OK: Final = CapabilityOut(available=True, code="ok", reason=None)


def _blocked(code: str, reason: str | None) -> CapabilityOut:
    return CapabilityOut(available=False, code=code, reason=reason)  # type: ignore[arg-type]


class FeatureService:
    def __init__(
        self,
        *,
        settings: Settings,
        engine: InferenceEngine,
        registry: ProviderRegistry | None,
        executors: ExecutorTable,
        runs: RunWorker,
        bus: EventBus,
        events: FeaturesEventPublisher,
    ) -> None:
        self._settings = settings
        self._engine = engine
        self._registry = registry
        self._executors = executors
        self._runs = runs
        self._bus = bus
        self._events = events

    def features(self) -> FeaturesOut:
        """The full ``FeaturesOut`` document (section 6.1)."""
        settings = self._settings
        image_to_3d = self._image_to_3d()
        prompt_to_3d = self._prompt_to_3d(image_to_3d)
        segmentation = self._segmentation()
        part_extraction = self._part_extraction()
        capabilities = {
            "image_to_3d": image_to_3d,
            "prompt_to_3d": prompt_to_3d,
            "segmentation": segmentation,
            "part_extraction": part_extraction,
        }
        node_states = resolve_node_states(capabilities)
        catalog = build_catalog(node_states, graph_max_bytes=settings.workflow_graph_max_bytes)
        workflow = self._workflow(catalog.etag)
        mesh_tuning_enabled = bool(settings.blender_enabled and settings.mesh_tuning_enabled)
        part_refinement_enabled = bool(settings.part_refinement_enabled)
        return FeaturesOut(
            api=FeaturesApiOut(
                workflows=self._workflows_implemented(),
                part_analyses=self._executor_present("part_analysis"),
                image_generations=self._executor_present("image_generation"),
                segmentations=self._executor_present("segmentation"),
            ),
            flags=FeatureFlagsOut(
                part_aware=bool(settings.part_aware_enabled),
                image_generation=feature_enabled(settings, "image_generation"),
                segmentation=feature_enabled(settings, "segmentation"),
                part_analysis=feature_enabled(settings, "part_analysis"),
                workflows=feature_enabled(settings, "workflows"),
                mesh_tuning=mesh_tuning_enabled,
                part_refinement=part_refinement_enabled,
                persist_generation_intermediates=False,
            ),
            pipeline=PipelineOut(
                image_to_3d=image_to_3d,
                prompt_to_3d=prompt_to_3d,
                segmentation=segmentation,
                part_extraction=part_extraction,
            ),
            workflow=workflow,
            nodes=feature_nodes(node_states),
            mesh_tuning=ToggleOut(enabled=mesh_tuning_enabled, reason=None if mesh_tuning_enabled else PAUSED_REASON),
            part_refinement=ToggleOut(
                enabled=part_refinement_enabled, reason=None if part_refinement_enabled else PAUSED_REASON
            ),
            models=self._models(),
            taxonomy=TaxonomyOut.current(),
            queues=QueuesOut(runs=RunQueueOut.model_validate(self._runs.queue_status().to_dict())),
            limits=FeatureLimitsOut(
                prompt_max_chars=settings.prompt_max_chars,
                negative_prompt_max_chars=NEGATIVE_PROMPT_MAX_CHARS,
                extra_terms_max=MAX_EXTRA_TERMS,
                views_max=MAX_VIEWS,
                workflow_graph_max_bytes=settings.workflow_graph_max_bytes,
                max_parts=MAX_PARTS_LIMIT,
            ),
        )

    def notify_changed(self) -> None:
        """Schedule a debounced ``features.updated`` (thread-safe)."""
        self._events.notify()

    # ------------------------------------------------------------------ pipeline capabilities
    def _image_to_3d(self) -> CapabilityOut:
        try:
            status = self._engine.status()
        except Exception as exc:  # noqa: BLE001 - discovery must answer even if the engine misbehaves
            return _blocked("engine_unavailable", f"{type(exc).__name__}: {exc}")
        if status.state in (EngineState.ERROR, EngineState.UNAVAILABLE):
            return _blocked("engine_unavailable", status.error or status.message or f"Engine is {status.state}")
        return _OK

    def _prompt_to_3d(self, image_to_3d: CapabilityOut) -> PromptToThreeDOut:
        registry = self._registry
        model_id = self._t2i_model_id()
        spec = registry.spec(model_id) if registry is not None and model_id is not None else None
        model_status = registry.status(model_id) if registry is not None and model_id is not None else None
        capabilities = spec.capabilities if spec is not None else {}
        cold_load_s = spec.cold_load_s if spec is not None else None
        verdict = (
            self._flag("image_generation")
            or self._executor("image_generation")
            or self._model_verdict(model_status, "text-to-image model")
            or (None if image_to_3d.available else image_to_3d)
            or _OK
        )
        state = model_status.state if model_status is not None else None
        return PromptToThreeDOut(
            **verdict.model_dump(),
            max_prompt_chars=self._settings.prompt_max_chars,
            presets=list(PROMPT_PRESETS),
            styles=list(PROMPT_STYLES),
            supports_negative_prompt=bool(capabilities.get("supports_negative_prompt", False)),
            default_model_id=model_id,
            default_model_state=state,
            cold_start_s=None if state == "loaded" else cold_load_s,
            max_images=int(capabilities.get("max_images", MAX_IMAGES)),
            sizes=[list(size) for size in capabilities.get("sizes", [])],
            selection_modes=list(SELECTION_MODES),
        )

    def _segmentation(self) -> SegmentationCapabilityOut:
        baseline = self._baseline_models()
        profiles = [SegmentationProfileOut(id="baseline", **baseline.model_dump())]
        for profile, subject in (("anime", "anime_character"), ("realistic", "realistic_human")):
            parser = self._role_verdict("parser", subject=subject, label=f"{profile} parser")
            verdict = parser or (None if baseline.available else baseline) or _OK
            profiles.append(SegmentationProfileOut(id=profile, **verdict.model_dump()))  # type: ignore[arg-type]
        first_usable = next((p for p in profiles if p.available), None)
        no_profile = (
            None
            if first_usable is not None
            else _blocked(profiles[0].code, profiles[0].reason or "No segmentation profile is usable")
        )
        verdict = self._flag("segmentation") or self._executor("segmentation") or no_profile or _OK
        return SegmentationCapabilityOut(
            **verdict.model_dump(),
            subjects=list(SEGMENTATION_SUBJECT_OPTIONS),
            profiles=profiles,
            default_profile=DEFAULT_PROFILE,
            max_extra_terms=MAX_EXTRA_TERMS,
            before_reconstruction=True,
        )

    def _part_extraction(self) -> PartExtractionCapabilityOut:
        gate = self._flag("part_analysis") or self._executor("part_analysis")
        baseline = self._baseline_models()
        semantic = (
            gate
            or (None if baseline.available else baseline)
            or self._role_verdict("lifter", label="label lifter")
            or _OK
        )
        strategies: list[StrategyOut] = []
        for strategy_id, label in STRATEGY_LABELS.items():
            if strategy_id in RESERVED_STRATEGIES:
                verdict = _blocked("not_implemented", PER_PART_REGENERATION_REASON)
            elif strategy_id in STRATEGIES_REQUIRING_SEGMENTATION:
                verdict = semantic
            else:
                verdict = gate or _OK
            strategies.append(
                StrategyOut(
                    id=strategy_id,  # type: ignore[arg-type]
                    label=label,
                    requires_segmentation=strategy_id in STRATEGIES_REQUIRING_SEGMENTATION,
                    **verdict.model_dump(),
                )
            )
        by_id = {strategy.id: strategy for strategy in strategies}
        default_strategy = next(
            (sid for sid in ("semantic_parts", "components") if by_id.get(sid) and by_id[sid].available), None
        )
        verdict = gate or _OK
        return PartExtractionCapabilityOut(
            **verdict.model_dump(),
            strategies=strategies,
            default_strategy=default_strategy,  # type: ignore[arg-type]
            separable_groups=PartGroupOut.separable_groups(),
            region_groups=PartGroupOut.region_groups(),
            max_views=MAX_VIEWS,
            default_views=self._settings.parts_default_views,
            rebuild=True,
            per_part_glbs="on_demand",
        )

    def _workflow(self, catalog_etag: str) -> WorkflowCapabilityOut:
        implemented = None if self._workflows_implemented() else _blocked(
            "not_implemented", "Workflows are not implemented in this build"
        )
        verdict = self._flag("workflows") or implemented or _OK
        return WorkflowCapabilityOut(
            **verdict.model_dump(),
            catalog_url=f"{API_PREFIX}/workflows/catalog",
            catalog_etag=catalog_etag,
            max_nodes=MAX_GRAPH_NODES,
            max_edges=MAX_GRAPH_EDGES,
            graph_max_bytes=self._settings.workflow_graph_max_bytes,
            selection_modes=list(SELECTION_MODES),
            legacy_job_node_map=dict(LEGACY_JOB_NODE_MAP),
        )

    # ------------------------------------------------------------------ helpers
    def _flag(self, feature: FeatureName) -> CapabilityOut | None:
        try:
            require_feature(self._settings, feature)
        except FeatureDisabledError as exc:
            return _blocked("disabled", exc.message)
        return None

    def _executor(self, kind: str) -> CapabilityOut | None:
        availability = self._executors.availability(kind)  # type: ignore[arg-type]
        return None if availability.available else _blocked(availability.code, availability.reason)

    def _executor_present(self, kind: str) -> bool:
        return self._executors.availability(kind).code != "not_implemented"  # type: ignore[arg-type]

    @staticmethod
    def _workflows_implemented() -> bool:
        try:
            return importlib.util.find_spec(WORKFLOW_COMPILER_MODULE) is not None
        except (ImportError, ValueError):
            return False

    def _t2i_model_id(self) -> str | None:
        registry = self._registry
        if registry is None:
            return None
        chosen = registry.default_model("text_to_image")
        if chosen is not None:
            return chosen
        return next((spec.id for spec in registry.specs("text_to_image") if spec.provider is not None), None)

    def _model_verdict(self, status: ProviderStatus | None, label: str) -> CapabilityOut | None:
        if self._registry is None:
            return _blocked("not_implemented", REGISTRY_MISSING_REASON)
        if status is None:
            return _blocked("unsupported", f"No {label} is in the model catalog")
        if status.usable:
            return None
        return _blocked(status.reason_code, status.reason or f"The {label} is {status.state}")

    def _role_verdict(self, role: str, *, subject: str | None = None, label: str) -> CapabilityOut | None:
        """``None`` when a usable model has ``role`` (and ``capabilities.subject``), else the first candidate's code."""
        registry = self._registry
        if registry is None:
            return _blocked("not_implemented", REGISTRY_MISSING_REASON)
        candidates = [
            spec
            for spec in registry.specs()
            if role in spec.roles and (subject is None or spec.capabilities.get("subject") == subject)
        ]
        statuses = [registry.status(spec.id) for spec in candidates]
        if any(status.usable for status in statuses):
            return None
        if not statuses:
            return _blocked("unsupported", f"No {label} is in the model catalog")
        supported = [s for spec, s in zip(candidates, statuses, strict=True) if spec.provider is not None]
        first = (supported or statuses)[0]
        return _blocked(first.reason_code, first.reason or f"The {label} is {first.state}")

    def _baseline_models(self) -> CapabilityOut:
        """The baseline segmentation stack: a usable detector and a usable refiner."""
        return (
            self._role_verdict("detector", label="detector")
            or self._role_verdict("refiner", label="mask refiner")
            or _OK
        )

    def _models(self) -> list[ModelOut]:
        registry = self._registry
        if registry is None:
            return []
        models: list[ModelOut] = []
        for spec in registry.specs():
            if spec.hidden_by_default:
                continue
            try:
                models.append(build_model_out(registry, self._settings, spec.id))
            except Exception:  # noqa: BLE001 - one broken entry must not break discovery
                logger.exception("Cannot describe model %s", spec.id)
        return models

