"""Find Blender installations on this machine and validate them by executing ``--factory-startup --version``.

Candidate sources (cheap, no execution; each wrapped in try/except and timed at DEBUG), in this order:
override, embedded runtime dir, extra search dirs, MSI Uninstall keys, Program Files folders, PATH, ``.blend``
ProgIDs + ``Applications`` keys, App Paths, scoop, winget portable, chocolatey, Steam, Blender Launcher V2 library,
Microsoft Store alias. Registry reads touch only specific keys (never a recursive walk or an HKCR enumeration).

Every surviving candidate is executed (parallel, max 4 threads) unless a cached probe for the same
``(normcase path, size, mtime_ns)`` exists. Registry/ProgID/Start-menu entries go stale and an ``X.Y`` folder does
not prove an install, so existence + execution checks are mandatory.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from .blender_version import BlenderVersion, format_min_version, parse_blender_version
from .config import InstallKind, LauncherSettings, atomic_write_text
from .platform_win import (
    HKEY_CLASSES_ROOT,
    HKEY_CURRENT_USER,
    HKEY_LOCAL_MACHINE,
    IS_WINDOWS,
    VIEW_32,
    VIEW_64,
    can_run_architecture,
    is_app_execution_alias,
    native_architecture,
    path_is_within,
    pe_info,
    reg_get_value,
    reg_subkeys,
    reg_value_names,
)
from .process_runner import CaptureResult, ProcessCapture, run_capture

logger = logging.getLogger(__name__)

EXE_NAME: Final = "blender.exe" if IS_WINDOWS else "blender"
LAUNCHER_EXE_NAME: Final = "blender-launcher.exe"
CACHE_SCHEMA: Final = 1
MAX_PROBE_THREADS: Final = 4

SOURCE_NAMES: Final[tuple[str, ...]] = (
    "override",
    "embedded_dir",
    "user",
    "registry_uninstall",
    "program_files",
    "path",
    "progid",
    "app_paths",
    "scoop",
    "winget",
    "choco",
    "steam",
    "blender_launcher",
    "store",
)

_UNINSTALL_KEY: Final = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
_APP_PATHS_KEY: Final = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"
_USER_CHOICE_KEY: Final = r"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.blend\UserChoice"
_CMD_EXE_RE: Final = re.compile(r'^\s*"([^"]+?\.exe)"|^\s*(\S+?\.exe)\b', re.IGNORECASE)

PeReader = Callable[[Path], tuple[str, str]]


@dataclass
class BlenderInstallation:
    """One discovered Blender executable and the outcome of probing it."""

    id: str
    executable: Path
    kind: InstallKind
    origins: list[str]
    version: BlenderVersion | None
    architecture: str | None
    valid: bool
    compatible: bool
    incompatible_reason: str | None
    error: str | None
    probe_ms: float | None
    subsystem: str | None = None
    emulated: bool = False

    @property
    def is_stable(self) -> bool:
        return self.version is not None and self.version.is_stable

    def to_dict(self) -> dict[str, Any]:
        version = self.version
        return {
            "id": self.id,
            "executable": str(self.executable),
            "kind": self.kind,
            "origins": list(self.origins),
            "version": version.number if version else None,
            "version_label": version.label if version else None,
            "version_tuple": list(version.tuple) if version else None,
            "cycle": version.cycle if version else None,
            "is_lts": version.lts if version else None,
            "is_stable": version.is_stable if version else None,
            "build_hash": version.build_hash if version else None,
            "build_branch": version.build_branch if version else None,
            "build_date": version.build_date if version else None,
            "architecture": self.architecture,
            "subsystem": self.subsystem,
            "emulated": self.emulated,
            "valid": self.valid,
            "compatible": self.compatible,
            "incompatible_reason": self.incompatible_reason,
            "error": self.error,
            "probe_ms": self.probe_ms,
        }


def installation_id(executable: str | os.PathLike[str]) -> str:
    """``sha1(normcase(realpath(exe)))[:12]``."""
    return hashlib.sha1(_path_key(executable).encode("utf-8")).hexdigest()[:12]


def normalize_executable(path: str | os.PathLike[str]) -> Path:
    """Expand variables, strip quotes, map a folder to ``<folder>/blender.exe`` and ``blender-launcher.exe``
    (GUI-subsystem wrapper whose piped output is empty) to its sibling ``blender.exe``."""
    text = os.path.normpath(os.path.expandvars(os.fspath(path).strip().strip('"').strip()))
    if os.path.isdir(text):
        text = os.path.join(text, EXE_NAME)
    if os.path.basename(text).lower() == LAUNCHER_EXE_NAME:
        text = os.path.join(os.path.dirname(text), EXE_NAME)
    return Path(text)


def _path_key(path: str | os.PathLike[str]) -> str:
    text = os.fspath(path)
    try:
        return os.path.normcase(os.path.realpath(text))
    except (OSError, ValueError):
        return os.path.normcase(os.path.abspath(text))


def _exe_from_command(command: Any) -> str | None:
    """Executable from a shell command string such as ``"C:\\...\\blender-launcher.exe" "%1"``."""
    if not isinstance(command, str) or not command.strip():
        return None
    match = _CMD_EXE_RE.match(os.path.expandvars(command))
    return (match.group(1) or match.group(2)) if match else None


def _env_path(name: str) -> Path | None:
    value = os.environ.get(name)
    return Path(value) if value else None


def _child_dirs(base: Path) -> list[Path]:
    try:
        return sorted(entry for entry in base.iterdir() if entry.is_dir())
    except OSError:
        return []


def _glob_limited(base: Path, name: str, max_depth: int) -> list[Path]:
    """``base/**/name`` limited to ``max_depth`` directory levels (no unbounded recursion)."""
    found: list[Path] = []
    frontier = [(base, 0)]
    while frontier:
        current, depth = frontier.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_file() and entry.name.lower() == name.lower():
                    found.append(Path(entry.path))
                elif entry.is_dir(follow_symlinks=False) and depth < max_depth:
                    frontier.append((Path(entry.path), depth + 1))
            except OSError:
                continue
    return sorted(found)


class BlenderDetector:
    """Discovers and probes Blender installations. Thread-safe; never raises for a single bad candidate.

    ``runner`` (``run_capture``-compatible), ``pe_reader`` and ``native_arch`` are injectable for tests;
    ``sources`` restricts the candidate sources (names from :data:`SOURCE_NAMES`).
    """

    def __init__(
        self,
        settings: LauncherSettings,
        *,
        runner: ProcessCapture | None = None,
        pe_reader: PeReader | None = None,
        native_arch: str | None = None,
        sources: Iterable[str] | None = None,
        use_cache: bool = True,
    ) -> None:
        self._settings = settings
        self._runner: ProcessCapture = runner or run_capture
        self._pe_reader: PeReader = pe_reader or pe_info
        self._native_arch = native_arch
        unknown = set(sources or ()) - set(SOURCE_NAMES)
        if unknown:
            raise ValueError(f"Unknown Blender candidate sources: {sorted(unknown)}")
        self._sources = tuple(SOURCE_NAMES if sources is None else [s for s in SOURCE_NAMES if s in set(sources)])
        self._use_cache = use_cache
        self._lock = threading.Lock()
        self._cache: dict[str, dict[str, Any]] | None = None
        self.last_timings_ms: dict[str, float] = {}

    @property
    def settings(self) -> LauncherSettings:
        return self._settings

    @property
    def native_arch(self) -> str:
        return self._native_arch or native_architecture()

    # ================================================================== candidates
    def candidates(self) -> list[tuple[Path, str]]:
        """``(exe path, first origin)`` per unique executable from every enabled source: normalized (launcher ->
        ``blender.exe``), de-duplicated by ``normcase(realpath)``, missing files dropped. No execution."""
        return [(path, origins[0]) for path, origins in self._collect_candidates()]

    def candidate_groups(self) -> list[tuple[Path, list[str]]]:
        """Like :meth:`candidates` but with every origin that reported the executable."""
        return self._collect_candidates()

    def _collect_candidates(self, extra: Iterable[tuple[Path | str, str]] = ()) -> list[tuple[Path, list[str]]]:
        raw: list[tuple[Path | str, str]] = []
        timings: dict[str, float] = {}
        for source in self._sources:
            t0 = time.perf_counter()
            try:
                found = list(getattr(self, f"_src_{source}")())
            except Exception as exc:  # noqa: BLE001 - a broken source must not break detection
                logger.warning("Blender candidate source %s failed: %s", source, exc)
                found = []
            timings[source] = round((time.perf_counter() - t0) * 1000, 3)
            logger.debug("Blender source %-18s %3d candidate(s) in %.2f ms", source, len(found), timings[source])
            raw += [(path, source) for path in found]
        raw += list(extra)
        self.last_timings_ms = timings

        merged: dict[str, tuple[Path, list[str]]] = {}
        for path, origin in raw:
            try:
                exe = normalize_executable(path)
            except (OSError, ValueError, TypeError):
                continue
            if not exe.is_file():
                logger.debug("Dropping missing Blender candidate %s (%s)", exe, origin)
                continue
            key = _path_key(exe)
            if key in merged:
                if origin not in merged[key][1]:
                    merged[key][1].append(origin)
            else:
                merged[key] = (exe, [origin])
        return list(merged.values())

    # ------------------------------------------------------------------ sources (each returns raw paths)
    def _src_override(self) -> list[Path]:
        return [self._settings.override_executable] if self._settings.override_executable else []

    def _src_embedded_dir(self) -> list[Path]:
        root = self._settings.runtime_root
        return [d / EXE_NAME for d in _child_dirs(root)] if root.is_dir() else []

    def _src_user(self) -> list[Path]:
        out: list[Path] = []
        for directory in self._settings.extra_search_dirs:
            if directory.is_file():
                out.append(directory)
                continue
            out.append(directory / EXE_NAME)
            out += [d / EXE_NAME for d in _child_dirs(directory)]
        return out

    def _src_registry_uninstall(self) -> list[str]:
        out: list[str] = []
        views = ((HKEY_LOCAL_MACHINE, VIEW_64), (HKEY_LOCAL_MACHINE, VIEW_32), (HKEY_CURRENT_USER, VIEW_64))
        for root, view in views:
            for sub in reg_subkeys(root, _UNINSTALL_KEY, view):
                key = _UNINSTALL_KEY + "\\" + sub
                name = str(reg_get_value(root, key, "DisplayName", view) or "")
                publisher = str(reg_get_value(root, key, "Publisher", view) or "")
                if "blender" not in name.lower() and publisher.strip().lower() != "blender foundation":
                    continue
                location = reg_get_value(root, key, "InstallLocation", view)
                if isinstance(location, str) and location.strip():
                    out.append(os.path.join(location.strip().strip('"'), EXE_NAME))
                icon = _exe_from_command(reg_get_value(root, key, "DisplayIcon", view))
                if icon:
                    out.append(icon)
        return out

    def _src_program_files(self) -> list[Path]:
        roots: list[Path] = []
        for var in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
            value = _env_path(var)
            if value is not None:
                roots.append(value)
        local = _env_path("LOCALAPPDATA")
        if local is not None:
            roots.append(local / "Programs")
        out: list[Path] = []
        seen: set[str] = set()
        for root in roots:
            base = root / "Blender Foundation"
            key = os.path.normcase(str(base))
            if key in seen or not base.is_dir():
                continue
            seen.add(key)
            out += [d / EXE_NAME for d in _child_dirs(base)]
        return out

    def _src_path(self) -> list[str]:
        out: list[str] = []
        for name in ("blender.exe", "blender") if IS_WINDOWS else ("blender",):
            found = shutil.which(name)
            if found:
                out.append(found)
        for directory in os.environ.get("PATH", "").split(os.pathsep):
            directory = directory.strip().strip('"')
            candidate = os.path.join(directory, EXE_NAME) if directory else ""
            if candidate and os.path.isfile(candidate):  # PATH is long: only report real hits
                out.append(candidate)
        return out

    def _src_progid(self) -> list[str]:
        if not IS_WINDOWS:
            return []
        progids: list[str] = []
        for root in (HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE):
            default = reg_get_value(root, r"Software\Classes\.blend")
            if isinstance(default, str) and default:
                progids.append(default)
            progids += reg_value_names(root, r"Software\Classes\.blend\OpenWithProgids")
        user_choice = reg_get_value(HKEY_CURRENT_USER, _USER_CHOICE_KEY, "ProgId")
        if isinstance(user_choice, str) and user_choice:
            progids.append(user_choice)
        out: list[str] = []
        for progid in dict.fromkeys(p for p in progids if p):
            exe = _exe_from_command(reg_get_value(HKEY_CLASSES_ROOT, progid + r"\shell\open\command"))
            if exe:
                out.append(exe)
        for app in ("blender.exe", "blender-launcher.exe"):
            exe = _exe_from_command(reg_get_value(HKEY_CLASSES_ROOT, rf"Applications\{app}\shell\open\command"))
            if exe:
                out.append(exe)
        return out

    def _src_app_paths(self) -> list[str]:
        out: list[str] = []
        for root in (HKEY_LOCAL_MACHINE, HKEY_CURRENT_USER):
            for app in ("blender.exe", "blender-launcher.exe"):
                value = reg_get_value(root, rf"{_APP_PATHS_KEY}\{app}")
                exe = _exe_from_command(f'"{value.strip().strip(chr(34))}"') if isinstance(value, str) else None
                if exe:
                    out.append(exe)
        return out

    def _src_scoop(self) -> list[Path]:
        roots: list[Path] = []
        scoop = _env_path("SCOOP")
        roots.append(scoop if scoop is not None else Path.home() / "scoop")
        scoop_global = _env_path("SCOOP_GLOBAL")
        roots.append(scoop_global if scoop_global is not None else Path(r"C:\ProgramData\scoop"))
        out: list[Path] = []
        for root in roots:
            app = root / "apps" / "blender"
            if app.is_dir():
                out += [d / EXE_NAME for d in _child_dirs(app)]  # <version>\ and the 'current' junction
        return out

    def _src_winget(self) -> list[Path]:
        local = _env_path("LOCALAPPDATA")
        if local is None:
            return []
        packages = local / "Microsoft" / "WinGet" / "Packages"
        if not packages.is_dir():
            return []
        out: list[Path] = []
        for package in packages.glob("BlenderFoundation.Blender*"):
            if package.is_dir():
                out += _glob_limited(package, EXE_NAME, max_depth=3)
        return out

    def _src_choco(self) -> list[Path]:
        root = _env_path("ChocolateyInstall") or Path(r"C:\ProgramData\chocolatey")
        lib = root / "lib" / "blender"
        return _glob_limited(lib, EXE_NAME, max_depth=4) if lib.is_dir() else []

    def _src_steam(self) -> list[str]:
        if not IS_WINDOWS:
            return []
        roots = [
            reg_get_value(HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
            reg_get_value(HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath", VIEW_32),
        ]
        libraries: list[str] = []
        for root in roots:
            if not isinstance(root, str) or not root.strip():
                continue
            root = os.path.normpath(root.strip())
            if not os.path.isdir(root):
                continue
            libraries.append(root)
            vdf = os.path.join(root, "steamapps", "libraryfolders.vdf")
            try:
                text = Path(vdf).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            libraries += [p.replace("\\\\", "\\") for p in re.findall(r'"path"\s+"([^"]+)"', text)]
        return [os.path.join(lib, "steamapps", "common", "Blender", EXE_NAME) for lib in dict.fromkeys(libraries)]

    def _src_blender_launcher(self) -> list[Path]:
        local = _env_path("LOCALAPPDATA")
        if local is None:
            return []
        libraries = [local / "Blender Launcher"]
        ini = local / "Blender Launcher" / "Blender Launcher.ini"
        try:
            if ini.is_file():
                match = re.search(r"(?m)^library_folder\s*=\s*(.+?)\s*$", ini.read_text("utf-8", errors="replace"))
                if match:
                    libraries.insert(0, Path(match.group(1).strip().strip('"')))
        except OSError:
            pass
        out: list[Path] = []
        for library in libraries:
            for sub in ("stable", "daily", "experimental", "custom"):
                folder = library / sub
                if folder.is_dir():
                    out += [d / EXE_NAME for d in _child_dirs(folder)]
        return out

    def _src_store(self) -> list[Path]:
        local = _env_path("LOCALAPPDATA")
        if local is None or not IS_WINDOWS:
            return []
        alias = local / "Microsoft" / "WindowsApps" / EXE_NAME
        return [alias] if os.path.lexists(alias) else []

    # ================================================================== detection
    def detect(self, *, force: bool = False, extra: Iterable[Path | str] = ()) -> list[BlenderInstallation]:
        """Collect, de-duplicate and probe every candidate. Sorted by version desc (invalid last).

        ``extra`` paths (e.g. the user's pinned executable) are always included with origin ``"user"``.
        Cached probes keyed by ``(path, size, mtime_ns)`` are reused unless ``force``.
        """
        t0 = time.perf_counter()
        candidates = self._collect_candidates([(Path(p), "user") for p in extra if p])
        t_collect = time.perf_counter()
        cache = self._load_cache() if self._use_cache else {}
        results: list[BlenderInstallation | None] = [None] * len(candidates)
        to_probe: list[int] = []
        for index, (exe, origins) in enumerate(candidates):
            entry = None if force else cache.get(self._cache_key(exe) or "")
            installation = self._from_cache(exe, origins, entry) if entry else None
            if installation is None:
                to_probe.append(index)
            else:
                results[index] = installation
        if to_probe:
            workers = min(MAX_PROBE_THREADS, len(to_probe))
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="blender-probe") as pool:
                probed = pool.map(lambda i: self._probe(candidates[i][0], candidates[i][1]), to_probe)
                for index, installation in zip(to_probe, probed, strict=True):
                    results[index] = installation
        installations = [r for r in results if r is not None]
        if self._use_cache:
            self._update_cache(installations)
        installations = sort_installations(installations)
        logger.debug(
            "Blender detection: %d candidate(s), %d probed, collect %.1f ms, total %.1f ms",
            len(candidates),
            len(to_probe),
            (t_collect - t0) * 1000,
            (time.perf_counter() - t0) * 1000,
        )
        return installations

    def probe(self, exe: Path | str, origin: str = "user") -> BlenderInstallation:
        """Probe one executable (always executes; result is cached for later scans)."""
        installation = self._probe(normalize_executable(exe), [origin])
        if self._use_cache and installation.valid:
            self._update_cache([installation], prune=False)
        return installation

    def _kind(self, exe: Path) -> InstallKind:
        return "embedded" if path_is_within(exe, self._settings.runtime_root) else "system"

    def _probe(self, exe: Path, origins: list[str]) -> BlenderInstallation:
        t0 = time.perf_counter()
        kind = self._kind(exe)
        base: dict[str, Any] = {
            "id": installation_id(exe),
            "executable": exe,
            "kind": kind,
            "origins": list(origins),
        }

        def invalid(
            message: str, *, architecture: str | None = None, subsystem: str | None = None
        ) -> BlenderInstallation:
            logger.debug("Blender candidate %s rejected: %s", exe, message)
            return BlenderInstallation(
                **base,
                version=None,
                architecture=architecture,
                valid=False,
                compatible=False,
                incompatible_reason="Not a working Blender executable",
                error=message,
                probe_ms=round((time.perf_counter() - t0) * 1000, 1),
                subsystem=subsystem,
            )

        if not exe.is_file():
            return invalid("File not found")
        architecture: str | None = None
        subsystem: str | None = None
        if not is_app_execution_alias(exe):  # Store aliases are 0-byte reparse points: just execute them
            try:
                architecture, subsystem = self._pe_reader(exe)
            except (OSError, ValueError) as exc:
                return invalid(f"Not a Windows executable ({exc})")
            if subsystem != "console":
                return invalid(
                    f"{subsystem.upper()}-subsystem executable: its output cannot be captured (use blender.exe)",
                    architecture=architecture,
                    subsystem=subsystem,
                )
            if not can_run_architecture(architecture, self.native_arch)[0]:
                return invalid(
                    f"{architecture} executable cannot run on this {self.native_arch} machine",
                    architecture=architecture,
                    subsystem=subsystem,
                )
        try:
            capture = self._runner(
                [str(exe), "--factory-startup", "--version"],
                timeout_s=self._settings.detect_timeout_s,
                cwd=exe.parent,
            )
        except OSError as exc:
            return invalid(f"Could not execute: {exc}", architecture=architecture, subsystem=subsystem)
        if capture.timed_out:
            return invalid(
                f"'--version' timed out after {self._settings.detect_timeout_s:.0f} s",
                architecture=architecture,
                subsystem=subsystem,
            )
        version = parse_blender_version(f"{capture.stdout}\n{capture.stderr}")
        if version is None:
            snippet = " ".join((capture.stdout + " " + capture.stderr).split())[:200]
            return invalid(
                f"Unrecognised '--version' output (exit code {capture.exit_code}): {snippet or '<empty>'}",
                architecture=architecture,
                subsystem=subsystem,
            )
        if architecture is None:  # Store alias: assume native (it was executable)
            architecture = self.native_arch
            subsystem = subsystem or "console"
        installation = BlenderInstallation(
            **base,
            version=version,
            architecture=architecture,
            valid=True,
            compatible=False,
            incompatible_reason=None,
            error=None,
            probe_ms=round((time.perf_counter() - t0) * 1000, 1),
            subsystem=subsystem,
        )
        self._apply_compatibility(installation)
        logger.debug("Probed %s -> %s (%s ms)", exe, version.label, installation.probe_ms)
        return installation

    def _apply_compatibility(self, installation: BlenderInstallation) -> None:
        version = installation.version
        assert version is not None
        runnable, emulated = can_run_architecture(installation.architecture, self.native_arch)
        installation.emulated = emulated
        reason: str | None = None
        if not version.at_least(self._settings.min_version):
            minimum = format_min_version(self._settings.min_version)
            reason = f"Blender {version.label_without_lts} is older than {minimum}"
        elif not runnable:
            reason = f"{installation.architecture} build cannot run on this {self.native_arch} machine"
        elif installation.subsystem not in (None, "console"):
            reason = "Not a console executable"
        installation.compatible = reason is None
        installation.incompatible_reason = reason

    # ================================================================== probe cache
    @staticmethod
    def _cache_key(exe: Path) -> str | None:
        try:
            st = os.stat(exe)
        except OSError:
            return None
        return f"{_path_key(exe)}|{st.st_size}|{st.st_mtime_ns}"

    def _load_cache(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            if self._cache is None:
                self._cache = {}
                path = self._settings.cache_path
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(data, dict) and data.get("schema") == CACHE_SCHEMA and isinstance(
                        data.get("entries"), dict
                    ):
                        self._cache = {k: v for k, v in data["entries"].items() if isinstance(v, dict)}
                except FileNotFoundError:
                    pass
                except (OSError, ValueError) as exc:
                    logger.warning("Ignoring unreadable Blender detection cache %s: %s", path, exc)
            return dict(self._cache)

    def _update_cache(self, installations: list[BlenderInstallation], *, prune: bool = True) -> None:
        self._load_cache()
        with self._lock:
            assert self._cache is not None
            before = dict(self._cache)
            for installation in installations:
                if not installation.valid or installation.version is None:
                    continue
                key = self._cache_key(installation.executable)
                if key is None:
                    continue
                v = installation.version
                self._cache[key] = {
                    "executable": str(installation.executable),
                    "architecture": installation.architecture,
                    "subsystem": installation.subsystem,
                    "probe_ms": installation.probe_ms,
                    "version": {
                        "major": v.major,
                        "minor": v.minor,
                        "patch": v.patch,
                        "cycle": v.cycle,
                        "lts": v.lts,
                        "build_hash": v.build_hash,
                        "build_branch": v.build_branch,
                        "build_date": v.build_date,
                        "raw": v.raw,
                    },
                }
            if prune:  # drop entries whose file vanished or changed (stale size/mtime)
                live = {self._cache_key(Path(e.get("executable", ""))) for e in self._cache.values()}
                self._cache = {k: e for k, e in self._cache.items() if k in live}
            if self._cache == before:
                return
            payload = {"schema": CACHE_SCHEMA, "entries": self._cache}
            try:
                atomic_write_text(self._settings.cache_path, json.dumps(payload, indent=1, ensure_ascii=False))
            except OSError as exc:
                logger.warning("Could not write Blender detection cache %s: %s", self._settings.cache_path, exc)

    def _from_cache(self, exe: Path, origins: list[str], entry: dict[str, Any]) -> BlenderInstallation | None:
        try:
            v = entry["version"]
            version = BlenderVersion(
                major=int(v["major"]),
                minor=int(v["minor"]),
                patch=int(v["patch"]),
                cycle=v.get("cycle", "release") if v.get("cycle") in ("release", "rc", "beta", "alpha") else "release",
                lts=bool(v.get("lts")),
                build_hash=v.get("build_hash"),
                build_branch=v.get("build_branch"),
                build_date=v.get("build_date"),
                raw=str(v.get("raw", "")),
            )
        except (KeyError, TypeError, ValueError):
            return None
        installation = BlenderInstallation(
            id=installation_id(exe),
            executable=exe,
            kind=self._kind(exe),
            origins=list(origins),
            version=version,
            architecture=entry.get("architecture"),
            valid=True,
            compatible=False,
            incompatible_reason=None,
            error=None,
            probe_ms=entry.get("probe_ms"),
            subsystem=entry.get("subsystem") or "console",
        )
        self._apply_compatibility(installation)
        return installation


def sort_installations(installations: Iterable[BlenderInstallation]) -> list[BlenderInstallation]:
    """Version descending (release > rc > beta > alpha within a version), invalid ones last, stable tiebreaks."""

    def key(item: BlenderInstallation) -> tuple[Any, ...]:
        version = item.version
        if not item.valid or version is None:
            return (1, (0, 0, 0, 0), 1, str(item.executable).lower())
        kind_rank = 0 if item.kind == "system" else 1
        return (0, tuple(-x for x in version.sort_key()), kind_rank, str(item.executable).lower())

    return sorted(installations, key=key)


__all__ = [
    "BlenderDetector",
    "BlenderInstallation",
    "CaptureResult",
    "SOURCE_NAMES",
    "installation_id",
    "normalize_executable",
    "sort_installations",
]
