"""Launcher exception hierarchy.

Every error carries a stable snake_case ``code`` and a human readable ``message``. The backend services map these
onto their own ``AppError`` subclasses (HTTP status codes live there, not here, so this package stays standalone).
"""

from __future__ import annotations

from typing import Any


class LauncherError(Exception):
    """Base class for every expected Blender-runtime error."""

    code: str = "launcher_error"

    def __init__(self, message: str, *, code: str | None = None, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"{type(self).__name__}(code={self.code!r}, message={self.message!r})"


class BlenderNotFoundError(LauncherError):
    """The path does not exist or is not a working Blender executable."""

    code = "blender_not_found"


class BlenderIncompatibleError(LauncherError):
    """Blender runs but cannot be used (older than the minimum, wrong architecture, not a console exe)."""

    code = "blender_incompatible"


class BlenderUnavailableError(LauncherError):
    """No usable runtime is selected. ``message`` is the user prompt (e.g. offer a download)."""

    code = "blender_unavailable"


class DownloadsDisabledError(LauncherError):
    code = "blender_download_disabled"


class DownloadInProgressError(LauncherError):
    code = "blender_download_in_progress"


class DownloadError(LauncherError):
    """Network, HTTP or disk failure while downloading/installing a portable Blender."""

    code = "blender_download_failed"


class ChecksumMismatchError(DownloadError):
    code = "blender_checksum_mismatch"


class UnsafeArchiveError(DownloadError):
    code = "blender_unsafe_archive"


class DownloadCancelledError(LauncherError):
    code = "blender_download_cancelled"

    def __init__(self, message: str = "Blender download was cancelled", **kwargs: Any) -> None:
        super().__init__(message, **kwargs)


class ReleaseResolveError(DownloadError):
    code = "blender_release_unresolved"


class PathTooLongError(LauncherError):
    """A path handed to Blender exceeds MAX_PATH (Blender executables are not long-path aware)."""

    code = "blender_path_too_long"
