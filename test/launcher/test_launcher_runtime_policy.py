"""Selection policy + RuntimeConfig persistence + manager state machine (fake installations / downloader)."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest
from lt_helpers import FAKE_EXE, TOP, FakeDetector, FakeDownloader, FakeRunner, fake_pe_reader, make_install

from launcher import (
    BlenderDetector,
    BlenderIncompatibleError,
    BlenderInstallation,
    BlenderNotFoundError,
    BlenderRuntimeManager,
    BlenderUnavailableError,
    DownloadError,
    DownloadInProgressError,
    DownloadsDisabledError,
    LauncherSettings,
    RuntimeConfig,
    RuntimeStatus,
    select_runtime,
)

MSG_42 = (
    "Blender 4.2.17 is installed but Auto Mesh Tuner requires Blender 5.1 or newer. "
    "Download a supported portable Blender?"
)
MSG_NONE = (
    "No Blender installation was found. Auto Mesh Tuner requires Blender 5.1 or newer. "
    "Download a supported portable Blender?"
)


def settings_for(out_dir: Path, **overrides: object) -> LauncherSettings:
    return LauncherSettings(runtime_root=out_dir / "rt", temp_root=out_dir / "tmp", **overrides)  # type: ignore[arg-type]


def sys_install(out_dir: Path, version: str, folder: str | None = None) -> BlenderInstallation:
    return make_install(out_dir / "sys" / (folder or f"Blender {version}") / "blender.exe", version)


def emb_install(out_dir: Path, version: str) -> BlenderInstallation:
    number = version.split()[0]
    return make_install(out_dir / "rt" / f"blender-{number}-windows-x64" / "blender.exe", version, kind="embedded")


def manager_for(out_dir: Path, installs: list[BlenderInstallation], config: RuntimeConfig | None = None,
                **settings_kw: object) -> BlenderRuntimeManager:
    settings = settings_for(out_dir, **settings_kw)
    if config is not None:
        config.save(settings.config_path)
    return BlenderRuntimeManager(settings, detector=FakeDetector(installs))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------------------------- table-driven policy
def test_highest_stable_system_wins(out_dir: Path) -> None:
    installs = [sys_install(out_dir, v) for v in ("3.6.5 LTS", "4.5.3 LTS", "5.1.2", "5.2.2 LTS")]
    status = manager_for(out_dir, installs).scan()
    assert status.state == "ready"
    assert status.selected is not None and status.selected.version.label == "5.2.2 LTS"  # type: ignore[union-attr]
    assert status.selection_reason == "Highest compatible system Blender"
    assert status.message is None and status.warnings == []
    assert [i.version.number for i in status.installations] == ["5.2.2", "5.1.2", "4.5.3", "3.6.5"]  # type: ignore[union-attr]


def test_only_old_blender_needs_download_with_exact_message(out_dir: Path) -> None:
    status = manager_for(out_dir, [sys_install(out_dir, "4.2.17 LTS")]).scan()
    assert status.state == "needs_download"
    assert status.selected is None
    assert status.message == MSG_42
    assert status.can_download is True


def test_nothing_installed_message(out_dir: Path) -> None:
    status = manager_for(out_dir, []).scan()
    assert status.state == "needs_download"
    assert status.message == MSG_NONE


def test_embedded_invalid_only_counts_as_nothing(out_dir: Path) -> None:
    old_embedded = emb_install(out_dir, "4.2.17 LTS")
    status = manager_for(out_dir, [old_embedded]).scan()
    assert status.message == MSG_NONE  # only *system* installs feed the "is installed but" prompt


def test_embedded_mode_pin_wins_over_system(out_dir: Path) -> None:
    system = sys_install(out_dir, "5.2.2 LTS")
    embedded = emb_install(out_dir, "5.1.1")
    config = RuntimeConfig(mode="embedded", pinned_executable=str(embedded.executable))
    status = manager_for(out_dir, [system, embedded], config).scan()
    assert status.selected is not None and status.selected.executable == embedded.executable
    assert status.selection_reason == "Pinned embedded Blender"
    assert status.mode == "embedded"
    # without a pin, embedded mode still prefers the (lower) embedded build over the system one
    status = manager_for(out_dir, [system, embedded], RuntimeConfig(mode="embedded")).scan()
    assert status.selected is not None and status.selected.kind == "embedded"
    assert status.selection_reason == "Highest compatible embedded Blender"


def test_embedded_mode_without_embedded_falls_back_to_system(out_dir: Path) -> None:
    system = sys_install(out_dir, "5.2.2 LTS")
    status = manager_for(out_dir, [system], RuntimeConfig(mode="embedded")).scan()
    assert status.state == "ready"
    assert status.mode == "embedded"  # mode stays as chosen
    assert status.selected is not None and status.selected.executable == system.executable
    assert "Embedded Blender is selected but not installed — using system Blender 5.2.2." in status.warnings


def test_system_pin_of_older_compatible_beats_auto_highest(out_dir: Path) -> None:
    b51 = sys_install(out_dir, "5.1.0")
    b52 = sys_install(out_dir, "5.2.2 LTS")
    config = RuntimeConfig(mode="system", pinned_executable=str(b51.executable))
    status = manager_for(out_dir, [b51, b52], config).scan()
    assert status.selected is not None and status.selected.executable == b51.executable
    assert status.selection_reason == "Pinned system Blender"
    assert status.warnings == []


def test_unstable_never_auto_selected_but_pinnable(out_dir: Path) -> None:
    beta = sys_install(out_dir, "5.3.0 Beta")
    b52 = sys_install(out_dir, "5.2.2 LTS")
    status = manager_for(out_dir, [beta, b52]).scan()
    assert status.selected is not None and status.selected.executable == b52.executable

    config = RuntimeConfig(mode="system", pinned_executable=str(beta.executable))
    status = manager_for(out_dir, [beta, b52], config).scan()
    assert status.selected is not None and status.selected.executable == beta.executable
    assert any("not a stable release" in w for w in status.warnings)

    status = manager_for(out_dir, [beta], RuntimeConfig()).scan()  # only an unstable build: never auto-selected
    assert status.state == "needs_download" and status.selected is None
    assert status.message is not None and status.message.startswith("Blender 5.3.0 Beta is installed but is not a stable")


def test_override_executable_wins(out_dir: Path) -> None:
    b51 = sys_install(out_dir, "5.1.0")
    b52 = sys_install(out_dir, "5.2.2 LTS")
    status = manager_for(out_dir, [b51, b52], override_executable=b51.executable).scan()
    assert status.selected is not None and status.selected.executable == b51.executable
    assert status.selection_reason == "Configured override"
    # an unusable override is ignored with a warning
    old = sys_install(out_dir, "4.2.17 LTS")
    status = manager_for(out_dir, [old, b52], override_executable=old.executable).scan()
    assert status.selected is not None and status.selected.executable == b52.executable
    assert any("override" in w and "older than 5.1" in w for w in status.warnings)


def test_unusable_system_pin_falls_back_with_warning(out_dir: Path) -> None:
    b52 = sys_install(out_dir, "5.2.2 LTS")
    config = RuntimeConfig(mode="system", pinned_executable=str(out_dir / "gone" / "blender.exe"))
    status = manager_for(out_dir, [b52], config).scan()
    assert status.selected is not None and status.selected.executable == b52.executable
    assert status.mode == "system"
    assert any(w.startswith("Pinned Blender") and "using system Blender 5.2.2" in w for w in status.warnings)


def test_system_mode_without_system_uses_embedded_with_warning(out_dir: Path) -> None:
    embedded = emb_install(out_dir, "5.2.2 LTS")
    old = sys_install(out_dir, "4.2.17 LTS")
    status = manager_for(out_dir, [embedded, old], RuntimeConfig(mode="system")).scan()
    assert status.selected is not None and status.selected.kind == "embedded"
    assert any("no compatible system install" in w for w in status.warnings)


def test_emulated_x64_on_arm64_is_only_a_fallback(out_dir: Path) -> None:
    native = make_install(out_dir / "a" / "blender.exe", "5.1.2", arch="arm64")
    emulated = make_install(out_dir / "b" / "blender.exe", "5.2.2 LTS")
    emulated.emulated = True
    decision = select_runtime([emulated, native], RuntimeConfig(), settings_for(out_dir))
    assert decision.selected is native


# ---------------------------------------------------------------------------------------------- persistence
def test_config_persisted_after_scan(out_dir: Path) -> None:
    b52 = sys_install(out_dir, "5.2.2 LTS")
    manager = manager_for(out_dir, [b52])
    manager.scan()
    data = json.loads(manager.settings.config_path.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert data["mode"] == "auto" and data["pinned_executable"] is None
    assert data["selected_executable"] == str(b52.executable)
    assert data["selected_version"] == "5.2.2 LTS"
    assert data["selected_kind"] == "system"
    assert data["updated_at"].endswith("Z")


@pytest.mark.parametrize("content", ["{not json", "[1, 2]", '{"mode": "turbo", "pinned_executable": 5}', ""])
def test_corrupt_config_yields_defaults(out_dir: Path, content: str) -> None:
    path = out_dir / "rt" / "blender_runtime.json"
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")
    config = RuntimeConfig.load(path)
    assert config.mode == "auto" and config.pinned_executable is None
    manager = manager_for(out_dir, [sys_install(out_dir, "5.2.2 LTS")])
    assert manager.scan().state == "ready"
    assert json.loads(path.read_text(encoding="utf-8"))["mode"] == "auto"  # rewritten cleanly


def test_missing_config_defaults(out_dir: Path) -> None:
    assert RuntimeConfig.load(out_dir / "nope.json") == RuntimeConfig()


# ---------------------------------------------------------------------------------------------- select()
def test_select_validates_and_pins(out_dir: Path) -> None:
    b51 = sys_install(out_dir, "5.1.0")
    b52 = sys_install(out_dir, "5.2.2 LTS")
    old = sys_install(out_dir, "4.2.17 LTS")
    embedded = emb_install(out_dir, "5.2.1")
    manager = manager_for(out_dir, [b51, b52, old, embedded])
    status = manager.select("auto", str(b51.executable))  # auto + explicit exe -> pinned in that kind's mode
    assert status.mode == "system" and status.selected.executable == b51.executable  # type: ignore[union-attr]
    assert manager.config().pinned_executable == str(b51.executable)

    with pytest.raises(BlenderNotFoundError):
        manager.select("system", str(out_dir / "missing" / "blender.exe"))
    with pytest.raises(BlenderIncompatibleError, match="older than 5.1"):
        manager.select("system", str(old.executable))
    with pytest.raises(BlenderIncompatibleError, match="embedded"):
        manager.select("system", str(embedded.executable))

    status = manager.select("embedded")
    assert status.mode == "embedded" and status.selected.executable == embedded.executable  # type: ignore[union-attr]
    status = manager.select("auto")
    assert status.selected.executable == b52.executable  # type: ignore[union-attr]
    saved = RuntimeConfig.load(manager.settings.config_path)
    assert saved.mode == "auto" and saved.pinned_executable is None and saved.selected_version == "5.2.2 LTS"


def test_select_unknown_path_is_probed_with_real_detector(out_dir: Path) -> None:
    exe = out_dir / "portable" / "blender.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(FAKE_EXE)
    junk = out_dir / "junk" / "blender.exe"
    junk.parent.mkdir(parents=True)
    junk.write_bytes(FAKE_EXE)
    settings = settings_for(out_dir)
    runner = FakeRunner({str(exe): "Blender 5.2.0\n"})
    detector = BlenderDetector(settings, runner=runner, pe_reader=fake_pe_reader(), sources=("embedded_dir",))
    manager = BlenderRuntimeManager(settings, detector=detector)
    assert manager.scan().state == "needs_download"
    status = manager.select("system", str(exe.parent))  # a folder is accepted too
    assert status.state == "ready" and status.selected.executable == exe  # type: ignore[union-attr]
    assert manager.require_runtime().executable == exe
    with pytest.raises(BlenderNotFoundError, match="not a working Blender"):
        manager.select("system", str(junk))
    # the pin survives a rescan (passed to the detector as an extra candidate)
    assert manager.scan(force=True).selected.executable == exe  # type: ignore[union-attr]


# ---------------------------------------------------------------------------------------------- state / events
def test_require_runtime_and_status_dict(out_dir: Path) -> None:
    manager = manager_for(out_dir, [sys_install(out_dir, "4.2.17 LTS")])
    with pytest.raises(BlenderUnavailableError) as info:
        manager.require_runtime()
    assert info.value.message == MSG_42 and info.value.code == "blender_unavailable"
    data = manager.status().to_dict()
    json.dumps(data)
    assert data["state"] == "needs_download" and data["min_version"] == "5.1"
    assert data["installations"][0]["selected"] is False
    assert data["installations"][0]["version_tuple"] == [4, 2, 17]

    b52 = sys_install(out_dir, "5.2.2 LTS")
    manager = manager_for(out_dir, [b52])
    assert manager.require_runtime().executable == b52.executable
    data = manager.status().to_dict()
    assert data["selected"]["selected"] is True and data["selected"]["is_lts"] is True
    assert data["installations"][0]["selected"] is True


def test_on_change_called_outside_lock(out_dir: Path) -> None:
    seen: list[RuntimeStatus] = []
    settings = settings_for(out_dir)

    def listener(status: RuntimeStatus) -> None:
        seen.append(status)
        manager.status()  # must not deadlock (called outside the lock, but RLock-safe anyway)

    manager = BlenderRuntimeManager(settings, detector=FakeDetector([sys_install(out_dir, "5.2.2 LTS")]),  # type: ignore[arg-type]
                                    on_change=listener)
    manager.scan()
    assert [s.state for s in seen] == ["detecting", "ready"]


def test_download_disabled(out_dir: Path) -> None:
    manager = manager_for(out_dir, [], allow_download=False)
    status = manager.scan()
    assert status.can_download is False
    assert status.message == "No Blender installation was found. Auto Mesh Tuner requires Blender 5.1 or newer. " \
                             "Install Blender 5.1 or newer and detect again."
    with pytest.raises(DownloadsDisabledError):
        manager.start_download()


def _wait(predicate, timeout: float = 10.0) -> None:  # type: ignore[no-untyped-def]
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition not reached")


def _embedded_manager(out_dir: Path, downloader_factory, installs=(), **kw):  # type: ignore[no-untyped-def]
    settings = settings_for(out_dir, **kw)
    runner = FakeRunner({str(settings.runtime_root / TOP / "blender.exe"): "Blender 5.9.1\n"})
    detector = BlenderDetector(settings, runner=runner, pe_reader=fake_pe_reader(), sources=("embedded_dir",))
    states: list[str] = []
    times: list[float] = []

    def listener(status: RuntimeStatus) -> None:
        states.append(status.state)
        times.append(time.monotonic())

    manager = BlenderRuntimeManager(settings, detector=detector, downloader=downloader_factory(settings),
                                    on_change=listener)
    return manager, states, times


def test_download_flow_selects_new_embedded(out_dir: Path) -> None:
    gate = threading.Event()
    manager, states, _ = _embedded_manager(out_dir, lambda s: FakeDownloader(s, gate=gate))
    assert manager.scan().state == "needs_download"
    status = manager.start_download()
    assert status.state == "downloading" and status.download is not None
    with pytest.raises(DownloadInProgressError):
        manager.start_download()
    with pytest.raises(BlenderUnavailableError, match="being downloaded"):
        manager.require_runtime()
    gate.set()
    _wait(lambda: manager.status().state == "ready")
    status = manager.status()
    assert status.selected is not None and status.selected.kind == "embedded"
    assert status.selected.version.number == "5.9.1"  # type: ignore[union-attr]
    assert status.download is not None and status.download.state == "completed"
    assert "detecting" not in states[states.index("downloading"):]  # rescan while downloading keeps the state
    manager.close()


def test_download_cancel_and_failure(out_dir: Path) -> None:
    gate = threading.Event()
    manager, _, _ = _embedded_manager(out_dir, lambda s: FakeDownloader(s, gate=gate))
    manager.scan()
    manager.start_download()

    def halfway() -> bool:
        download = manager.status().download
        return download is not None and download.bytes_done >= 5 * 1024 * 1024

    _wait(halfway)
    manager.cancel_download()
    _wait(lambda: manager.status().state != "downloading")
    status = manager.status()
    assert status.state == "needs_download" and status.download.state == "cancelled"  # type: ignore[union-attr]
    assert status.error is None

    manager2, _, _ = _embedded_manager(out_dir / "f", lambda s: FakeDownloader(s, fail=DownloadError("boom")))
    manager2.scan()
    manager2.start_download()
    _wait(lambda: manager2.status().state != "downloading")
    status = manager2.status()
    assert status.state == "needs_download" and status.error == "boom"
    assert status.download is not None and status.download.state == "failed"
    assert status.download.error_code == "blender_download_failed"


def test_download_never_overrides_system_pin(out_dir: Path) -> None:
    def factory(settings: LauncherSettings) -> FakeDownloader:
        return FakeDownloader(settings)

    manager, _, _ = _embedded_manager(out_dir, factory)
    pinned = out_dir / "pinned" / "blender.exe"
    config = RuntimeConfig(mode="system", pinned_executable=str(pinned))
    config.save(manager.settings.config_path)
    manager = BlenderRuntimeManager(manager.settings, detector=manager.detector, downloader=manager.downloader)
    manager.scan()
    manager.start_download()
    _wait(lambda: manager.status().state != "downloading")
    assert manager.config().mode == "system" and manager.config().pinned_executable == str(pinned)
    status = manager.status()
    assert status.selected is not None and status.selected.kind == "embedded"  # fallback, with a warning
    assert any("Pinned Blender" in w for w in status.warnings)


def test_download_progress_notifications_throttled(out_dir: Path) -> None:
    window: dict[str, float] = {}

    class SlowDownloader(FakeDownloader):
        def download_and_install(self, release, *, cancel, on_progress):  # type: ignore[no-untyped-def]
            from dataclasses import replace

            from launcher import DownloadProgress
            p = DownloadProgress.idle()
            p.state = "downloading"
            window["start"] = time.monotonic()
            for i in range(200):  # 200 progress callbacks in >= 1 s
                p.bytes_done = i
                on_progress(replace(p))
                time.sleep(0.005)
            window["end"] = time.monotonic()
            return super().download_and_install(release, cancel=cancel, on_progress=on_progress)

    manager, states, times = _embedded_manager(out_dir, lambda s: SlowDownloader(s))
    manager.scan()
    manager.start_download()
    _wait(lambda: manager.status().state == "ready")
    inside = [t for t in times if window["start"] <= t <= window["end"]]
    duration = window["end"] - window["start"]
    assert duration >= 0.9
    assert 2 <= len(inside) <= duration / 0.25 + 2  # <= 4 notifications/s while 200 updates arrive
    assert states[-1] == "ready"
