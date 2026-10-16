"""Application settings.

Values come from (highest priority first): init kwargs, process environment, ``backend/.env``, defaults.
App settings use the ``IMG3D_`` prefix; the secrets that already live in ``.env`` are unprefixed
(``HF_TOKEN``, ``UI_USERNAME``, ``UI_PASSWORD``). Secrets are :class:`~pydantic.SecretStr` and never logged.

Relative paths are resolved against the backend directory (never the current working directory).
"""

from __future__ import annotations

import json
import os
import re
from functools import cached_property, lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from models.enums import ReferenceMode

if TYPE_CHECKING:  # pragma: no cover
    from storage.paths import RuntimePaths

BACKEND_DIR: Path = Path(__file__).resolve().parent.parent
"""Absolute path of the ``backend`` directory (parent of the ``config`` package)."""

ENV_FILE: Path = BACKEND_DIR / ".env"

APP_VERSION = "1.0.0"

_DEFAULT_CORS = ["http://localhost:5173", "http://127.0.0.1:5173"]
_VERSION_RE = re.compile(r"^\d+\.\d+(\.\d+)?$")
BLENDER_RUNTIME_DIR_NAME = "blender"


def _resolve_backend_path(value: Path) -> Path:
    """Expand ``%VARS%``/``~`` and resolve a relative path against the backend directory."""
    path = Path(os.path.expandvars(str(value).strip().strip('"'))).expanduser()
    if not path.is_absolute():
        path = BACKEND_DIR / path
    return path.resolve()


class Settings(BaseSettings):
    """All runtime configuration for the Img3D backend."""

    model_config = SettingsConfigDict(
        env_prefix="IMG3D_",
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
        validate_default=True,
        case_sensitive=False,
    )

    # ---------------------------------------------------------------- application
    app_name: str = "Img3D"
    environment: Literal["development", "production"] = "development"
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=lambda: list(_DEFAULT_CORS))

    # ---------------------------------------------------------------- model / engine
    model_dir: Path = Path(".model/TRELLIS.2-4B")
    aux_model_root: Path = Path(".model")
    trellis_source_dir: Path = Path("third_party/TRELLIS.2")
    hf_home: Path = Path(".model/.hf_cache")
    hf_token: SecretStr | None = Field(default=None, validation_alias=AliasChoices("HF_TOKEN", "hf_token"))
    allow_model_downloads: bool = True
    engine: str = "trellis2"
    device: str = "cuda"
    low_vram: bool = True
    attention_backend: Literal["auto", "flash_attn", "xformers", "sdpa"] = "auto"
    preload_model: bool = True
    reference_mode: ReferenceMode = ReferenceMode.PRIMARY_ONLY
    glb_webp_textures: bool = True

    # ---------------------------------------------------------------- storage / logging
    runtime_dir: Path = Path("runtime")
    log_dir: Path = Path("logs")
    log_level: str = "INFO"
    log_json_console: bool = False

    # ---------------------------------------------------------------- limits
    max_upload_mb: int = Field(default=25, ge=1, le=1024)
    max_image_pixels: int = Field(default=64_000_000, ge=1_000_000)
    min_image_side: int = Field(default=64, ge=1)
    max_detail_images: int = Field(default=8, ge=0, le=64)
    max_queue_size: int = Field(default=16, ge=1, le=1000)
    job_retention_days: int = Field(default=14, ge=0)
    max_stored_jobs: int = Field(default=200, ge=1)
    reference_set_retention_days: int = Field(default=30, ge=0)

    # ---------------------------------------------------------------- auth
    auth_enabled: bool | None = None
    ui_username: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("UI_USERNAME", "ui_username")
    )
    ui_password: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("UI_PASSWORD", "ui_password")
    )
    auth_token_ttl_hours: float = Field(default=12, gt=0, le=24 * 30)
    auth_secret: SecretStr | None = None

    # ---------------------------------------------------------------- serving / runtime tuning
    frontend_dist_dir: Path = Path("../frontend/dist")
    shutdown_timeout_s: float = Field(default=20, ge=0)
    ws_ping_interval_s: float = Field(default=20.0, gt=0)
    device_info_timeout_s: float = Field(default=2.0, gt=0)
    device_info_cache_s: float = Field(default=5.0, ge=0)
    retention_interval_s: float = Field(default=3600.0, gt=0)

    # ---------------------------------------------------------------- Auto Mesh Tuner (Blender runtime)
    blender_enabled: bool = True
    blender_executable: Path | None = None
    """Administrator override (highest selection priority when compatible)."""
    blender_search_dirs: Annotated[list[Path], NoDecode] = Field(default_factory=list)
    blender_min_version: str = "5.1"
    blender_install_dir: Path | None = None
    """Portable Blender root (``None`` -> ``<runtime_dir>/blender``)."""
    blender_allow_download: bool = True
    blender_download_base_url: str = "https://download.blender.org/release/"
    blender_download_channel: Literal["latest", "lts"] = "latest"
    blender_detect_on_startup: bool = True
    blender_detect_timeout_s: float = Field(default=20.0, gt=0)
    blender_tune_timeout_s: float = Field(default=1800.0, gt=0)
    blender_max_queue_size: int = Field(default=4, ge=1, le=100)

    # ---------------------------------------------------------------- part-aware layer (part-aware DESIGN.md 8.1)
    part_aware_enabled: bool = True
    """Master switch: when false no RunWorker/workflow sweep runs and mutating part-aware routes return 503."""
    image_generation_enabled: bool = True
    segmentation_enabled: bool = True
    part_analysis_enabled: bool = True
    workflows_enabled: bool = True
    mesh_tuning_enabled: bool = False
    """Code gate of ``POST /jobs/{id}/tunings`` (tuning stays paused until approved)."""
    part_refinement_enabled: bool = False
    approved_models: Annotated[list[str] | None, NoDecode] = None
    """``None`` -> the catalog models with ``default_approved=True``; ``[]`` approves nothing."""
    model_paths: Annotated[dict[str, Path], NoDecode] = Field(default_factory=dict)
    """Model id -> local weights directory override (relative paths resolve against the backend directory)."""
    allow_restricted_licenses: bool = False
    t2i_default_model: str | None = None
    segmentation_default_profile: Literal["auto", "baseline", "anime", "realistic"] = "auto"
    run_queue_max_size: int = Field(default=8, ge=1, le=100)
    """Shared by image generations, segmentations and part analyses."""
    aux_keep_models_in_ram: bool = True
    aux_model_idle_unload_s: float = Field(default=0.0, ge=0)
    """0 = never unload idle auxiliary models."""
    aux_vram_budget_gb: float = Field(default=28.0, gt=0)
    engine_resident_vram_gb: float = Field(default=17.0, ge=0)
    aux_vram_margin_gb: float = Field(default=1.5, ge=0)
    prompt_max_chars: int = Field(default=2000, ge=1, le=8000)
    parts_default_views: int = Field(default=8, ge=0, le=24)
    parts_min_part_faces: int | None = Field(default=None, ge=0, le=100_000)
    """``None`` -> ``max(50, round(1e-4 * faces))`` per analysis."""
    parts_max_parts: int = Field(default=64, ge=1, le=256)
    run_max_defer_s: float = Field(default=600.0, ge=0, le=3600)
    """Longest wait of an auxiliary GPU phase behind pending/running jobs (0 = no jobs-first gate)."""
    parts_export_cache_max_gb: float = Field(default=10.0, ge=0)
    part_analysis_timeout_s: float = Field(default=1800.0, gt=0)
    """Watchdog of part analyses, counted over active phase time only."""
    workflow_graph_max_bytes: int = Field(default=65536, ge=1024, le=1_048_576)
    workflow_sweep_interval_s: float = Field(default=5.0, gt=0)
    imagegen_retention_days: int = Field(default=14, ge=0)
    segmentation_retention_days: int = Field(default=14, ge=0)
    workflow_retention_days: int = Field(default=30, ge=0)
    max_stored_image_generations: int = Field(default=200, ge=1)
    max_stored_segmentations: int = Field(default=200, ge=1)
    max_stored_workflows: int = Field(default=500, ge=1)
    persist_generation_intermediates: bool = False
    """Inert (SD1 not approved): nothing reads it and ``/features`` always reports ``false``."""

    # ------------------------------------------------------------------ validators
    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_cors(cls, value: Any) -> Any:
        """Accept a JSON list (``["a","b"]``) or a comma-separated string (``a,b``)."""
        if value is None:
            return []
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return []
            if text.startswith("["):
                return json.loads(text)
            return [part.strip() for part in text.split(",") if part.strip()]
        return value

    @field_validator(
        "hf_token",
        "ui_username",
        "ui_password",
        "auth_secret",
        "auth_enabled",
        "blender_executable",
        "blender_install_dir",
        mode="before",
    )
    @classmethod
    def _blank_secret_is_none(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("blender_search_dirs", mode="before")
    @classmethod
    def _parse_search_dirs(cls, value: Any) -> Any:
        """Accept a JSON list (``["C:/a","D:/b"]``) or a ``;``-separated string (``C:/a;D:/b``)."""
        if value is None:
            return []
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return []
            if text.startswith("["):
                return json.loads(text)
            return [part.strip().strip('"') for part in text.split(";") if part.strip()]
        return value

    @field_validator("approved_models", mode="before")
    @classmethod
    def _parse_approved_models(cls, value: Any) -> Any:
        """JSON list (``[]`` approves nothing) or ``,``/``;``-separated ids; blank -> ``None`` (catalog defaults)."""
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            if text.startswith("["):
                return json.loads(text)
            return [part.strip() for part in re.split(r"[,;]", text) if part.strip()]
        return value

    @field_validator("approved_models", mode="after")
    @classmethod
    def _strip_approved_models(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return [item.strip() for item in value if item.strip()]

    @field_validator("model_paths", mode="before")
    @classmethod
    def _parse_model_paths(cls, value: Any) -> Any:
        """JSON object ``{"model_id": "path", ...}``; blank -> no overrides."""
        if value is None:
            return {}
        if isinstance(value, str):
            text = value.strip()
            return json.loads(text) if text else {}
        return value

    @field_validator("t2i_default_model", "parts_min_part_faces", mode="before")
    @classmethod
    def _blank_is_none(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("blender_min_version", mode="before")
    @classmethod
    def _validate_min_version(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = value.strip()
            if not _VERSION_RE.match(value):
                raise ValueError("blender_min_version must look like '5.1' or '5.1.0'")
        return value

    @field_validator("blender_download_base_url", mode="after")
    @classmethod
    def _validate_download_url(cls, value: str) -> str:
        value = value.strip()
        if not value.lower().startswith("https://"):
            raise ValueError("blender_download_base_url must be an https:// URL")
        return value if value.endswith("/") else value + "/"

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalize_log_level(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = value.strip().upper()
            if value not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
                raise ValueError("log_level must be one of CRITICAL, ERROR, WARNING, INFO, DEBUG")
        return value

    @model_validator(mode="after")
    def _resolve_paths(self) -> Settings:
        for name in (
            "model_dir",
            "aux_model_root",
            "trellis_source_dir",
            "hf_home",
            "runtime_dir",
            "log_dir",
            "frontend_dist_dir",
        ):
            value: Path = getattr(self, name)
            if not value.is_absolute():
                value = BACKEND_DIR / value
            object.__setattr__(self, name, value.resolve())
        for name in ("blender_executable", "blender_install_dir"):
            optional: Path | None = getattr(self, name)
            if optional is not None:
                object.__setattr__(self, name, _resolve_backend_path(optional))
        object.__setattr__(self, "blender_search_dirs", [_resolve_backend_path(p) for p in self.blender_search_dirs])
        object.__setattr__(
            self, "model_paths", {model_id: _resolve_backend_path(p) for model_id, p in self.model_paths.items()}
        )
        return self

    # ------------------------------------------------------------------ derived values
    @property
    def app_version(self) -> str:
        return APP_VERSION

    @property
    def backend_dir(self) -> Path:
        return BACKEND_DIR

    @property
    def auth_required(self) -> bool:
        """Explicit ``auth_enabled`` wins; otherwise auth is on iff UI_USERNAME and UI_PASSWORD are both set."""
        if self.auth_enabled is not None:
            return self.auth_enabled
        return self.has_ui_credentials

    @property
    def has_ui_credentials(self) -> bool:
        return bool(
            self.ui_username
            and self.ui_username.get_secret_value()
            and self.ui_password
            and self.ui_password.get_secret_value()
        )

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @cached_property
    def paths(self) -> RuntimePaths:
        from storage.paths import RuntimePaths

        return RuntimePaths(self.runtime_dir)

    @property
    def blender_runtime_dir(self) -> Path:
        """Root of the managed Blender runtime (persisted selection, portable builds, downloads)."""
        return self.blender_install_dir or (self.runtime_dir / BLENDER_RUNTIME_DIR_NAME)

    @property
    def blender_temp_dir(self) -> Path:
        """Parent of the per-run Blender ``TEMP``/``TMP`` folders (inside ``temp/``, purged at startup)."""
        return self.runtime_dir / "temp" / BLENDER_RUNTIME_DIR_NAME

    @property
    def blender_scripts_dir(self) -> Path:
        """Blender-side scripts (run inside Blender's Python, never imported by the backend)."""
        return BACKEND_DIR / "blender_scripts"

    def safe_summary(self) -> dict[str, Any]:
        """Non-secret configuration summary for startup logs."""
        return {
            "environment": self.environment,
            "engine": self.engine,
            "device": self.device,
            "low_vram": self.low_vram,
            "attention_backend": self.attention_backend,
            "preload_model": self.preload_model,
            "reference_mode": str(self.reference_mode),
            "model_dir": str(self.model_dir),
            "runtime_dir": str(self.runtime_dir),
            "log_dir": str(self.log_dir),
            "max_upload_mb": self.max_upload_mb,
            "max_queue_size": self.max_queue_size,
            "auth_required": self.auth_required,
            "cors_origins": list(self.cors_origins),
            "frontend_dist_dir": str(self.frontend_dist_dir),
            "blender_enabled": self.blender_enabled,
            "blender_executable": str(self.blender_executable) if self.blender_executable else None,
            "blender_search_dirs": [str(p) for p in self.blender_search_dirs],
            "blender_min_version": self.blender_min_version,
            "blender_runtime_dir": str(self.blender_runtime_dir),
            "blender_allow_download": self.blender_allow_download,
            "blender_download_channel": self.blender_download_channel,
            "blender_detect_on_startup": self.blender_detect_on_startup,
            "blender_tune_timeout_s": self.blender_tune_timeout_s,
            "blender_max_queue_size": self.blender_max_queue_size,
        }

    def part_aware_summary(self) -> dict[str, Any]:
        """Non-secret summary of the part-aware settings (logged by the part-aware container at startup)."""
        return {
            "part_aware_enabled": self.part_aware_enabled,
            "image_generation_enabled": self.image_generation_enabled,
            "segmentation_enabled": self.segmentation_enabled,
            "part_analysis_enabled": self.part_analysis_enabled,
            "workflows_enabled": self.workflows_enabled,
            "mesh_tuning_enabled": self.mesh_tuning_enabled,
            "part_refinement_enabled": self.part_refinement_enabled,
            "approved_models": list(self.approved_models) if self.approved_models is not None else None,
            "model_paths": {model_id: str(path) for model_id, path in self.model_paths.items()},
            "allow_restricted_licenses": self.allow_restricted_licenses,
            "aux_model_root": str(self.aux_model_root),
            "t2i_default_model": self.t2i_default_model,
            "segmentation_default_profile": self.segmentation_default_profile,
            "run_queue_max_size": self.run_queue_max_size,
            "aux_keep_models_in_ram": self.aux_keep_models_in_ram,
            "aux_model_idle_unload_s": self.aux_model_idle_unload_s,
            "aux_vram_budget_gb": self.aux_vram_budget_gb,
            "engine_resident_vram_gb": self.engine_resident_vram_gb,
            "aux_vram_margin_gb": self.aux_vram_margin_gb,
            "run_max_defer_s": self.run_max_defer_s,
            "part_analysis_timeout_s": self.part_analysis_timeout_s,
            "parts_export_cache_max_gb": self.parts_export_cache_max_gb,
            "persist_generation_intermediates": False,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide cached settings instance."""
    return Settings()
