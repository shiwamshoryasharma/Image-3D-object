"""Live checks against download.blender.org (marker ``network``; opt-in). Never downloads the full ZIP."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from launcher import BlenderDownloader, LauncherSettings

pytestmark = pytest.mark.network

SHA_522_X64 = "3849d17a682cba006075aaa3f3597ecb5c9c30ec31035b2e092c53e40679b535"
SIZE_522_X64 = 404453484


def _tuple(version: str) -> tuple[int, ...]:
    return tuple(int(p) for p in version.split("."))


def test_resolve_latest_and_range_request(out_dir: Path) -> None:
    settings = LauncherSettings(runtime_root=out_dir / "rt", temp_root=out_dir / "tmp")
    downloader = BlenderDownloader(settings)
    arch = downloader.native_architecture()
    release = downloader.resolve_latest()
    report: dict[str, object] = {"release": release.to_dict(), "arch": arch}

    assert _tuple(release.version) >= (5, 2, 2)
    assert release.architecture == arch
    assert release.file_name == f"blender-{release.version}-windows-{arch}.zip"
    assert release.url == (f"https://download.blender.org/release/Blender{release.series}/{release.file_name}")
    assert release.sha256 is not None and re.fullmatch(r"[0-9a-f]{64}", release.sha256)
    assert release.checksum_url == f"https://download.blender.org/release/Blender{release.series}/" \
                                   f"blender-{release.version}.sha256"
    assert release.size_bytes and release.size_bytes > 100_000_000
    assert release.release_date and release.release_date.endswith("Z")
    if release.version == "5.2.2" and arch == "x64":
        assert release.sha256 == SHA_522_X64
        assert release.size_bytes == SIZE_522_X64
        assert release.is_lts is True

    with downloader.open_url(release.url, method="HEAD") as head:
        assert head.status == 200
        assert int(head.headers["Content-Length"]) == release.size_bytes
        etag = head.headers.get("ETag")
        report["head"] = {"status": head.status, "etag": etag, "accept_ranges": head.headers.get("Accept-Ranges")}
    headers = {"Range": "bytes=0-1023"}
    if etag:
        headers["If-Range"] = etag
    with downloader.open_url(release.url, headers=headers) as part:
        body = part.read()
        assert part.status == 206
        assert part.headers["Content-Range"] == f"bytes 0-1023/{release.size_bytes}"
        assert len(body) == 1024 and body[:4] == b"PK\x03\x04"  # ZIP local file header
        report["range"] = {"status": part.status, "content_range": part.headers["Content-Range"]}

    pinned = downloader.resolve_latest(version="5.1")
    assert pinned.series == "5.1" and _tuple(pinned.version) >= (5, 1, 2) and pinned.sha256
    report["pinned_5_1"] = pinned.to_dict()
    (out_dir / "network_report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
