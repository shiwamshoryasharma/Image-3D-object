"""Errors of the part-aware layer (part-aware DESIGN.md section 6.8).

Every class is an :class:`~core.exceptions.AppError`, so the existing handlers render it in the uniform envelope
``{"error": {"code", "message", "details", "request_id"}}``. The ``details`` shapes are part of the API contract:

======================================  ====  ==========================  =====================================
class                                   HTTP  code                        details
======================================  ====  ==========================  =====================================
:class:`FeatureDisabledError`           503   ``feature_disabled``        ``{"feature", "flag"}``
:class:`CapabilityUnavailableError`     503   ``capability_unavailable``  ``{"capability", "reasons": [...]}``
:class:`ModelUnavailableError`          503   ``model_unavailable``       ``{"model_id", "state", "reason_code"}``
:class:`InvalidWorkflowError`           422   ``invalid_workflow``        ``{"issues": [GraphIssue, ...]}``
:class:`InsufficientVramError`          503   ``insufficient_vram``       ``{"model_ids", "free_mb", "required_mb"}``
:class:`JobDeletedError`                409   ``job_deleted``             ``{"job_id"}``
======================================  ====  ==========================  =====================================

The last two are raised inside runs (registry live VRAM check, :class:`~storage.run_store.RunStore`) and become
``run.error.code``. :class:`ServiceNotImplementedError` is what the S1 service stubs raise until their slice merges
(a ``capability_unavailable`` whose reason code is ``not_implemented``).

:func:`require_feature` is the one flag check every mutating part-aware entry point uses.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final, Literal

from core.exceptions import AppError

if TYPE_CHECKING:  # pragma: no cover
    from config.settings import Settings

FeatureName = Literal[
    "part_aware",
    "image_generation",
    "segmentation",
    "part_analysis",
    "workflows",
    "mesh_tuning",
    "part_refinement",
]

FEATURE_FLAGS: Final[Mapping[str, tuple[str, str]]] = MappingProxyType(
    {
        "part_aware": ("part_aware_enabled", "IMG3D_PART_AWARE_ENABLED"),
        "image_generation": ("image_generation_enabled", "IMG3D_IMAGE_GENERATION_ENABLED"),
        "segmentation": ("segmentation_enabled", "IMG3D_SEGMENTATION_ENABLED"),
        "part_analysis": ("part_analysis_enabled", "IMG3D_PART_ANALYSIS_ENABLED"),
        "workflows": ("workflows_enabled", "IMG3D_WORKFLOWS_ENABLED"),
        "mesh_tuning": ("mesh_tuning_enabled", "IMG3D_MESH_TUNING_ENABLED"),
        "part_refinement": ("part_refinement_enabled", "IMG3D_PART_REFINEMENT_ENABLED"),
    }
)
"""Feature name -> (``Settings`` field, environment variable) (DESIGN.md section 8.1)."""

_MASTER_GATED: Final = frozenset({"image_generation", "segmentation", "part_analysis", "workflows"})
"""Features that also need the ``part_aware`` master switch."""

_FEATURE_LABELS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "part_aware": "The part-aware layer",
        "image_generation": "Image generation",
        "segmentation": "Segmentation",
        "part_analysis": "Part analysis",
        "workflows": "Workflows",
        "mesh_tuning": "Auto Mesh Tuner",
        "part_refinement": "Part refinement",
    }
)


class FeatureDisabledError(AppError):
    """A mutating route whose feature flag is off (GET routes keep working so history stays readable)."""

    status_code = 503
    code = "feature_disabled"

    def __init__(self, message: str, *, feature: str, flag: str) -> None:
        super().__init__(message, details={"feature": feature, "flag": flag})
        self.feature = feature
        self.flag = flag


class CapabilityUnavailableError(AppError):
    """No usable model/provider for a requested capability (``reasons`` = one entry per blocking model)."""

    status_code = 503
    code = "capability_unavailable"

    def __init__(self, message: str, *, capability: str, reasons: Iterable[Mapping[str, Any] | Any] = ()) -> None:
        reason_list = [_reason_dict(reason) for reason in reasons]
        super().__init__(message, details={"capability": capability, "reasons": reason_list})
        self.capability = capability
        self.reasons = reason_list


class ServiceNotImplementedError(CapabilityUnavailableError):
    """Raised by the S1 service stubs: the slice that implements ``capability`` has not merged yet."""

    def __init__(self, capability: str, *, operation: str) -> None:
        reason = f"{operation} is not implemented in this build"
        super().__init__(
            reason,
            capability=capability,
            reasons=[{"model_id": None, "state": None, "reason_code": "not_implemented", "reason": reason}],
        )
        self.operation = operation


class ModelUnavailableError(AppError):
    """An explicitly requested model is not usable (state and reason come from the registry)."""

    status_code = 503
    code = "model_unavailable"

    def __init__(self, message: str, *, model_id: str, state: str, reason_code: str) -> None:
        super().__init__(message, details={"model_id": model_id, "state": state, "reason_code": reason_code})
        self.model_id = model_id
        self.state = state
        self.reason_code = reason_code


class InvalidWorkflowError(AppError):
    """A workflow graph failed server-side validation (``issues`` are ``GraphIssue`` records or dicts)."""

    status_code = 422
    code = "invalid_workflow"

    def __init__(self, message: str, *, issues: Iterable[Mapping[str, Any] | Any]) -> None:
        issue_list = [_reason_dict(issue) for issue in issues]
        super().__init__(message, details={"issues": issue_list})
        self.issues = issue_list


class InsufficientVramError(AppError):
    """The live VRAM check at the start of a GPU phase failed (the run fails instead of running out of memory)."""

    status_code = 503
    code = "insufficient_vram"

    def __init__(self, message: str, *, model_ids: Iterable[str], free_mb: float, required_mb: float) -> None:
        ids = list(model_ids)
        super().__init__(
            message, details={"model_ids": ids, "free_mb": round(free_mb, 1), "required_mb": round(required_mb, 1)}
        )


class JobDeletedError(AppError):
    """The job a run belongs to was deleted; its folder is never recreated."""

    status_code = 409
    code = "job_deleted"

    def __init__(self, message: str = "The job was deleted", *, job_id: str) -> None:
        super().__init__(message, details={"job_id": job_id})
        self.job_id = job_id


def feature_enabled(settings: Settings, feature: FeatureName) -> bool:
    """``True`` if ``feature``'s flag is on (and, for run/workflow features, the ``part_aware`` master switch)."""
    if feature not in FEATURE_FLAGS:
        raise KeyError(f"Unknown feature {feature!r}")
    field, _env = FEATURE_FLAGS[feature]
    if not bool(getattr(settings, field)):
        return False
    if feature in _MASTER_GATED:
        return bool(settings.part_aware_enabled)
    return True


def require_feature(settings: Settings, feature: FeatureName) -> None:
    """Raise :class:`FeatureDisabledError` (503) naming the flag that is off."""
    if feature not in FEATURE_FLAGS:
        raise KeyError(f"Unknown feature {feature!r}")
    if feature in _MASTER_GATED and not settings.part_aware_enabled:
        _field, env = FEATURE_FLAGS["part_aware"]
        raise FeatureDisabledError(
            f"{_FEATURE_LABELS['part_aware']} is disabled ({env}=false)", feature="part_aware", flag=env
        )
    field, env = FEATURE_FLAGS[feature]
    if not bool(getattr(settings, field)):
        raise FeatureDisabledError(f"{_FEATURE_LABELS[feature]} is disabled ({env}=false)", feature=feature, flag=env)


def _reason_dict(value: Mapping[str, Any] | Any) -> dict[str, Any]:
    """JSON-friendly dict of a reason/issue given as a mapping, a dataclass or an object with ``model_dump``."""
    if isinstance(value, Mapping):
        return dict(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dict(dump(mode="json"))
    raise TypeError(f"Cannot convert {type(value).__name__} to an error detail")
