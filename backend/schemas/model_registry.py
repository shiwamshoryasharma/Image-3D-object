"""Model registry schemas: ``GET /models``, ``GET /models/{id}``, ``POST /models/{id}/load|unload`` (part-aware
DESIGN.md sections 5 and 6.1). Frozen in slice S1.

``ModelOut`` never carries an absolute path: ``local_path`` is relative to ``backend/``. ``FeaturesOut.models``
lists the same full ``ModelOut`` entries (hidden models excluded), each equal to ``GET /models/{id}``.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from inference.providers.base import CommercialUse, LoadedDevice, ModelKind, ModelUiState, ReasonCode
from models.types import UtcDateTime
from schemas.common import ApiModel


class LicenseOut(ApiModel):
    id: str
    name: str
    url: str
    commercial: CommercialUse
    gated: bool
    requires_acceptance: bool
    accepted: bool
    """``True`` when no acceptance is required (models are installed manually, the licence was accepted then)."""
    conditions: list[str] = Field(default_factory=list)
    """Use conditions the UI shows next to the model (e.g. the Sapiens2 licence restrictions)."""
    attribution: str | None = None


class DependencyOut(ApiModel):
    distribution: str
    requirement: str
    installed: str | None
    """Installed version, or ``None`` when the distribution is absent."""
    ok: bool


class InstallOut(ApiModel):
    """Install state. In-app downloads are not supported (deferred D5): weights are installed manually."""

    supported: bool = False
    hint: str | None = None
    bytes_done: int | None = None
    bytes_total: int | None = None


class ModelOut(ApiModel):
    id: str
    kind: ModelKind
    roles: list[str]
    display_name: str
    state: ModelUiState
    reason_code: ReasonCode
    reason: str | None
    approved: bool
    is_default: bool
    repo_id: str | None
    revision: str | None
    size_bytes: int | None
    vram_estimate_gb: float | None
    ram_gb: float | None
    cold_load_s: float | None
    license: LicenseOut
    dependencies: list[DependencyOut]
    vendored_source: str | None
    local_path: str | None
    """Relative to ``backend/`` (never absolute)."""
    install: InstallOut
    capabilities: dict[str, Any]
    loaded_device: LoadedDevice | None
    last_used_at: UtcDateTime | None
    hidden_by_default: bool


class ModelList(ApiModel):
    items: list[ModelOut]
