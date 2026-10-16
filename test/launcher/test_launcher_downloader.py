"""BlenderDownloader against a local ThreadingHTTPServer: resolve, download, resume, verify, safe extract."""

from __future__ import annotations

import io
import json
import shutil
import threading
import zipfile
from collections import namedtuple
from collections.abc import Iterator
from pathlib import Path

import pytest
from lt_helpers import (
    FAKE_EXE,
    MIB,
    TOP,
    ZIP_NAME,
    ZIP_URL_PATH,
    LocalServer,
    ProgressLog,
    build_zip,
    fake_validator,
    local_settings,
    make_release,
    portable_zip,
    sha256,
)

from launcher import (
    BlenderDownloader,
    ChecksumMismatchError,
    DownloadCancelledError,
    DownloadError,
    LauncherError,
    LauncherSettings,
    PathTooLongError,
    ReleaseInfo,
    ReleaseResolveError,
    UnsafeArchiveError,
    make_opener,
)


@pytest.fixture
def server() -> Iterator[LocalServer]:
    srv = LocalServer()
    try:
        yield srv
    finally:
        srv.close()


@pytest.fixture(scope="session")
def zip_bytes() -> bytes:
    return portable_zip(6.0)


def make_downloader(settings: LauncherSettings, **kw: object) -> BlenderDownloader:
    return BlenderDownloader(settings, opener=make_opener(settings, proxies={}), validator=fake_validator,
                             retry_backoff_s=0.01, **kw)  # type: ignore[arg-type]


def run(downloader: BlenderDownloader, release: ReleaseInfo, log: ProgressLog | None = None,
        cancel: threading.Event | None = None) -> Path:
    return downloader.download_and_install(release, cancel=cancel or threading.Event(), on_progress=log)


def assert_clean(settings: LauncherSettings) -> None:
    """No staging folder and no partially installed top folder left behind."""
    root = settings.runtime_root
    assert not [p for p in root.iterdir() if p.name.startswith(".staging-")]
    assert not (root / TOP).exists()


# ---------------------------------------------------------------------------------------------- happy path
def test_full_download_verify_extract(out_dir: Path, server: LocalServer, zip_bytes: bytes) -> None:
    server.add_file(ZIP_URL_PATH, zip_bytes)
    settings = local_settings(out_dir, server)
    log = ProgressLog()
    exe = run(make_downloader(settings), make_release(server, zip_bytes), log)

    assert exe == settings.runtime_root / TOP / "blender.exe"
    assert exe.read_bytes() == FAKE_EXE
    assert (exe.parent / "5.9" / "datafiles" / "payload.bin").stat().st_size == 6 * MIB
    manifest = json.loads((exe.parent / ".img3d_install.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "5.9.1" and manifest["verified"] is True
    assert manifest["sha256"] == sha256(zip_bytes) and manifest["size_bytes"] == len(zip_bytes)
    assert manifest["installed_at"].endswith("Z")
    downloads = settings.downloads_dir
    assert not (downloads / ZIP_NAME).exists()  # deleted after install
    assert not (downloads / f"{ZIP_NAME}.part").exists() and not (downloads / f"{ZIP_NAME}.part.json").exists()
    assert_clean_staging(settings)

    assert log.states == ["downloading", "verifying", "extracting", "validating", "completed"]
    final = log.items[-1]
    assert final.verified is True and final.install_dir == str(exe.parent) and final.bytes_done == len(zip_bytes)
    assert final.percent == 100.0 and final.resumed is False
    downloading = [p for p in log.items if p.state == "downloading" and p.bytes_done]
    assert downloading and downloading[-1].bytes_total == len(zip_bytes)

    reqs = server.requests()
    assert [r["method"] for r in reqs] == ["HEAD", "GET"]
    assert "Range" not in reqs[1]["headers"]
    assert all(r["headers"].get("User-Agent") == settings.user_agent for r in reqs)
    assert all(r["headers"].get("Accept-Encoding") == "identity" for r in reqs)


def assert_clean_staging(settings: LauncherSettings) -> None:
    assert not [p for p in settings.runtime_root.iterdir() if p.name.startswith(".staging-")]


def test_keep_zip_after_install(out_dir: Path, server: LocalServer, zip_bytes: bytes) -> None:
    server.add_file(ZIP_URL_PATH, zip_bytes)
    settings = local_settings(out_dir, server, keep_zip_after_install=True)
    run(make_downloader(settings), make_release(server, zip_bytes))
    assert (settings.downloads_dir / ZIP_NAME).stat().st_size == len(zip_bytes)


# ---------------------------------------------------------------------------------------------- resume
def test_resume_after_dropped_connection(out_dir: Path, server: LocalServer, zip_bytes: bytes) -> None:
    drop = int(2.5 * MIB)
    server.add_file(ZIP_URL_PATH, zip_bytes)
    server.state.drop_after[ZIP_URL_PATH] = [drop]
    settings = local_settings(out_dir, server)
    log = ProgressLog()
    exe = run(make_downloader(settings), make_release(server, zip_bytes), log)
    assert exe.is_file()
    gets = server.requests("GET")
    assert len(gets) == 2
    assert gets[0]["dropped"] and gets[0]["sent"] == drop and "Range" not in gets[0]["headers"]
    assert gets[1]["headers"]["Range"] == f"bytes={drop}-"
    assert gets[1]["headers"]["If-Range"] == server.state.files[ZIP_URL_PATH].etag
    assert gets[1]["status"] == 206 and gets[1]["start"] == drop
    assert gets[0]["sent"] + gets[1]["sent"] == len(zip_bytes)  # nothing downloaded twice
    assert log.items[-1].resumed is True and log.items[-1].verified is True


def test_cancel_keeps_part_then_resumes(out_dir: Path, server: LocalServer, zip_bytes: bytes) -> None:
    server.add_file(ZIP_URL_PATH, zip_bytes)
    settings = local_settings(out_dir, server)
    downloader = make_downloader(settings)
    release = make_release(server, zip_bytes)
    cancel = threading.Event()
    log = ProgressLog(cancel, cancel_at=2 * MIB)
    with pytest.raises(DownloadCancelledError):
        run(downloader, release, log, cancel)
    part = settings.downloads_dir / f"{ZIP_NAME}.part"
    meta = json.loads((settings.downloads_dir / f"{ZIP_NAME}.part.json").read_text(encoding="utf-8"))
    kept = part.stat().st_size
    assert 2 * MIB <= kept < len(zip_bytes)
    assert meta["url"] == release.url and meta["size"] == len(zip_bytes) and meta["sha256"] == release.sha256
    assert meta["etag"] == server.state.files[ZIP_URL_PATH].etag
    assert log.items[-1].state == "cancelled" and log.items[-1].error_code == "blender_download_cancelled"
    assert_clean(settings)

    log2 = ProgressLog()
    exe = run(downloader, release, log2)
    assert exe.is_file()
    last_get = server.requests("GET")[-1]
    assert last_get["headers"]["Range"] == f"bytes={kept}-" and last_get["status"] == 206
    assert last_get["sent"] == len(zip_bytes) - kept
    assert log2.items[-1].resumed is True and log2.items[-1].verified is True


def _write_partial(settings: LauncherSettings, release: ReleaseInfo, data: bytes, etag: str, n: int) -> None:
    settings.downloads_dir.mkdir(parents=True, exist_ok=True)
    (settings.downloads_dir / f"{ZIP_NAME}.part").write_bytes(data[:n])
    (settings.downloads_dir / f"{ZIP_NAME}.part.json").write_text(json.dumps(
        {"url": release.url, "etag": etag, "size": len(data), "sha256": release.sha256}), encoding="utf-8")


def test_if_range_mismatch_restarts_from_zero(out_dir: Path, server: LocalServer, zip_bytes: bytes) -> None:
    server.add_file(ZIP_URL_PATH, zip_bytes)
    server.state.if_range_mismatch = True  # server answers the conditional range with a full 200
    settings = local_settings(out_dir, server)
    release = make_release(server, zip_bytes)
    _write_partial(settings, release, zip_bytes, server.state.files[ZIP_URL_PATH].etag, 3 * MIB)
    log = ProgressLog()
    exe = run(make_downloader(settings), release, log)
    assert exe.is_file()
    get = server.requests("GET")[0]
    assert get["headers"]["Range"] == f"bytes={3 * MIB}-" and "If-Range" in get["headers"]
    assert get["status"] == 200 and get["sent"] == len(zip_bytes)
    assert log.items[-1].verified is True and log.items[-1].resumed is False


def test_416_restarts_from_zero(out_dir: Path, server: LocalServer, zip_bytes: bytes) -> None:
    server.add_file(ZIP_URL_PATH, zip_bytes)
    server.state.force_416 = 1
    settings = local_settings(out_dir, server)
    release = make_release(server, zip_bytes)
    _write_partial(settings, release, zip_bytes, server.state.files[ZIP_URL_PATH].etag, 1 * MIB)
    exe = run(make_downloader(settings), release)
    assert exe.is_file()
    gets = server.requests("GET")
    assert [g["status"] for g in gets] == [416, 200]
    assert "Range" in gets[0]["headers"] and "Range" not in gets[1]["headers"]


def test_stale_partial_with_other_etag_is_discarded(out_dir: Path, server: LocalServer, zip_bytes: bytes) -> None:
    server.add_file(ZIP_URL_PATH, zip_bytes)
    settings = local_settings(out_dir, server)
    release = make_release(server, zip_bytes)
    _write_partial(settings, release, zip_bytes, '"some-old-etag"', 2 * MIB)
    run(make_downloader(settings), release)
    assert "Range" not in server.requests("GET")[0]["headers"]


# ---------------------------------------------------------------------------------------------- checksum
def test_checksum_mismatch_deletes_part(out_dir: Path, server: LocalServer, zip_bytes: bytes) -> None:
    server.add_file(ZIP_URL_PATH, zip_bytes)
    settings = local_settings(out_dir, server)
    log = ProgressLog()
    with pytest.raises(ChecksumMismatchError) as info:
        run(make_downloader(settings), make_release(server, zip_bytes, sha="0" * 64), log)
    assert info.value.code == "blender_checksum_mismatch"
    assert isinstance(info.value, DownloadError)
    assert not (settings.downloads_dir / f"{ZIP_NAME}.part").exists()
    assert not (settings.downloads_dir / f"{ZIP_NAME}.part.json").exists()
    assert not (settings.downloads_dir / ZIP_NAME).exists()
    assert log.items[-1].state == "failed" and log.items[-1].error_code == "blender_checksum_mismatch"
    assert_clean(settings)


def test_no_official_checksum_installs_unverified(out_dir: Path, server: LocalServer, zip_bytes: bytes) -> None:
    server.add_file(ZIP_URL_PATH, zip_bytes)
    settings = local_settings(out_dir, server)
    log = ProgressLog()
    exe = run(make_downloader(settings), make_release(server, zip_bytes, sha=None), log)
    manifest = json.loads((exe.parent / ".img3d_install.json").read_text(encoding="utf-8"))
    assert manifest["verified"] is False and manifest["sha256"] is None
    assert log.items[-1].state == "completed" and log.items[-1].verified is False


# ---------------------------------------------------------------------------------------------- HTTP policy
def test_redirect_to_non_allowlisted_host_refused(out_dir: Path, server: LocalServer, zip_bytes: bytes) -> None:
    server.add_file(ZIP_URL_PATH, zip_bytes)
    server.state.redirects["/evil"] = server.url(ZIP_URL_PATH, host="localhost")
    settings = local_settings(out_dir, server)
    with pytest.raises(DownloadError, match="Refusing to contact localhost"):
        run(make_downloader(settings), make_release(server, zip_bytes, path="/evil"))
    assert [r["path"] for r in server.requests()] == ["/evil"]  # the redirect target was never contacted
    assert_clean(settings)


def test_redirect_within_allowlist_followed(out_dir: Path, server: LocalServer, zip_bytes: bytes) -> None:
    server.add_file(ZIP_URL_PATH, zip_bytes)
    server.state.redirects["/mirror"] = ZIP_URL_PATH  # relative Location, same host
    settings = local_settings(out_dir, server)
    exe = run(make_downloader(settings), make_release(server, zip_bytes, path="/mirror"))
    assert exe.is_file()
    assert [(r["method"], r["path"]) for r in server.requests()] == [
        ("HEAD", "/mirror"), ("HEAD", ZIP_URL_PATH), ("GET", "/mirror"), ("GET", ZIP_URL_PATH)]


def test_plain_http_refused_when_https_required(out_dir: Path, server: LocalServer, zip_bytes: bytes) -> None:
    server.add_file(ZIP_URL_PATH, zip_bytes)
    settings = local_settings(out_dir, server, require_https=True)
    with pytest.raises(DownloadError, match="non-HTTPS"):
        run(make_downloader(settings), make_release(server, zip_bytes))
    with pytest.raises(DownloadError, match="non-HTTPS"):
        make_downloader(settings).resolve_latest()
    assert server.requests() == []


def test_host_not_allowlisted_refused(out_dir: Path, server: LocalServer, zip_bytes: bytes) -> None:
    settings = local_settings(out_dir, server, allowed_hosts=("download.blender.org",))
    with pytest.raises(DownloadError, match="not an allowed Blender download host"):
        run(make_downloader(settings), make_release(server, zip_bytes))
    assert server.requests() == []


def test_insufficient_disk_space(out_dir: Path, server: LocalServer, zip_bytes: bytes,
                                 monkeypatch: pytest.MonkeyPatch) -> None:
    server.add_file(ZIP_URL_PATH, zip_bytes)
    settings = local_settings(out_dir, server)
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda path: usage(10**12, 10**12 - 1000, 1000))
    with pytest.raises(DownloadError, match="Not enough free disk space"):
        run(make_downloader(settings), make_release(server, zip_bytes))
    assert server.requests("GET") == []


def test_http_404_is_fatal_without_retries(out_dir: Path, server: LocalServer, zip_bytes: bytes) -> None:
    settings = local_settings(out_dir, server)
    with pytest.raises(DownloadError, match="HTTP 404"):
        run(make_downloader(settings), make_release(server, zip_bytes))
    assert len(server.requests()) == 1


def test_retries_exhausted(out_dir: Path, server: LocalServer, zip_bytes: bytes) -> None:
    server.add_file(ZIP_URL_PATH, zip_bytes)
    server.state.drop_after[ZIP_URL_PATH] = [0] * 10  # every GET dies immediately
    settings = local_settings(out_dir, server, max_download_retries=2)
    with pytest.raises(DownloadError, match="after 3 attempts"):
        run(make_downloader(settings), make_release(server, zip_bytes))
    assert len(server.requests("GET")) == 3


# ---------------------------------------------------------------------------------------------- unsafe archives
def _symlink_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{TOP}/blender.exe", FAKE_EXE)
        info = zipfile.ZipInfo(f"{TOP}/link")
        info.create_system = 3
        info.external_attr = (0o120777 << 16)
        zf.writestr(info, "../../../Windows")
    return buf.getvalue()


def _backslash_zip() -> bytes:
    raw = build_zip([(f"{TOP}/blender.exe", FAKE_EXE), (f"{TOP}/a_b.txt", b"x")])
    return raw.replace(b"a_b.txt", b"a\\b.txt")


def _too_many_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr(f"{TOP}/blender.exe", FAKE_EXE)
        for i in range(20_000):
            zf.writestr(f"{TOP}/f/{i}", b"")
    return buf.getvalue()


UNSAFE = {
    "traversal": (lambda: build_zip([(f"{TOP}/blender.exe", FAKE_EXE), (f"{TOP}/../evil.txt", b"x")]),
                  UnsafeArchiveError, "traversal"),
    "abs_drive": (lambda: build_zip([(f"{TOP}/blender.exe", FAKE_EXE), ("C:/evil.txt", b"x")]),
                  UnsafeArchiveError, "Absolute"),
    "abs_root": (lambda: build_zip([(f"{TOP}/blender.exe", FAKE_EXE), ("/evil.txt", b"x")]),
                 UnsafeArchiveError, "Absolute"),
    "symlink": (_symlink_zip, UnsafeArchiveError, "Symbolic link"),
    "backslash": (_backslash_zip, UnsafeArchiveError, "backslash"),
    "two_tops": (lambda: build_zip([(f"{TOP}/blender.exe", FAKE_EXE), ("other/readme.txt", b"x")]),
                 UnsafeArchiveError, "single top folder"),
    "wrong_top": (lambda: build_zip([("blender-5.9.2-windows-x64/blender.exe", FAKE_EXE)]),
                  UnsafeArchiveError, "Unexpected top folder"),
    "root_file": (lambda: build_zip([(f"{TOP}", FAKE_EXE)]), UnsafeArchiveError, "outside the top folder"),
    "ads": (lambda: build_zip([(f"{TOP}/blender.exe", FAKE_EXE), (f"{TOP}/x.txt:stream", b"x")]),
            UnsafeArchiveError, "alternate data stream"),
    "reserved": (lambda: build_zip([(f"{TOP}/blender.exe", FAKE_EXE), (f"{TOP}/CON.txt", b"x")]),
                 UnsafeArchiveError, "Invalid Windows file name"),
    "too_many": (_too_many_zip, UnsafeArchiveError, "too many entries"),
    "ratio": (lambda: build_zip([(f"{TOP}/blender.exe", FAKE_EXE), (f"{TOP}/bomb.bin", b"\0" * (11 * MIB))]),
              UnsafeArchiveError, "compression ratio"),
    "no_exe": (lambda: build_zip([(f"{TOP}/readme.txt", b"x")]), UnsafeArchiveError, "does not contain"),
    "long_path": (lambda: build_zip([(f"{TOP}/blender.exe", FAKE_EXE),
                                     (f"{TOP}/{'a' * 100}/{'b' * 100}/c.txt", b"x")]),
                  PathTooLongError, "path of"),
}


@pytest.mark.parametrize("case", list(UNSAFE))
def test_unsafe_archives_rejected(case: str, out_dir: Path, server: LocalServer) -> None:
    builder, error, match = UNSAFE[case]
    data = builder()
    server.add_file(ZIP_URL_PATH, data)
    settings = local_settings(out_dir, server)
    log = ProgressLog()
    with pytest.raises(error, match=match):
        run(make_downloader(settings), make_release(server, data), log)
    assert_clean(settings)
    assert not (settings.downloads_dir / f"{ZIP_NAME}.part").exists()
    if error is UnsafeArchiveError:
        assert not (settings.downloads_dir / ZIP_NAME).exists()  # unsafe archive is not kept
        assert log.items[-1].error_code == "blender_unsafe_archive"
    assert not (out_dir / "evil.txt").exists() and not (out_dir / "rt" / "evil.txt").exists()


def test_total_uncompressed_limit(out_dir: Path, server: LocalServer, zip_bytes: bytes) -> None:
    server.add_file(ZIP_URL_PATH, zip_bytes)
    settings = local_settings(out_dir, server)
    downloader = make_downloader(settings)
    downloader.max_total_uncompressed = 1 * MIB  # production limit is 4 GB; same code path
    with pytest.raises(UnsafeArchiveError, match="expands to"):
        run(downloader, make_release(server, zip_bytes))
    assert_clean(settings)


def test_cancel_during_extraction_keeps_verified_zip(out_dir: Path, server: LocalServer, zip_bytes: bytes) -> None:
    server.add_file(ZIP_URL_PATH, zip_bytes)
    settings = local_settings(out_dir, server)
    downloader = make_downloader(settings)
    release = make_release(server, zip_bytes)
    cancel = threading.Event()

    def on_progress(progress) -> None:  # type: ignore[no-untyped-def]
        if progress.state == "extracting":
            cancel.set()

    with pytest.raises(DownloadCancelledError):
        downloader.download_and_install(release, cancel=cancel, on_progress=on_progress)
    assert_clean(settings)
    assert (settings.downloads_dir / ZIP_NAME).stat().st_size == len(zip_bytes)  # verified zip kept
    gets_before = len(server.requests("GET"))
    log = ProgressLog()
    exe = run(downloader, release, log)
    assert exe.is_file() and len(server.requests("GET")) == gets_before  # no second download
    assert log.items[-1].verified is True
    assert not (settings.downloads_dir / ZIP_NAME).exists()


def test_inspect_real_layout_passes(out_dir: Path, zip_bytes: bytes) -> None:
    settings = LauncherSettings(runtime_root=out_dir / "rt", temp_root=out_dir / "tmp")
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        entries = BlenderDownloader(settings).inspect_archive(zf, TOP)
    assert len(entries) == 4 and entries[0][1] == [TOP]


# ---------------------------------------------------------------------------------------------- reuse / safety
def test_existing_valid_install_is_reused(out_dir: Path, server: LocalServer, zip_bytes: bytes) -> None:
    server.add_file(ZIP_URL_PATH, zip_bytes)
    settings = local_settings(out_dir, server)
    exe = settings.runtime_root / TOP / "blender.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(FAKE_EXE)
    log = ProgressLog()
    assert run(make_downloader(settings), make_release(server, zip_bytes), log) == exe
    assert server.requests() == []
    assert (exe.parent / ".img3d_install.json").is_file()
    assert log.items[-1].state == "completed"


def test_invalid_existing_install_is_replaced(out_dir: Path, server: LocalServer, zip_bytes: bytes) -> None:
    server.add_file(ZIP_URL_PATH, zip_bytes)
    settings = local_settings(out_dir, server)
    broken = settings.runtime_root / TOP / "blender.exe"
    broken.parent.mkdir(parents=True)
    broken.write_bytes(b"not blender")
    (broken.parent / "stale.txt").write_text("old")
    exe = run(make_downloader(settings), make_release(server, zip_bytes))
    assert exe.read_bytes() == FAKE_EXE and not (exe.parent / "stale.txt").exists()


def test_stale_staging_is_cleaned(out_dir: Path, server: LocalServer, zip_bytes: bytes) -> None:
    server.add_file(ZIP_URL_PATH, zip_bytes)
    settings = local_settings(out_dir, server)
    stale = settings.runtime_root / ".staging-deadbeef" / "junk"
    stale.mkdir(parents=True)
    run(make_downloader(settings), make_release(server, zip_bytes))
    assert not stale.parent.exists()


def test_containment_guard_refuses_outside_runtime_root(out_dir: Path, server: LocalServer) -> None:
    settings = local_settings(out_dir, server)
    settings.runtime_root.mkdir(parents=True)
    downloader = make_downloader(settings)
    outside_dir = out_dir / "outside"
    outside_dir.mkdir()
    outside_file = out_dir / "precious.txt"
    outside_file.write_text("keep me")
    for action in (lambda: downloader._remove_tree(outside_dir),
                   lambda: downloader._remove_tree(settings.runtime_root),
                   lambda: downloader._remove_tree(settings.runtime_root / ".." / "outside"),
                   lambda: downloader._remove_file(outside_file),
                   lambda: downloader._replace(outside_file, settings.runtime_root / "x")):
        with pytest.raises(LauncherError) as info:
            action()
        assert info.value.code == "launcher_unsafe_path"
    assert outside_dir.is_dir() and outside_file.read_text() == "keep me"
    inside = settings.runtime_root / "tmpdir"
    inside.mkdir()
    downloader._remove_tree(inside)
    assert not inside.exists()


def test_release_with_bad_file_name_rejected(out_dir: Path, server: LocalServer, zip_bytes: bytes) -> None:
    settings = local_settings(out_dir, server)
    release = make_release(server, zip_bytes)
    release.file_name = "../../evil.zip"
    with pytest.raises(DownloadError, match="Not an official"):
        run(make_downloader(settings), release)


# ---------------------------------------------------------------------------------------------- resolve
INDEX = """<html><body><pre><a href="../">../</a>
<a href="Blender2.28a/">Blender2.28a/</a>                                       01-Jan-2004 00:00                   -
<a href="Blender5.0/">Blender5.0/</a>                                        18-Nov-2025 09:00                   -
<a href="Blender5.8/">Blender5.8/</a>                                        15-Jul-2027 09:21                   -
<a href="Blender5.9/">Blender5.9/</a>                                        15-Sep-2027 09:21                   -
<a href="Blender5.10/">Blender5.10/</a>                                      15-Oct-2027 09:21                   -
<a href="BlenderBenchmark2.0/">BlenderBenchmark2.0/</a>                      01-Jan-2020 00:00                   -
</pre></body></html>"""


def _listing(rows: list[tuple[str, str, int]]) -> str:
    lines = [f'<a href="{n}">{n}</a>                  {d}           {s}' for n, d, s in rows]
    return "<html><body><pre>\n" + "\n".join(lines) + "\n</pre></body></html>"


def test_resolve_latest_from_local_index(out_dir: Path, server: LocalServer) -> None:
    sums = (f"{'a' * 64}  blender-5.9.1-linux-x64.tar.xz\n{'b' * 64}  blender-5.9.1-windows-x64.zip\n"
            f"{'c' * 64}  blender-5.9.1-windows-arm64.zip\n")
    server.add_text("/release/", INDEX)
    server.add_text("/release/Blender5.10/", _listing([("blender-5.10.0-macos-arm64.dmg", "15-Oct-2027 09:00", 1)]))
    server.add_text("/release/Blender5.9/", _listing([
        ("blender-5.9.0-windows-x64.zip", "15-Aug-2027 09:16", 1000),
        ("blender-5.9.1-windows-x64.zip", "15-Sep-2027 09:16", 404453484),
        ("blender-5.9.1-windows-arm64.zip", "15-Sep-2027 09:16", 274036892),
        ("blender-5.9.1-windows-x64.msi", "15-Sep-2027 09:16", 1),
        ("blender-5.9.2rc1-windows-x64.zip", "16-Sep-2027 09:16", 1),
        ("blender-5.9.1.sha256", "15-Sep-2027 09:16", 777),
        ("blender-5.9.0.sha256", "15-Aug-2027 09:16", 777),
    ]))
    server.add_text("/release/Blender5.9/blender-5.9.1.sha256", sums)
    server.add_text("/release/Blender5.9/blender-5.9.0.sha256", f"{'d' * 64}  blender-5.9.0-windows-x64.zip\n")
    server.add_text("/release/Blender5.8/", _listing([("blender-5.8.3-windows-x64.zip", "15-Jul-2027 09:16", 5)]))
    settings = local_settings(out_dir, server)
    downloader = make_downloader(settings, arch="x64")

    release = downloader.resolve_latest()
    assert release.version == "5.9.1" and release.series == "5.9" and release.architecture == "x64"
    assert release.file_name == "blender-5.9.1-windows-x64.zip"
    assert release.url == server.url("/release/Blender5.9/blender-5.9.1-windows-x64.zip")
    assert release.size_bytes == 404453484 and release.sha256 == "b" * 64
    assert release.checksum_url == server.url("/release/Blender5.9/blender-5.9.1.sha256")
    assert release.release_date == "2027-09-15T09:16:00Z" and release.is_lts is False
    assert release.to_dict()["host"] == "127.0.0.1"

    arm = make_downloader(settings, arch="arm64").resolve_latest()
    assert arm.file_name == "blender-5.9.1-windows-arm64.zip" and arm.sha256 == "c" * 64
    pinned = downloader.resolve_latest(version="5.9.0")
    assert pinned.version == "5.9.0" and pinned.sha256 == "d" * 64
    series = downloader.resolve_latest(version="5.8")
    assert series.version == "5.8.3" and series.sha256 is None and series.checksum_url is None
    lts_channel = make_downloader(local_settings(out_dir, server, download_channel="lts"), arch="x64")
    assert lts_channel.resolve_latest().version == "5.9.1"  # no LTS series >= 5.1 -> newest stable
    with pytest.raises(ReleaseResolveError):
        downloader.resolve_latest(version="5.9.7")
    assert all(r["headers"].get("User-Agent") == settings.user_agent for r in server.requests())


def test_manager_end_to_end_with_local_release(out_dir: Path, server: LocalServer, zip_bytes: bytes) -> None:
    """Manager + real downloader + real detector (fake --version runner): needs_download -> download -> ready."""
    import time

    from lt_helpers import FakeRunner, fake_pe_reader

    from launcher import BlenderDetector, BlenderRuntimeManager, RuntimeStatus

    server.add_text("/release/", INDEX)
    server.add_text("/release/Blender5.9/", _listing([(ZIP_NAME, "15-Sep-2027 09:16", len(zip_bytes)),
                                                       ("blender-5.9.1.sha256", "15-Sep-2027 09:16", 100)]))
    server.add_text("/release/Blender5.9/blender-5.9.1.sha256", f"{sha256(zip_bytes)}  {ZIP_NAME}\n")
    server.add_file(ZIP_URL_PATH, zip_bytes)
    settings = local_settings(out_dir, server)
    runner = FakeRunner({str(settings.runtime_root / TOP / "blender.exe"): "Blender 5.9.1\n"})
    detector = BlenderDetector(settings, runner=runner, pe_reader=fake_pe_reader(), sources=("embedded_dir",))
    seen: list[RuntimeStatus] = []
    manager = BlenderRuntimeManager(settings, detector=detector, downloader=make_downloader(settings, arch="x64"),
                                    on_change=seen.append)
    try:
        assert manager.scan().state == "needs_download"
        release = manager.resolve_release()
        assert release.version == "5.9.1" and release.sha256 == sha256(zip_bytes)
        manager.start_download()
        deadline = time.monotonic() + 30
        while manager.status().state == "downloading" and time.monotonic() < deadline:
            time.sleep(0.05)
        status = manager.status()
        assert status.state == "ready", status.to_dict()
        assert status.selected is not None and status.selected.kind == "embedded"
        assert status.download is not None and status.download.state == "completed" and status.download.verified
        assert manager.require_runtime().executable == settings.runtime_root / TOP / "blender.exe"
        download_states = [s.download.state for s in seen if s.download is not None]
        assert {"resolving", "downloading", "verifying", "extracting", "validating", "completed"} <= set(download_states)
        assert len(server.requests("GET", "/release/")) == 1  # release resolved once (cached for the download)
    finally:
        manager.close()


def test_resolve_errors(out_dir: Path, server: LocalServer) -> None:
    settings = local_settings(out_dir, server)
    with pytest.raises(ReleaseResolveError, match="HTTP 404"):
        make_downloader(settings).resolve_latest()
    server.add_text("/release/", INDEX.replace("Blender5.", "Blender4."))
    with pytest.raises(ReleaseResolveError, match="No Blender 5.1"):
        make_downloader(settings).resolve_latest()
    with pytest.raises(ReleaseResolveError):
        make_downloader(settings, arch="x86").resolve_latest()
