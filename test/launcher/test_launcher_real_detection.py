"""Real detection on this PC (marker ``blender``): 5.2.2 LTS compatible, 4.2.17 incompatible, empty 4.4/4.5 folders
ignored, auto selection = 5.2.2, cold detect < 3 s, and the exact prompt when only 4.2 is visible."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pytest
from lt_helpers import BLENDER_42, BLENDER_52

from launcher import (
    BlenderDetector,
    BlenderDownloader,
    BlenderRuntimeManager,
    DownloadError,
    LauncherSettings,
    ReleaseInfo,
)

pytestmark = pytest.mark.blender

MSG_42 = (
    "Blender 4.2.17 is installed but Auto Mesh Tuner requires Blender 5.1 or newer. "
    "Download a supported portable Blender?"
)


def settings_for(out_dir: Path, **kw: object) -> LauncherSettings:
    return LauncherSettings(runtime_root=out_dir / "rt", temp_root=out_dir / "tmp", **kw)  # type: ignore[arg-type]


def test_real_detection_and_selection(out_dir: Path) -> None:
    settings = settings_for(out_dir)
    detector = BlenderDetector(settings)
    started = time.perf_counter()
    installs = detector.detect(force=True)
    cold_s = time.perf_counter() - started
    assert cold_s < 3.0, cold_s

    by_path = {os.path.normcase(str(i.executable)): i for i in installs}
    b52 = by_path[os.path.normcase(str(BLENDER_52))]
    assert b52.valid and b52.compatible and b52.kind == "system"
    assert b52.version is not None and b52.version.label == "5.2.2 LTS" and b52.version.lts and b52.is_stable
    assert b52.architecture == "x64" and b52.subsystem == "console" and not b52.emulated
    assert b52.version.build_hash == "d13f752e3b9c" and b52.version.build_branch == "blender-v5.2-release"
    assert {"registry_uninstall", "program_files"} <= set(b52.origins)

    if BLENDER_42.is_file():
        b42 = by_path[os.path.normcase(str(BLENDER_42))]
        assert b42.valid and not b42.compatible and b42.kind == "system"
        assert b42.version is not None and b42.version.label == "4.2.17 LTS"
        assert b42.incompatible_reason is not None and "5.1" in b42.incompatible_reason
    assert not [p for p in by_path if "blender 4.4" in p or "blender 4.5" in p]
    assert all(not os.path.basename(p).startswith("blender-launcher") for p in by_path)

    manager = BlenderRuntimeManager(settings, detector=detector)
    status = manager.scan()
    assert status.state == "ready" and status.selected is not None
    assert os.path.normcase(str(status.selected.executable)) == os.path.normcase(str(BLENDER_52))
    assert status.selection_reason == "Highest compatible system Blender" and status.warnings == []
    assert manager.require_runtime().executable == status.selected.executable
    json.dumps(status.to_dict())

    warm = time.perf_counter()
    detector.detect()
    warm_ms = (time.perf_counter() - warm) * 1000
    (out_dir / "timings.json").write_text(json.dumps({
        "cold_detect_s": round(cold_s, 3),
        "warm_detect_ms": round(warm_ms, 1),
        "source_ms": detector.last_timings_ms,
        "installations": [i.to_dict() for i in installs],
    }, indent=1), encoding="utf-8")


@pytest.mark.skipif(not BLENDER_42.is_file(), reason="Blender 4.2 not installed")
def test_only_42_visible_gives_exact_prompt(out_dir: Path) -> None:
    settings = settings_for(out_dir, extra_search_dirs=[BLENDER_42.parent])
    detector = BlenderDetector(settings, sources=("user",))
    status = BlenderRuntimeManager(settings, detector=detector).scan()
    assert [i.version.label for i in status.installations] == ["4.2.17 LTS"]  # type: ignore[union-attr]
    assert status.state == "needs_download" and status.selected is None
    assert status.message == MSG_42


def test_pin_real_52_in_system_mode(out_dir: Path) -> None:
    settings = settings_for(out_dir)
    manager = BlenderRuntimeManager(settings)
    status = manager.select("system", str(BLENDER_52.parent / "blender-launcher.exe"))
    assert status.mode == "system" and status.selection_reason == "Pinned system Blender"
    assert os.path.normcase(str(status.selected.executable)) == os.path.normcase(str(BLENDER_52))  # type: ignore[union-attr]
    if BLENDER_42.is_file():
        from launcher import BlenderIncompatibleError

        with pytest.raises(BlenderIncompatibleError, match="4.2.17 is older than 5.1"):
            manager.select("system", str(BLENDER_42))


def test_default_post_install_validation_with_real_exe(out_dir: Path) -> None:
    downloader = BlenderDownloader(settings_for(out_dir))
    release = ReleaseInfo(version="5.2.2", series="5.2", file_name="blender-5.2.2-windows-x64.zip",
                          url="https://download.blender.org/release/Blender5.2/blender-5.2.2-windows-x64.zip",
                          size_bytes=None, sha256=None, checksum_url=None, architecture="x64", is_lts=True,
                          release_date=None)
    downloader._validate(BLENDER_52, release)  # default validator: PE + --version == requested
    release.version = "5.2.1"
    with pytest.raises(DownloadError, match="expected 5.2.1"):
        downloader._validate(BLENDER_52, release)


def test_concurrent_scans_are_serialized(out_dir: Path) -> None:
    manager = BlenderRuntimeManager(settings_for(out_dir))
    results: list[str] = []
    threads = [threading.Thread(target=lambda: results.append(manager.scan().state)) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert results == ["ready"] * 3
