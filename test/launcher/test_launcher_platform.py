"""platform_win helpers: native architecture, PE header, long paths, registry readers, Job Object kill-tree."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

from launcher import KillOnCloseJob, long_paths_enabled, native_architecture, pe_info
from launcher.platform_win import (
    CREATE_NO_WINDOW,
    CREATE_SUSPENDED,
    HKEY_LOCAL_MACHINE,
    STATUS_CONTROL_C_EXIT,
    can_run_architecture,
    extended_path,
    is_app_execution_alias,
    path_is_within,
    reg_get_value,
    reg_subkeys,
    reg_value_names,
    resume_process,
)

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only helpers")


def test_native_architecture_and_pe_info(out_dir: Path) -> None:
    assert native_architecture() in ("x64", "arm64")
    assert pe_info(sys.executable) == ("x64", "console")
    notepad = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "notepad.exe"
    if notepad.is_file():
        assert pe_info(notepad)[1] == "gui"
    junk = out_dir / "junk.exe"
    junk.write_bytes(b"MZ" + b"\0" * 10)
    with pytest.raises(ValueError):
        pe_info(junk)
    text = out_dir / "text.exe"
    text.write_text("hello")
    with pytest.raises(ValueError):
        pe_info(text)


def test_can_run_architecture() -> None:
    assert can_run_architecture("x64", "x64") == (True, False)
    assert can_run_architecture("x64", "arm64") == (True, True)
    assert can_run_architecture("arm64", "x64") == (False, False)
    assert can_run_architecture("x86", "x64") == (False, False)
    assert can_run_architecture(None, "x64") == (False, False)


def test_long_paths_and_extended_path(out_dir: Path) -> None:
    assert isinstance(long_paths_enabled(), bool)
    assert extended_path(out_dir).startswith("\\\\?\\") and extended_path(r"\\server\share\x") == (
        "\\\\?\\UNC\\server\\share\\x")
    assert path_is_within(out_dir / "a" / ".." / "b", out_dir)
    assert not path_is_within(out_dir.parent, out_dir)


def test_registry_readers_are_graceful() -> None:
    assert reg_get_value(HKEY_LOCAL_MACHINE, r"SOFTWARE\Img3D\Does\Not\Exist", "x") is None
    assert reg_subkeys(HKEY_LOCAL_MACHINE, r"SOFTWARE\Img3D\Does\Not\Exist") == []
    assert reg_value_names(HKEY_LOCAL_MACHINE, r"SOFTWARE\Img3D\Does\Not\Exist") == []
    assert reg_get_value(HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion", "CurrentBuild")
    assert reg_subkeys(HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall")


def test_store_alias_detection() -> None:
    alias = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WindowsApps" / "winget.exe"
    if not os.path.lexists(alias):
        pytest.skip("no winget execution alias on this machine")
    assert is_app_execution_alias(alias)
    assert not is_app_execution_alias(sys.executable)


def test_job_object_kills_tree() -> None:
    script = (
        "import subprocess, sys, time; "
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
        "print(p.pid, flush=True); time.sleep(60)"
    )
    with KillOnCloseJob() as job:
        proc = subprocess.Popen([sys.executable, "-s", "-c", script], stdout=subprocess.PIPE,
                                stdin=subprocess.DEVNULL, creationflags=CREATE_SUSPENDED | CREATE_NO_WINDOW)
        job.assign(proc.pid)
        resume_process(proc.pid)
        child_pid = int(proc.stdout.readline())  # type: ignore[union-attr]
        assert job.active_processes() >= 2
        child = psutil.Process(child_pid)
        assert job.terminate(STATUS_CONTROL_C_EXIT)
        assert proc.wait(10) == STATUS_CONTROL_C_EXIT
        child.wait(10)
        assert not child.is_running()
        assert job.active_processes() == 0
        proc.stdout.close()  # type: ignore[union-attr]


def test_job_close_kills_survivors() -> None:
    job = KillOnCloseJob()
    proc = subprocess.Popen([sys.executable, "-s", "-c", "import time; time.sleep(60)"],
                            stdin=subprocess.DEVNULL, creationflags=CREATE_SUSPENDED | CREATE_NO_WINDOW)
    job.assign(proc.pid)
    resume_process(proc.pid)
    time.sleep(0.2)
    job.close()
    assert proc.wait(10) is not None
    job.close()  # idempotent
    assert not job.available
