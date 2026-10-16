"""System/health schemas (contract 7.2)."""

from __future__ import annotations

from typing import Any

from pydantic import ConfigDict

from models.enums import EngineState, ReferenceMode
from schemas.common import ApiModel


class HealthResponse(ApiModel):
    status: str
    app: str
    version: str
    uptime_seconds: float
    time: str


class AppInfo(ApiModel):
    name: str
    version: str
    environment: str


class LoadProgressOut(ApiModel):
    loaded: int
    total: int
    current: str | None


class PolygonTargetOut(ApiModel):
    min: int
    max: int
    default: int


class CapabilitiesOut(ApiModel):
    resolutions: list[int]
    texture_sizes: list[int]
    polygon_target: PolygonTargetOut
    max_reference_images: int
    reference_modes: list[ReferenceMode]
    supports_cancel: bool
    supports_texture: bool


class EngineInfo(ApiModel):
    name: str
    state: EngineState
    message: str | None
    load_progress: LoadProgressOut | None
    error: str | None
    loaded_at: str | None
    details: dict[str, Any]
    capabilities: CapabilitiesOut


class DeviceInfo(ApiModel):
    """GPU/runtime description reported by the engine. Engines may add extra keys."""

    model_config = ConfigDict(extra="allow")

    cuda_available: bool | None = None
    name: str | None = None
    compute_capability: str | None = None
    vram_total_mb: int | float | None = None
    vram_used_mb: int | float | None = None
    vram_free_mb: int | float | None = None
    torch_version: str | None = None
    cuda_version: str | None = None


class QueueInfo(ApiModel):
    pending: int
    running_job_id: str | None
    max_size: int


class LimitsInfo(ApiModel):
    max_upload_mb: int
    allowed_mime_types: list[str]
    max_detail_images: int
    min_image_side: int
    slots: list[str]


class DefaultsInfo(ApiModel):
    resolution: int
    seed: int
    randomize_seed: bool
    polygon_target: int
    texture_size: int
    remove_background: bool
    reference_mode: ReferenceMode


class SystemStatus(ApiModel):
    app: AppInfo
    engine: EngineInfo
    device: DeviceInfo | None
    queue: QueueInfo
    limits: LimitsInfo
    defaults: DefaultsInfo
    auth_required: bool
