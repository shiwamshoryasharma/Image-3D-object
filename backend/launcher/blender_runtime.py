"""Blender runtime manager: detection + selection policy + persisted choice + background portable download.

Selection policy (DESIGN.md section 2). Compatible = valid, version >= min, runnable architecture (x64 on ARM64 only
as a fallback), console executable. Automatic choices only consider *stable* builds; a compatible alpha/beta/RC is
used only when pinned explicitly (with a warning).

0. ``override_executable`` (IMG3D_BLENDER_EXECUTABLE) if compatible.
1. mode ``embedded``: the pinned embedded install, else the highest stable embedded install.
2. mode ``system`` with a pin: the pinned system install.
3. The highest stable compatible system install.
4. The highest stable compatible embedded install.
5. Nothing: state ``needs_download`` with a user prompt.

An unusable explicit choice falls back down the list and adds a warning; the mode stays as chosen.
All public methods are thread-safe (one RLock, never held while running Blender or doing network I/O);
``on_change`` is called outside the lock after every state change (download progress throttled to 4/s).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Final, Literal

from .blender_detector import BlenderDetector, BlenderInstallation, normalize_executable, sort_installations
from .blender_downloader import BlenderDownloader, DownloadProgress, ReleaseInfo
from .config import RUNTIME_MODES, LauncherSettings, RuntimeConfig, RuntimeMode, utcnow_iso
from .errors import (
    BlenderIncompatibleError,
    BlenderNotFoundError,
    BlenderUnavailableError,
    DownloadCancelledError,
    DownloadError,
    DownloadInProgressError,
    DownloadsDisabledError,
    LauncherError,
)
from .process_runner import BlenderProcessRunner

logger = logging.getLogger(__name__)

RuntimeState = Literal["detecting", "ready", "needs_download", "downloading", "error"]

RELEASE_CACHE_TTL_S: Final = 3600.0
PROGRESS_NOTIFY_INTERVAL_S: Final = 0.25

REASON_OVERRIDE: Final = "Configured override"
REASON_PINNED_EMBEDDED: Final = "Pinned embedded Blender"
REASON_HIGHEST_EMBEDDED: Final = "Highest compatible embedded Blender"
REASON_PINNED_SYSTEM: Final = "Pinned system Blender"
REASON_HIGHEST_SYSTEM: Final = "Highest compatible system Blender"


@dataclass
class RuntimeStatus:
    state: RuntimeState
    mode: RuntimeMode
    min_version: str
    selected: BlenderInstallation | None
    selection_reason: str | None
    installations: list[BlenderInstallation]
    message: str | None
    warnings: list[str]
    runtime_root: str
    can_download: bool
    download: DownloadProgress | None
    release: ReleaseInfo | None
    last_scan_at: str | None
    scan_duration_ms: float | None
    error: str | None

    def to_dict(self) -> dict[str, Any]:
        selected_id = self.selected.id if self.selected else None

        def install(item: BlenderInstallation) -> dict[str, Any]:
            data = item.to_dict()
            data["selected"] = item.id == selected_id
            return data

        return {
            "state": self.state,
            "mode": self.mode,
            "min_version": self.min_version,
            "selected": install(self.selected) if self.selected else None,
            "selection_reason": self.selection_reason,
            "installations": [install(item) for item in self.installations],
            "message": self.message,
            "warnings": list(self.warnings),
            "runtime_root": self.runtime_root,
            "can_download": self.can_download,
            "download": self.download.to_dict() if self.download else None,
            "release": self.release.to_dict() if self.release else None,
            "last_scan_at": self.last_scan_at,
            "scan_duration_ms": self.scan_duration_ms,
            "error": self.error,
        }


@dataclass
class SelectionDecision:
    """Pure result of :func:`select_runtime`."""

    selected: BlenderInstallation | None
    reason: str | None
    message: str | None
    warnings: list[str] = field(default_factory=list)


def _key(path: str | os.PathLike[str]) -> str:
    try:
        return os.path.normcase(os.path.realpath(os.fspath(path)))
    except (OSError, ValueError):
        return os.path.normcase(os.path.abspath(os.fspath(path)))


def _rank(item: BlenderInstallation) -> tuple[Any, ...]:
    assert item.version is not None
    return (0 if item.emulated else 1, item.version.sort_key())


def _best(items: list[BlenderInstallation]) -> BlenderInstallation | None:
    return max(items, key=_rank) if items else None


def _short(item: BlenderInstallation) -> str:
    return item.version.label_without_lts if item.version else str(item.executable)


def _why_unusable(item: BlenderInstallation | None, required_kind: str | None = None) -> str:
    if item is None:
        return "not found"
    if not item.valid:
        return item.error or "not a working Blender executable"
    if not item.compatible:
        return item.incompatible_reason or "incompatible"
    if required_kind and item.kind != required_kind:
        return f"it is a {item.kind} install"
    return "unknown reason"


def select_runtime(
    installations: list[BlenderInstallation], config: RuntimeConfig, settings: LauncherSettings
) -> SelectionDecision:
    """Apply the selection policy (pure function; no I/O)."""
    by_path = {_key(item.executable): item for item in installations}
    compatible = [i for i in installations if i.compatible and i.version is not None]
    stable = [i for i in compatible if i.version is not None and i.version.is_stable]
    stable_system = [i for i in stable if i.kind == "system"]
    stable_embedded = [i for i in stable if i.kind == "embedded"]
    warnings: list[str] = []

    def explicit(item: BlenderInstallation, reason: str) -> SelectionDecision:
        if item.version is not None and not item.version.is_stable:
            warnings.append(
                f"Blender {item.version.label} is not a stable release; it is used only because it was selected "
                "explicitly."
            )
        return SelectionDecision(item, reason, None, warnings)

    # 0. administrator override
    if settings.override_executable is not None:
        override = by_path.get(_key(normalize_executable(settings.override_executable)))
        if override is not None and override.compatible:
            return explicit(override, REASON_OVERRIDE)
        warnings.append(
            f"Configured Blender override {settings.override_executable} is not usable "
            f"({_why_unusable(override)}); ignoring it."
        )

    mode = config.mode
    pin = config.pinned_executable
    pinned = by_path.get(_key(normalize_executable(pin))) if pin else None

    pin_problem: str | None = None

    # 1. embedded mode
    if mode == "embedded":
        if pin:
            if pinned is not None and pinned.compatible and pinned.kind == "embedded":
                return explicit(pinned, REASON_PINNED_EMBEDDED)
            pin_problem = f"Pinned Blender {pin} is not usable ({_why_unusable(pinned, 'embedded')})"
        best = _best(stable_embedded)
        if best is not None:
            if pin_problem:
                warnings.append(f"{pin_problem} — using embedded Blender {_short(best)}.")
            return SelectionDecision(best, REASON_HIGHEST_EMBEDDED, None, warnings)
        fallback = _best(stable_system)
        if fallback is not None:
            if pin_problem:
                warnings.append(f"{pin_problem}.")
            warnings.append(
                f"Embedded Blender is selected but not installed — using system Blender {_short(fallback)}."
            )
            return SelectionDecision(fallback, REASON_HIGHEST_SYSTEM, None, warnings)

    # 2. pinned system install
    if mode == "system" and pin:
        if pinned is not None and pinned.compatible and pinned.kind == "system":
            return explicit(pinned, REASON_PINNED_SYSTEM)
        pin_problem = f"Pinned Blender {pin} is not usable ({_why_unusable(pinned, 'system')})"

    # 3. highest stable system install
    best = _best(stable_system)
    if best is not None:
        if pin_problem:
            warnings.append(f"{pin_problem} — using system Blender {_short(best)}.")
        return SelectionDecision(best, REASON_HIGHEST_SYSTEM, None, warnings)

    # 4. highest stable embedded install
    best = _best(stable_embedded)
    if best is not None:
        if pin_problem:
            warnings.append(f"{pin_problem} — using embedded Blender {_short(best)}.")
        elif mode == "system":
            warnings.append(
                f"System Blender is selected but no compatible system install was found — using embedded "
                f"Blender {_short(best)}."
            )
        return SelectionDecision(best, REASON_HIGHEST_EMBEDDED, None, warnings)

    # 5. nothing usable
    if pin_problem:
        warnings.append(f"{pin_problem}.")
    return SelectionDecision(None, None, needs_download_message(installations, settings), warnings)


def needs_download_message(installations: list[BlenderInstallation], settings: LauncherSettings) -> str:
    """User prompt when no runtime can be selected (exact wording from DESIGN.md)."""
    minimum = settings.min_version_label
    tail = (
        "Download a supported portable Blender?"
        if settings.allow_download
        else f"Install Blender {minimum} or newer and detect again."
    )
    unstable = [i for i in installations if i.compatible and i.version is not None]
    if unstable:  # only alpha/beta/RC builds qualify (never auto-selected)
        top = max(unstable, key=_rank)
        return (
            f"Blender {_short(top)} is installed but is not a stable release. Auto Mesh Tuner requires a stable "
            f"Blender {minimum} or newer. {tail}"
        )
    valid_system = [i for i in installations if i.valid and i.kind == "system" and i.version is not None]
    if valid_system:
        top = max(valid_system, key=lambda i: i.version.sort_key())  # type: ignore[union-attr]
        return f"Blender {_short(top)} is installed but Auto Mesh Tuner requires Blender {minimum} or newer. {tail}"
    return f"No Blender installation was found. Auto Mesh Tuner requires Blender {minimum} or newer. {tail}"


class BlenderRuntimeManager:
    """Owns the detected installations, the persisted selection and the (single) background download."""

    def __init__(
        self,
        settings: LauncherSettings,
        *,
        detector: BlenderDetector | None = None,
        downloader: BlenderDownloader | None = None,
        runner: BlenderProcessRunner | None = None,
        on_change: Callable[[RuntimeStatus], None] | None = None,
    ) -> None:
        self._settings = settings
        self._detector = detector or BlenderDetector(settings)
        self._downloader = downloader or BlenderDownloader(settings)
        self._runner = runner or BlenderProcessRunner()
        self._on_change = on_change
        self._lock = threading.RLock()
        self._scan_lock = threading.RLock()  # serializes detection; re-entrant for on_change listeners
        self._config = RuntimeConfig.load(settings.config_path)
        self._installations: list[BlenderInstallation] = []
        self._decision = SelectionDecision(None, None, None, [])
        self._state: RuntimeState = "detecting"
        self._error: str | None = None
        self._scanned = False
        self._last_scan_at: str | None = None
        self._scan_ms: float | None = None
        self._downloading = False
        self._worker: threading.Thread | None = None
        self._cancel = threading.Event()
        self._download: DownloadProgress | None = None
        self._release: ReleaseInfo | None = None
        self._release_at = 0.0
        self._last_progress_notify = 0.0

    # ------------------------------------------------------------------ accessors
    @property
    def settings(self) -> LauncherSettings:
        return self._settings

    @property
    def runner(self) -> BlenderProcessRunner:
        return self._runner

    @property
    def detector(self) -> BlenderDetector:
        return self._detector

    @property
    def downloader(self) -> BlenderDownloader:
        return self._downloader

    def config(self) -> RuntimeConfig:
        with self._lock:
            return replace(self._config)

    def status(self) -> RuntimeStatus:
        with self._lock:
            return self._snapshot()

    # ------------------------------------------------------------------ detection
    def scan(self, *, force: bool = False) -> RuntimeStatus:
        """Detect installations (blocking), apply the policy, persist the selection."""
        with self._scan_lock:
            with self._lock:
                if not self._downloading:
                    self._state = "detecting"
                extra = [self._config.pinned_executable] if self._config.pinned_executable else []
            self._notify()
            started = time.perf_counter()
            try:
                installations = self._detector.detect(force=force, extra=extra)
            except Exception as exc:  # noqa: BLE001 - detection must never take the app down
                logger.exception("Blender detection failed")
                with self._lock:
                    self._state = "downloading" if self._downloading else "error"
                    self._error = f"Blender detection failed: {exc}"
                    snapshot = self._snapshot()
                self._notify(snapshot)
                return snapshot
            with self._lock:
                self._installations = sort_installations(installations)
                self._scanned = True
                self._last_scan_at = utcnow_iso()
                self._scan_ms = round((time.perf_counter() - started) * 1000, 1)
                if self._state != "downloading":
                    self._error = None
                self._apply_policy()
                snapshot = self._snapshot()
            sel = snapshot.selected
            logger.info(
                "Blender scan: %d installation(s) in %.0f ms; selected %s (%s)",
                len(installations),
                snapshot.scan_duration_ms or 0,
                f"{sel.version.label if sel.version else '?'} {sel.executable}" if sel else "none",
                snapshot.selection_reason or snapshot.message,
            )
            self._notify(snapshot)
            return snapshot

    def select(self, mode: RuntimeMode, executable: str | None = None) -> RuntimeStatus:
        """Change the runtime mode and optionally pin a specific executable (validated, probed if unknown).

        With ``mode="auto"`` a given executable switches the mode to that install's kind. Raises
        :class:`BlenderNotFoundError` / :class:`BlenderIncompatibleError`.
        """
        if mode not in RUNTIME_MODES:
            raise LauncherError(f"Invalid Blender runtime mode {mode!r}", code="blender_invalid_mode")
        if not self._scanned:
            self.scan()
        pinned: str | None = None
        if executable:
            exe = normalize_executable(executable)
            if not exe.is_file():
                raise BlenderNotFoundError(f"Blender executable not found: {exe}", details={"executable": str(exe)})
            with self._lock:
                known = next((i for i in self._installations if _key(i.executable) == _key(exe)), None)
            install = known if known is not None and known.valid else self._detector.probe(exe, origin="user")
            if not install.valid:
                raise BlenderNotFoundError(
                    f"{exe} is not a working Blender executable ({install.error or 'no version reported'})",
                    details={"executable": str(exe)},
                )
            if not install.compatible:
                raise BlenderIncompatibleError(
                    f"{install.incompatible_reason}. Auto Mesh Tuner requires Blender "
                    f"{self._settings.min_version_label} or newer.",
                    details={"executable": str(exe), "version": install.version.label if install.version else None},
                )
            if mode == "auto":
                mode = install.kind
            elif mode != install.kind:
                raise BlenderIncompatibleError(
                    f"{exe} is a {install.kind} Blender and cannot be pinned as the {mode} runtime",
                    details={"executable": str(exe), "kind": install.kind},
                )
            pinned = str(install.executable)
            if known is None:
                with self._lock:
                    if not any(_key(i.executable) == _key(install.executable) for i in self._installations):
                        self._installations = sort_installations([*self._installations, install])
        with self._lock:
            self._config.mode = mode
            self._config.pinned_executable = pinned
            self._apply_policy(force_save=True)
            snapshot = self._snapshot()
        logger.info("Blender runtime selection set: mode=%s pinned=%s", mode, pinned)
        self._notify(snapshot)
        return snapshot

    def require_runtime(self) -> BlenderInstallation:
        """The selected compatible installation, or :class:`BlenderUnavailableError` with the user prompt."""
        if not self._scanned:
            self.scan()
        with self._lock:
            selected = self._decision.selected
        if selected is not None and not selected.executable.is_file():
            logger.warning("Selected Blender %s disappeared; rescanning", selected.executable)
            self.scan(force=True)
            with self._lock:
                selected = self._decision.selected
        with self._lock:
            if selected is not None and selected.compatible and selected.executable.is_file():
                return selected
            if self._downloading:
                message = "A portable Blender is being downloaded. Auto Tune will be available when it finishes."
            else:
                message = self._decision.message or (
                    f"No compatible Blender {self._settings.min_version_label}+ runtime is selected."
                )
        raise BlenderUnavailableError(message)

    # ------------------------------------------------------------------ release / download
    def resolve_release(self, *, force: bool = False) -> ReleaseInfo:
        """Official latest release for this machine (cached for 1 h). Raises :class:`ReleaseResolveError`."""
        with self._lock:
            if not force and self._release is not None and time.monotonic() - self._release_at < RELEASE_CACHE_TTL_S:
                return self._release
        release = self._downloader.resolve_latest()
        with self._lock:
            self._release = release
            self._release_at = time.monotonic()
        self._notify()
        return release

    def start_download(self, *, version: str | None = None) -> RuntimeStatus:
        """Start downloading the official portable Blender in a background thread (the caller confirmed)."""
        if not self._settings.allow_download:
            raise DownloadsDisabledError("Downloading Blender is disabled by configuration")
        with self._lock:
            if self._downloading:
                raise DownloadInProgressError("A Blender download is already in progress")
            self._downloading = True
            self._cancel = threading.Event()
            now = utcnow_iso()
            self._download = replace(DownloadProgress.idle(), state="resolving", started_at=now, updated_at=now)
            self._state = "downloading"
            self._error = None
            worker = threading.Thread(
                target=self._download_worker, args=(version, self._cancel), name="blender-download", daemon=True
            )
            self._worker = worker
            worker.start()
            snapshot = self._snapshot()
        logger.info("Blender download started (version=%s)", version or "latest")
        self._notify(snapshot)
        return snapshot

    def cancel_download(self) -> RuntimeStatus:
        with self._lock:
            if self._downloading:
                logger.info("Cancelling the Blender download")
                self._cancel.set()
            return self._snapshot()

    def close(self, timeout: float = 5.0) -> None:
        """Cancel a running download and wait for its thread."""
        with self._lock:
            self._cancel.set()
            worker = self._worker
        if worker is not None and worker.is_alive():
            worker.join(timeout)
            if worker.is_alive():
                logger.warning("Blender download thread did not stop within %.1f s", timeout)

    def _download_worker(self, version: str | None, cancel: threading.Event) -> None:
        try:
            with self._lock:
                cached = self._release
                fresh = cached is not None and time.monotonic() - self._release_at < RELEASE_CACHE_TTL_S
            if cached is not None and fresh and (version is None or cached.version == version):
                release = cached
            else:
                release = self._downloader.resolve_latest(version=version)
                with self._lock:
                    self._release = release
                    self._release_at = time.monotonic()
            if cancel.is_set():
                raise DownloadCancelledError()
            with self._lock:
                assert self._download is not None
                self._download = replace(
                    self._download, state="downloading", version=release.version, file_name=release.file_name,
                    url=release.url, bytes_total=release.size_bytes, updated_at=utcnow_iso(),
                )
            self._notify()
            exe = self._downloader.download_and_install(release, cancel=cancel, on_progress=self._on_progress)
        except DownloadCancelledError as exc:
            self._finish_download("cancelled", exc)
        except LauncherError as exc:
            self._finish_download("failed", exc)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Blender download thread crashed")
            self._finish_download("failed", DownloadError(f"Blender download failed: {exc}"))
        else:
            self.scan()  # still flagged as downloading: the state goes straight to ready afterwards
            self._finish_download("completed", None)
            try:
                self._select_new_install(exe)
            except Exception:  # noqa: BLE001
                logger.exception("Could not select the downloaded Blender")

    def _finish_download(self, state: str, error: LauncherError | None) -> None:
        with self._lock:
            self._downloading = False
            now = utcnow_iso()
            if self._download is not None:
                changes: dict[str, Any] = {"updated_at": now, "speed_bps": None, "eta_s": None}
                if self._download.state != state:
                    changes["state"] = state
                if error is not None:
                    changes.update(error=error.message, error_code=error.code)
                self._download = replace(self._download, **changes)
            self._error = error.message if error is not None and state == "failed" else None
            self._apply_policy()
            snapshot = self._snapshot()
        logger.info("Blender download finished: %s%s", state, f" ({error.message})" if error else "")
        self._notify(snapshot)

    def _select_new_install(self, exe: Path) -> None:
        """After a successful download: select the new build if mode is embedded or no system Blender qualifies
        (never overrides a system pin)."""
        with self._lock:
            new = next((i for i in self._installations if _key(i.executable) == _key(exe)), None)
            if new is None or not new.compatible:
                logger.warning("Downloaded Blender %s is not usable after rescan", exe)
                return
            mode = self._config.mode
            has_system = any(i.compatible and i.kind == "system" and i.is_stable for i in self._installations)
            changed = False
            if mode == "embedded":
                self._config.pinned_executable = str(new.executable)
                changed = True
            elif mode == "system" and self._config.pinned_executable:
                return
            elif not has_system and (self._decision.selected is None or _key(self._decision.selected.executable)
                                     != _key(new.executable)):
                self._config.mode = "embedded"
                self._config.pinned_executable = str(new.executable)
                changed = True
            if not changed:
                return
            self._apply_policy(force_save=True)
            snapshot = self._snapshot()
        logger.info("Selected the downloaded Blender %s", exe)
        self._notify(snapshot)

    def _on_progress(self, progress: DownloadProgress) -> None:
        with self._lock:
            previous = self._download.state if self._download is not None else None
            self._download = progress
            now = time.monotonic()
            notify = progress.state != previous or now - self._last_progress_notify >= PROGRESS_NOTIFY_INTERVAL_S
            if notify:
                self._last_progress_notify = now
                snapshot = self._snapshot()
        if notify:
            self._notify(snapshot)

    # ------------------------------------------------------------------ internals (lock held)
    def _apply_policy(self, *, force_save: bool = False) -> None:
        decision = select_runtime(self._installations, self._config, self._settings)
        self._decision = decision
        if self._downloading:
            self._state = "downloading"
        elif decision.selected is not None:
            self._state = "ready"
        elif self._scanned:
            self._state = "needs_download"
        for warning in decision.warnings:
            logger.warning("%s", warning)
        selected = decision.selected
        values = (
            str(selected.executable) if selected else None,
            selected.version.label if selected and selected.version else None,
            selected.kind if selected else None,
        )
        current = (self._config.selected_executable, self._config.selected_version, self._config.selected_kind)
        if force_save or values != current or not self._settings.config_path.is_file():
            (
                self._config.selected_executable,
                self._config.selected_version,
                self._config.selected_kind,
            ) = values
            try:
                self._config.save(self._settings.config_path)
            except OSError as exc:
                logger.warning("Could not save the Blender runtime config %s: %s", self._settings.config_path, exc)

    def _snapshot(self) -> RuntimeStatus:
        decision = self._decision
        return RuntimeStatus(
            state=self._state,
            mode=self._config.mode,
            min_version=self._settings.min_version_label,
            selected=decision.selected,
            selection_reason=decision.reason,
            installations=list(self._installations),
            message=decision.message if decision.selected is None and self._scanned else None,
            warnings=list(decision.warnings),
            runtime_root=str(self._settings.runtime_root),
            can_download=self._settings.allow_download,
            download=replace(self._download) if self._download is not None else None,
            release=self._release,
            last_scan_at=self._last_scan_at,
            scan_duration_ms=self._scan_ms,
            error=self._error,
        )

    def _notify(self, snapshot: RuntimeStatus | None = None) -> None:
        if self._on_change is None:
            return
        if snapshot is None:
            snapshot = self.status()
        try:
            self._on_change(snapshot)
        except Exception:  # noqa: BLE001 - listeners must not break the manager
            logger.exception("Blender runtime on_change listener failed")
