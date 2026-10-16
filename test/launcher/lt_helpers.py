"""Test doubles for the launcher tests: fake installations/runner/detector/downloader, a local HTTP server
(HEAD, Range, If-Range, 206/200/416, mid-stream drops, redirects, request log) and ZIP builders."""

from __future__ import annotations

import hashlib
import io
import random
import re
import threading
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from launcher import (
    BlenderInstallation,
    CaptureResult,
    DownloadCancelledError,
    DownloadProgress,
    LauncherSettings,
    ReleaseInfo,
    parse_blender_version,
)
from launcher.blender_detector import installation_id

HERE = Path(__file__).resolve().parent
OUT_ROOT = HERE.parent / "outputs" / "mesh_tuner" / "launcher"
BLENDER_52 = Path(r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe")
BLENDER_42 = Path(r"C:\Program Files\Blender Foundation\Blender 4.2\blender.exe")

MIB = 1024 * 1024
TOP = "blender-5.9.1-windows-x64"
ZIP_NAME = f"{TOP}.zip"
FAKE_EXE = b"MZ-FAKE-BLENDER-EXE\x00" + b"\x90" * 64
ZIP_URL_PATH = f"/release/Blender5.9/{ZIP_NAME}"


# ---------------------------------------------------------------------------------------------- installations
def make_install(
    path: Path,
    version_text: str,
    *,
    kind: str = "system",
    arch: str = "x64",
    min_version: tuple[int, int, int] = (5, 1, 0),
    create: bool = True,
) -> BlenderInstallation:
    """A probed installation as the detector would report it (creates a dummy exe file by default)."""
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(FAKE_EXE)
    version = parse_blender_version(f"Blender {version_text}")
    assert version is not None, version_text
    compatible = version.at_least(min_version)
    return BlenderInstallation(
        id=installation_id(path),
        executable=path,
        kind=kind,  # type: ignore[arg-type]
        origins=["test"],
        version=version,
        architecture=arch,
        valid=True,
        compatible=compatible,
        incompatible_reason=None if compatible else f"Blender {version.label_without_lts} is older than 5.1",
        error=None,
        probe_ms=1.0,
        subsystem="console",
    )


class FakeDetector:
    """Returns a fixed installation list; ``probe`` looks paths up in it."""

    def __init__(self, installs: list[BlenderInstallation]) -> None:
        self.installs = list(installs)
        self.detect_calls: list[dict[str, Any]] = []

    def detect(self, *, force: bool = False, extra: Any = ()) -> list[BlenderInstallation]:
        self.detect_calls.append({"force": force, "extra": [str(p) for p in extra]})
        return list(self.installs)

    def probe(self, exe: Any, origin: str = "user") -> BlenderInstallation:
        for item in self.installs:
            if str(item.executable).lower() == str(exe).lower():
                return item
        return BlenderInstallation(
            id=installation_id(exe), executable=Path(exe), kind="system", origins=[origin], version=None,
            architecture=None, valid=False, compatible=False, incompatible_reason="Not a working Blender executable",
            error="not blender", probe_ms=1.0,
        )


class FakeRunner:
    """``run_capture`` replacement: maps executable path (lowercase) -> ``--version`` output."""

    def __init__(self, outputs: dict[str, str] | None = None, default: str | None = None) -> None:
        self.outputs = {k.lower(): v for k, v in (outputs or {}).items()}
        self.default = default
        self.calls: list[list[str]] = []
        self.lock = threading.Lock()
        self.timeout_paths: set[str] = set()

    def __call__(self, cmd: list[str], *, timeout_s: float, cwd: Path | None = None) -> CaptureResult:
        with self.lock:
            self.calls.append(list(cmd))
        exe = str(cmd[0]).lower()
        if exe in self.timeout_paths:
            return CaptureResult(None, "", "", timeout_s, True)
        out = self.outputs.get(exe, self.default)
        if out is None:
            return CaptureResult(1, "", "Error: unknown", 0.01, False)
        return CaptureResult(0, out, "", 0.01, False)


def fake_pe_reader(table: dict[str, tuple[str, str]] | None = None) -> Callable[[Path], tuple[str, str]]:
    table = {k.lower(): v for k, v in (table or {}).items()}

    def read(path: Path) -> tuple[str, str]:
        return table.get(str(path).lower(), ("x64", "console"))

    return read


def fake_validator(exe: Path, release: ReleaseInfo) -> None:
    from launcher import DownloadError

    if not exe.is_file() or not exe.read_bytes().startswith(b"MZ-FAKE-BLENDER"):
        raise DownloadError(f"fake validation failed for {exe}")


# ---------------------------------------------------------------------------------------------- zips
def build_zip(entries: list[tuple[str, bytes]], *, compression: int = zipfile.ZIP_DEFLATED) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=compression) as zf:
        for name, data in entries:
            if name.endswith("/"):
                zf.writestr(zipfile.ZipInfo(name), b"")
            else:
                zf.writestr(name, data)
    return buf.getvalue()


def portable_zip(payload_mib: float = 6.0) -> bytes:
    """Synthetic portable Blender ZIP with a random (incompressible) payload so ranges are meaningful."""
    payload = random.Random(1234).randbytes(int(payload_mib * MIB))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(zipfile.ZipInfo(f"{TOP}/"), b"")
        zf.writestr(f"{TOP}/blender.exe", FAKE_EXE, compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr(f"{TOP}/5.9/scripts/startup/hello.py", b"print('hi')\n", compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr(f"{TOP}/5.9/datafiles/payload.bin", payload, compress_type=zipfile.ZIP_STORED)
    return buf.getvalue()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------------------------- HTTP server
@dataclass
class ServedFile:
    data: bytes
    etag: str
    content_type: str = "application/zip"


@dataclass
class ServerState:
    files: dict[str, ServedFile] = field(default_factory=dict)
    redirects: dict[str, str] = field(default_factory=dict)
    drop_after: dict[str, list[int]] = field(default_factory=dict)  # path -> per-GET byte counts before a drop
    force_416: int = 0
    if_range_mismatch: bool = False
    chunk_delay_s: float = 0.0
    requests: list[dict[str, Any]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: _Server

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - silence stderr logging
        pass

    def do_HEAD(self) -> None:  # noqa: N802
        self._serve(head=True)

    def do_GET(self) -> None:  # noqa: N802
        self._serve(head=False)

    def _record(self, status: int, sent: int = 0, **extra: Any) -> dict[str, Any]:
        entry = {
            "method": self.command,
            "path": urlsplit(self.path).path,
            "headers": {k: v for k, v in self.headers.items()},
            "status": status,
            "sent": sent,
            **extra,
        }
        with self.server.state.lock:
            self.server.state.requests.append(entry)
        return entry

    def _serve(self, *, head: bool) -> None:
        state = self.server.state
        path = urlsplit(self.path).path
        self.close_connection = True
        if path in state.redirects:
            self._record(302)
            self.send_response(302)
            self.send_header("Location", state.redirects[path])
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()
            return
        served = state.files.get(path)
        if served is None:
            self._record(404)
            body = b"not found"
            self.send_response(404)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            if not head:
                self.wfile.write(body)
            return
        data, size = served.data, len(served.data)
        start, status = 0, 200
        range_header = self.headers.get("Range")
        if range_header and not head:
            with state.lock:
                force_416 = state.force_416 > 0
                if force_416:
                    state.force_416 -= 1
            match = re.match(r"bytes=(\d+)-$", range_header.strip())
            requested = int(match.group(1)) if match else 0
            if force_416 or requested >= size:
                self._record(416)
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.send_header("Connection", "close")
                self.end_headers()
                return
            if_range = self.headers.get("If-Range")
            if not state.if_range_mismatch and (if_range is None or if_range == served.etag):
                start, status = requested, 206
        body = data[start:]
        drops = state.drop_after.get(path) if not head else None
        drop_at = None
        if drops:
            with state.lock:
                drop_at = drops.pop(0) if drops else None
        self.send_response(status)
        self.send_header("Content-Type", served.content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("ETag", served.etag)
        self.send_header("Accept-Ranges", "bytes")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{size - 1}/{size}")
        self.send_header("Connection", "close")
        self.end_headers()
        if head:
            self._record(status)
            return
        limit = len(body) if drop_at is None else min(drop_at, len(body))
        sent = 0
        try:
            while sent < limit:
                piece = body[sent : min(limit, sent + 64 * 1024)]
                self.wfile.write(piece)
                sent += len(piece)
                if state.chunk_delay_s:
                    time.sleep(state.chunk_delay_s)
            self.wfile.flush()
        except OSError:  # client went away (cancel)
            pass
        self._record(status, sent, dropped=drop_at is not None, start=start)


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.state = ServerState()

    def handle_error(self, request: Any, client_address: Any) -> None:  # client disconnects are expected
        pass


class LocalServer:
    """Threaded HTTP server on 127.0.0.1 (random port)."""

    def __init__(self) -> None:
        self.httpd = _Server()
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()

    @property
    def state(self) -> ServerState:
        return self.httpd.state

    def url(self, path: str, host: str = "127.0.0.1") -> str:
        return f"http://{host}:{self.port}{path}"

    def add_file(self, path: str, data: bytes, *, etag: str | None = None, content_type: str = "application/zip") -> None:
        self.state.files[path] = ServedFile(data, etag or f'"{sha256(data)[:16]}"', content_type)

    def add_text(self, path: str, text: str) -> None:
        self.add_file(path, text.encode("utf-8"), content_type="text/html")

    def requests(self, method: str | None = None, path: str | None = None) -> list[dict[str, Any]]:
        with self.state.lock:
            items = list(self.state.requests)
        return [r for r in items if (method is None or r["method"] == method) and (path is None or r["path"] == path)]

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(5)


def local_settings(root: Path, server: LocalServer, **overrides: Any) -> LauncherSettings:
    values: dict[str, Any] = {
        "runtime_root": root / "rt",
        "temp_root": root / "tmp",
        "allowed_hosts": ("127.0.0.1",),
        "require_https": False,
        "download_base_url": server.url("/release/"),
        "max_download_retries": 3,
        "http_timeout_s": 10.0,
    }
    values.update(overrides)
    return LauncherSettings(**values)


def make_release(server: LocalServer, data: bytes, *, sha: str | None = "auto", path: str = ZIP_URL_PATH) -> ReleaseInfo:
    return ReleaseInfo(
        version="5.9.1",
        series="5.9",
        file_name=ZIP_NAME,
        url=server.url(path),
        size_bytes=len(data),
        sha256=sha256(data) if sha == "auto" else sha,
        checksum_url=None,
        architecture="x64",
        is_lts=False,
        release_date=None,
    )


class ProgressLog:
    """Collects DownloadProgress snapshots; optional cancel trigger."""

    def __init__(self, cancel: threading.Event | None = None, cancel_at: int | None = None) -> None:
        self.items: list[DownloadProgress] = []
        self.cancel = cancel
        self.cancel_at = cancel_at

    def __call__(self, progress: DownloadProgress) -> None:
        self.items.append(progress)
        if self.cancel is not None and self.cancel_at is not None and progress.bytes_done >= self.cancel_at:
            self.cancel.set()

    @property
    def states(self) -> list[str]:
        out: list[str] = []
        for item in self.items:
            if not out or out[-1] != item.state:
                out.append(item.state)
        return out


class FakeDownloader:
    """Manager-level double: 'installs' a fake embedded build after an optional gate."""

    def __init__(self, settings: LauncherSettings, *, gate: threading.Event | None = None, fail: Exception | None = None):
        self.settings = settings
        self.gate = gate
        self.fail = fail
        self.calls = 0

    def resolve_latest(self, *, version: str | None = None) -> ReleaseInfo:
        return ReleaseInfo(version="5.9.1", series="5.9", file_name=ZIP_NAME,
                           url=f"https://download.blender.org/release/Blender5.9/{ZIP_NAME}", size_bytes=10 * MIB,
                           sha256="0" * 64, checksum_url=None, architecture="x64", is_lts=False, release_date=None)

    def download_and_install(self, release: ReleaseInfo, *, cancel: threading.Event,
                             on_progress: Callable[[DownloadProgress], None]) -> Path:
        self.calls += 1
        base = DownloadProgress.idle()
        for done in range(0, 11):
            base.state = "downloading"
            base.bytes_done, base.bytes_total = done * MIB, 10 * MIB
            on_progress(replace(base))
            if cancel.is_set():
                base.state = "cancelled"
                on_progress(replace(base))
                raise DownloadCancelledError()
            if self.gate is not None and done == 5:
                while not self.gate.wait(0.02):
                    if cancel.is_set():
                        break
        if self.fail is not None:
            raise self.fail
        exe = self.settings.runtime_root / TOP / "blender.exe"
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_bytes(FAKE_EXE)
        base.state = "completed"
        on_progress(replace(base))
        return exe
