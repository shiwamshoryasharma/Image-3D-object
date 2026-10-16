"""Launcher settings (static configuration) and the persisted runtime selection (``blender_runtime.json``)."""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Literal, get_args

from .blender_version import MIN_SUPPORTED_VERSION, format_min_version, parse_min_version

logger = logging.getLogger(__name__)

RuntimeMode = Literal["auto", "system", "embedded"]
InstallKind = Literal["system", "embedded"]
DownloadChannel = Literal["latest", "lts"]

RUNTIME_MODES: Final[tuple[str, ...]] = get_args(RuntimeMode)
CONFIG_SCHEMA_VERSION: Final = 1
CONFIG_FILE_NAME: Final = "blender_runtime.json"
CACHE_FILE_NAME: Final = "detect_cache.json"
DOWNLOADS_DIR_NAME: Final = "downloads"
USER_RESOURCES_DIR_NAME: Final = "user"


def utcnow_iso() -> str:
    """Current UTC time as ISO-8601 with milliseconds and a ``Z`` suffix (same format as the backend)."""
    now = datetime.now(UTC)
    return now.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def atomic_write_text(path: Path, text: str) -> None:
    """Write UTF-8 text atomically (temp file in the same directory + ``os.replace``, retried on sharing errors)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{time.monotonic_ns()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        for attempt in range(1, 9):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:  # transient sharing violation (AV scanner, concurrent reader)
                if attempt == 8:
                    raise
                time.sleep(0.05 * attempt)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:  # pragma: no cover - best effort
            pass


@dataclass
class LauncherSettings:
    """Static configuration of the Blender runtime manager (built by the backend settings or the CLI)."""

    runtime_root: Path
    temp_root: Path
    min_version: tuple[int, int, int] = MIN_SUPPORTED_VERSION
    override_executable: Path | None = None
    extra_search_dirs: list[Path] = field(default_factory=list)
    allow_download: bool = True
    download_base_url: str = "https://download.blender.org/release/"
    download_channel: DownloadChannel = "latest"
    allowed_hosts: tuple[str, ...] = ("download.blender.org", "www.blender.org")
    user_agent: str = "Img3D-BlenderRuntime/1.0 (+local app)"
    detect_timeout_s: float = 20.0
    http_timeout_s: float = 30.0
    max_download_retries: int = 5
    keep_zip_after_install: bool = False
    require_https: bool = True

    def __post_init__(self) -> None:
        self.runtime_root = Path(self.runtime_root).absolute()
        self.temp_root = Path(self.temp_root).absolute()
        if isinstance(self.min_version, str):
            self.min_version = parse_min_version(self.min_version)
        else:
            parts = (tuple(self.min_version) + (0, 0, 0))[:3]
            self.min_version = (int(parts[0]), int(parts[1]), int(parts[2]))
        if self.override_executable is not None and str(self.override_executable).strip():
            self.override_executable = Path(os.path.expandvars(str(self.override_executable).strip().strip('"')))
        else:
            self.override_executable = None
        self.extra_search_dirs = [Path(os.path.expandvars(str(p))) for p in (self.extra_search_dirs or [])]
        if self.download_channel not in get_args(DownloadChannel):
            raise ValueError(f"Invalid download_channel {self.download_channel!r}")
        self.allowed_hosts = tuple(h.strip().lower() for h in self.allowed_hosts if h and h.strip())
        if not self.download_base_url.endswith("/"):
            self.download_base_url += "/"
        self.max_download_retries = max(0, int(self.max_download_retries))

    @property
    def min_version_label(self) -> str:
        """``"5.1"`` for ``(5, 1, 0)``."""
        return format_min_version(self.min_version)

    @property
    def config_path(self) -> Path:
        return self.runtime_root / CONFIG_FILE_NAME

    @property
    def cache_path(self) -> Path:
        return self.runtime_root / CACHE_FILE_NAME

    @property
    def downloads_dir(self) -> Path:
        return self.runtime_root / DOWNLOADS_DIR_NAME

    @property
    def user_resources_dir(self) -> Path:
        return self.runtime_root / USER_RESOURCES_DIR_NAME


@dataclass
class RuntimeConfig:
    """Persisted runtime selection (``runtime_root/blender_runtime.json``, schema_version 1)."""

    mode: RuntimeMode = "auto"
    pinned_executable: str | None = None
    selected_executable: str | None = None
    selected_version: str | None = None
    selected_kind: InstallKind | None = None
    updated_at: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RuntimeConfig:
        """Build from a JSON object. Invalid fields are replaced by defaults (with a warning)."""
        config = cls()
        mode = data.get("mode", "auto")
        if mode in RUNTIME_MODES:
            config.mode = mode
        else:
            logger.warning("Ignoring invalid Blender runtime mode %r in config; using 'auto'", mode)
        for name in ("pinned_executable", "selected_executable", "selected_version", "updated_at"):
            value = data.get(name)
            setattr(config, name, value if isinstance(value, str) and value else None)
        kind = data.get("selected_kind")
        config.selected_kind = kind if kind in get_args(InstallKind) else None
        return config

    @classmethod
    def load(cls, path: Path) -> RuntimeConfig:
        """Load the config. A missing or corrupt file yields defaults (logged); never raises."""
        try:
            raw = Path(path).read_text(encoding="utf-8")
        except FileNotFoundError:
            logger.debug("No Blender runtime config at %s; using defaults", path)
            return cls()
        except OSError as exc:
            logger.warning("Could not read Blender runtime config %s (%s); using defaults", path, exc)
            return cls()
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("top-level JSON value is not an object")
        except ValueError as exc:
            logger.warning("Corrupt Blender runtime config %s (%s); using defaults", path, exc)
            return cls()
        return cls.from_dict(data)

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": CONFIG_SCHEMA_VERSION, **asdict(self)}

    def save(self, path: Path) -> None:
        """Persist atomically (stamps ``updated_at``)."""
        self.updated_at = utcnow_iso()
        atomic_write_text(Path(path), json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n")
