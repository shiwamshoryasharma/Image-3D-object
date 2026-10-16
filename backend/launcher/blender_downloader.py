"""Resolve, download, verify and safely install the official portable Blender ZIP from download.blender.org.

Rules (DESIGN.md section 2): HTTPS only; exact host allowlist on the initial URL and on every redirect hop;
explicit User-Agent (Cloudflare answers 403 to ``Python-urllib``); HEAD for size/ETag; stream to
``downloads/<file>.part`` (+ ``.part.json`` resume state) in 1 MiB chunks; resume with ``Range`` + ``If-Range``
(accept only a 206 whose Content-Range matches, 200 means restart from 0, 416 means restart); transient errors are
retried with exponential backoff; sha256 over the whole file against the official ``blender-X.Y.Z.sha256``;
zip-slip-safe extraction into ``.staging-<hex>`` then ``os.replace`` into ``runtime_root/<top folder>``; the new
executable is validated by running it. Nothing outside ``runtime_root`` is ever deleted or replaced.
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import re
import secrets
import shutil
import ssl
import stat
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from http.client import HTTPException, HTTPResponse
from pathlib import Path
from typing import Any, Final, Literal

from .blender_version import parse_blender_version
from .config import LauncherSettings, atomic_write_text, utcnow_iso
from .errors import (
    ChecksumMismatchError,
    DownloadCancelledError,
    DownloadError,
    DownloadInProgressError,
    LauncherError,
    PathTooLongError,
    ReleaseResolveError,
    UnsafeArchiveError,
)
from .platform_win import (
    IS_WINDOWS,
    MAX_PATH_CHARS,
    extended_path,
    is_same_path,
    native_architecture,
    path_is_within,
    pe_info,
)
from .process_runner import ProcessCapture, run_capture

logger = logging.getLogger(__name__)

DownloadState = Literal[
    "idle", "resolving", "downloading", "verifying", "extracting", "validating", "completed", "failed", "cancelled"
]
Validator = Callable[[Path, "ReleaseInfo"], None]

ZIP_NAME_RE: Final = re.compile(
    r"^blender-(?P<ver>(?P<maj>\d+)\.(?P<min>\d+)\.(?P<pat>\d+))-windows-(?P<arch>x64|arm64)\.zip$"
)
TOP_FOLDER_RE: Final = re.compile(r"^blender-\d+\.\d+\.\d+-windows-(x64|arm64)$")
_DIR_RE: Final = re.compile(r'<a href="Blender(?P<maj>\d+)\.(?P<min>\d+)/">')
_ROW_RE: Final = re.compile(
    r'<a href="(?P<name>[^"/]+)">[^<]*</a>\s+(?P<date>\d{2}-[A-Z][a-z]{2}-\d{4} \d{2}:\d{2})\s+(?P<size>\d+|-)'
)
_SUM_LINE_RE: Final = re.compile(r"^(?P<sha>[0-9a-fA-F]{64})[ \t]+\*?(?P<name>[A-Za-z0-9._+-]+)[ \t]*\r?$", re.M)
_LTS_RE: Final = re.compile(r"Blender\s+(\d+)\.(\d+)\s+LTS")
_CONTENT_RANGE_RE: Final = re.compile(r"^\s*bytes\s+(\d+)-(\d+)/(\d+|\*)\s*$", re.IGNORECASE)
_RESERVED_NAMES: Final = frozenset(
    ["CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"]
    + [f"COM{i}" for i in range(1, 10)]
    + [f"LPT{i}" for i in range(1, 10)]
)
_MONTHS: Final = {m: i for i, m in enumerate(
    ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), start=1)}

LTS_PAGE_URL: Final = "https://www.blender.org/download/lts/"
LTS_FALLBACK_SERIES: Final = frozenset({"4.2", "4.5", "5.2"})
CHUNK_SIZE: Final = 1024 * 1024
DOWNLOAD_FREE_SPACE_MARGIN: Final = int(1.2 * 1024**3)  # zip + 1.2 GB (extraction ~0.95 GB + slack)
EXTRACT_FREE_SPACE_MARGIN: Final = 256 * 1024 * 1024
INSTALL_MANIFEST: Final = ".img3d_install.json"
EXE_NAME: Final = "blender.exe"
_TEXT_LIMIT: Final = 8 * 1024 * 1024
_SPEED_WINDOW_S: Final = 4.0
_MAX_RESTARTS: Final = 3


# ---------------------------------------------------------------------------------------------- data
@dataclass
class ReleaseInfo:
    version: str
    series: str
    file_name: str
    url: str
    size_bytes: int | None
    sha256: str | None
    checksum_url: str | None
    architecture: str
    is_lts: bool | None
    release_date: str | None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["host"] = urllib.parse.urlsplit(self.url).hostname
        return data


@dataclass
class DownloadProgress:
    state: DownloadState
    version: str | None
    file_name: str | None
    url: str | None
    bytes_done: int
    bytes_total: int | None
    percent: float | None
    speed_bps: float | None
    eta_s: float | None
    resumed: bool
    verified: bool | None
    error: str | None
    error_code: str | None
    install_dir: str | None
    started_at: str | None
    updated_at: str

    @classmethod
    def idle(cls) -> DownloadProgress:
        return cls(
            state="idle", version=None, file_name=None, url=None, bytes_done=0, bytes_total=None, percent=None,
            speed_bps=None, eta_s=None, resumed=False, verified=None, error=None, error_code=None,
            install_dir=None, started_at=None, updated_at=utcnow_iso(),
        )

    @property
    def active(self) -> bool:
        return self.state in ("resolving", "downloading", "verifying", "extracting", "validating")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class _Restart(Exception):
    """The server cannot continue the partial download: restart from byte 0."""


class _Transient(Exception):
    """Retryable network failure (timeout, reset, dropped connection, 5xx)."""


@dataclass
class _Transfer:
    url: str
    part: Path
    size: int | None
    etag: str | None
    hasher: Any = field(default_factory=hashlib.sha256)
    resumed: bool = False
    restarts: int = 0


class _ProgressReporter:
    """Builds :class:`DownloadProgress` snapshots (speed over a sliding window) and forwards them."""

    def __init__(self, release: ReleaseInfo, callback: Callable[[DownloadProgress], None] | None) -> None:
        now = utcnow_iso()
        self._callback = callback
        self._samples: deque[tuple[float, int]] = deque()
        self.progress = DownloadProgress(
            state="downloading", version=release.version, file_name=release.file_name, url=release.url,
            bytes_done=0, bytes_total=release.size_bytes, percent=None, speed_bps=None, eta_s=None, resumed=False,
            verified=None, error=None, error_code=None, install_dir=None, started_at=now, updated_at=now,
        )

    def update(self, **changes: Any) -> None:
        for key, value in changes.items():
            setattr(self.progress, key, value)
        self._emit()

    def transferred(self, done: int, total: int | None) -> None:
        now = time.monotonic()
        self._samples.append((now, done))
        while len(self._samples) > 2 and now - self._samples[0][0] > _SPEED_WINDOW_S:
            self._samples.popleft()
        speed = None
        if len(self._samples) >= 2:
            (t0, b0), (t1, b1) = self._samples[0], self._samples[-1]
            if t1 > t0 and b1 >= b0:
                speed = (b1 - b0) / (t1 - t0)
        p = self.progress
        p.bytes_done = done
        p.bytes_total = total
        p.percent = round(min(100.0, done * 100.0 / total), 2) if total else None
        p.speed_bps = round(speed, 1) if speed is not None else None
        p.eta_s = round((total - done) / speed, 1) if (speed and total and total >= done) else None
        self._emit()

    def reset_speed(self) -> None:
        self._samples.clear()

    def _emit(self) -> None:
        self.progress.updated_at = utcnow_iso()
        if self._callback is None:
            return
        try:
            self._callback(replace(self.progress))
        except Exception:  # noqa: BLE001 - a UI callback must never break the download
            logger.exception("Download progress callback failed")


class _AllowlistRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follows redirects only to allowlisted HTTPS hosts (checked on every hop)."""

    max_redirections = 5

    def __init__(self, check_url: Callable[[str], None]) -> None:
        super().__init__()
        self._check_url = check_url

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        absolute = urllib.parse.urljoin(req.full_url, newurl)
        try:
            self._check_url(absolute)
        except LauncherError:
            fp.close()
            raise
        return super().redirect_request(req, fp, code, msg, headers, absolute)


def make_opener(
    settings: LauncherSettings, *, proxies: dict[str, str] | None = None
) -> urllib.request.OpenerDirector:
    """urllib opener with the allowlisting redirect handler and a verifying TLS context (no file/ftp/data).

    ``proxies=None`` uses the system proxy configuration; ``{}`` disables proxies (tests against localhost).
    """
    checker = _UrlPolicy(settings)
    opener = urllib.request.OpenerDirector()
    for handler in (
        urllib.request.ProxyHandler(proxies),
        urllib.request.UnknownHandler(),
        urllib.request.HTTPHandler(),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
        urllib.request.HTTPDefaultErrorHandler(),
        _AllowlistRedirectHandler(checker.check),
        urllib.request.HTTPErrorProcessor(),
    ):
        opener.add_handler(handler)
    return opener


class _UrlPolicy:
    def __init__(self, settings: LauncherSettings) -> None:
        self._settings = settings

    def allowed(self, url: str) -> bool:
        try:
            self.check(url)
        except LauncherError:
            return False
        return True

    def check(self, url: str) -> None:
        """Raise :class:`DownloadError` unless ``url`` is HTTPS (or allowed HTTP) to an allowlisted host."""
        try:
            parts = urllib.parse.urlsplit(url)
            host = (parts.hostname or "").lower()
        except ValueError as exc:
            raise DownloadError(f"Invalid download URL {url!r}: {exc}") from exc
        scheme = parts.scheme.lower()
        if scheme != "https" and (self._settings.require_https or scheme != "http"):
            raise DownloadError(f"Refusing non-HTTPS download URL: {url}", details={"url": url})
        if parts.username or parts.password:
            raise DownloadError("Refusing a download URL with embedded credentials", details={"url": url})
        if host not in self._settings.allowed_hosts:
            raise DownloadError(
                f"Refusing to contact {host or '<no host>'}: not an allowed Blender download host "
                f"({', '.join(self._settings.allowed_hosts)})",
                details={"url": url, "host": host},
            )


def _parse_listing_date(text: str) -> str | None:
    """``"15-Sep-2026 09:16"`` (UTC, nginx autoindex) -> ``"2026-09-15T09:16:00Z"`` (locale independent)."""
    match = re.match(r"^(\d{2})-([A-Z][a-z]{2})-(\d{4}) (\d{2}):(\d{2})$", text.strip())
    if not match or match.group(2) not in _MONTHS:
        return None
    try:
        value = datetime(int(match.group(3)), _MONTHS[match.group(2)], int(match.group(1)),
                         int(match.group(4)), int(match.group(5)), tzinfo=UTC)
    except ValueError:
        return None
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------------------------- downloader
class BlenderDownloader:
    """Official portable Blender resolver/installer. ``opener``, ``validator``, ``runner`` and ``arch`` are
    injectable for tests; production defaults stay strict."""

    max_entries: int = 20_000
    max_total_uncompressed: int = 4 * 1024**3
    max_ratio: float = 200.0
    ratio_min_size: int = 10 * 1024 * 1024

    def __init__(
        self,
        settings: LauncherSettings,
        *,
        opener: urllib.request.OpenerDirector | None = None,
        validator: Validator | None = None,
        runner: ProcessCapture | None = None,
        arch: str | None = None,
        retry_backoff_s: float = 1.0,
    ) -> None:
        self._settings = settings
        self._policy = _UrlPolicy(settings)
        self._opener = opener or make_opener(settings)
        self._validator = validator
        self._runner: ProcessCapture = runner or run_capture
        self._arch = arch
        self._retry_backoff_s = max(0.0, retry_backoff_s)
        self._lts_cache: frozenset[str] | None = None

    @property
    def settings(self) -> LauncherSettings:
        return self._settings

    def native_architecture(self) -> str:
        """Native machine architecture (``IsWow64Process2`` nativeMachine): ``"x64"`` / ``"arm64"`` / ..."""
        return self._arch or native_architecture()

    # ================================================================== HTTP
    def check_url(self, url: str) -> None:
        self._policy.check(url)

    def open_url(
        self, url: str, *, method: str = "GET", headers: dict[str, str] | None = None, timeout: float | None = None
    ) -> HTTPResponse:
        """Open ``url`` through the allowlisted opener with the explicit User-Agent (caller closes the response).

        Raises :class:`DownloadError` for a refused URL/redirect and lets ``urllib.error.HTTPError`` /
        ``URLError`` / ``OSError`` propagate for the caller to classify.
        """
        self._policy.check(url)
        request = urllib.request.Request(
            url,
            method=method,
            headers={"User-Agent": self._settings.user_agent, "Accept-Encoding": "identity", **(headers or {})},
        )
        response = self._opener.open(request, timeout=timeout or self._settings.http_timeout_s)
        try:
            self._policy.check(response.geturl())
        except LauncherError:
            response.close()
            raise
        return response  # type: ignore[return-value]

    def _get_text(self, url: str) -> str:
        try:
            with self.open_url(url) as response:
                data = response.read(_TEXT_LIMIT + 1)
        except urllib.error.HTTPError as exc:
            exc.close()
            raise ReleaseResolveError(
                f"HTTP {exc.code} while fetching {url}", details={"url": url, "status": exc.code}
            ) from exc
        except (urllib.error.URLError, OSError, HTTPException) as exc:
            reason = getattr(exc, "reason", exc)
            raise ReleaseResolveError(f"Could not fetch {url}: {reason}", details={"url": url}) from exc
        if len(data) > _TEXT_LIMIT:
            raise ReleaseResolveError(f"Response from {url} is unexpectedly large", details={"url": url})
        return data.decode("utf-8", "replace")

    # ================================================================== resolve
    def resolve_latest(self, *, version: str | None = None) -> ReleaseInfo:
        """Newest official stable portable ZIP >= ``min_version`` for the native architecture.

        ``version`` pins a release (``"5.2.2"``) or a series (``"5.2"`` -> newest 5.2.x). Raises
        :class:`ReleaseResolveError` if nothing suitable is published or the index cannot be read.
        """
        arch = self.native_architecture()
        if arch not in ("x64", "arm64"):
            raise ReleaseResolveError(f"No official portable Blender build exists for the {arch} architecture")
        base = self._settings.download_base_url
        minimum = tuple(self._settings.min_version)
        wanted: tuple[int, ...] | None = None
        if version:
            match = re.fullmatch(r"\s*(\d+)\.(\d+)(?:\.(\d+))?\s*", version)
            if not match:
                raise ReleaseResolveError(f"Invalid Blender version {version!r}")
            wanted = tuple(int(g) for g in match.groups() if g is not None)
            series_list = [(wanted[0], wanted[1])]
        else:
            index = self._get_text(base)
            found = {(int(m.group("maj")), int(m.group("min"))) for m in _DIR_RE.finditer(index)}
            series_list = sorted((s for s in found if s >= minimum[:2]), reverse=True)
            if not series_list:
                raise ReleaseResolveError(
                    f"No Blender {self._settings.min_version_label}+ release series listed at {base}"
                )
        lts_series = self._lts_series()
        if not version and self._settings.download_channel == "lts":
            lts_only = [s for s in series_list if f"{s[0]}.{s[1]}" in lts_series]
            if lts_only:
                series_list = lts_only
            else:
                logger.info("No LTS series >= %s published; using the newest stable series",
                            self._settings.min_version_label)
        for major, minor in series_list:
            listing_url = urllib.parse.urljoin(base, f"Blender{major}.{minor}/")
            try:
                listing = self._get_text(listing_url)
            except ReleaseResolveError as exc:
                if (exc.details or {}).get("status") == 404 and not version:
                    logger.debug("Release series listing %s missing; skipping", listing_url)
                    continue
                raise
            rows = {m.group("name"): (m.group("date"), m.group("size")) for m in _ROW_RE.finditer(listing)}
            builds: list[tuple[tuple[int, int, int], str]] = []
            for name in rows:
                zm = ZIP_NAME_RE.match(name)
                if not zm or zm.group("arch") != arch:
                    continue
                ver = (int(zm.group("maj")), int(zm.group("min")), int(zm.group("pat")))
                if (zm.group("maj"), zm.group("min")) != (str(major), str(minor)) or ver < minimum:
                    continue
                if wanted is not None and len(wanted) == 3 and ver != wanted:
                    continue
                builds.append((ver, name))
            if not builds:
                if wanted is not None:
                    break
                logger.debug("No %s portable build in %s; trying older series", arch, listing_url)
                continue
            ver, name = max(builds)
            version_text = ".".join(map(str, ver))
            date_text, size_text = rows[name]
            sha256, checksum_url = self._official_checksum(listing_url, rows, version_text, name)
            release = ReleaseInfo(
                version=version_text,
                series=f"{major}.{minor}",
                file_name=name,
                url=urllib.parse.urljoin(listing_url, name),
                size_bytes=int(size_text) if size_text.isdigit() else None,
                sha256=sha256,
                checksum_url=checksum_url,
                architecture=arch,
                is_lts=f"{major}.{minor}" in lts_series,
                release_date=_parse_listing_date(date_text),
            )
            logger.info("Resolved official Blender %s (%s, %s bytes, sha256 %s)", release.version, release.file_name,
                        release.size_bytes, "present" if sha256 else "MISSING")
            return release
        target = f" {version}" if version else f" >= {self._settings.min_version_label}"
        raise ReleaseResolveError(f"No official portable Blender{target} build for {arch} found at {base}")

    def _official_checksum(
        self, listing_url: str, rows: dict[str, tuple[str, str]], version: str, zip_name: str
    ) -> tuple[str | None, str | None]:
        name = f"blender-{version}.sha256"
        if name not in rows:
            logger.warning("No official checksum file %s published for %s", name, zip_name)
            return None, None
        url = urllib.parse.urljoin(listing_url, name)
        text = self._get_text(url)
        for match in _SUM_LINE_RE.finditer(text):
            if match.group("name") == zip_name:
                return match.group("sha").lower(), url
        logger.warning("Checksum file %s has no entry for %s", url, zip_name)
        return None, url

    def _lts_series(self) -> frozenset[str]:
        """LTS series from blender.org/download/lts (current section only) merged with the built-in fallback."""
        if self._lts_cache is not None:
            return self._lts_cache
        series = set(LTS_FALLBACK_SERIES)
        if self._policy.allowed(LTS_PAGE_URL):
            try:
                page = html.unescape(self._get_text(LTS_PAGE_URL))
                current = page.split("Previous LTS Releases", 1)[0]
                scraped = {f"{a}.{b}" for a, b in _LTS_RE.findall(current)}
                if scraped:
                    series |= scraped
            except LauncherError as exc:
                logger.info("Could not read the Blender LTS page (%s); using built-in LTS list", exc.message)
        self._lts_cache = frozenset(series)
        return self._lts_cache

    # ================================================================== download + install
    def download_and_install(
        self,
        release: ReleaseInfo,
        *,
        cancel: threading.Event,
        on_progress: Callable[[DownloadProgress], None] | None = None,
    ) -> Path:
        """Download (resumable), verify, safely extract and validate ``release``. Returns the new blender.exe.

        Raises :class:`DownloadCancelledError` (``.part`` kept for resume), :class:`ChecksumMismatchError`
        (``.part`` deleted), :class:`UnsafeArchiveError`, :class:`PathTooLongError` or :class:`DownloadError`.
        """
        reporter = _ProgressReporter(release, on_progress)
        try:
            exe = self._install(release, cancel, reporter)
        except DownloadCancelledError as exc:
            reporter.update(state="cancelled", error=exc.message, error_code=exc.code, speed_bps=None, eta_s=None)
            logger.info("Blender %s download cancelled", release.version)
            raise
        except LauncherError as exc:
            reporter.update(state="failed", error=exc.message, error_code=exc.code, speed_bps=None, eta_s=None)
            logger.error("Blender %s download/install failed: %s", release.version, exc.message)
            raise
        except Exception as exc:  # noqa: BLE001 - surface anything unexpected as a download failure
            logger.exception("Unexpected error while installing Blender %s", release.version)
            error = DownloadError(f"Blender download failed: {exc}")
            reporter.update(state="failed", error=error.message, error_code=error.code, speed_bps=None, eta_s=None)
            raise error from exc
        reporter.update(state="completed", install_dir=str(exe.parent), speed_bps=None, eta_s=None)
        return exe

    def _install(self, release: ReleaseInfo, cancel: threading.Event, reporter: _ProgressReporter) -> Path:
        top = self._top_folder(release)
        self._policy.check(release.url)
        root = self._settings.runtime_root
        root.mkdir(parents=True, exist_ok=True)
        install_dir = root / top
        exe = install_dir / EXE_NAME
        if len(str(exe)) > MAX_PATH_CHARS:
            raise PathTooLongError(
                f"The Blender runtime folder path is too long ({len(str(exe))} > {MAX_PATH_CHARS} characters): {root}"
            )
        with self._install_lock():
            self._cleanup_staging()
            if exe.is_file():
                try:
                    self._validate(exe, release)
                except LauncherError as exc:
                    logger.warning("Existing %s is not a valid Blender %s (%s); reinstalling",
                                   install_dir, release.version, exc.message)
                else:
                    logger.info("Blender %s is already installed at %s; reusing it", release.version, install_dir)
                    if not (install_dir / INSTALL_MANIFEST).is_file():
                        self._write_manifest(install_dir, release, verified=None)
                    reporter.update(bytes_done=release.size_bytes or 0, percent=100.0, verified=None)
                    return exe
            zip_path = self._settings.downloads_dir / release.file_name
            verified = self._obtain_zip(release, zip_path, cancel, reporter)
            reporter.update(state="extracting", verified=verified, percent=0.0, speed_bps=None, eta_s=None)
            exe = self._extract_and_activate(release, zip_path, top, cancel, reporter, verified)
            if not self._settings.keep_zip_after_install:
                self._remove_file(zip_path)
            return exe

    @staticmethod
    def _top_folder(release: ReleaseInfo) -> str:
        match = ZIP_NAME_RE.match(release.file_name or "")
        if not match:
            raise DownloadError(f"Not an official portable Blender ZIP name: {release.file_name!r}")
        if match.group("ver") != release.version:
            raise DownloadError(f"ZIP {release.file_name} does not match version {release.version}")
        return release.file_name[: -len(".zip")]

    # ------------------------------------------------------------------ download
    def _obtain_zip(
        self, release: ReleaseInfo, zip_path: Path, cancel: threading.Event, reporter: _ProgressReporter
    ) -> bool:
        downloads = self._settings.downloads_dir
        downloads.mkdir(parents=True, exist_ok=True)
        expected_sha = (release.sha256 or "").strip().lower() or None
        if zip_path.is_file():  # verified ZIP left by an interrupted extraction
            if expected_sha and self._hash_file(zip_path, cancel) == expected_sha:
                logger.info("Reusing previously downloaded %s", zip_path.name)
                size = zip_path.stat().st_size
                reporter.update(bytes_done=size, bytes_total=size, percent=100.0, verified=True)
                return True
            self._remove_file(zip_path)

        part = downloads / (release.file_name + ".part")
        meta_path = downloads / (release.file_name + ".part.json")
        size, etag = self._head(release)
        existing = part.stat().st_size if part.is_file() else 0
        meta = self._read_meta(meta_path)
        if existing and not self._can_resume(meta, release, size, etag, existing):
            logger.info("Discarding stale partial download %s", part.name)
            self._remove_file(part)
            existing = 0
        self._check_free_space(downloads, (size - existing if size else 0) + DOWNLOAD_FREE_SPACE_MARGIN, "download")
        if existing and not etag and meta:
            etag = meta.get("etag") or None  # HEAD failed: keep the validator recorded for this .part
        self._write_meta(meta_path, release, size, etag)

        transfer = _Transfer(url=release.url, part=part, size=size, etag=etag)
        if existing:
            transfer.resumed = True
            self._hash_into(part, transfer.hasher, cancel)
            reporter.update(resumed=True)
            logger.info("Resuming %s at %d of %s bytes", release.file_name, existing, size)
        self._transfer_with_retries(transfer, cancel, reporter)

        reporter.update(state="verifying", speed_bps=None, eta_s=None)
        digest = transfer.hasher.hexdigest()
        if expected_sha:
            if digest != expected_sha:
                self._remove_file(part)
                self._remove_file(meta_path)
                raise ChecksumMismatchError(
                    f"Checksum mismatch for {release.file_name}: expected {expected_sha}, got {digest}. "
                    "The partial download was deleted; please retry.",
                    details={"expected": expected_sha, "actual": digest, "file_name": release.file_name},
                )
            verified = True
            logger.info("sha256 verified for %s", release.file_name)
        else:
            verified = False
            logger.warning("No official sha256 for %s; installing UNVERIFIED (sha256 %s)", release.file_name, digest)
        self._replace(part, zip_path)
        self._remove_file(meta_path)
        reporter.update(verified=verified)
        return verified

    def _head(self, release: ReleaseInfo) -> tuple[int | None, str | None]:
        """``(size, etag)`` from a HEAD request; falls back to the listing size. Refused redirects propagate."""
        size, etag = release.size_bytes, None
        try:
            with self.open_url(release.url, method="HEAD") as response:
                length = response.headers.get("Content-Length")
                etag = (response.headers.get("ETag") or "").strip() or None
                if length and length.isdigit():
                    head_size = int(length)
                    if size is not None and head_size != size:
                        logger.warning("HEAD size %d differs from the listing size %d for %s; using HEAD",
                                       head_size, size, release.file_name)
                    size = head_size
        except urllib.error.HTTPError as exc:
            exc.close()
            if exc.code in (401, 403, 404, 410):
                raise DownloadError(f"Download URL returned HTTP {exc.code}: {release.url}",
                                    details={"url": release.url, "status": exc.code}) from exc
            logger.info("HEAD %s failed with HTTP %s; continuing without it", release.url, exc.code)
        except (urllib.error.URLError, OSError, HTTPException) as exc:
            logger.info("HEAD %s failed (%s); continuing without it", release.url, getattr(exc, "reason", exc))
        return size, etag

    @staticmethod
    def _can_resume(meta: dict[str, Any] | None, release: ReleaseInfo, size: int | None, etag: str | None,
                    existing: int) -> bool:
        if not meta or meta.get("url") != release.url:
            return False
        if (meta.get("sha256") or None) != ((release.sha256 or "").lower() or None):
            return False
        if size is not None and (meta.get("size") not in (None, size) or existing > size):
            return False
        if etag and meta.get("etag") and meta.get("etag") != etag:
            return False
        return True

    def _transfer_with_retries(self, t: _Transfer, cancel: threading.Event, reporter: _ProgressReporter) -> None:
        failures = 0
        while True:
            self._raise_if_cancelled(cancel)
            offset = t.part.stat().st_size if t.part.is_file() else 0
            if t.size is not None and offset == t.size:
                return
            try:
                self._transfer_once(t, offset, cancel, reporter)
                return
            except _Restart as exc:
                t.restarts += 1
                if t.restarts > _MAX_RESTARTS:
                    raise DownloadError(f"Download of {t.url} kept restarting ({exc}); giving up") from exc
                logger.info("Restarting download of %s from 0: %s", t.url, exc)
                self._truncate(t)
            except _Transient as exc:
                failures += 1
                if failures > self._settings.max_download_retries:
                    raise DownloadError(
                        f"Download failed after {failures} attempts: {exc}", details={"url": t.url}
                    ) from exc
                delay = min(30.0, self._retry_backoff_s * (2 ** (failures - 1)))
                logger.warning("Transient download error (%s); retry %d/%d in %.1f s", exc, failures,
                               self._settings.max_download_retries, delay)
                reporter.reset_speed()
                if cancel.wait(delay):
                    raise DownloadCancelledError()

    def _truncate(self, t: _Transfer) -> None:
        with open(t.part, "wb"):
            pass
        t.hasher = hashlib.sha256()

    def _transfer_once(self, t: _Transfer, offset: int, cancel: threading.Event, reporter: _ProgressReporter) -> None:
        headers: dict[str, str] = {}
        if offset > 0:
            headers["Range"] = f"bytes={offset}-"
            if t.etag and not t.etag.startswith("W/"):
                headers["If-Range"] = t.etag
        try:
            response = self.open_url(t.url, headers=headers)
        except urllib.error.HTTPError as exc:
            exc.close()
            if exc.code == 416:
                raise _Restart("HTTP 416 Range Not Satisfiable") from exc
            if exc.code in (408, 425, 429) or exc.code >= 500:
                raise _Transient(f"HTTP {exc.code}") from exc
            raise DownloadError(f"Download failed: HTTP {exc.code} for {t.url}",
                                details={"url": t.url, "status": exc.code}) from exc
        except (urllib.error.URLError, OSError, HTTPException) as exc:
            raise _Transient(str(getattr(exc, "reason", exc))) from exc
        with response:
            status = response.status
            if offset > 0 and status == 206:
                match = _CONTENT_RANGE_RE.match(response.headers.get("Content-Range") or "")
                total = int(match.group(3)) if match and match.group(3).isdigit() else None
                if not match or int(match.group(1)) != offset or (t.size is not None and total != t.size):
                    raise _Restart(f"unexpected Content-Range {response.headers.get('Content-Range')!r}")
                mode = "ab"
                if not t.resumed:
                    t.resumed = True
                    reporter.update(resumed=True)
                logger.info("Resuming %s at byte %d", t.url, offset)
            elif status == 200:
                if offset > 0:
                    logger.info("Server answered the range request with 200 (file changed or If-Range mismatch); "
                                "restarting from 0")
                    t.hasher = hashlib.sha256()
                    offset = 0
                    t.resumed = False
                    reporter.update(resumed=False)
                mode = "wb"
            else:
                raise DownloadError(f"Unexpected HTTP {status} for {t.url}", details={"url": t.url, "status": status})
            length_text = response.headers.get("Content-Length")
            length = int(length_text) if length_text and length_text.isdigit() else None
            if status == 200 and length is not None and t.size != length:
                if t.size is not None:
                    logger.warning("Server reports %d bytes for %s (expected %d)", length, t.url, t.size)
                t.size = length
            expected_end = offset + length if length is not None else t.size
            received = offset
            with open(t.part, mode) as fh:
                while True:
                    self._raise_if_cancelled(cancel)
                    try:
                        chunk = response.read(CHUNK_SIZE)
                    except (OSError, HTTPException) as exc:
                        raise _Transient(f"connection error after {received} bytes: {exc}") from exc
                    if not chunk:
                        break
                    fh.write(chunk)
                    t.hasher.update(chunk)
                    received += len(chunk)
                    if expected_end is not None and received > expected_end:
                        raise _Restart("server sent more data than announced")
                    reporter.transferred(received, t.size)
        if expected_end is not None and received < expected_end:
            raise _Transient(f"connection closed after {received} of {expected_end} bytes")

    # ------------------------------------------------------------------ extraction
    def inspect_archive(self, zf: zipfile.ZipFile, top: str) -> list[tuple[zipfile.ZipInfo, list[str]]]:
        """Validate every entry before anything is written. Raises :class:`UnsafeArchiveError` /
        :class:`PathTooLongError`; returns ``(info, path parts)`` for each entry."""
        infos = zf.infolist()
        if not infos:
            raise UnsafeArchiveError("The archive is empty")
        if len(infos) > self.max_entries:
            raise UnsafeArchiveError(f"The archive has too many entries ({len(infos)} > {self.max_entries})")
        entries: list[tuple[zipfile.ZipInfo, list[str]]] = []
        for info in infos:
            name, original = info.filename, info.orig_filename
            if "\x00" in name or "\x00" in original:
                raise UnsafeArchiveError(f"Archive entry with a NUL byte: {original!r}")
            if "\\" in original or "\\" in name:
                raise UnsafeArchiveError(f"Archive entry with a backslash: {original!r}")
            if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
                raise UnsafeArchiveError(f"Absolute path in archive: {original!r}")
            if ":" in name:
                raise UnsafeArchiveError(f"Drive letter or alternate data stream in archive entry: {original!r}")
            mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_ISLNK(mode):
                raise UnsafeArchiveError(f"Symbolic link in archive: {original!r}")
            if info.flag_bits & 0x1:
                raise UnsafeArchiveError(f"Encrypted archive entry: {original!r}")
            if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                raise UnsafeArchiveError(f"Unsupported compression method {info.compress_type} for {original!r}")
            parts = name.rstrip("/").split("/")
            for part in parts:
                if part in ("", ".", ".."):
                    raise UnsafeArchiveError(f"Path traversal or empty path component in archive: {original!r}")
                if part.endswith((".", " ")) or part.split(".", 1)[0].upper() in _RESERVED_NAMES:
                    raise UnsafeArchiveError(f"Invalid Windows file name in archive: {original!r}")
            entries.append((info, parts))
        tops = {parts[0] for _, parts in entries}
        if len(tops) != 1:
            raise UnsafeArchiveError(f"The archive must contain a single top folder, found {len(tops)}: "
                                     f"{', '.join(sorted(tops)[:5])}")
        found_top = next(iter(tops))
        if not TOP_FOLDER_RE.match(found_top) or found_top != top:
            raise UnsafeArchiveError(f"Unexpected top folder {found_top!r} (expected {top!r})")
        root = str(self._settings.runtime_root)
        total = 0
        for info, parts in entries:
            if len(parts) == 1 and not info.is_dir():
                raise UnsafeArchiveError(f"File outside the top folder: {info.orig_filename!r}")
            final = os.path.join(root, *parts)
            if len(final) > MAX_PATH_CHARS:
                raise PathTooLongError(
                    f"Installing Blender here would create a path of {len(final)} characters (limit "
                    f"{MAX_PATH_CHARS}); choose a shorter Blender install folder than {root}",
                    details={"path": final, "length": len(final), "limit": MAX_PATH_CHARS},
                )
            if info.file_size > self.ratio_min_size and (
                info.compress_size <= 0 or info.file_size / info.compress_size > self.max_ratio
            ):
                raise UnsafeArchiveError(f"Suspicious compression ratio for {info.orig_filename!r} "
                                         f"({info.file_size} / {info.compress_size} bytes)")
            total += info.file_size
        if total > self.max_total_uncompressed:
            raise UnsafeArchiveError(f"The archive expands to {total} bytes (limit {self.max_total_uncompressed})")
        return entries

    def _extract_and_activate(
        self,
        release: ReleaseInfo,
        zip_path: Path,
        top: str,
        cancel: threading.Event,
        reporter: _ProgressReporter,
        verified: bool,
    ) -> Path:
        """Safe-extract into staging, move into place, validate, write the manifest. An unsafe or corrupt
        archive is deleted (staging is always removed)."""
        try:
            archive = zipfile.ZipFile(zip_path)
        except (zipfile.BadZipFile, OSError) as exc:
            self._remove_file(zip_path)
            raise DownloadError(f"The downloaded file is not a valid ZIP archive: {exc}") from exc
        try:
            return self._extract_validated(archive, release, top, cancel, reporter, verified)
        except (UnsafeArchiveError, zipfile.BadZipFile) as exc:
            archive.close()
            self._remove_file(zip_path)
            if isinstance(exc, zipfile.BadZipFile):
                raise DownloadError(f"The downloaded archive is corrupt: {exc}") from exc
            raise
        finally:
            archive.close()

    def _extract_validated(
        self,
        archive: zipfile.ZipFile,
        release: ReleaseInfo,
        top: str,
        cancel: threading.Event,
        reporter: _ProgressReporter,
        verified: bool,
    ) -> Path:
        root = self._settings.runtime_root
        staging = root / f".staging-{secrets.token_hex(4)}"
        try:
            with archive:
                entries = self.inspect_archive(archive, top)
                total = sum(info.file_size for info, _ in entries) or 1
                self._check_free_space(root, total + EXTRACT_FREE_SPACE_MARGIN, "extraction")
                staging.mkdir(parents=True)
                done = 0
                last_report = 0.0
                for index, (info, parts) in enumerate(entries):
                    if index % 32 == 0:
                        self._raise_if_cancelled(cancel)
                    target = extended_path(os.path.join(str(staging), *parts))
                    if info.is_dir():
                        os.makedirs(target, exist_ok=True)
                        continue
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    written = 0
                    with archive.open(info) as src, open(target, "wb") as dst:
                        while True:
                            chunk = src.read(CHUNK_SIZE)
                            if not chunk:
                                break
                            written += len(chunk)
                            if written > info.file_size:
                                raise UnsafeArchiveError(f"Entry {info.orig_filename!r} is larger than declared")
                            dst.write(chunk)
                            if written > CHUNK_SIZE:
                                self._raise_if_cancelled(cancel)
                    done += written
                    now = time.monotonic()
                    if now - last_report >= 0.25:
                        last_report = now
                        reporter.update(percent=round(min(100.0, done * 100.0 / total), 2))
            staged_install = staging / top
            if not (staged_install / EXE_NAME).is_file():
                raise UnsafeArchiveError(f"The archive does not contain {top}/{EXE_NAME}")
            reporter.update(percent=100.0)
            final = root / top
            if final.exists():
                try:
                    self._validate(final / EXE_NAME, release)
                except LauncherError:
                    logger.info("Replacing invalid existing install %s", final)
                    self._remove_tree(final)
                else:
                    logger.info("A valid Blender %s appeared at %s meanwhile; reusing it", release.version, final)
                    return final / EXE_NAME
            self._replace(staged_install, final)
        finally:
            if staging.exists():
                self._remove_tree(staging)
        reporter.update(state="validating")
        try:
            self._validate(final / EXE_NAME, release)
        except LauncherError:
            self._remove_tree(final)
            raise
        self._write_manifest(final, release, verified=verified)
        logger.info("Installed portable Blender %s at %s (verified=%s)", release.version, final, verified)
        return final / EXE_NAME

    # ------------------------------------------------------------------ validation / manifest
    def _validate(self, exe: Path, release: ReleaseInfo) -> None:
        if self._validator is not None:
            self._validator(exe, release)
            return
        try:
            machine, subsystem = pe_info(exe)
        except (OSError, ValueError) as exc:
            raise DownloadError(f"{exe} is not a valid executable: {exc}") from exc
        if machine != release.architecture or subsystem != "console":
            raise DownloadError(f"{exe} is a {machine}/{subsystem} binary, expected {release.architecture}/console")
        try:
            capture = self._runner([str(exe), "--factory-startup", "--version"],
                                   timeout_s=max(60.0, self._settings.detect_timeout_s), cwd=exe.parent)
        except OSError as exc:
            raise DownloadError(f"Could not run the installed Blender: {exc}") from exc
        if capture.timed_out:
            raise DownloadError("The installed Blender did not answer '--version' in time")
        version = parse_blender_version(f"{capture.stdout}\n{capture.stderr}")
        if version is None:
            raise DownloadError("The installed Blender did not report a version")
        if version.number != release.version or not version.is_stable:
            raise DownloadError(f"The installed Blender reports {version.label}, expected {release.version}")
        if release.is_lts is not None and version.lts != release.is_lts:
            logger.warning("Blender %s LTS flag differs from the release index (%s vs %s)",
                           release.version, version.lts, release.is_lts)

    def _write_manifest(self, install_dir: Path, release: ReleaseInfo, *, verified: bool | None) -> None:
        manifest = {
            "version": release.version,
            "url": release.url,
            "sha256": release.sha256,
            "verified": verified,
            "installed_at": utcnow_iso(),
            "size_bytes": release.size_bytes,
            "file_name": release.file_name,
            "architecture": release.architecture,
            "is_lts": release.is_lts,
        }
        path = install_dir / INSTALL_MANIFEST
        self._assert_contained(path)
        atomic_write_text(path, json.dumps(manifest, indent=2) + "\n")

    # ------------------------------------------------------------------ resume metadata / hashing
    @staticmethod
    def _read_meta(path: Path) -> dict[str, Any] | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def _write_meta(self, path: Path, release: ReleaseInfo, size: int | None, etag: str | None) -> None:
        self._assert_contained(path)
        atomic_write_text(path, json.dumps({
            "url": release.url, "etag": etag, "size": size, "sha256": (release.sha256 or "").lower() or None,
            "file_name": release.file_name, "version": release.version, "updated_at": utcnow_iso(),
        }, indent=2))

    def _hash_into(self, path: Path, hasher: Any, cancel: threading.Event) -> None:
        with open(path, "rb") as fh:
            while True:
                self._raise_if_cancelled(cancel)
                chunk = fh.read(8 * CHUNK_SIZE)
                if not chunk:
                    return
                hasher.update(chunk)

    def _hash_file(self, path: Path, cancel: threading.Event) -> str:
        hasher = hashlib.sha256()
        self._hash_into(path, hasher, cancel)
        return hasher.hexdigest()

    # ------------------------------------------------------------------ disk / safety
    @staticmethod
    def _check_free_space(directory: Path, needed: int, what: str) -> None:
        try:
            free = shutil.disk_usage(directory).free
        except OSError as exc:
            logger.warning("Could not determine free disk space at %s: %s", directory, exc)
            return
        if free < needed:
            raise DownloadError(
                f"Not enough free disk space for the Blender {what}: {free / 1024**3:.2f} GB free, "
                f"{needed / 1024**3:.2f} GB needed on {directory.anchor or directory}",
                details={"free_bytes": free, "needed_bytes": needed, "path": str(directory)},
            )

    @staticmethod
    def _raise_if_cancelled(cancel: threading.Event) -> None:
        if cancel.is_set():
            raise DownloadCancelledError()

    def _assert_contained(self, path: Path) -> None:
        """Refuse any destructive operation outside (or on) ``runtime_root``."""
        root = self._settings.runtime_root
        if is_same_path(path, root) or not path_is_within(path, root):
            raise LauncherError(
                f"Refusing to modify {path}: it is outside the Blender runtime folder {root}",
                code="launcher_unsafe_path",
                details={"path": str(path), "runtime_root": str(root)},
            )

    def _remove_tree(self, path: Path) -> None:
        self._assert_contained(path)
        if not path.exists():
            return

        def _onexc(func: Callable[..., object], target: str, exc: BaseException) -> None:
            try:
                os.chmod(target, stat.S_IWRITE)
                func(target)
            except OSError:
                pass

        for attempt in range(1, 9):
            shutil.rmtree(extended_path(path), onexc=_onexc)
            if not path.exists():
                return
            time.sleep(0.1 * attempt)
        logger.warning("Could not fully delete %s", path)

    def _remove_file(self, path: Path) -> None:
        self._assert_contained(path)
        for attempt in range(1, 9):
            try:
                path.unlink(missing_ok=True)
                return
            except PermissionError:
                if attempt == 8:
                    raise
                time.sleep(0.05 * attempt)

    def _replace(self, src: Path, dst: Path) -> None:
        self._assert_contained(src)
        self._assert_contained(dst)
        for attempt in range(1, 21):  # AV scanners briefly lock freshly written executables
            try:
                os.replace(src, dst)
                return
            except PermissionError:
                if attempt == 20:
                    raise
                time.sleep(0.1 * attempt)

    def _cleanup_staging(self) -> None:
        root = self._settings.runtime_root
        try:
            stale = [p for p in root.iterdir() if p.is_dir() and p.name.startswith(".staging-")]
        except OSError:
            return
        for path in stale:
            logger.info("Removing stale staging folder %s", path)
            self._remove_tree(path)

    @contextmanager
    def _install_lock(self) -> Iterator[None]:
        """Cross-process exclusive lock (``downloads/.install.lock``); released automatically if we crash."""
        downloads = self._settings.downloads_dir
        downloads.mkdir(parents=True, exist_ok=True)
        fh = open(downloads / ".install.lock", "a+b")  # noqa: SIM115
        try:
            try:
                if IS_WINDOWS:
                    import msvcrt

                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:  # pragma: no cover
                    import fcntl

                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise DownloadInProgressError("Another process is already installing Blender") from exc
            try:
                yield
            finally:
                try:
                    if IS_WINDOWS:
                        import msvcrt

                        fh.seek(0)
                        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:  # pragma: no cover
                    pass
        finally:
            fh.close()


__all__ = [
    "BlenderDownloader",
    "DownloadProgress",
    "DownloadState",
    "INSTALL_MANIFEST",
    "ReleaseInfo",
    "ZIP_NAME_RE",
    "make_opener",
]
