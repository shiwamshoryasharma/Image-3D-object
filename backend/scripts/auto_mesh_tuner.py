"""Img3D Auto Mesh Tuner - standalone command line ("Launch Auto Mesh Tuner" flow, DESIGN.md sections 2-3).

Uses the same Blender runtime management as the backend (``backend/launcher`` + ``IMG3D_BLENDER_*`` settings from
``backend/.env``), so a selection or portable download made here is what the app uses and vice versa.

Usage (from the project root)::

    backend\\python\\python.exe -s backend\\scripts\\auto_mesh_tuner.py detect [--json] [--force]
    backend\\python\\python.exe -s backend\\scripts\\auto_mesh_tuner.py status [--json]
    backend\\python\\python.exe -s backend\\scripts\\auto_mesh_tuner.py select auto|system|embedded [--executable P]
    backend\\python\\python.exe -s backend\\scripts\\auto_mesh_tuner.py release [--json]
    backend\\python\\python.exe -s backend\\scripts\\auto_mesh_tuner.py download [--yes] [--version X.Y.Z]
    backend\\python\\python.exe -s backend\\scripts\\auto_mesh_tuner.py tune INPUT.glb [-o DIR] [--preset P]
        [--options JSON] [--yes] [--timeout S]

``tune``: scan -> if no compatible Blender >= 5.1, ask "<backend message> [y/N]" -> download the official portable
build (progress bar, sha256 verified) -> run the tuner in Blender (streamed step progress) -> print a summary.
Nothing is ever downloaded without a "y" answer (or ``--yes``).

Exit codes: 0 ok, 1 failure, 2 bad usage/options, 3 no usable Blender (download declined/disabled), 130 interrupted.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import site
import sys
import threading
import time
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[1]

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_NO_RUNTIME = 3
EXIT_INTERRUPTED = 130


def _bootstrap_sys_path() -> None:
    """Make ``backend/`` importable and drop the per-user site-packages (see ``download_aux_models.py``)."""
    try:
        user_site = os.path.normcase(os.path.abspath(site.getusersitepackages()))
    except Exception:  # noqa: BLE001 - site may be minimal in embedded builds
        user_site = None
    if user_site:
        sys.path[:] = [p for p in sys.path if os.path.normcase(os.path.abspath(p or ".")) != user_site]
    site.ENABLE_USER_SITE = False
    backend = str(BACKEND_DIR)
    if backend not in sys.path:
        sys.path.insert(0, backend)


_bootstrap_sys_path()

from config.settings import Settings, get_settings  # noqa: E402
from launcher import (  # noqa: E402
    BlenderInstallation,
    BlenderProcessRunner,
    BlenderRunRequest,
    BlenderRunResult,
    BlenderRuntimeManager,
    BlenderUnavailableError,
    LauncherError,
    RuntimeStatus,
)
from launcher.config import utcnow_iso  # noqa: E402
from services.blender_runtime_service import build_launcher_settings  # noqa: E402

logger = logging.getLogger("img3d.amt_cli")

STEP_LABELS = {
    "load": "Load mesh",
    "analyze": "Mesh analysis",
    "geometry_repair": "Geometry repair",
    "face_refinement": "Face refinement",
    "topology_tuning": "Topology tuning",
    "rig_optimization": "Rig optimization",
    "validation": "Validation",
    "export": "Save results",
}
_BAR_WIDTH = 30


# --------------------------------------------------------------------------------------------------
# console helpers
# --------------------------------------------------------------------------------------------------


class Console:
    """Line output plus a single in-place progress line (``\\r``) when stdout is a terminal."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tty = sys.stdout.isatty()
        self._live = False
        self._last_plain = 0.0

    def line(self, text: str = "") -> None:
        with self._lock:
            if self._live:
                sys.stdout.write("\n")
                self._live = False
            sys.stdout.write(text + "\n")
            sys.stdout.flush()

    def progress(self, text: str, *, force: bool = False) -> None:
        """Update the live progress line (non-terminals: at most one line every 2 s)."""
        with self._lock:
            if self._tty:
                sys.stdout.write("\r" + text[:150].ljust(110))
                sys.stdout.flush()
                self._live = True
                return
            now = time.monotonic()
            if force or now - self._last_plain >= 2.0:
                self._last_plain = now
                sys.stdout.write(text + "\n")
                sys.stdout.flush()

    def end_progress(self) -> None:
        with self._lock:
            if self._live:
                sys.stdout.write("\n")
                sys.stdout.flush()
                self._live = False


def bar(fraction: float | None) -> str:
    if fraction is None:
        return "[" + "?" * _BAR_WIDTH + "]"
    filled = int(round(max(0.0, min(1.0, fraction)) * _BAR_WIDTH))
    return "[" + "#" * filled + "-" * (_BAR_WIDTH - filled) + "]"


def human_bytes(value: float | None) -> str:
    if value is None:
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if abs(value) < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GB"  # pragma: no cover


def ask_yes_no(console: Console, question: str, *, assume_yes: bool) -> bool:
    if assume_yes:
        console.line(f"{question} [y/N] y (--yes)")
        return True
    if not sys.stdin or not sys.stdin.isatty():
        console.line(f"{question} [y/N] (no interactive input; answering N - pass --yes to accept)")
        return False
    try:
        answer = input(f"{question} [y/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in {"y", "yes"}


# --------------------------------------------------------------------------------------------------
# runtime
# --------------------------------------------------------------------------------------------------


class Runtime:
    """Backend-equivalent runtime manager with a console download progress listener."""

    def __init__(self, settings: Settings, console: Console) -> None:
        self.settings = settings
        self.console = console
        self.launcher_settings = build_launcher_settings(settings)
        self._show_download = False
        self._last_download_state: str | None = None
        self.manager = BlenderRuntimeManager(self.launcher_settings, on_change=self._on_change)

    def _on_change(self, status: RuntimeStatus) -> None:
        if not self._show_download or status.download is None:
            return
        d = status.download
        if d.state != self._last_download_state:
            if self._last_download_state is not None:
                self.console.end_progress()
            self._last_download_state = d.state
            if d.state in ("resolving", "verifying", "extracting", "validating"):
                self.console.line(f"  {d.state.capitalize()}...")
        if d.state == "downloading":
            fraction = d.bytes_done / d.bytes_total if d.bytes_total else None
            speed = f"{human_bytes(d.speed_bps)}/s" if d.speed_bps else "-"
            eta = f"ETA {d.eta_s:.0f}s" if d.eta_s is not None else ""
            pct = f"{100 * fraction:5.1f}%" if fraction is not None else "  ?  "
            self.console.progress(
                f"  {bar(fraction)} {pct} {human_bytes(d.bytes_done)}/{human_bytes(d.bytes_total)} {speed} {eta}"
                + (" (resumed)" if d.resumed else "")
            )

    def download(self, version: str | None) -> RuntimeStatus:
        """Start the download and block until it finishes (Ctrl+C cancels; the partial file is kept)."""
        self._show_download = True
        self._last_download_state = None
        status = self.manager.start_download(version=version)
        try:
            while True:
                status = self.manager.status()
                if status.state != "downloading" and not (status.download and status.download.active):
                    break
                time.sleep(0.2)
        except KeyboardInterrupt:
            self.console.end_progress()
            self.console.line("Cancelling the download (the partial file is kept for resuming)...")
            self.manager.cancel_download()
            self.manager.close(timeout=30.0)
            raise
        finally:
            self._show_download = False
        self.console.end_progress()
        return self.manager.status()


def print_status(console: Console, status: RuntimeStatus) -> None:
    console.line(f"Blender runtime : {status.state} (mode {status.mode}, minimum {status.min_version})")
    sel = status.selected
    if sel is not None:
        label = sel.version.label if sel.version else "?"
        console.line(f"Selected        : Blender {label} [{sel.kind}] {sel.executable}")
        console.line(f"Reason          : {status.selection_reason}")
    else:
        console.line("Selected        : none")
    if status.installations:
        console.line("Installations   :")
        for item in status.installations:
            marker = "*" if sel is not None and item.id == sel.id else " "
            label = item.version.label if item.version else "unknown"
            verdict = "compatible" if item.compatible else (item.incompatible_reason or item.error or "unusable")
            console.line(f"  {marker} {label:<18} {item.kind:<9} {item.architecture or '?':<6} {verdict}")
            console.line(f"      {item.executable}  ({', '.join(item.origins)})")
    else:
        console.line("Installations   : none found")
    for warning in status.warnings:
        console.line(f"Warning         : {warning}")
    if status.message:
        console.line(f"Message         : {status.message}")
    if status.error:
        console.line(f"Error           : {status.error}")
    if status.download is not None and status.download.state != "idle":
        d = status.download
        extra = f" - {d.error}" if d.error else ""
        console.line(f"Last download   : {d.state} {d.version or ''} ({human_bytes(d.bytes_done)}){extra}")
    console.line(f"Runtime root    : {status.runtime_root}")
    if status.last_scan_at:
        console.line(f"Last scan       : {status.last_scan_at} ({status.scan_duration_ms or 0:.0f} ms)")


def runtime_json(status: RuntimeStatus) -> str:
    from services.blender_runtime_service import runtime_out

    return json.dumps(runtime_out(status), indent=2, ensure_ascii=False)


# --------------------------------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------------------------------


def cmd_detect(args: argparse.Namespace, runtime: Runtime, console: Console) -> int:
    status = runtime.manager.scan(force=args.force)
    if args.json:
        console.line(runtime_json(status))
    else:
        print_status(console, status)
    return EXIT_OK if status.state in ("ready", "needs_download") else EXIT_FAILED


def cmd_status(args: argparse.Namespace, runtime: Runtime, console: Console) -> int:
    status = runtime.manager.scan(force=False)  # cached probes: fast
    if args.json:
        console.line(runtime_json(status))
        return EXIT_OK
    print_status(console, status)
    config = runtime.manager.config()
    console.line(f"Config file     : {runtime.launcher_settings.config_path} (pinned: {config.pinned_executable or '-'})")
    downloads = runtime.launcher_settings.downloads_dir
    parts = sorted(downloads.glob("*.part")) if downloads.is_dir() else []
    for part in parts:
        console.line(f"Partial download: {part.name} ({human_bytes(part.stat().st_size)}, resumable)")
    return EXIT_OK


def cmd_select(args: argparse.Namespace, runtime: Runtime, console: Console) -> int:
    try:
        status = runtime.manager.select(args.mode, args.executable)
    except LauncherError as exc:
        console.line(f"error: {exc.message} ({exc.code})")
        return EXIT_FAILED
    print_status(console, status)
    return EXIT_OK if status.selected is not None else EXIT_NO_RUNTIME


def cmd_release(args: argparse.Namespace, runtime: Runtime, console: Console) -> int:
    try:
        release = runtime.manager.resolve_release(force=True)
    except LauncherError as exc:
        console.line(f"error: {exc.message} ({exc.code})")
        return EXIT_FAILED
    if args.json:
        console.line(json.dumps(release.to_dict(), indent=2))
        return EXIT_OK
    info = release.to_dict()
    console.line(f"Latest official Blender for this PC: {release.version}"
                 f"{' LTS' if release.is_lts else ''} ({release.architecture})")
    console.line(f"  file     : {release.file_name} ({human_bytes(release.size_bytes)})")
    console.line(f"  url      : {release.url}")
    console.line(f"  host     : {info.get('host')}")
    console.line(f"  sha256   : {release.sha256 or 'not published'}")
    console.line(f"  released : {release.release_date or '?'}")
    return EXIT_OK


def _confirm_and_download(
    runtime: Runtime, console: Console, question: str, *, assume_yes: bool, version: str | None
) -> int:
    if not runtime.settings.blender_allow_download:
        console.line("Downloading Blender is disabled (IMG3D_BLENDER_ALLOW_DOWNLOAD=false).")
        return EXIT_NO_RUNTIME
    try:
        release = runtime.manager.resolve_release()
    except LauncherError as exc:
        console.line(question)
        console.line(f"Cannot download a portable Blender: {exc.message} ({exc.code})")
        return EXIT_FAILED
    host = release.to_dict().get("host")
    console.line(f"Official portable Blender {release.version} ({release.architecture}): {release.file_name}, "
                 f"{human_bytes(release.size_bytes)} from {host}; sha256 {release.sha256 or 'not published'}")
    console.line(f"Install folder: {runtime.launcher_settings.runtime_root}")
    if not ask_yes_no(console, question, assume_yes=assume_yes):
        console.line("Download declined.")
        return EXIT_NO_RUNTIME
    started = time.monotonic()
    try:
        status = runtime.download(version)
    except LauncherError as exc:
        console.line(f"error: {exc.message} ({exc.code})")
        return EXIT_FAILED
    d = status.download
    if d is None or d.state != "completed":
        console.line(f"Download {d.state if d else 'failed'}: {d.error if d and d.error else 'unknown error'}")
        return EXIT_FAILED
    console.line(f"Installed Blender {d.version} in {time.monotonic() - started:.0f} s -> {d.install_dir}"
                 f" (checksum {'verified' if d.verified else 'NOT verified'})")
    return EXIT_OK


def cmd_download(args: argparse.Namespace, runtime: Runtime, console: Console) -> int:
    status = runtime.manager.scan(force=False)
    if status.selected is not None:
        console.line(f"Note: a compatible Blender is already selected ({status.selected.executable}).")
    code = _confirm_and_download(runtime, console, "Download the official portable Blender?",
                                 assume_yes=args.yes, version=args.version)
    if code == EXIT_OK:
        print_status(console, runtime.manager.status())
    return code


def _parse_options(args: argparse.Namespace) -> dict[str, Any]:
    from pydantic import ValidationError

    from schemas.mesh_tuner import TuningOptionsIn

    raw: dict[str, Any] = {}
    if args.options:
        try:
            raw = json.loads(args.options)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"error: --options is not valid JSON: {exc}") from None
        if not isinstance(raw, dict):
            raise SystemExit("error: --options must be a JSON object")
    if args.preset:
        raw["preset"] = args.preset
    try:
        return TuningOptionsIn.model_validate(raw).effective()
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())
        raise SystemExit(f"error: invalid tuning options: {problems}") from None


class TuneProgress:
    """Prints ``@@AMT@@`` protocol events as step lines + a live overall progress line (reader threads)."""

    def __init__(self, console: Console) -> None:
        self.console = console
        self.result: dict[str, Any] | None = None
        self.errors: list[dict[str, Any]] = []
        self.blender_version: str | None = None
        self.overall = 0.0

    def __call__(self, event: dict[str, Any]) -> None:
        kind = event.get("event")
        overall = event.get("progress")
        if isinstance(overall, (int, float)) and not isinstance(overall, bool):
            self.overall = max(self.overall, float(overall))
        if kind == "hello":
            self.blender_version = event.get("blender_version")
            gpu = event.get("gpu")
            self.console.line(f"Blender {self.blender_version} started (Python {event.get('python')}, "
                              f"GPU previews: {'yes' if gpu else 'no' if gpu is False else 'n/a'})")
        elif kind == "step":
            sid = str(event.get("id"))
            label = STEP_LABELS.get(sid, sid)
            status = event.get("status")
            message = event.get("message") or ""
            if status == "running":
                self.console.progress(f"  {bar(self.overall)} {100 * self.overall:5.1f}%  {label}...", force=True)
            else:
                duration = event.get("duration_s")
                took = f" ({duration:.1f} s)" if isinstance(duration, (int, float)) else ""
                self.console.line(f"  [{status:^8}] {label}{took}: {message}")
        elif kind == "progress":
            sid = str(event.get("id"))
            text = event.get("message") or STEP_LABELS.get(sid, sid)
            self.console.progress(f"  {bar(self.overall)} {100 * self.overall:5.1f}%  {text}")
        elif kind == "log" and event.get("level") in ("warning", "error"):
            self.console.line(f"  {event.get('level')}: {event.get('message')}")
        elif kind == "error":
            self.errors.append(event)
            self.console.line(f"  ERROR {event.get('code')}: {event.get('message')}")
        elif kind == "result":
            self.result = event


def _print_summary(console: Console, result: BlenderRunResult, progress: TuneProgress, out_dir: Path) -> None:
    console.end_progress()
    console.line("")
    console.line(f"Result: {result.status} (exit code {result.exit_code}) in {result.duration_s:.1f} s")
    event = progress.result or result.result
    if result.status == "succeeded" and event:
        for key, label in (("model", "Tuned model"), ("report", "Report"), ("blend", ".blend")):
            name = event.get(key)
            if name:
                console.line(f"  {label:<12}: {out_dir / name}")
        previews = event.get("previews") or []
        if previews:
            console.line(f"  {'Previews':<12}: {out_dir / 'previews'} ({len(previews)} image(s))")
        metrics = [m for m in event.get("metrics") or [] if isinstance(m, dict)]
        if metrics:
            console.line("  Metrics (before -> after):")
            for metric in metrics:
                unit = f" {metric.get('unit')}" if metric.get("unit") else ""
                console.line(f"    {metric.get('label', metric.get('key')):<32} "
                             f"{metric.get('before')!s:>12} -> {metric.get('after')!s:<12}{unit}")
        validation = event.get("validation") or {}
        if validation:
            console.line(f"  Validation: {'passed' if validation.get('passed') else 'FAILED'}")
            for check in validation.get("checks") or []:
                if isinstance(check, dict) and check.get("status") != "pass":
                    console.line(f"    {check.get('status')}: {check.get('label')} - {check.get('message')}")
        for warning in event.get("warnings") or []:
            console.line(f"  Warning: {warning}")
    else:
        for error in result.errors[-5:]:
            console.line("  " + error.replace("\n", "\n  "))
    console.line(f"  Log: {result.log_path}")


def _default_output_dir(settings: Settings, input_path: Path) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return settings.runtime_dir / "cli_tunings" / f"{input_path.stem[:40]}-{stamp}"


def cmd_tune(args: argparse.Namespace, runtime: Runtime, console: Console) -> int:
    settings = runtime.settings
    input_path = Path(args.input).expanduser().resolve()
    if not input_path.is_file():
        console.line(f"error: input file not found: {input_path}")
        return EXIT_USAGE
    try:
        options = _parse_options(args)
    except SystemExit as exc:
        console.line(str(exc))
        return EXIT_USAGE

    console.line("Scanning for Blender installations...")
    status = runtime.manager.scan(force=False)
    install: BlenderInstallation | None = None
    try:
        install = runtime.manager.require_runtime()
    except BlenderUnavailableError as exc:
        if not status.can_download:
            console.line(exc.message)
            return EXIT_NO_RUNTIME
        code = _confirm_and_download(runtime, console, exc.message, assume_yes=args.yes, version=None)
        if code != EXIT_OK:
            return code
        try:
            install = runtime.manager.require_runtime()
        except BlenderUnavailableError as again:
            console.line(f"error: {again.message}")
            return EXIT_NO_RUNTIME
    assert install is not None
    label = install.version.label if install.version else "?"
    console.line(f"Using Blender {label} [{install.kind}] {install.executable}"
                 f" ({runtime.manager.status().selection_reason})")

    out_dir = Path(args.output).expanduser().resolve() if args.output else _default_output_dir(settings, input_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    run_id = f"cli-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"
    config = {
        "schema": 1,
        "input": str(input_path),
        "output_dir": str(out_dir),
        "options": options,
        "outputs": {"model": "tuned.glb", "report": "report.json",
                    "blend": "tuned.blend" if options.get("save_blend") else None, "previews_dir": "previews"},
        "context": {"job_id": None, "tuning_id": run_id, "source": "cli", "created_at": utcnow_iso()},
    }
    config_path = out_dir / "config.json"
    config_path.write_text(json.dumps(config, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    console.line(f"Input : {input_path} ({human_bytes(input_path.stat().st_size)})")
    console.line(f"Output: {out_dir}")
    console.line(f"Preset: {options['preset']}")

    temp_dir = settings.blender_temp_dir / run_id
    cancel = threading.Event()
    progress = TuneProgress(console)
    request = BlenderRunRequest(
        executable=install.executable,
        script=settings.blender_scripts_dir / "auto_mesh_tuner.py",
        script_args=["--config", str(config_path)],
        timeout_s=float(args.timeout or settings.blender_tune_timeout_s),
        temp_dir=temp_dir,
        user_resources_dir=runtime.launcher_settings.user_resources_dir,
        log_path=out_dir / "blender.log",
        cancel_event=cancel,
        on_event=progress,
    )
    outcome: dict[str, Any] = {}

    def _run() -> None:
        try:
            outcome["result"] = BlenderProcessRunner().run(request)
        except LauncherError as exc:  # PathTooLongError
            outcome["error"] = exc
        except Exception as exc:  # noqa: BLE001
            logger.exception("Blender run failed")
            outcome["error"] = exc

    worker = threading.Thread(target=_run, name="blender-run", daemon=True)
    worker.start()
    interrupted = False
    try:
        while worker.is_alive():
            worker.join(0.2)
    except KeyboardInterrupt:
        interrupted = True
        console.end_progress()
        console.line("Interrupted - stopping Blender...")
        cancel.set()
        worker.join(30)
    finally:
        _remove_tree(temp_dir, settings.blender_temp_dir)
    if "error" in outcome:
        error = outcome["error"]
        console.end_progress()
        console.line(f"error: {getattr(error, 'message', None) or error}")
        return EXIT_FAILED
    result: BlenderRunResult | None = outcome.get("result")
    if result is None:
        return EXIT_INTERRUPTED if interrupted else EXIT_FAILED
    _print_summary(console, result, progress, out_dir)
    if interrupted:
        return EXIT_INTERRUPTED
    return EXIT_OK if result.status == "succeeded" else EXIT_FAILED


def _remove_tree(path: Path, root: Path) -> None:
    """Delete a per-run temp dir (only inside ``root``)."""
    import shutil

    try:
        if path.exists() and root.resolve() in path.resolve().parents:
            shutil.rmtree(path, ignore_errors=True)
    except OSError:  # pragma: no cover - best effort
        pass


# --------------------------------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="auto_mesh_tuner.py",
        description="Img3D Auto Mesh Tuner: Blender >= 5.1 runtime detection/selection/download and mesh tuning.",
    )
    parser.add_argument("-v", "--verbose", action="count", default=0, help="log INFO (-v) or DEBUG (-vv)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("detect", help="scan for Blender installations and apply the selection policy")
    p.add_argument("--json", action="store_true", help="print the status as JSON (API BlenderRuntimeOut shape)")
    p.add_argument("--force", action="store_true", help="re-run every --version probe (ignore the cache)")
    p.set_defaults(func=cmd_detect)

    p = sub.add_parser("status", help="show the selected runtime (cached scan)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("select", help="choose the runtime mode and optionally pin an executable")
    p.add_argument("mode", choices=("auto", "system", "embedded"))
    p.add_argument("--executable", help="blender.exe (or its folder) to pin")
    p.set_defaults(func=cmd_select)

    p = sub.add_parser("release", help="resolve the latest official portable Blender (network)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_release)

    p = sub.add_parser("download", help="download + verify + install the official portable Blender")
    p.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    p.add_argument("--version", help="exact version X.Y.Z (default: latest stable >= minimum)")
    p.set_defaults(func=cmd_download)

    p = sub.add_parser("tune", help="tune a mesh (downloads Blender first if needed, after confirmation)")
    p.add_argument("input", help="input mesh (.glb/.gltf/.obj/.fbx/.ply/.stl/.blend)")
    p.add_argument("-o", "--output", help="output folder (default: runtime/cli_tunings/<name>-<time>)")
    p.add_argument("--preset", choices=("conservative", "balanced", "strong"))
    p.add_argument("--options", help='JSON object of tuning options, e.g. \'{"smoothing": 0.3}\'')
    p.add_argument("--yes", action="store_true", help="accept the Blender download prompt")
    p.add_argument("--timeout", type=float, help="Blender run timeout in seconds (default IMG3D_BLENDER_TUNE_TIMEOUT_S)")
    p.set_defaults(func=cmd_tune)
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    level = logging.WARNING if args.verbose == 0 else logging.INFO if args.verbose == 1 else logging.DEBUG
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s", stream=sys.stderr)
    console = Console()
    settings = get_settings()
    if not settings.blender_enabled:
        console.line("Auto Mesh Tuner is disabled by configuration (IMG3D_BLENDER_ENABLED=false).")
        return EXIT_NO_RUNTIME
    runtime = Runtime(settings, console)
    try:
        return int(args.func(args, runtime, console))
    except KeyboardInterrupt:
        console.end_progress()
        console.line("Interrupted.")
        return EXIT_INTERRUPTED
    finally:
        runtime.manager.close(timeout=5.0)


if __name__ == "__main__":
    raise SystemExit(main())
