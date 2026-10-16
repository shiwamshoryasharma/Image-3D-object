"""TEST-ONLY doubles for the Auto Mesh Tuner backend integration.

* :class:`FakeBlenderEnv` - a real :class:`launcher.BlenderRuntimeManager` wired to a fake detector (fixed list of
  installations whose ``blender.exe`` are dummy files under the test runtime dir) and a fake downloader (gated,
  cancellable, "installs" a dummy portable build). Pass ``env.factory`` as ``create_app(blender_runtime_factory=)``.
* :class:`FakeTunerRunner` - replaces :class:`launcher.BlenderProcessRunner`: reads the worker's ``cfg.json``,
  emits ``@@AMT@@`` protocol events through ``on_event`` (DESIGN.md section 3), writes real outputs (tuned GLB,
  report, PNG previews) and returns a :class:`launcher.BlenderRunResult` for the chosen scenario.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from fakes import write_test_glb
from PIL import Image

from launcher import (
    BlenderInstallation,
    BlenderRunRequest,
    BlenderRunResult,
    BlenderRuntimeManager,
    DownloadCancelledError,
    DownloadProgress,
    LauncherSettings,
    ReleaseInfo,
    ReleaseResolveError,
    RuntimeStatus,
    parse_blender_version,
)
from launcher.blender_detector import installation_id

FAKE_EXE = b"MZ-FAKE-BLENDER-EXE\x00" + b"\x90" * 64
PORTABLE_VERSION = "5.9.1"
PORTABLE_TOP = f"blender-{PORTABLE_VERSION}-windows-x64"
STEPS: tuple[tuple[str, str], ...] = (
    ("load", "Load mesh"),
    ("analyze", "Mesh analysis"),
    ("geometry_repair", "Geometry repair"),
    ("face_refinement", "Face refinement"),
    ("topology_tuning", "Topology tuning"),
    ("rig_optimization", "Rig optimization"),
    ("validation", "Validation"),
    ("export", "Save results"),
)
WEIGHTS = (0.05, 0.07, 0.30, 0.15, 0.20, 0.05, 0.13, 0.05)


# --------------------------------------------------------------------------------------------------
# Installations / detector / downloader
# --------------------------------------------------------------------------------------------------


def make_install(path: Path, version_text: str, *, kind: str = "system", create: bool = True) -> BlenderInstallation:
    """A probed installation as the real detector would report it (dummy exe file created by default)."""
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(FAKE_EXE)
    version = parse_blender_version(f"Blender {version_text}")
    assert version is not None, version_text
    compatible = version.at_least((5, 1, 0))
    return BlenderInstallation(
        id=installation_id(path),
        executable=path,
        kind=kind,  # type: ignore[arg-type]
        origins=["test"],
        version=version,
        architecture="x64",
        valid=True,
        compatible=compatible,
        incompatible_reason=None if compatible else f"Blender {version.label_without_lts} is older than 5.1",
        error=None,
        probe_ms=1.0,
        subsystem="console",
    )


class FakeDetector:
    def __init__(self, installs: list[BlenderInstallation]) -> None:
        self.installs = list(installs)
        self.detect_calls = 0
        self.lock = threading.Lock()

    def detect(self, *, force: bool = False, extra: Any = ()) -> list[BlenderInstallation]:
        with self.lock:
            self.detect_calls += 1
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


class FakeDownloader:
    """Gated fake download: progress frames, cancel support, dummy portable install under ``runtime_root``."""

    def __init__(self, settings: LauncherSettings, detector: FakeDetector) -> None:
        self.settings = settings
        self.detector = detector
        self.gate = threading.Event()
        self.gate.set()
        self.resolve_error: str | None = None
        self.fail_with: Exception | None = None
        self.started = threading.Event()
        self.calls = 0

    def release(self) -> ReleaseInfo:
        name = f"{PORTABLE_TOP}.zip"
        return ReleaseInfo(
            version=PORTABLE_VERSION, series="5.9", file_name=name,
            url=f"https://download.blender.org/release/Blender5.9/{name}", size_bytes=10 * 1024 * 1024,
            sha256="ab" * 32, checksum_url=f"https://download.blender.org/release/Blender5.9/blender-{PORTABLE_VERSION}.sha256",
            architecture="x64", is_lts=False, release_date="2026-10-01",
        )

    def native_architecture(self) -> str:
        return "x64"

    def resolve_latest(self, *, version: str | None = None) -> ReleaseInfo:
        if self.resolve_error:
            raise ReleaseResolveError(self.resolve_error)
        return self.release()

    def download_and_install(
        self, release: ReleaseInfo, *, cancel: threading.Event, on_progress: Callable[[DownloadProgress], None]
    ) -> Path:
        self.calls += 1
        self.started.set()
        total = release.size_bytes or 1
        progress = replace(DownloadProgress.idle(), state="downloading", version=release.version,
                           file_name=release.file_name, url=release.url, bytes_total=total)
        for index in range(1, 11):
            if cancel.is_set():
                on_progress(replace(progress, state="cancelled"))
                raise DownloadCancelledError()
            while not self.gate.wait(0.02):
                if cancel.is_set():
                    on_progress(replace(progress, state="cancelled"))
                    raise DownloadCancelledError()
            done = total * index // 10
            progress = replace(progress, bytes_done=done, percent=round(100 * done / total, 1), speed_bps=1e6)
            on_progress(progress)
            time.sleep(0.005)
        if self.fail_with is not None:
            raise self.fail_with
        for state in ("verifying", "extracting", "validating"):
            on_progress(replace(progress, state=state))
        exe = self.settings.runtime_root / PORTABLE_TOP / "blender.exe"
        install = make_install(exe, PORTABLE_VERSION, kind="embedded")
        with self.detector.lock:
            self.detector.installs.append(install)
        on_progress(replace(progress, state="completed", install_dir=str(exe.parent), verified=True))
        return exe


@dataclass
class FakeBlenderEnv:
    """Installations for a test app. ``versions`` maps a label (``"5.2.2 LTS"``) to the install kind."""

    root: Path
    versions: dict[str, str] = field(default_factory=lambda: {"5.2.2 LTS": "system", "4.2.17 LTS": "system"})
    detector: FakeDetector | None = None
    downloader: FakeDownloader | None = None
    manager: BlenderRuntimeManager | None = None
    statuses: list[RuntimeStatus] = field(default_factory=list)

    def __post_init__(self) -> None:
        installs = []
        for label, kind in self.versions.items():
            folder = f"Blender {'.'.join(label.split()[0].split('.')[:2])}"
            installs.append(make_install(self.root / "programs" / folder / "blender.exe", label, kind=kind))
        self.detector = FakeDetector(installs)

    def exe(self, version_prefix: str) -> Path:
        assert self.detector is not None
        for item in self.detector.installs:
            if item.version is not None and item.version.number.startswith(version_prefix):
                return item.executable
        raise KeyError(version_prefix)

    def factory(
        self, launcher_settings: LauncherSettings, on_change: Callable[[RuntimeStatus], None]
    ) -> BlenderRuntimeManager:
        assert self.detector is not None

        def listener(status: RuntimeStatus) -> None:
            self.statuses.append(status)
            on_change(status)

        self.downloader = FakeDownloader(launcher_settings, self.detector)
        self.manager = BlenderRuntimeManager(
            launcher_settings, detector=self.detector, downloader=self.downloader, on_change=listener  # type: ignore[arg-type]
        )
        return self.manager


# --------------------------------------------------------------------------------------------------
# Tuner runner
# --------------------------------------------------------------------------------------------------


def write_png(path: Path, color: tuple[int, int, int] = (90, 140, 200)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 32), color).save(path, format="PNG")


class FakeTunerRunner:
    """Scenario-driven stand-in for :class:`launcher.BlenderProcessRunner`.

    Scenarios: ``ok`` | ``warnings`` | ``error_event`` (error event + rc 4 in geometry_repair) | ``script_error``
    (rc 86 + traceback) | ``crash`` (rc 0xC0000005) | ``timeout`` | ``no_result`` (rc 0 without result event).
    ``hold()`` blocks the run inside ``geometry_repair`` until ``release()`` or the cancel event.
    """

    def __init__(self, scenario: str = "ok") -> None:
        self.scenario = scenario
        self.gate = threading.Event()
        self.gate.set()
        self.started = threading.Event()
        self.blocked = threading.Event()
        """Set while a held run waits at the gate (inside geometry_repair, after its progress events)."""
        self.requests: list[BlenderRunRequest] = []
        self.configs: list[dict[str, Any]] = []
        self.threads: list[str] = []
        self.input_sizes: list[int] = []
        self.cancelled_runs: list[str] = []
        self.progress_events = 25

    def hold(self) -> None:
        self.blocked.clear()
        self.gate.clear()

    def release(self) -> None:
        self.gate.set()

    # ------------------------------------------------------------------ run
    def run(self, req: BlenderRunRequest) -> BlenderRunResult:
        started = time.monotonic()
        self.requests.append(req)
        self.threads.append(threading.current_thread().name)
        assert req.script_args[0] == "--config"
        cfg = json.loads(Path(req.script_args[1]).read_text(encoding="utf-8"))
        self.configs.append(cfg)
        self.input_sizes.append(Path(cfg["input"]).stat().st_size)
        out = Path(cfg["output_dir"])
        events: list[dict[str, Any]] = []
        log = open(req.log_path, "w", encoding="utf-8") if req.log_path else None  # noqa: SIM115

        def emit(obj: dict[str, Any]) -> None:
            events.append(obj)
            if log is not None:
                log.write("@@AMT@@" + json.dumps(obj, separators=(",", ":")) + "\n")
                log.flush()
            if req.on_event is not None:
                req.on_event(obj)

        def finish(status: str, exit_code: int | None, errors: list[str] | None = None,
                   result: dict[str, Any] | None = None) -> BlenderRunResult:
            if log is not None:
                log.write(f"# finished status={status} exit_code={exit_code}\n")
                log.close()
            return BlenderRunResult(
                status=status,  # type: ignore[arg-type]
                exit_code=exit_code, duration_s=round(time.monotonic() - started, 3), command=["fake-blender"],
                events=events, result=result, errors=errors or [], stdout_tail="", stderr_tail="",
                blender_version="5.2.2 LTS", log_path=req.log_path, timed_out=status == "timed_out",
                cancelled=status == "cancelled", pid=12345,
            )

        emit({"event": "hello", "protocol": 1, "script_version": "fake", "blender_version": "5.2.2 LTS",
              "python": "3.13.13", "gpu": False})
        emit({"event": "plan", "steps": [{"id": sid, "label": label} for sid, label in STEPS]})
        self.started.set()
        overall = 0.0
        for index, (sid, label) in enumerate(STEPS):
            emit({"event": "step", "id": sid, "status": "running", "message": f"{label}...", "duration_s": None,
                  "progress": round(overall, 4), "details": {}})
            if sid == "geometry_repair":
                for i in range(self.progress_events):
                    emit({"event": "progress", "id": sid, "fraction": (i + 1) / self.progress_events,
                          "progress": round(overall + WEIGHTS[index] * (i + 1) / self.progress_events, 4),
                          "message": f"Repairing {i + 1}/{self.progress_events}"})
                if not self.gate.is_set():
                    self.blocked.set()
                while not self.gate.wait(0.02):
                    if req.cancel_event is not None and req.cancel_event.is_set():
                        self.cancelled_runs.append(cfg["context"]["tuning_id"])
                        return finish("cancelled", 3221225786, ["Blender run was cancelled"])
                if req.cancel_event is not None and req.cancel_event.is_set():
                    self.cancelled_runs.append(cfg["context"]["tuning_id"])
                    return finish("cancelled", 3221225786, ["Blender run was cancelled"])
                if self.scenario == "error_event":
                    emit({"event": "error", "code": "repair_failed", "message": "Hole filling failed: boom",
                          "step": sid, "traceback": None})
                    return finish("failed", 4, ["Hole filling failed: boom"])
            if sid == "face_refinement" and self.scenario == "script_error":
                tb = "Traceback (most recent call last):\n  File \"x.py\", line 1\nZeroDivisionError: division by zero"
                return finish("script_error", 86, [tb])
            if sid == "topology_tuning" and self.scenario == "crash":
                return finish("crashed", 0xC0000005, ["Blender exited abnormally (exit code 0xC0000005)"])
            if sid == "validation" and self.scenario == "timeout":
                return finish("timed_out", None, [f"Blender did not finish within {req.timeout_s:.0f} s"])
            overall += WEIGHTS[index]
            status = "warning" if (sid == "face_refinement" and self.scenario == "warnings") else "done"
            if sid == "rig_optimization":
                status = "skipped"
            emit({"event": "step", "id": sid, "status": status,
                  "message": "Eye correction skipped" if status == "warning" else f"{label} ok",
                  "duration_s": 0.01, "progress": round(overall, 4), "details": {"n": index}})
        if self.scenario == "no_result":
            return finish("succeeded", 0)

        outputs = cfg["outputs"]
        write_test_glb(out / outputs["model"])
        previews = [
            {"name": "before_front.png", "view": "front", "variant": "before"},
            {"name": "after_front.png", "view": "front", "variant": "after"},
        ]
        for item in previews:
            write_png(out / outputs["previews_dir"] / item["name"])
        write_png(out / outputs["previews_dir"] / "Not-Listed.PNG")  # invalid name: must never be served
        if outputs.get("blend"):
            (out / outputs["blend"]).write_bytes(b"BLENDER-fake")
        metrics = [
            {"key": "faces", "label": "Faces", "unit": None, "before": 1_900_000, "after": 1_000_000,
             "better": "neutral"},
            {"key": "holes", "label": "Holes", "unit": None, "before": 12, "after": 0, "better": "lower"},
        ]
        warnings = ["Eye correction skipped"] if self.scenario == "warnings" else []
        validation = {"passed": True, "checks": [{"id": "manifold", "label": "Manifold", "status": "pass",
                                                  "value": 0, "threshold": 0, "message": "ok"}]}
        report = {"schema": 1, "tool": "Img3D Auto Mesh Tuner", "script_version": "fake", "protocol": 1,
                  "metrics": metrics, "warnings": warnings, "validation": validation,
                  "options": cfg["options"], "steps": []}
        (out / outputs["report"]).write_text(json.dumps(report), encoding="utf-8")
        result = {"event": "result", "status": "completed_with_warnings" if warnings else "completed",
                  "model": outputs["model"], "report": outputs["report"], "blend": outputs.get("blend"),
                  "previews": previews + [{"name": "../../escape.png", "view": "x", "variant": "y"}],
                  "metrics": metrics, "warnings": warnings, "validation": validation}
        emit(result)
        return finish("succeeded", 0, result=result)


# --------------------------------------------------------------------------------------------------
# App harness
# --------------------------------------------------------------------------------------------------


@dataclass
class TunerHarness:
    app: Any
    client: Any
    engine: Any
    settings: Any
    env: FakeBlenderEnv
    runner: FakeTunerRunner | None

    @property
    def container(self) -> Any:
        return self.app.state.container


def start_tuner_app(
    stack: Any,
    settings: Any,
    root: Path,
    *,
    versions: dict[str, str] | None = None,
    runner: Any = None,
    engine: Any = None,
    real_runtime: bool = False,
) -> TunerHarness:
    """Create + start (lifespan) an app with the fake engine, a fake Blender env and an optional runner double.

    ``real_runtime=True`` keeps the real runtime manager (real detection) instead of the fake env.
    """
    from fakes import FakeEngine
    from starlette.testclient import TestClient

    from main import create_app

    env = FakeBlenderEnv(root / "fake_blender", **({"versions": versions} if versions is not None else {}))
    engine = engine if engine is not None else FakeEngine()
    app = create_app(
        settings,
        engine=engine,
        blender_runtime_factory=None if real_runtime else env.factory,
        blender_runner=runner,
    )
    client = stack.enter_context(TestClient(app))
    return TunerHarness(app=app, client=client, engine=engine, settings=settings, env=env, runner=runner)
