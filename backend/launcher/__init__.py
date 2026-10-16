"""Blender runtime management for the Auto Mesh Tuner (standalone, stdlib only, no FastAPI/backend imports).

Detects installed Blender builds (executing each one to read the real version), applies the selection policy
(>= 5.1, stable only unless pinned), optionally downloads the official portable ZIP into ``runtime/blender`` and
runs Blender in background mode with a kill-tree Job Object. See ``.claude/skills/img3d-auto-mesh-tuner/DESIGN.md``.
"""

from __future__ import annotations

from .blender_detector import SOURCE_NAMES, BlenderDetector, BlenderInstallation, normalize_executable
from .blender_downloader import BlenderDownloader, DownloadProgress, DownloadState, ReleaseInfo, make_opener
from .blender_runtime import (
    BlenderRuntimeManager,
    RuntimeState,
    RuntimeStatus,
    SelectionDecision,
    needs_download_message,
    select_runtime,
)
from .blender_version import (
    MIN_SUPPORTED_VERSION,
    BlenderVersion,
    format_min_version,
    parse_blender_version,
    parse_min_version,
)
from .config import InstallKind, LauncherSettings, RuntimeConfig, RuntimeMode
from .errors import (
    BlenderIncompatibleError,
    BlenderNotFoundError,
    BlenderUnavailableError,
    ChecksumMismatchError,
    DownloadCancelledError,
    DownloadError,
    DownloadInProgressError,
    DownloadsDisabledError,
    LauncherError,
    PathTooLongError,
    ReleaseResolveError,
    UnsafeArchiveError,
)
from .platform_win import KillOnCloseJob, long_paths_enabled, native_architecture, pe_info
from .process_runner import (
    PROTOCOL_SENTINEL,
    SCRIPT_EXCEPTION_EXIT_CODE,
    BlenderProcessRunner,
    BlenderRunRequest,
    BlenderRunResult,
    CaptureResult,
    ProcessCapture,
    RunStatus,
    parse_protocol_line,
    run_capture,
)

__all__ = [
    "MIN_SUPPORTED_VERSION",
    "PROTOCOL_SENTINEL",
    "SCRIPT_EXCEPTION_EXIT_CODE",
    "SOURCE_NAMES",
    "BlenderDetector",
    "BlenderDownloader",
    "BlenderIncompatibleError",
    "BlenderInstallation",
    "BlenderNotFoundError",
    "BlenderProcessRunner",
    "BlenderRunRequest",
    "BlenderRunResult",
    "BlenderRuntimeManager",
    "BlenderUnavailableError",
    "BlenderVersion",
    "CaptureResult",
    "ChecksumMismatchError",
    "DownloadCancelledError",
    "DownloadError",
    "DownloadInProgressError",
    "DownloadProgress",
    "DownloadState",
    "DownloadsDisabledError",
    "InstallKind",
    "KillOnCloseJob",
    "LauncherError",
    "LauncherSettings",
    "PathTooLongError",
    "ProcessCapture",
    "ReleaseInfo",
    "ReleaseResolveError",
    "RunStatus",
    "RuntimeConfig",
    "RuntimeMode",
    "RuntimeState",
    "RuntimeStatus",
    "SelectionDecision",
    "UnsafeArchiveError",
    "format_min_version",
    "long_paths_enabled",
    "make_opener",
    "native_architecture",
    "needs_download_message",
    "normalize_executable",
    "parse_blender_version",
    "parse_min_version",
    "parse_protocol_line",
    "pe_info",
    "run_capture",
    "select_runtime",
]
