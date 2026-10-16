"""Validation of :class:`schemas.jobs.GenerationSettings` against the engine's capabilities (422 on mismatch)."""

from __future__ import annotations

from typing import Any

from core.exceptions import ValidationAppError
from inference.base import EngineCapabilities
from models.enums import ReferenceMode
from schemas.jobs import GenerationSettings


def validate_against_capabilities(
    settings: GenerationSettings, reference_mode: ReferenceMode, capabilities: EngineCapabilities
) -> None:
    """Raise :class:`ValidationAppError` listing every unsupported value."""
    issues: list[dict[str, Any]] = []

    def issue(field: str, msg: str, **ctx: Any) -> None:
        issues.append({"loc": ["body", "settings", field], "msg": msg, "type": "unsupported_by_engine", "ctx": ctx})

    if settings.resolution not in capabilities.resolutions:
        issue(
            "resolution",
            f"Resolution {settings.resolution} is not supported; allowed: {list(capabilities.resolutions)}",
            allowed=list(capabilities.resolutions),
        )
    if settings.texture_size not in capabilities.texture_sizes:
        issue(
            "texture_size",
            f"Texture size {settings.texture_size} is not supported; allowed: {list(capabilities.texture_sizes)}",
            allowed=list(capabilities.texture_sizes),
        )
    poly = capabilities.polygon_target
    if not poly.min <= settings.polygon_target <= poly.max:
        issue(
            "polygon_target",
            f"Polygon target must be between {poly.min} and {poly.max}",
            min=poly.min,
            max=poly.max,
        )
    if reference_mode not in capabilities.reference_modes:
        issue(
            "reference_mode",
            f"Reference mode '{reference_mode}' is not supported by the current engine",
            allowed=[str(m) for m in capabilities.reference_modes],
        )
    if issues:
        raise ValidationAppError("Generation settings are not supported by the current engine", details=issues)
