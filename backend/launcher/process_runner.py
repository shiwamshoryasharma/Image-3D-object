"""Run Blender in background mode with a hard kill-tree, streamed output and the ``@@AMT@@`` JSON-line protocol.

Windows: the child is started ``CREATE_SUSPENDED | CREATE_NO_WINDOW``, assigned to a kill-on-close Job Object and
only then resumed, so no grandchild can escape the job. Cancel/timeout call ``TerminateJobObject`` (exit code
``0xC000013A``) which takes blender.exe, its conhost.exe and every grandchild down within ~20 ms. The job handle is
always closed (also kills survivors after a normal exit). Elsewhere a new session + ``killpg`` is used.

Blender's own C output is block-buffered and interleaves out of order with Python prints, so protocol lines are
located by substring (``line.find(PROTOCOL_SENTINEL)``) and decoded with ``raw_decode`` (tolerates glued prefixes
and trailing garbage).
"""

from __future__ import annotations

import json
import logging
import os
import re
import signal
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any, Final, Literal, Protocol

from .config import utcnow_iso
from .errors import PathTooLongError
from .platform_win import (
    CREATE_NO_WINDOW,
    CREATE_SUSPENDED,
    IS_WINDOWS,
    MAX_PATH_CHARS,
    STATUS_CONTROL_C_EXIT,
    KillOnCloseJob,
    resume_process,
)

logger = logging.getLogger(__name__)

RunStatus = Literal["succeeded", "failed", "script_error", "cancelled", "timed_out", "crashed", "launch_failed"]

PROTOCOL_SENTINEL: Final = "@@AMT@@"
SCRIPT_EXCEPTION_EXIT_CODE: Final = 86
SCRIPT_FAILURE_EXIT_CODES: Final = frozenset({2, 3, 4})
TAIL_LINES: Final = 200
_TRACEBACK_MAX_LINES: Final = 60
_READER_JOIN_TIMEOUT_S: Final = 10.0
_KILL_WAIT_S: Final = 15.0
_DECODER: Final = json.JSONDecoder()
_ABS_PATH_RE: Final = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")


# ---------------------------------------------------------------------------------------------- short probes
@dataclass(slots=True)
class CaptureResult:
    exit_code: int | None
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool


class ProcessCapture(Protocol):
    """Signature of :func:`run_capture` (injectable into the detector/downloader for tests)."""

    def __call__(self, cmd: list[str], *, timeout_s: float, cwd: Path | None = None) -> CaptureResult: ...


def run_capture(cmd: list[str], *, timeout_s: float, cwd: Path | None = None) -> CaptureResult:
    """Run a short command (e.g. ``blender --factory-startup --version``) and capture its output.

    Hidden console, stdin closed, UTF-8 decoding with replacement. Raises ``OSError`` if it cannot be started; a
    timeout kills the process and returns ``timed_out=True`` with ``exit_code=None``.
    """
    start = time.perf_counter()
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "cwd": str(cwd) if cwd else None,
    }
    if IS_WINDOWS:
        kwargs["creationflags"] = CREATE_NO_WINDOW
    proc = subprocess.Popen([str(part) for part in cmd], **kwargs)
    timed_out = False
    try:
        out, err = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        proc.kill()
        try:
            out, err = proc.communicate(timeout=5.0)
        except subprocess.TimeoutExpired:  # a grandchild keeps the pipes open
            out, err = b"", b""
    return CaptureResult(
        exit_code=None if timed_out else proc.returncode,
        stdout=(out or b"").decode("utf-8", "replace"),
        stderr=(err or b"").decode("utf-8", "replace"),
        duration_s=round(time.perf_counter() - start, 4),
        timed_out=timed_out,
    )


# ---------------------------------------------------------------------------------------------- protocol
def parse_protocol_line(line: str) -> dict[str, Any] | None:
    """Decode a ``@@AMT@@{json}`` protocol event found anywhere in ``line`` (``None`` if absent/invalid)."""
    index = line.find(PROTOCOL_SENTINEL)
    if index < 0:
        return None
    payload = line[index + len(PROTOCOL_SENTINEL) :].lstrip()
    try:
        obj, _ = _DECODER.raw_decode(payload)
    except ValueError:
        logger.debug("Ignoring malformed protocol line: %.200s", line)
        return None
    if not isinstance(obj, dict) or not isinstance(obj.get("event"), str):
        return None
    return obj


# ---------------------------------------------------------------------------------------------- request / result
@dataclass
class BlenderRunRequest:
    executable: Path
    script: Path
    script_args: list[str]
    timeout_s: float
    temp_dir: Path
    user_resources_dir: Path | None = None
    log_path: Path | None = None
    cwd: Path | None = None
    cancel_event: threading.Event | None = None
    on_event: Callable[[dict[str, Any]], None] | None = None
    on_output: Callable[[str, str], None] | None = None
    extra_env: dict[str, str] | None = None
    factory_startup: bool = True


@dataclass
class BlenderRunResult:
    status: RunStatus
    exit_code: int | None
    duration_s: float
    command: list[str]
    events: list[dict[str, Any]]
    result: dict[str, Any] | None
    errors: list[str]
    stdout_tail: str
    stderr_tail: str
    blender_version: str | None
    log_path: Path | None
    timed_out: bool
    cancelled: bool
    pid: int | None = None

    @property
    def ok(self) -> bool:
        return self.status == "succeeded"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "exit_code": self.exit_code,
            "duration_s": self.duration_s,
            "command": list(self.command),
            "events": list(self.events),
            "result": self.result,
            "errors": list(self.errors),
            "stdout_tail": self.stdout_tail,
            "stderr_tail": self.stderr_tail,
            "blender_version": self.blender_version,
            "log_path": str(self.log_path) if self.log_path else None,
            "timed_out": self.timed_out,
            "cancelled": self.cancelled,
            "pid": self.pid,
        }


@dataclass
class _Collector:
    """Thread-safe sink for both output streams (tails, log file, protocol events, callbacks)."""

    request: BlenderRunRequest
    log: IO[str] | None
    lock: threading.Lock = field(default_factory=threading.Lock)
    events: list[dict[str, Any]] = field(default_factory=list)
    tails: dict[str, deque[str]] = field(
        default_factory=lambda: {"stdout": deque(maxlen=TAIL_LINES), "stderr": deque(maxlen=TAIL_LINES)}
    )
    callback_failures: int = 0

    def handle(self, stream: str, line: str) -> None:
        with self.lock:
            self.tails[stream].append(line)
            if self.log is not None:
                try:
                    self.log.write(line + "\n")
                except (OSError, ValueError):  # log closed / disk full: keep running
                    self.log = None
        event = parse_protocol_line(line)
        if event is not None:
            with self.lock:
                self.events.append(event)
        self._callback(self.request.on_output, stream, line)
        if event is not None:
            self._callback(self.request.on_event, event)

    def _callback(self, fn: Callable[..., None] | None, *args: Any) -> None:
        if fn is None:
            return
        try:
            fn(*args)
        except Exception:  # noqa: BLE001 - a broken callback must never kill the reader thread
            self.callback_failures += 1
            if self.callback_failures <= 3:
                logger.exception("Blender run output callback failed")

    def write_log(self, text: str) -> None:
        with self.lock:
            if self.log is not None:
                try:
                    self.log.write(text)
                except (OSError, ValueError):
                    self.log = None


def _reader(pipe: IO[bytes], stream: str, collector: _Collector) -> None:
    try:
        for raw in iter(pipe.readline, b""):
            collector.handle(stream, raw.decode("utf-8", "replace").rstrip("\r\n").replace("\r", ""))
    except (OSError, ValueError):  # pipe closed underneath us
        pass
    finally:
        try:
            pipe.close()
        except OSError:  # pragma: no cover
            pass


# ---------------------------------------------------------------------------------------------- runner
class BlenderProcessRunner:
    """Blocking Blender runner. Never raises for process failures (only :class:`PathTooLongError` up front)."""

    def __init__(self, *, use_job_object: bool = True, poll_interval_s: float = 0.1) -> None:
        self._use_job_object = use_job_object and IS_WINDOWS
        self._poll_interval_s = poll_interval_s

    # ------------------------------------------------------------------ building blocks
    @staticmethod
    def build_command(req: BlenderRunRequest) -> list[str]:
        cmd = [str(req.executable), "--background"]
        if req.factory_startup:
            cmd.append("--factory-startup")
        cmd += [
            "--disable-autoexec",
            "--offline-mode",
            "--python-exit-code",
            str(SCRIPT_EXCEPTION_EXIT_CODE),
            "--python",
            str(req.script),
            "--",
            *[str(arg) for arg in req.script_args],
        ]
        return cmd

    @staticmethod
    def build_env(req: BlenderRunRequest) -> dict[str, str]:
        """Copy of ``os.environ`` without ``PYTHON*`` / ``BLENDER_*``; ``TEMP``/``TMP`` = run temp dir."""
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.upper().startswith("PYTHON") and not key.upper().startswith("BLENDER_")
        }
        temp = str(Path(req.temp_dir).absolute())
        env["TEMP"] = temp
        env["TMP"] = temp
        if not IS_WINDOWS:  # pragma: no cover
            env["TMPDIR"] = temp
        if req.user_resources_dir is not None:
            env["BLENDER_USER_RESOURCES"] = str(Path(req.user_resources_dir).absolute())
        if req.extra_env:
            env.update({str(k): str(v) for k, v in req.extra_env.items()})
        return env

    @staticmethod
    def validate_paths(req: BlenderRunRequest) -> None:
        """Raise :class:`PathTooLongError` if any path handed to Blender exceeds MAX_PATH (259 chars)."""
        named: list[tuple[str, str]] = [
            ("executable", str(Path(req.executable).absolute())),
            ("script", str(Path(req.script).absolute())),
            ("temp_dir", str(Path(req.temp_dir).absolute())),
        ]
        if req.user_resources_dir is not None:
            named.append(("user_resources_dir", str(Path(req.user_resources_dir).absolute())))
        if req.cwd is not None:
            named.append(("cwd", str(Path(req.cwd).absolute())))
        for index, arg in enumerate(req.script_args):
            text = str(arg)
            value = text.split("=", 1)[1] if text.startswith("-") and "=" in text else text
            if _ABS_PATH_RE.match(value) or (not IS_WINDOWS and value.startswith("/")):
                named.append((f"script_args[{index}]", value))
        for name, value in named:
            if len(value) > MAX_PATH_CHARS:
                raise PathTooLongError(
                    f"Path too long for Blender ({len(value)} > {MAX_PATH_CHARS} characters): {name}. "
                    "Blender is not long-path aware; move the project or runtime folder to a shorter path.",
                    details={"argument": name, "length": len(value), "limit": MAX_PATH_CHARS, "path": value},
                )

    # ------------------------------------------------------------------ run
    def run(self, req: BlenderRunRequest) -> BlenderRunResult:
        self.validate_paths(req)
        command = self.build_command(req)
        start = time.monotonic()
        log_fh: IO[str] | None = None
        try:
            Path(req.temp_dir).mkdir(parents=True, exist_ok=True)
            if req.user_resources_dir is not None:
                Path(req.user_resources_dir).mkdir(parents=True, exist_ok=True)
            if req.log_path is not None:
                Path(req.log_path).parent.mkdir(parents=True, exist_ok=True)
                # line-buffered so the backend can tail the log while Blender runs
                log_fh = open(  # noqa: SIM115
                    req.log_path, "w", encoding="utf-8", errors="replace", newline="\n", buffering=1
                )
        except OSError as exc:
            return self._launch_failed(req, command, start, f"Could not prepare the Blender run directories: {exc}")

        collector = _Collector(request=req, log=log_fh)
        collector.write_log(
            f"# Img3D Blender run - started {utcnow_iso()}\n# command: {subprocess.list2cmdline(command)}\n"
        )
        try:
            proc, job = self._spawn(req, command)
        except OSError as exc:
            collector.write_log(f"# launch failed: {exc}\n")
            self._close_log(collector)
            return self._launch_failed(req, command, start, f"Could not start Blender: {exc}")

        logger.info("Started Blender pid=%s: %s", proc.pid, subprocess.list2cmdline(command))
        readers = [
            threading.Thread(target=_reader, args=(proc.stdout, "stdout", collector), daemon=True,
                             name=f"blender-{proc.pid}-stdout"),
            threading.Thread(target=_reader, args=(proc.stderr, "stderr", collector), daemon=True,
                             name=f"blender-{proc.pid}-stderr"),
        ]
        for thread in readers:
            thread.start()

        cancelled = timed_out = False
        exit_code: int | None = None
        deadline = start + max(0.0, float(req.timeout_s))
        kill_deadline: float | None = None
        try:
            while True:
                try:
                    exit_code = proc.wait(timeout=self._poll_interval_s)
                    break
                except subprocess.TimeoutExpired:
                    pass
                now = time.monotonic()
                if kill_deadline is None:
                    if req.cancel_event is not None and req.cancel_event.is_set():
                        cancelled = True
                    elif now >= deadline:
                        timed_out = True
                    if cancelled or timed_out:
                        logger.info("Blender pid=%s %s; killing process tree", proc.pid,
                                    "cancelled" if cancelled else f"timed out after {req.timeout_s:.0f} s")
                        self._kill_tree(proc, job)
                        kill_deadline = now + _KILL_WAIT_S
                elif now >= kill_deadline:  # pragma: no cover - should never happen with a job object
                    logger.error("Blender pid=%s did not exit after kill; forcing TerminateProcess", proc.pid)
                    proc.kill()
                    try:
                        exit_code = proc.wait(timeout=_KILL_WAIT_S)
                    except subprocess.TimeoutExpired:
                        logger.error("Blender pid=%s is unkillable; giving up waiting", proc.pid)
                    break
        finally:
            if job is not None:
                job.close()  # KILL_ON_JOB_CLOSE: also reaps grandchildren left after a normal exit
            elif not IS_WINDOWS and proc.poll() is not None:  # pragma: no cover
                self._killpg(proc.pid)
        for thread in readers:
            thread.join(_READER_JOIN_TIMEOUT_S)
            if thread.is_alive():  # pragma: no cover - pipe held by an escaped process
                logger.warning("Blender output reader %s did not finish (pipe still open)", thread.name)

        duration = round(time.monotonic() - start, 3)
        with collector.lock:
            events = list(collector.events)
            stdout_lines = list(collector.tails["stdout"])
            stderr_lines = list(collector.tails["stderr"])
        error_events = [e for e in events if e.get("event") == "error"]
        status = self._classify(exit_code, cancelled=cancelled, timed_out=timed_out, has_error_event=bool(error_events))
        errors = self._collect_errors(status, exit_code, error_events, stderr_lines, req.timeout_s)
        result_event = next((e for e in reversed(events) if e.get("event") == "result"), None)
        hello = next((e for e in events if e.get("event") == "hello"), None)
        collector.write_log(f"# finished {utcnow_iso()} status={status} exit_code={exit_code} duration_s={duration}\n")
        self._close_log(collector)
        logger.info("Blender pid=%s finished: status=%s exit_code=%s in %.2f s", proc.pid, status, exit_code, duration)
        return BlenderRunResult(
            status=status,
            exit_code=exit_code,
            duration_s=duration,
            command=command,
            events=events,
            result=result_event,
            errors=errors,
            stdout_tail="\n".join(stdout_lines),
            stderr_tail="\n".join(stderr_lines),
            blender_version=(hello or {}).get("blender_version"),
            log_path=Path(req.log_path) if req.log_path else None,
            timed_out=timed_out,
            cancelled=cancelled,
            pid=proc.pid,
        )

    # ------------------------------------------------------------------ process control
    def _spawn(
        self, req: BlenderRunRequest, command: list[str]
    ) -> tuple[subprocess.Popen[bytes], KillOnCloseJob | None]:
        kwargs: dict[str, Any] = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "env": self.build_env(req),
            "cwd": str(req.cwd or req.temp_dir),
        }
        if not IS_WINDOWS:  # pragma: no cover
            return subprocess.Popen(command, start_new_session=True, **kwargs), None
        job: KillOnCloseJob | None = None
        if self._use_job_object:
            try:
                job = KillOnCloseJob()
            except OSError as exc:  # pragma: no cover - job objects unavailable (very restricted parent job)
                logger.warning("Could not create a Job Object (%s); falling back to taskkill /T", exc)
        if job is None:
            return subprocess.Popen(command, creationflags=CREATE_NO_WINDOW, **kwargs), None
        try:
            proc = subprocess.Popen(command, creationflags=CREATE_SUSPENDED | CREATE_NO_WINDOW, **kwargs)
        except OSError:
            job.close()
            raise
        try:
            job.assign(proc.pid)
        except OSError as exc:  # pragma: no cover - parent job forbids nesting
            logger.warning("Could not assign Blender to a Job Object (%s); falling back to taskkill /T", exc)
            job.close()
            job = None
        try:
            resume_process(proc.pid)
        except OSError:
            proc.kill()
            proc.wait()
            if job is not None:
                job.close()
            raise
        return proc, job

    def _kill_tree(self, proc: subprocess.Popen[bytes], job: KillOnCloseJob | None) -> None:
        if job is not None and job.terminate(STATUS_CONTROL_C_EXIT):
            return
        if not IS_WINDOWS:  # pragma: no cover
            self._killpg(proc.pid)
            return
        try:  # pragma: no cover - only without a job object
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover
            logger.warning("taskkill failed (%s); terminating Blender only", exc)
        if proc.poll() is None:  # pragma: no cover
            proc.kill()

    @staticmethod
    def _killpg(pid: int) -> None:  # pragma: no cover - non-Windows
        try:
            os.killpg(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
        except (OSError, AttributeError):
            pass

    # ------------------------------------------------------------------ result helpers
    @staticmethod
    def _classify(exit_code: int | None, *, cancelled: bool, timed_out: bool, has_error_event: bool) -> RunStatus:
        if cancelled:
            return "cancelled"
        if timed_out:
            return "timed_out"
        if exit_code is None:
            return "crashed"
        if exit_code == 0:
            return "failed" if has_error_event else "succeeded"
        if exit_code == SCRIPT_EXCEPTION_EXIT_CODE:
            return "script_error"
        if exit_code < 0 or exit_code >= 0xC0000000:
            return "crashed"
        return "failed"  # 2/3/4 are script-defined failures; anything else is an unexpected Blender error

    @staticmethod
    def _collect_errors(
        status: RunStatus,
        exit_code: int | None,
        error_events: list[dict[str, Any]],
        stderr_lines: list[str],
        timeout_s: float,
    ) -> list[str]:
        errors = [str(e.get("message") or e.get("code") or "Blender script reported an error") for e in error_events]
        start = None
        for index in range(len(stderr_lines) - 1, -1, -1):
            if stderr_lines[index].startswith("Traceback (most recent call last):"):
                start = index
                break
        if start is not None:
            block = stderr_lines[start : start + _TRACEBACK_MAX_LINES]
            errors.append("\n".join(block).rstrip())
        else:
            errors += [line.strip() for line in stderr_lines if line.startswith("Error: script failed")]
        if status == "script_error" and start is None:
            errors.append(f"Blender script raised an uncaught exception (exit code {SCRIPT_EXCEPTION_EXIT_CODE})")
        elif status == "timed_out":
            errors.append(f"Blender did not finish within {timeout_s:.0f} s and was stopped")
        elif status == "cancelled":
            errors.append("Blender run was cancelled")
        elif status == "crashed":
            code = "unknown" if exit_code is None else f"0x{exit_code & 0xFFFFFFFF:08X}"
            errors.append(f"Blender exited abnormally (exit code {code})")
        elif status == "failed" and not error_events and exit_code not in (None, 0):
            errors.append(f"Blender exited with code {exit_code}")
        return errors

    @staticmethod
    def _close_log(collector: _Collector) -> None:
        with collector.lock:
            log, collector.log = collector.log, None
        if log is not None:
            try:
                log.close()
            except OSError:  # pragma: no cover
                pass

    @staticmethod
    def _launch_failed(req: BlenderRunRequest, command: list[str], start: float, message: str) -> BlenderRunResult:
        logger.error("%s", message)
        return BlenderRunResult(
            status="launch_failed",
            exit_code=None,
            duration_s=round(time.monotonic() - start, 3),
            command=command,
            events=[],
            result=None,
            errors=[message],
            stdout_tail="",
            stderr_tail="",
            blender_version=None,
            log_path=Path(req.log_path) if req.log_path else None,
            timed_out=False,
            cancelled=False,
        )
