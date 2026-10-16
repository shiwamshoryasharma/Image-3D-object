"""Blender runtime API (DESIGN.md section 6): status/detect/selection, release, download, ``blender.updated``.

Uses the real :class:`launcher.BlenderRuntimeManager` (selection policy, messages, persisted config, download
thread) with a fake detector/downloader (``fakes_blender.FakeBlenderEnv``); nothing executes a real Blender.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fakes_blender import PORTABLE_VERSION, FakeBlenderEnv, TunerHarness, start_tuner_app
from helpers import assert_error, wait_for, ws_collect_until, ws_receive

from config.settings import Settings
from launcher import (
    BlenderIncompatibleError,
    BlenderNotFoundError,
    BlenderUnavailableError,
    ChecksumMismatchError,
    DownloadInProgressError,
    DownloadsDisabledError,
    LauncherError,
    PathTooLongError,
    ReleaseResolveError,
)
from services.blender_runtime_service import map_launcher_error

NEEDS_DOWNLOAD_42 = (
    "Blender 4.2.17 is installed but Auto Mesh Tuner requires Blender 5.1 or newer. "
    "Download a supported portable Blender?"
)
RUNTIME_KEYS = {
    "state", "mode", "min_version", "selected", "selection_reason", "installations", "message", "warnings",
    "runtime_root", "can_download", "download", "release", "last_scan_at", "scan_duration_ms", "error",
}
INSTALL_KEYS = {
    "id", "executable", "kind", "origins", "version", "version_label", "version_tuple", "cycle", "is_lts",
    "is_stable", "architecture", "valid", "compatible", "incompatible_reason", "error", "selected",
}


@pytest.fixture
def make_runtime_app(make_settings: Callable[..., Settings], runtime_dir: Path) -> Iterator[Callable[..., TunerHarness]]:
    stack = contextlib.ExitStack()

    def factory(*, versions: dict[str, str] | None = None, **overrides: object) -> TunerHarness:
        return start_tuner_app(stack, make_settings(**overrides), runtime_dir, versions=versions)

    with stack:
        yield factory


def _status(h: TunerHarness) -> dict:
    response = h.client.get("/api/v1/blender/runtime")
    assert response.status_code == 200, response.text
    return response.json()


def _wait_state(h: TunerHarness, predicate: Callable[[dict], bool], timeout: float = 10.0) -> dict:
    def check() -> dict | None:
        status = _status(h)
        return status if predicate(status) else None

    return wait_for(check, timeout=timeout, message="runtime state")


def _detect(h: TunerHarness) -> dict:
    response = h.client.post("/api/v1/blender/runtime/detect")
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------------------------------
# status / detect / selection
# --------------------------------------------------------------------------------------------------


def test_status_detects_lazily_and_reports_installations(make_runtime_app):
    h = make_runtime_app()
    first = _status(h)
    assert set(first) == RUNTIME_KEYS
    assert first["min_version"] == "5.1"
    status = _wait_state(h, lambda s: s["state"] == "ready")
    assert status["mode"] == "auto"
    assert status["selection_reason"] == "Highest compatible system Blender"
    selected = status["selected"]
    assert set(selected) >= INSTALL_KEYS
    assert selected["version_label"] == "5.2.2 LTS" and selected["kind"] == "system" and selected["selected"]
    by_version = {i["version"]: i for i in status["installations"]}
    assert set(by_version) == {"5.2.2", "4.2.17"}
    old = by_version["4.2.17"]
    assert not old["compatible"] and not old["selected"]
    assert old["incompatible_reason"] == "Blender 4.2.17 is older than 5.1"
    assert status["message"] is None and status["can_download"] is True
    assert status["runtime_root"] == str(h.settings.blender_runtime_dir)

    calls = h.env.detector.detect_calls
    detected = _detect(h)
    assert detected["state"] == "ready" and detected["last_scan_at"]
    assert h.env.detector.detect_calls == calls + 1


def test_detection_on_startup_runs_in_background(make_runtime_app):
    h = make_runtime_app(blender_detect_on_startup=True)
    wait_for(lambda: h.env.detector.detect_calls >= 1, timeout=10, message="startup detection")
    status = _wait_state(h, lambda s: s["state"] == "ready")
    assert status["selected"]["version"] == "5.2.2"


def test_needs_download_message_is_exact(make_runtime_app):
    h = make_runtime_app(versions={"4.2.17 LTS": "system"})
    status = _detect(h)
    assert status["state"] == "needs_download"
    assert status["selected"] is None
    assert status["message"] == NEEDS_DOWNLOAD_42


def test_no_blender_at_all_message(make_runtime_app):
    h = make_runtime_app(versions={})
    status = _detect(h)
    assert status["state"] == "needs_download"
    assert status["message"] == (
        "No Blender installation was found. Auto Mesh Tuner requires Blender 5.1 or newer. "
        "Download a supported portable Blender?"
    )


def test_selection_pin_and_errors(make_runtime_app, runtime_dir):
    h = make_runtime_app()
    _detect(h)
    exe52 = str(h.env.exe("5.2"))
    response = h.client.put("/api/v1/blender/runtime/selection", json={"mode": "system", "executable": exe52})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mode"] == "system" and body["selection_reason"] == "Pinned system Blender"
    config = json.loads((h.settings.blender_runtime_dir / "blender_runtime.json").read_text("utf-8"))
    assert config["mode"] == "system" and Path(config["pinned_executable"]) == Path(exe52)

    missing = str(runtime_dir / "nope" / "blender.exe")
    error = assert_error(
        h.client.put("/api/v1/blender/runtime/selection", json={"mode": "system", "executable": missing}),
        404, "blender_not_found",
    )
    assert "not found" in error["message"]
    error = assert_error(
        h.client.put("/api/v1/blender/runtime/selection", json={"mode": "auto", "executable": str(h.env.exe("4.2"))}),
        422, "blender_incompatible",
    )
    assert "5.1" in error["message"]
    assert_error(h.client.put("/api/v1/blender/runtime/selection", json={"mode": "bogus"}), 422, "validation_error")
    assert_error(
        h.client.put("/api/v1/blender/runtime/selection", json={"mode": "auto", "extra": 1}), 422, "validation_error"
    )

    embedded = h.client.put("/api/v1/blender/runtime/selection", json={"mode": "embedded"}).json()
    assert embedded["mode"] == "embedded" and embedded["state"] == "ready"
    assert embedded["selected"]["kind"] == "system"
    assert "Embedded Blender is selected but not installed — using system Blender 5.2.2." in embedded["warnings"]
    auto = h.client.put("/api/v1/blender/runtime/selection", json={"mode": "auto"}).json()
    assert auto["mode"] == "auto" and auto["warnings"] == []


def test_blender_disabled(make_runtime_app):
    h = make_runtime_app(blender_enabled=False)
    status = _status(h)
    assert status["state"] == "error" and status["can_download"] is False
    assert "disabled" in status["message"]
    assert_error(h.client.post("/api/v1/blender/runtime/detect"), 503, "blender_unavailable")
    assert_error(
        h.client.post("/api/v1/blender/runtime/download", json={"confirm": True}), 503, "blender_unavailable"
    )
    assert h.env.detector.detect_calls == 0


# --------------------------------------------------------------------------------------------------
# release / download
# --------------------------------------------------------------------------------------------------


def test_release_endpoint(make_runtime_app):
    h = make_runtime_app()
    response = h.client.post("/api/v1/blender/runtime/release")
    assert response.status_code == 200, response.text
    release = response.json()
    assert release["version"] == PORTABLE_VERSION
    assert release["host"] == "download.blender.org"
    assert release["file_name"] == f"blender-{PORTABLE_VERSION}-windows-x64.zip"
    assert release["sha256"] == "ab" * 32 and release["size_bytes"] == 10 * 1024 * 1024
    assert _status(h)["release"]["version"] == PORTABLE_VERSION

    h.env.downloader.resolve_error = "download.blender.org is unreachable"
    error = assert_error(h.client.post("/api/v1/blender/runtime/release"), 502, "blender_release_unresolved")
    assert "unreachable" in error["message"]


def test_download_requires_confirmation(make_runtime_app):
    h = make_runtime_app(versions={"4.2.17 LTS": "system"})
    _detect(h)
    assert_error(h.client.post("/api/v1/blender/runtime/download", json={"confirm": False}), 422,
                 "confirmation_required")
    assert_error(h.client.post("/api/v1/blender/runtime/download", json={}), 422, "validation_error")
    assert_error(h.client.post("/api/v1/blender/runtime/download", json={"confirm": True, "version": "latest"}), 422,
                 "validation_error")
    assert h.env.downloader.calls == 0


def test_download_installs_and_selects_embedded_build(make_runtime_app):
    h = make_runtime_app(versions={"4.2.17 LTS": "system"})
    _detect(h)
    h.env.downloader.gate.clear()
    response = h.client.post("/api/v1/blender/runtime/download", json={"confirm": True})
    assert response.status_code == 202, response.text
    assert response.json()["state"] == "downloading"
    wait_for(h.env.downloader.started.is_set, timeout=5)
    assert_error(h.client.post("/api/v1/blender/runtime/download", json={"confirm": True}), 409,
                 "blender_download_in_progress")
    h.env.downloader.gate.set()
    status = _wait_state(h, lambda s: s["state"] == "ready" and s["selected"] is not None)
    assert status["download"]["state"] == "completed"
    assert status["selected"]["kind"] == "embedded"
    assert status["selected"]["version"] == PORTABLE_VERSION
    assert status["selection_reason"] == "Highest compatible embedded Blender"
    assert status["message"] is None


def test_download_cancel(make_runtime_app):
    h = make_runtime_app(versions={"4.2.17 LTS": "system"})
    _detect(h)
    h.env.downloader.gate.clear()
    assert h.client.post("/api/v1/blender/runtime/download", json={"confirm": True}).status_code == 202
    wait_for(h.env.downloader.started.is_set, timeout=5)
    response = h.client.delete("/api/v1/blender/runtime/download")
    assert response.status_code == 202, response.text
    status = _wait_state(h, lambda s: (s["download"] or {}).get("state") == "cancelled")
    assert status["state"] == "needs_download"
    assert status["download"]["error_code"] == "blender_download_cancelled"
    assert status["message"] == NEEDS_DOWNLOAD_42
    # a cancel without a running download is a no-op
    assert h.client.delete("/api/v1/blender/runtime/download").status_code == 202


def test_download_disabled(make_runtime_app):
    h = make_runtime_app(versions={"4.2.17 LTS": "system"}, blender_allow_download=False)
    status = _detect(h)
    assert status["can_download"] is False
    assert status["message"].endswith("Install Blender 5.1 or newer and detect again.")
    assert_error(h.client.post("/api/v1/blender/runtime/download", json={"confirm": True}), 403,
                 "blender_download_disabled")


# --------------------------------------------------------------------------------------------------
# WebSocket
# --------------------------------------------------------------------------------------------------


def test_blender_updated_websocket_events(make_runtime_app):
    h = make_runtime_app(versions={"4.2.17 LTS": "system"})
    with h.client.websocket_connect("/api/v1/ws?job_id=job_aaaaaaaaaaaa") as ws:  # not job-scoped: still delivered
        assert ws_receive(ws)["type"] == "hello"
        _detect(h)
        messages = ws_collect_until(
            ws, lambda m: m["type"] == "blender.updated" and m["data"]["state"] == "needs_download"
        )
        update = messages[-1]
        assert update["ts"].endswith("Z")
        assert set(update["data"]) == RUNTIME_KEYS
        assert update["data"]["message"] == NEEDS_DOWNLOAD_42

        assert h.client.post("/api/v1/blender/runtime/download", json={"confirm": True}).status_code == 202
        messages = ws_collect_until(
            ws, lambda m: m["type"] == "blender.updated" and m["data"]["state"] == "ready", timeout=15
        )
    states = [m["data"]["state"] for m in messages if m["type"] == "blender.updated"]
    assert "downloading" in states
    downloads = [m["data"]["download"]["state"] for m in messages
                 if m["type"] == "blender.updated" and m["data"]["download"]]
    assert "completed" in downloads
    assert messages[-1]["data"]["selected"]["kind"] == "embedded"


# --------------------------------------------------------------------------------------------------
# unit: error mapping + settings
# --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (BlenderNotFoundError("x"), 404, "blender_not_found"),
        (BlenderIncompatibleError("x"), 422, "blender_incompatible"),
        (BlenderUnavailableError("x"), 503, "blender_unavailable"),
        (DownloadsDisabledError("x"), 403, "blender_download_disabled"),
        (DownloadInProgressError("x"), 409, "blender_download_in_progress"),
        (ReleaseResolveError("x"), 502, "blender_release_unresolved"),
        (ChecksumMismatchError("x"), 502, "blender_checksum_mismatch"),
        (PathTooLongError("x"), 422, "blender_path_too_long"),
        (LauncherError("x", code="blender_invalid_mode"), 422, "blender_invalid_mode"),
    ],
)
def test_launcher_error_mapping(error, status, code):
    mapped = map_launcher_error(error)
    assert (mapped.status_code, mapped.code, mapped.message) == (status, code, "x")


def test_blender_settings(make_settings, runtime_dir):
    s = make_settings()
    assert s.blender_enabled and s.blender_allow_download
    assert s.blender_min_version == "5.1" and s.blender_tune_timeout_s == 1800 and s.blender_max_queue_size == 4
    assert s.blender_runtime_dir == runtime_dir.resolve() / "blender"
    assert s.blender_temp_dir == runtime_dir.resolve() / "temp" / "blender"
    assert s.blender_executable is None and s.blender_search_dirs == []
    s = make_settings(
        blender_search_dirs="D:/Tools/Blender;rel/dir", blender_executable="", blender_install_dir="portable"
    )
    assert s.blender_search_dirs[0] == Path("D:/Tools/Blender").resolve()
    assert s.blender_search_dirs[1] == (s.backend_dir / "rel" / "dir").resolve()
    assert s.blender_executable is None
    assert s.blender_runtime_dir == (s.backend_dir / "portable").resolve()
    s = make_settings(blender_search_dirs='["C:/a", "C:/b"]', blender_download_base_url="https://example.org/r")
    assert [p.name for p in s.blender_search_dirs] == ["a", "b"]
    assert s.blender_download_base_url == "https://example.org/r/"
    for bad in ({"blender_min_version": "five"}, {"blender_download_base_url": "http://x/"},
                {"blender_max_queue_size": 0}, {"blender_download_channel": "nightly"}):
        with pytest.raises(ValueError):
            make_settings(**bad)
    summary = make_settings().safe_summary()
    assert summary["blender_min_version"] == "5.1" and "blender_runtime_dir" in summary


def test_fake_env_paths_are_isolated(runtime_dir):
    env = FakeBlenderEnv(runtime_dir / "fake")
    assert env.exe("5.2").is_file() and runtime_dir in env.exe("5.2").parents


# --------------------------------------------------------------------------------------------------
# standalone CLI (backend/scripts/auto_mesh_tuner.py) with the fake runtime + runner
# --------------------------------------------------------------------------------------------------


def _load_cli():
    import importlib.util

    path = Path(__file__).resolve().parents[2] / "backend" / "scripts" / "auto_mesh_tuner.py"
    spec = importlib.util.spec_from_file_location("img3d_amt_cli_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_tune_prompts_downloads_and_runs(monkeypatch, make_settings, runtime_dir, capsys):
    from fakes import write_test_glb
    from fakes_blender import FakeTunerRunner

    cli = _load_cli()
    env = FakeBlenderEnv(runtime_dir / "fake", versions={"4.2.17 LTS": "system"})
    settings = make_settings()
    runner = FakeTunerRunner()
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "BlenderRuntimeManager", lambda ls, on_change: env.factory(ls, on_change))
    monkeypatch.setattr(cli, "BlenderProcessRunner", lambda: runner)
    source = write_test_glb(runtime_dir / "inputs" / "cube.glb")
    out = runtime_dir / "cli_out"

    # no interactive input -> the exact backend prompt is shown and answered "N": nothing is downloaded
    assert cli.main(["tune", str(source), "-o", str(out)]) == cli.EXIT_NO_RUNTIME
    text = capsys.readouterr().out
    assert f"{NEEDS_DOWNLOAD_42} [y/N]" in text and "Download declined." in text
    assert env.downloader.calls == 0 and runner.requests == []

    assert cli.main(["tune", str(source), "-o", str(out), "--yes", "--preset", "conservative",
                     "--options", '{"smoothing": 0.1}']) == cli.EXIT_OK
    text = capsys.readouterr().out
    assert f"{NEEDS_DOWNLOAD_42} [y/N] y (--yes)" in text
    assert f"Installed Blender {PORTABLE_VERSION}" in text and "checksum verified" in text
    assert f"Using Blender {PORTABLE_VERSION} [embedded]" in text
    assert "Result: succeeded (exit code 0)" in text and "Holes" in text
    assert env.downloader.calls == 1 and len(runner.requests) == 1
    cfg = json.loads((out / "config.json").read_text("utf-8"))
    assert cfg["input"] == str(source.resolve()) and cfg["options"]["preset"] == "conservative"
    assert cfg["options"]["smoothing"] == 0.1 and cfg["options"]["max_hole_edges"] == 120
    assert (out / "tuned.glb").is_file() and (out / "report.json").is_file()
    assert not any(settings.blender_temp_dir.glob("cli-*"))  # per-run TEMP removed

    assert cli.main(["tune", str(source), "--options", '{"smoothing": 5}']) == cli.EXIT_USAGE
    assert "invalid tuning options" in capsys.readouterr().out
    assert cli.main(["detect", "--json"]) == cli.EXIT_OK
    status = json.loads(capsys.readouterr().out)
    assert status["state"] == "ready" and status["selected"]["kind"] == "embedded"
