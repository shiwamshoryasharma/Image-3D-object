"""BlenderProcessRunner with the REAL Blender 5.2: protocol parsing, status mapping, kill-tree, env, logs."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import psutil
import pytest
from lt_helpers import BLENDER_42, BLENDER_52, HERE

from launcher import (
    BlenderProcessRunner,
    BlenderRunRequest,
    PathTooLongError,
    parse_blender_version,
    parse_protocol_line,
    run_capture,
)

pytestmark = pytest.mark.blender

PROBE = HERE / "blender_side" / "amt_probe.py"


def request(out_dir: Path, mode: str, **kw: Any) -> BlenderRunRequest:
    values: dict[str, Any] = {
        "executable": BLENDER_52,
        "script": PROBE,
        "script_args": [mode],
        "timeout_s": 120.0,
        "temp_dir": out_dir / "tmp",
        "user_resources_dir": out_dir / "user",
        "log_path": out_dir / "blender.log",
    }
    values.update(kw)
    return BlenderRunRequest(**values)


class TreeWatcher:
    """on_event hook: when the script reports its spawned child, snapshot blender's whole process tree."""

    def __init__(self, cancel: threading.Event | None = None) -> None:
        self.cancel = cancel
        self.tree: list[tuple[int, str, float]] = []
        self.child_pid: int | None = None
        self.ready = threading.Event()

    def __call__(self, event: dict[str, Any]) -> None:
        if event.get("message") != "spawned":
            return
        self.child_pid = int(event["child_pid"])
        blender = psutil.Process(int(event["pid"]))
        procs = [blender, *blender.children(recursive=True)]
        self.tree = [(p.pid, p.name(), p.create_time()) for p in procs]
        self.ready.set()
        if self.cancel is not None:
            self.cancel.set()

    def survivors(self) -> list[tuple[int, str]]:
        alive = []
        for pid, name, created in self.tree:
            try:
                proc = psutil.Process(pid)
                if abs(proc.create_time() - created) < 0.01 and proc.is_running() and proc.status() != "zombie":
                    alive.append((pid, name))
            except psutil.NoSuchProcess:
                continue
        return alive


def wait_no_survivors(watcher: TreeWatcher, timeout: float = 3.0) -> list[tuple[int, str]]:
    end = time.monotonic() + timeout
    while True:
        alive = watcher.survivors()
        if not alive or time.monotonic() > end:
            return alive
        time.sleep(0.05)


# ---------------------------------------------------------------------------------------------- success path
def test_protocol_events_env_and_log(out_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTHONFOO_AMT", "1")
    monkeypatch.setenv("PYTHONPATH", str(out_dir))
    monkeypatch.setenv("BLENDER_USER_CONFIG", str(out_dir / "should-not-leak"))
    monkeypatch.setenv("BLENDER_SYSTEM_SCRIPTS", str(out_dir / "should-not-leak"))
    outputs: list[tuple[str, str]] = []
    events: list[dict[str, Any]] = []
    req = request(out_dir, "ok", on_output=lambda s, line: outputs.append((s, line)), on_event=events.append)
    result = BlenderProcessRunner().run(req)

    assert result.status == "succeeded", result.errors
    assert result.exit_code == 0 and not result.timed_out and not result.cancelled
    assert result.blender_version == "5.2.2 LTS"
    assert [e["event"] for e in result.events] == ["hello", "step", "log", "step", "result"]
    assert events == result.events  # on_event saw every protocol object
    assert result.result is not None and result.result["status"] == "completed"
    assert result.errors == []
    assert result.command[:9] == [str(BLENDER_52), "--background", "--factory-startup", "--disable-autoexec",
                                  "--offline-mode", "--python-exit-code", "86", "--python", str(PROBE)]
    assert result.command[9:] == ["--", "ok"]

    # glued line: Python text without newline + protocol line still parsed by substring
    glued = [line for _, line in outputs if "noise-without-newline" in line]
    assert glued and "@@AMT@@" in glued[0]
    assert result.events[1]["message"] == "Loading ✓ é 日本"  # non-ASCII round trip
    assert ("stdout", "plain non-ascii stdout: é ✓ 日本 Ω") in outputs
    assert ("stderr", "stderr non-ascii: é ✓ 日本 Ω") in outputs
    assert all("\r" not in line for _, line in outputs)
    # Blender's own C output (banner/"Info: Saved"/"Blender quit") interleaves but does not break parsing
    assert any("Blender quit" in line for _, line in outputs)

    env = result.events[2]
    temp = os.path.normcase(str((out_dir / "tmp").absolute()))
    assert os.path.normcase(env["tempdir"]) == temp
    assert os.path.normcase(env["env_temp"]) == temp and os.path.normcase(env["env_tmp"]) == temp
    assert os.path.normcase(env["bpy_tempdir"]).startswith(temp)  # blender_aNNNN lives in the run temp
    assert os.path.normcase(env["user_resources"]) == os.path.normcase(str((out_dir / "user").absolute()))
    assert env["filtered_env"] == {"BLENDER_USER_RESOURCES": str((out_dir / "user").absolute())}
    assert os.path.normcase(env["cwd"]) == temp  # default cwd = run temp dir
    assert (out_dir / "tmp" / "amt_probe.blend").is_file()

    log = (out_dir / "blender.log").read_text(encoding="utf-8")
    assert log.startswith("# Img3D Blender run")
    assert "@@AMT@@" in log and "plain non-ascii stdout: é ✓ 日本 Ω" in log
    assert "status=succeeded exit_code=0" in log
    assert "@@AMT@@" in result.stdout_tail and "stderr non-ascii" in result.stderr_tail


# ---------------------------------------------------------------------------------------------- failures
def test_sys_exit_3_is_failed(out_dir: Path) -> None:
    result = BlenderProcessRunner().run(request(out_dir, "exit3"))
    assert result.status == "failed" and result.exit_code == 3
    assert result.errors == ["Blender exited with code 3"]


def test_uncaught_exception_is_script_error(out_dir: Path) -> None:
    result = BlenderProcessRunner().run(request(out_dir, "raise"))
    assert result.status == "script_error" and result.exit_code == 86
    traceback = next(e for e in result.errors if e.startswith("Traceback"))
    assert "RuntimeError: boom ✓ é" in traceback
    assert "Error: script failed" in traceback
    assert result.blender_version == "5.2.2 LTS"


def test_error_event_with_exit_0_is_failed(out_dir: Path) -> None:
    result = BlenderProcessRunner().run(request(out_dir, "error_event"))
    assert result.exit_code == 0 and result.status == "failed"
    assert result.errors == ["Could not read input"]


def test_launch_failed_for_missing_executable(out_dir: Path) -> None:
    result = BlenderProcessRunner().run(request(out_dir, "ok", executable=out_dir / "missing" / "blender.exe"))
    assert result.status == "launch_failed" and result.exit_code is None
    assert result.errors and "Could not start Blender" in result.errors[0]


def test_path_too_long_rejected_before_launch(out_dir: Path) -> None:
    long_temp = out_dir / ("t" * 120) / ("u" * 120)
    with pytest.raises(PathTooLongError) as info:
        BlenderProcessRunner().run(request(out_dir, "ok", temp_dir=long_temp))
    assert info.value.code == "blender_path_too_long" and info.value.details["argument"] == "temp_dir"
    assert not long_temp.exists()
    with pytest.raises(PathTooLongError, match=r"script_args\[1\]"):
        BlenderProcessRunner().run(request(out_dir, "ok", script_args=["ok", f"--input={long_temp / 'in.glb'}"]))
    assert not (out_dir / "blender.log").exists()


# ---------------------------------------------------------------------------------------------- kill tree
def test_timeout_kills_whole_tree(out_dir: Path) -> None:
    watcher = TreeWatcher()
    started = time.monotonic()
    result = BlenderProcessRunner().run(request(out_dir, "spawn", timeout_s=8.0, on_event=watcher))
    elapsed = time.monotonic() - started
    assert watcher.ready.is_set(), result.stdout_tail
    names = {name.lower() for _, name, _ in watcher.tree}
    assert {"blender.exe", "conhost.exe", "python.exe"} <= names
    assert result.status == "timed_out" and result.timed_out and not result.cancelled
    assert result.exit_code == 0xC000013A
    assert 8.0 <= elapsed < 20.0
    assert any("did not finish within 8 s" in e for e in result.errors)
    assert wait_no_survivors(watcher) == []


def test_cancel_event_kills_whole_tree(out_dir: Path) -> None:
    cancel = threading.Event()
    watcher = TreeWatcher(cancel)
    started = time.monotonic()
    result = BlenderProcessRunner().run(request(out_dir, "spawn", timeout_s=120.0, on_event=watcher,
                                                cancel_event=cancel))
    assert watcher.ready.is_set()
    assert result.status == "cancelled" and result.cancelled and not result.timed_out
    assert result.exit_code == 0xC000013A
    assert time.monotonic() - started < 30.0
    assert wait_no_survivors(watcher) == []
    assert "status=cancelled" in (out_dir / "blender.log").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------------------------- run_capture / protocol
def test_run_capture_version_probe() -> None:
    for exe, expected in ((BLENDER_52, "5.2.2 LTS"), (BLENDER_42, "4.2.17 LTS")):
        if not exe.is_file():
            continue
        cap = run_capture([str(exe), "--factory-startup", "--version"], timeout_s=30, cwd=exe.parent)
        assert cap.exit_code == 0 and not cap.timed_out
        version = parse_blender_version(cap.stdout + "\n" + cap.stderr)
        assert version is not None and version.label == expected


def test_run_capture_timeout() -> None:
    cap = run_capture([sys.executable, "-c", "import time; time.sleep(30)"], timeout_s=0.5)
    assert cap.timed_out and cap.exit_code is None and cap.duration_s < 10
    with pytest.raises(OSError):
        run_capture([str(HERE / "no-such.exe")], timeout_s=1)


def test_parse_protocol_line_variants() -> None:
    assert parse_protocol_line('@@AMT@@{"event":"hello","protocol":1}') == {"event": "hello", "protocol": 1}
    assert parse_protocol_line('Info: Saved "x.blend"@@AMT@@{"event":"step","id":"load"}  trailing') == {
        "event": "step", "id": "load"}
    assert parse_protocol_line("no sentinel here") is None
    assert parse_protocol_line("@@AMT@@{broken json") is None
    assert parse_protocol_line('@@AMT@@{"no_event":1}') is None
    assert parse_protocol_line("@@AMT@@[1,2]") is None


def test_build_env_strips_python_and_blender_vars(out_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTHONHOME", "x")
    monkeypatch.setenv("BLENDER_USER_SCRIPTS", "y")
    req = request(out_dir, "ok", extra_env={"AMT_EXTRA": "1"})
    env = BlenderProcessRunner.build_env(req)
    assert not [k for k in env if k.upper().startswith("PYTHON")]
    assert [k for k in env if k.upper().startswith("BLENDER_")] == ["BLENDER_USER_RESOURCES"]
    assert env["TEMP"] == env["TMP"] == str((out_dir / "tmp").absolute()) and env["AMT_EXTRA"] == "1"
    assert subprocess.list2cmdline(BlenderProcessRunner.build_command(req)).count("--python-exit-code 86") == 1
