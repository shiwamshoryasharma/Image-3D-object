"""The model registry: honest model states, lazy provider construction, VRAM rules (part-aware DESIGN.md 4.8, 5.3).

Torch-free. Implementation modules (the only places that import torch/diffusers/transformers/nvdiffrast) are imported
lazily by :meth:`ProviderRegistry.provider` through :data:`PROVIDER_PATHS` and constructed as
``ProviderClass(spec, settings=settings, model_dir=<Path | None>)``. Before every such import the registry calls the
torch-free ``configure_hf_environment(settings.hf_home)``, so no provider imports ``huggingface_hub``/``transformers``
before the Hugging Face cache is configured (the later call in ``prepare_runtime`` is then a no-op). Any exception
while importing or constructing substitutes an :class:`~inference.providers.unavailable.UnavailableProvider`
(``reason_code="error"``, ``reason="<ExceptionType>: <message>"``; ``not_implemented`` when the implementation module
itself is not part of this build). Test doubles come from ``PartProviderOverrides.providers`` (by model id): an
override stands in for the installed model, so the on-disk rules 5-8 are skipped for it while the policy rules
(1-4, 9) still apply.

State resolution (first matching rule wins; the on-disk rules 5-8 are cached for :data:`STATUS_CACHE_TTL_S` and
invalidated on load, unload or error):

1. ``provider is None`` -> ``unavailable / unsupported``;
2. the master flag or the flag of the model's kind is off -> ``unavailable / disabled``;
3. approval required and not approved -> ``unavailable / not_approved``;
4. restricted licence, approval required, ``allow_restricted_licenses`` off -> ``unavailable / license_blocked``;
5. vendored source without Python sources -> ``vendored_source_missing``; vendored modules that import their own
   top-level packages dynamically (``vendor_import.VendoredPackage.scan_dynamic_imports``) -> ``incompatible``;
6. a dependency absent or outside its specifier -> ``unavailable / missing_dependency`` (nothing is imported);
7. required files missing -> ``not_installed / not_installed``;
8. a text-encoder ``config.json`` written by transformers >= 5 -> ``unavailable / incompatible``;
9. the static VRAM check of section 4.8 fails -> ``unavailable / insufficient_vram``;
10. the live state (``loading`` while a warm-up runs, a recorded load error, or the provider's own
    ``loading``/``loaded``/``error``) -> that state; otherwise ``installed / ok``.

Threading: ``status()`` and friends are cheap and thread-safe; ``ensure_vram``/``offload`` and every provider
operation except ``load()`` run on the ``gpu-worker`` thread. Provider construction is serialised per model.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import json
import logging
import os
import re
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from config.settings import Settings
from core.exceptions import NotFoundError
from core.feature_errors import (
    CapabilityUnavailableError,
    FeatureDisabledError,
    FeatureName,
    InsufficientVramError,
    ModelUnavailableError,
    require_feature,
)
from inference.base import InferenceEngine
from inference.providers.base import (
    MODEL_ID_PATTERN,
    RESTRICTED_COMMERCIAL_USES,
    USABLE_STATES,
    ModelKind,
    ModelProvider,
    ModelSpec,
    ProviderStatus,
)
from inference.providers.catalog import SEG_DOWNLOAD_MANIFEST, VENDORED_SOURCES
from inference.providers.unavailable import UnavailableProvider
from inference.providers.vram import empty_cuda_cache, free_vram_mb, torch_globals_guard
from inference.trellis.environment import configure_hf_environment
from utils.timeutil import utcnow

if TYPE_CHECKING:  # pragma: no cover
    from core.part_aware_container import PartProviderOverrides

logger = logging.getLogger(__name__)

PROVIDER_PATHS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "zimage": "inference.imagegen.zimage:ZImageTurboProvider",
        "hf_grounding_dino": "inference.segmentation.gdino:GroundingDinoProvider",
        "hf_sam2": "inference.segmentation.sam2:Sam2Provider",
        "hf_florence2": "inference.segmentation.florence2:Florence2Provider",
        "vendored_see_through": "inference.segmentation.see_through:SeeThroughProvider",
        "vendored_sapiens2": "inference.segmentation.sapiens2:Sapiens2Provider",
        "nvdiffrast_raster": "inference.parts.raster_lifter:RasterLifterProvider",
    }
)
"""``ModelSpec.provider`` key -> ``"module:Class"`` of the implementation (owned by S4, S5, S6 and S7)."""

VENDOR_IMPORT_MODULE: Final = "inference.providers.vendor_import"
"""Stdlib-only vendored-import helper (S6); rule 5 uses its AST scan once the module exists."""
STATUS_CACHE_TTL_S: Final = 5.0
KIND_FEATURES: Final[Mapping[str, FeatureName]] = MappingProxyType(
    {"text_to_image": "image_generation", "segmentation": "segmentation", "part_extraction": "part_analysis"}
)
"""Feature flag gating each model kind (rule 2); the future 3D kinds are unsupported before any flag matters."""
INCOMPATIBLE_TRANSFORMERS: Final = Version("5")
LIVE_STATES: Final = frozenset({"loading", "loaded", "error", "unavailable"})
_MAX_LISTED: Final = 3


@dataclass(frozen=True, slots=True)
class DependencyState:
    """One ``DependencySpec`` checked against the installed distributions (``/models`` ``dependencies``)."""

    distribution: str
    requirement: str
    installed: str | None
    ok: bool


@dataclass(frozen=True, slots=True)
class _Verdict:
    state: str
    reason_code: str
    reason: str | None
    revision: str | None = None


def _listed(items: Sequence[str]) -> str:
    shown = ", ".join(items[:_MAX_LISTED])
    return shown if len(items) <= _MAX_LISTED else f"{shown} (+{len(items) - _MAX_LISTED} more)"


def _has_python_sources(root: Path) -> bool:
    """``root`` holds an ``__init__.py``, a top-level module or a package (an uninitialised submodule does not)."""
    if not root.is_dir():
        return False
    try:
        for entry in root.iterdir():
            if entry.is_file() and entry.suffix == ".py":
                return True
            if entry.is_dir() and (entry / "__init__.py").is_file():
                return True
    except OSError:
        return False
    return False


def _files_present(model_dir: Path, patterns: Iterable[str]) -> list[str]:
    """The required files (glob patterns allowed) that do not exist under ``model_dir``."""
    missing: list[str] = []
    for pattern in patterns:
        if any(ch in pattern for ch in "*?["):
            if not any(path.is_file() for path in model_dir.glob(pattern)):
                missing.append(pattern)
        elif not (model_dir / pattern).is_file():
            missing.append(pattern)
    return missing


def check_dependency(distribution: str, requirement: str) -> DependencyState:
    """Installed version of ``distribution`` and whether it satisfies ``requirement`` (the package is not imported)."""
    try:
        installed = importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return DependencyState(distribution, requirement, None, False)
    try:
        ok = SpecifierSet(requirement).contains(installed, prereleases=True)
    except (InvalidSpecifier, InvalidVersion):
        ok = False
    return DependencyState(distribution, requirement, installed, ok)


class ProviderRegistry:
    """Catalog + honest states + lazily constructed providers (satisfies ``pipelines.runs.context.RunProviderRegistry``)."""

    def __init__(
        self,
        settings: Settings,
        catalog: Sequence[ModelSpec],
        *,
        engine: InferenceEngine,
        overrides: PartProviderOverrides | None = None,
        on_change: Callable[[], None] | None = None,
    ) -> None:
        pattern = re.compile(MODEL_ID_PATTERN)
        specs: dict[str, ModelSpec] = {}
        for spec in catalog:
            if not pattern.fullmatch(spec.id):
                raise ValueError(f"Invalid model id {spec.id!r} (expected {MODEL_ID_PATTERN})")
            if spec.id in specs:
                raise ValueError(f"Duplicate model id {spec.id!r} in the catalog")
            specs[spec.id] = spec
        self._settings = settings
        self._engine = engine
        self._specs: Mapping[str, ModelSpec] = MappingProxyType(specs)
        self._overrides: dict[str, ModelProvider] = dict(overrides.providers) if overrides is not None else {}
        self._on_change = on_change
        self._lock = threading.RLock()
        self._create_locks: dict[str, threading.Lock] = {model_id: threading.Lock() for model_id in specs}
        self._providers: dict[str, ModelProvider] = {}
        self._loading: set[str] = set()
        self._errors: dict[str, str] = {}
        self._last_used: dict[str, datetime] = {}
        self._cache: dict[str, tuple[float, _Verdict]] = {}
        self._dependency_cache: dict[str, tuple[float, tuple[DependencyState, ...]]] = {}
        self._vendor_scans: dict[str, list[str]] = {}
        self._installed_revisions = self._read_download_manifest()
        unknown_overrides = sorted(set(self._overrides) - set(specs))
        if unknown_overrides:
            logger.warning("Provider overrides for unknown model ids are ignored: %s", ", ".join(unknown_overrides))
        default = settings.t2i_default_model
        if default is not None and (default not in specs or specs[default].kind != "text_to_image"):
            logger.warning("IMG3D_T2I_DEFAULT_MODEL=%s is not a text-to-image model of the catalog; ignored", default)

    # ------------------------------------------------------------------ catalog
    def specs(self, kind: ModelKind | None = None) -> list[ModelSpec]:
        return [spec for spec in self._specs.values() if kind is None or spec.kind == kind]

    def has(self, model_id: str) -> bool:
        return model_id in self._specs

    def spec(self, model_id: str) -> ModelSpec:
        spec = self._specs.get(model_id)
        if spec is None:
            raise NotFoundError(f"Model '{model_id}' not found", details={"model_id": model_id})
        return spec

    def model_dir(self, model_id: str) -> Path | None:
        """``settings.model_paths[id]`` or ``aux_model_root/local_subdir`` (``None`` for models without weights)."""
        spec = self.spec(model_id)
        override = self._settings.model_paths.get(model_id)
        if override is not None:
            return Path(override)
        if spec.local_subdir is None:
            return None
        return Path(self._settings.aux_model_root) / spec.local_subdir

    def effective_approved_models(self) -> frozenset[str]:
        approved = self._settings.approved_models
        if approved is not None:
            return frozenset(approved)
        return frozenset(spec.id for spec in self._specs.values() if spec.default_approved)

    def is_approved(self, model_id: str) -> bool:
        spec = self.spec(model_id)
        return not spec.approval_required or spec.id in self.effective_approved_models()

    def dependencies(self, model_id: str) -> list[DependencyState]:
        """The model's dependencies checked against the installed distributions (cached like rules 5-8)."""
        spec = self.spec(model_id)
        now = time.monotonic()
        with self._lock:
            cached = self._dependency_cache.get(model_id)
            if cached is not None and now - cached[0] < STATUS_CACHE_TTL_S:
                return list(cached[1])
        states = [check_dependency(dep.distribution, dep.requirement) for dep in spec.dependencies]
        with self._lock:
            self._dependency_cache[model_id] = (now, tuple(states))
        return states

    def preferred_model_ids(self) -> frozenset[str]:
        """The model each role picks when none is named: the T2I default and the first supported model per
        ``(kind, role, subject)`` in catalog order (``ModelOut.is_default``)."""
        preferred: set[str] = set()
        t2i = self._configured_t2i_default() or next(
            (spec.id for spec in self.specs("text_to_image") if spec.provider is not None), None
        )
        if t2i is not None:
            preferred.add(t2i)
        seen: set[tuple[str, str, Any]] = set()
        for spec in self._specs.values():
            if spec.provider is None or spec.kind == "text_to_image":
                continue
            for role in spec.roles:
                key = (spec.kind, role, spec.capabilities.get("subject"))
                if key not in seen:
                    seen.add(key)
                    preferred.add(spec.id)
        return frozenset(preferred)

    def default_model(self, kind: ModelKind) -> str | None:
        """``settings.t2i_default_model`` for text-to-image when set, otherwise the first usable model of ``kind``."""
        if kind == "text_to_image":
            configured = self._configured_t2i_default()
            if configured is not None:
                return configured
        return next((spec.id for spec in self.specs(kind) if self.usable(spec.id)), None)

    def _configured_t2i_default(self) -> str | None:
        configured = self._settings.t2i_default_model
        spec = self._specs.get(configured) if configured else None
        return spec.id if spec is not None and spec.kind == "text_to_image" else None

    # ------------------------------------------------------------------ states
    def status(self, model_id: str) -> ProviderStatus:
        spec = self.spec(model_id)
        policy = self._policy_verdict(spec)
        if policy is not None:
            return self._to_status(spec, policy)
        verdict = self._installation_verdict(spec)
        if verdict.state != "installed":
            return self._to_status(spec, verdict)
        vram_reason = self._static_vram_reason(spec)
        if vram_reason is not None:
            return self._to_status(spec, _Verdict("unavailable", "insufficient_vram", vram_reason, verdict.revision))
        return self._live_status(spec, verdict.revision)

    def statuses(self, kind: ModelKind | None = None) -> list[ProviderStatus]:
        return [self.status(spec.id) for spec in self.specs(kind)]

    def usable(self, model_id: str) -> bool:
        if model_id not in self._specs:
            return False
        return self.status(model_id).state in USABLE_STATES

    def is_loading(self, model_id: str) -> bool:
        with self._lock:
            return model_id in self._loading

    @staticmethod
    def _to_status(spec: ModelSpec, verdict: _Verdict) -> ProviderStatus:
        return ProviderStatus(
            model_id=spec.id,
            state=verdict.state,  # type: ignore[arg-type]
            reason_code=verdict.reason_code,  # type: ignore[arg-type]
            reason=verdict.reason,
            revision=verdict.revision,
        )

    def _policy_verdict(self, spec: ModelSpec) -> _Verdict | None:
        """Rules 1-4 (never cached: they follow the settings)."""
        if spec.provider is None:
            return _Verdict("unavailable", "unsupported", spec.unsupported_reason or "Not supported in this build")
        feature = KIND_FEATURES.get(spec.kind)
        if feature is not None:
            try:
                require_feature(self._settings, feature)
            except FeatureDisabledError as exc:
                return _Verdict("unavailable", "disabled", exc.message)
        if spec.approval_required and spec.id not in self.effective_approved_models():
            return _Verdict(
                "unavailable",
                "not_approved",
                f"{spec.display_name} is not in the approved model list (IMG3D_APPROVED_MODELS)",
            )
        if (
            spec.approval_required
            and spec.license.commercial in RESTRICTED_COMMERCIAL_USES
            and not self._settings.allow_restricted_licenses
        ):
            return _Verdict(
                "unavailable",
                "license_blocked",
                f"{spec.license.name} restricts use ({spec.license.commercial}); "
                "set IMG3D_ALLOW_RESTRICTED_LICENSES=true to allow it",
            )
        return None

    def _installation_verdict(self, spec: ModelSpec) -> _Verdict:
        """Rules 5-8, cached for :data:`STATUS_CACHE_TTL_S`."""
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(spec.id)
            if cached is not None and now - cached[0] < STATUS_CACHE_TTL_S:
                return cached[1]
        if spec.id in self._overrides:
            verdict = _Verdict("installed", "ok", None, spec.revision)
        else:
            verdict = self._check_installation(spec)
        with self._lock:
            self._cache[spec.id] = (now, verdict)
        return verdict

    def _check_installation(self, spec: ModelSpec) -> _Verdict:
        if spec.vendored_source is not None:
            verdict = self._check_vendored(spec)
            if verdict is not None:
                return verdict
        broken = [dep for dep in self.dependencies(spec.id) if not dep.ok]
        if broken:
            parts = [
                f"{dep.distribution}{dep.requirement} (installed: {dep.installed or 'absent'})" for dep in broken
            ]
            return _Verdict("unavailable", "missing_dependency", f"Missing or incompatible dependency: {_listed(parts)}")
        model_dir = self.model_dir(spec.id)
        if spec.required_files:
            where = self._display_path(model_dir)
            if model_dir is None or not model_dir.is_dir():
                return _Verdict("not_installed", "not_installed", f"Model files are not installed under {where}")
            missing = _files_present(model_dir, spec.required_files)
            if missing:
                return _Verdict("not_installed", "not_installed", f"Missing under {where}: {_listed(missing)}")
        if spec.kind == "text_to_image" and model_dir is not None:
            reason = self._text_encoder_incompatibility(model_dir)
            if reason is not None:
                return _Verdict("unavailable", "incompatible", reason)
        return _Verdict("installed", "ok", None, self._installed_revisions.get(spec.id, spec.revision))

    def _check_vendored(self, spec: ModelSpec) -> _Verdict | None:
        assert spec.vendored_source is not None
        root = Path(self._settings.backend_dir) / spec.vendored_source
        vendored = VENDORED_SOURCES.get(spec.id)
        label = vendored.label if vendored is not None else spec.display_name
        if not _has_python_sources(root):
            return _Verdict(
                "unavailable",
                "vendored_source_missing",
                f"{label} code is not vendored under backend/{Path(spec.vendored_source).as_posix()}",
            )
        if vendored is None:
            return None
        findings = self._dynamic_import_findings(spec.id, vendored.package, root)
        if findings:
            return _Verdict(
                "unavailable",
                "incompatible",
                f"{label} imports its own packages dynamically ({_listed(findings)}); it cannot be loaded "
                "without touching sys.path",
            )
        return None

    def _dynamic_import_findings(self, model_id: str, package: str, root: Path) -> list[str]:
        with self._lock:
            cached = self._vendor_scans.get(model_id)
        if cached is not None:
            return cached
        if importlib.util.find_spec(VENDOR_IMPORT_MODULE) is None:
            return []  # the helper merges with S6, together with the vendored sources
        try:
            helper = importlib.import_module(VENDOR_IMPORT_MODULE)
            findings = list(helper.VendoredPackage(package, root).scan_dynamic_imports())
        except Exception as exc:  # noqa: BLE001 - report instead of crashing discovery
            findings = [f"scan failed: {type(exc).__name__}: {exc}"]
        with self._lock:
            self._vendor_scans[model_id] = findings
        return findings

    @staticmethod
    def _text_encoder_incompatibility(model_dir: Path) -> str | None:
        for config in sorted(model_dir.glob("text_encoder*/config.json")):
            try:
                written_by = json.loads(config.read_text(encoding="utf-8")).get("transformers_version")
            except (OSError, ValueError):
                continue
            if not written_by:
                continue
            try:
                incompatible = Version(str(written_by)) >= INCOMPATIBLE_TRANSFORMERS
            except InvalidVersion:
                continue
            if incompatible:
                return (
                    f"{config.parent.name} was written by transformers {written_by}; transformers 5 checkpoints "
                    "are not supported (transformers 4.57.6 is pinned)"
                )
        return None

    def _static_vram_reason(self, spec: ModelSpec) -> str | None:
        """Rule 9 / section 4.8 rule 1 (never cached: follows the engine state)."""
        settings = self._settings
        resident = 0.0
        if not settings.low_vram:
            try:
                if self._engine.status().is_ready:
                    resident = settings.engine_resident_vram_gb
            except Exception:  # noqa: BLE001 - an engine status failure must not break discovery
                logger.exception("Engine status query failed during the VRAM check")
        available = settings.aux_vram_budget_gb - resident
        if spec.vram_peak_gb <= available:
            return None
        detail = (
            f"{settings.aux_vram_budget_gb:g} GB budget minus {resident:g} GB held by the resident TRELLIS.2 engine "
            "(IMG3D_LOW_VRAM=false)"
            if resident
            else f"IMG3D_AUX_VRAM_BUDGET_GB={settings.aux_vram_budget_gb:g}"
        )
        return f"{spec.display_name} needs {spec.vram_peak_gb:g} GB of VRAM; {available:g} GB are available ({detail})"

    def _live_status(self, spec: ModelSpec, revision: str | None) -> ProviderStatus:
        with self._lock:
            loading = spec.id in self._loading
            error = self._errors.get(spec.id)
            provider = self._providers.get(spec.id)
            last_used = self._last_used.get(spec.id)
        live: ProviderStatus | None = None
        if provider is not None:
            try:
                live = provider.status()
            except Exception as exc:  # noqa: BLE001 - status() must never break discovery
                live = ProviderStatus(spec.id, "error", "error", f"{type(exc).__name__}: {exc}")
        loaded_device = live.loaded_device if live is not None else None
        last_used = (live.last_used_at if live is not None else None) or last_used
        revision = (live.revision if live is not None else None) or revision
        if loading:
            return ProviderStatus(spec.id, "loading", "ok", None, revision, loaded_device, last_used)
        if error is not None:
            return ProviderStatus(spec.id, "error", "error", error, revision, loaded_device, last_used)
        if live is not None and live.state in LIVE_STATES:
            return ProviderStatus(
                spec.id, live.state, live.reason_code, live.reason, revision, loaded_device, last_used
            )
        return ProviderStatus(spec.id, "installed", "ok", None, revision, loaded_device, last_used)

    def _display_path(self, path: Path | None) -> str:
        if path is None:
            return "(no model directory)"
        try:
            return "backend/" + Path(path).resolve().relative_to(Path(self._settings.backend_dir).resolve()).as_posix()
        except ValueError:
            return str(path)

    def _read_download_manifest(self) -> dict[str, str]:
        """Installed segmentation revisions keyed by model id (``seg/download_manifest.json``, keyed by folder)."""
        path = Path(self._settings.aux_model_root) / SEG_DOWNLOAD_MANIFEST
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            logger.warning("Cannot read %s: %s", path, exc)
            return {}
        revisions: dict[str, str] = {}
        if not isinstance(manifest, dict):
            return revisions
        for spec in self._specs.values():
            if spec.local_subdir is None:
                continue
            entry = manifest.get(Path(spec.local_subdir).name)
            revision = entry.get("revision") if isinstance(entry, dict) else None
            if not isinstance(revision, str):
                continue
            revisions[spec.id] = revision
            if spec.revision is not None and revision != spec.revision:
                logger.warning(
                    "%s: installed revision %s differs from the catalog revision %s (%s)",
                    spec.id,
                    revision,
                    spec.revision,
                    path,
                )
        return revisions

    # ------------------------------------------------------------------ providers
    def instantiated(self, model_id: str) -> ModelProvider | None:
        with self._lock:
            return self._providers.get(model_id)

    def provider(self, model_id: str) -> ModelProvider:
        """The provider of a usable model, imported and constructed on first use (``ModelUnavailableError`` else)."""
        spec = self.spec(model_id)
        self._raise_unless_usable(spec)  # a substituted UnavailableProvider reports error/unavailable here
        with self._create_locks[model_id]:
            with self._lock:
                provider = self._providers.get(model_id)
            if provider is None:
                provider = self._construct(spec)
                with self._lock:
                    self._providers[model_id] = provider
                    self._cache.pop(model_id, None)
                if isinstance(provider, UnavailableProvider):
                    self._notify()
        if isinstance(provider, UnavailableProvider):
            raise ModelUnavailableError(
                f"{spec.display_name} is not available: {provider.reason}",
                model_id=spec.id,
                state=provider.state,
                reason_code=provider.reason_code,
            )
        return provider

    def _raise_unless_usable(self, spec: ModelSpec) -> None:
        status = self.status(spec.id)
        if status.state not in USABLE_STATES:
            raise ModelUnavailableError(
                f"{spec.display_name} is not usable: {status.reason or status.state}",
                model_id=spec.id,
                state=status.state,
                reason_code=status.reason_code,
            )

    def _construct(self, spec: ModelSpec) -> ModelProvider:
        hf_home = Path(self._settings.hf_home)
        configure_hf_environment(hf_home)
        if os.environ.get("HF_HOME") != str(hf_home):
            raise RuntimeError(f"HF_HOME is {os.environ.get('HF_HOME')!r}, expected {str(hf_home)!r}")
        override = self._overrides.get(spec.id)
        if override is not None:
            return override
        path = PROVIDER_PATHS.get(spec.provider or "")
        if path is None:
            return UnavailableProvider(spec, reason=f"No implementation is registered for provider '{spec.provider}'")
        module_name, _sep, attr = path.partition(":")
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            if exc.name is not None and (module_name == exc.name or module_name.startswith(exc.name + ".")):
                logger.info("%s: implementation module %s is not part of this build", spec.id, module_name)
                return UnavailableProvider(
                    spec, reason_code="not_implemented", reason=f"{module_name} is not part of this build"
                )
            return self._failed(spec, path, exc)
        except Exception as exc:  # noqa: BLE001 - e.g. OSError from a broken CUDA DLL
            return self._failed(spec, path, exc)
        try:
            provider_cls = getattr(module, attr)
            provider = provider_cls(spec, settings=self._settings, model_dir=self.model_dir(spec.id))
        except Exception as exc:  # noqa: BLE001
            return self._failed(spec, path, exc)
        if not isinstance(provider, ModelProvider):
            return UnavailableProvider(spec, reason=f"{path} did not build a ModelProvider")
        logger.info("Provider for %s: %s", spec.id, path)
        return provider

    @staticmethod
    def _failed(spec: ModelSpec, path: str, exc: BaseException) -> UnavailableProvider:
        reason = f"{type(exc).__name__}: {exc}"
        logger.warning("Provider %s for %s is not importable: %s", path, spec.id, reason)
        return UnavailableProvider(spec, reason_code="error", reason=reason)

    def resolve_role(self, role: str, preferred: Sequence[str] = ()) -> ModelProvider | None:
        """The first usable provider with ``role`` (``preferred`` ids first, then catalog order)."""
        candidates = [*preferred, *(spec.id for spec in self._specs.values() if role in spec.roles)]
        for model_id in dict.fromkeys(candidates):
            spec = self._specs.get(model_id)
            if spec is None or role not in spec.roles or not self.usable(model_id):
                continue
            try:
                return self.provider(model_id)
            except ModelUnavailableError:
                continue
        return None

    def guard(self) -> AbstractContextManager[None]:
        """``torch_globals_guard()`` (section 4.9); wraps every auxiliary GPU callable on the GPU thread."""
        return torch_globals_guard()

    # ------------------------------------------------------------------ VRAM
    def check_vram_static(self, model_id: str) -> None:
        spec = self.spec(model_id)
        reason = self._static_vram_reason(spec)
        if reason is not None:
            raise CapabilityUnavailableError(
                reason,
                capability=spec.kind,
                reasons=[
                    {"model_id": spec.id, "state": "unavailable", "reason_code": "insufficient_vram", "reason": reason}
                ],
            )

    def ensure_vram(self, model_ids: Sequence[str]) -> None:
        """Live check at the start of a GPU phase (GPU thread): free VRAM must cover the models plus the margin."""
        specs = [self.spec(model_id) for model_id in dict.fromkeys(model_ids)]
        if not specs:
            return
        free_mb = free_vram_mb(self._settings.device)
        if free_mb is None:
            return  # no usable CUDA device: the phase reports the real error itself
        required_mb = (sum(spec.vram_peak_gb for spec in specs) + self._settings.aux_vram_margin_gb) * 1024.0
        if free_mb < required_mb:
            names = ", ".join(spec.display_name for spec in specs)
            raise InsufficientVramError(
                f"Not enough free VRAM for {names}: {free_mb:.0f} MB free, {required_mb:.0f} MB required",
                model_ids=[spec.id for spec in specs],
                free_mb=round(free_mb, 1),
                required_mb=round(required_mb, 1),
            )

    def offload(self, model_ids: Sequence[str]) -> None:
        """Weights of the constructed providers back to CPU, then ``empty_cache`` (GPU thread)."""
        first_error: BaseException | None = None
        for model_id in dict.fromkeys(model_ids):
            provider = self.instantiated(model_id)
            if provider is None or isinstance(provider, UnavailableProvider):
                continue
            try:
                with provider.lock:
                    provider.offload()
            except Exception as exc:  # noqa: BLE001 - offload the others, re-raise the first failure
                logger.exception("Offloading %s failed", model_id)
                first_error = first_error or exc
            finally:
                self.mark_used(model_id)
        empty_cuda_cache()
        if first_error is not None:
            raise first_error

    def idle_providers(self, now: datetime) -> list[ModelProvider]:
        """Loaded providers unused for ``aux_model_idle_unload_s`` (empty while idle unload is off, the default)."""
        threshold = self._settings.aux_model_idle_unload_s
        if threshold <= 0:
            return []
        idle: list[ModelProvider] = []
        for model_id in self._specs:
            provider = self.instantiated(model_id)
            if provider is None or isinstance(provider, UnavailableProvider) or self.is_loading(model_id):
                continue
            status = self.status(model_id)
            if status.state == "loaded" and status.last_used_at is not None:
                if (now - status.last_used_at).total_seconds() >= threshold:
                    idle.append(provider)
        return idle

    # ------------------------------------------------------------------ transitions (model registry service)
    def begin_load(self, model_id: str) -> bool:
        """Mark ``model_id`` as loading (warm-up started). ``False`` if a warm-up is already running."""
        self.spec(model_id)
        with self._lock:
            if model_id in self._loading:
                return False
            self._loading.add(model_id)
            self._errors.pop(model_id, None)
            self._cache.pop(model_id, None)
        self._notify()
        return True

    def end_load(self, model_id: str, error: str | None = None) -> None:
        """Warm-up finished; ``error`` keeps the model in state ``error`` until the next load or unload."""
        with self._lock:
            self._loading.discard(model_id)
            if error is not None:
                self._errors[model_id] = error
            self._cache.pop(model_id, None)
        if error is None:
            self.mark_used(model_id)
        self._notify()

    def after_unload(self, model_id: str) -> None:
        """The weights were dropped: clear a recorded error and a substituted :class:`UnavailableProvider` (so the
        next use imports the implementation again)."""
        with self._lock:
            self._errors.pop(model_id, None)
            if isinstance(self._providers.get(model_id), UnavailableProvider):
                del self._providers[model_id]
            self._cache.pop(model_id, None)
            self._vendor_scans.pop(model_id, None)
        self._notify()

    def mark_used(self, model_id: str) -> None:
        with self._lock:
            self._last_used[model_id] = utcnow()

    def refresh(self) -> None:
        """Drop the cached on-disk checks (e.g. after weights were installed) and announce the change."""
        with self._lock:
            self._cache.clear()
            self._dependency_cache.clear()
            self._vendor_scans.clear()
        self._notify()

    def _notify(self) -> None:
        callback = self._on_change
        if callback is None:
            return
        try:
            callback()
        except Exception:  # noqa: BLE001 - a broken listener must not break a state transition
            logger.exception("Model registry change listener failed")
