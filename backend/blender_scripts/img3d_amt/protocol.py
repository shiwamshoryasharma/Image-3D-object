"""``@@AMT@@`` stdout protocol (DESIGN section 3).

Every event is ONE line ``@@AMT@@{json}`` (``ensure_ascii=True``, compact separators) written to stdout and flushed
immediately. Blender's own C-level prints interleave with ours, so the backend finds events by substring search
(``line.find("@@AMT@@")``); we always write the full line in a single ``write`` call.

This module has no bpy dependency (unit-testable outside Blender).
"""

from __future__ import annotations

import json
import logging
import math
import sys
import threading
from collections.abc import Mapping
from typing import Any

SENTINEL = "@@AMT@@"
LOG_LEVELS = ("debug", "info", "warning", "error")
STEP_STATUSES = ("running", "done", "warning", "skipped", "failed")

_lock = threading.Lock()
_stream = None  # overridable for tests; None -> sys.stdout at call time


def set_stream(stream) -> None:
    """Redirect protocol output (tests). ``None`` restores ``sys.stdout``."""
    global _stream
    _stream = stream


def configure_stdout() -> None:
    """Make stdout line-buffered + UTF-8 safe (Blender's Python stdout is block-buffered when piped)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(line_buffering=True, errors="backslashreplace")
        except (AttributeError, ValueError, OSError):
            pass


def to_jsonable(value: Any) -> Any:
    """Recursively convert numpy scalars/arrays, tuples, sets, paths to plain JSON types.

    Non-finite floats become ``None`` (strict JSON for the frontend's ``JSON.parse``).
    """
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Mapping):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [to_jsonable(v) for v in value]
    # numpy (duck-typed so this module never imports numpy)
    tolist = getattr(value, "tolist", None)
    if callable(tolist):
        return to_jsonable(tolist())
    item = getattr(value, "item", None)
    if callable(item):
        return to_jsonable(item())
    if hasattr(value, "__fspath__"):
        return str(value.__fspath__())
    return str(value)


def encode(event: str, fields: Mapping[str, Any]) -> str:
    """Return the full protocol line (without newline) for ``event`` + ``fields``."""
    payload = {"event": event}
    payload.update(to_jsonable(dict(fields)))
    return SENTINEL + json.dumps(payload, ensure_ascii=True, separators=(",", ":"), allow_nan=False)


def emit(event: str, **fields: Any) -> None:
    """Write one protocol event line to stdout and flush. Never raises (a broken pipe must not kill the run)."""
    try:
        line = encode(event, fields)
    except (TypeError, ValueError) as exc:  # unserializable payload: still tell the backend something happened
        line = encode("log", {"level": "error", "message": f"protocol: could not encode {event!r} event: {exc}"})
    stream = _stream or sys.stdout
    with _lock:
        try:
            stream.write(line + "\n")
            stream.flush()
        except (OSError, ValueError):
            pass


# ----------------------------------------------------------------------------------------------------------------
# typed helpers (one per event type)
# ----------------------------------------------------------------------------------------------------------------


def hello(*, script_version: str, blender_version: str, python: str, gpu: bool | None, protocol: int = 1,
          **extra: Any) -> None:
    """``hello`` {protocol, script_version, blender_version, python, gpu}."""
    emit("hello", protocol=protocol, script_version=script_version, blender_version=blender_version,
         python=python, gpu=gpu, **extra)


def plan(steps: list[dict]) -> None:
    """``plan`` {steps:[{id,label}]}."""
    emit("plan", steps=[{"id": s["id"], "label": s["label"]} for s in steps])


def step(step_id: str, status: str, *, message: str = "", duration_s: float | None = None,
         progress: float | None = None, details: Mapping[str, Any] | None = None) -> None:
    """``step`` {id, status, message, duration_s, progress (overall 0..1), details}."""
    if status not in STEP_STATUSES:
        raise ValueError(f"invalid step status {status!r}")
    emit("step", id=step_id, status=status, message=message,
         duration_s=None if duration_s is None else round(float(duration_s), 3),
         progress=None if progress is None else round(min(max(float(progress), 0.0), 1.0), 4),
         details=dict(details or {}))


def progress(step_id: str, fraction: float, overall: float, message: str = "") -> None:
    """``progress`` {id, fraction (within step), progress (overall), message}."""
    emit("progress", id=step_id, fraction=round(min(max(float(fraction), 0.0), 1.0), 4),
         progress=round(min(max(float(overall), 0.0), 1.0), 4), message=message)


def log(level: str, message: str) -> None:
    """``log`` {level, message}; unknown levels are mapped to ``info``."""
    emit("log", level=level if level in LOG_LEVELS else "info", message=str(message))


def result(payload: Mapping[str, Any]) -> None:
    """``result`` {status, model, report, blend, previews, metrics, warnings, validation}."""
    emit("result", **dict(payload))


def error(code: str, message: str, *, step: str | None = None, traceback: str | None = None) -> None:
    """``error`` {code, message, step|null, traceback|null}."""
    emit("error", code=code, message=message, step=step, traceback=traceback)


# ----------------------------------------------------------------------------------------------------------------
# logging bridge: logging.getLogger("img3d_amt.*") records -> ``log`` events
# ----------------------------------------------------------------------------------------------------------------

_PY_TO_PROTOCOL = {logging.DEBUG: "debug", logging.INFO: "info", logging.WARNING: "warning",
                   logging.ERROR: "error", logging.CRITICAL: "error"}


class ProtocolLogHandler(logging.Handler):
    """Forwards log records as protocol ``log`` events (message + exception summary)."""

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D401 - logging API
        try:
            message = record.getMessage()
            if record.exc_info and record.exc_info[1] is not None:
                message = f"{message}: {type(record.exc_info[1]).__name__}: {record.exc_info[1]}"
            level = _PY_TO_PROTOCOL.get(record.levelno, "error" if record.levelno > logging.ERROR else "info")
            log(level, message)
        except Exception:  # pragma: no cover - logging must never raise
            self.handleError(record)


def install_logging(level: str | int = "info") -> logging.Logger:
    """Route the ``img3d_amt`` logger hierarchy to protocol ``log`` events (idempotent)."""
    logger = logging.getLogger("img3d_amt")
    if isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)
    logger.setLevel(level)
    logger.propagate = False
    if not any(isinstance(h, ProtocolLogHandler) for h in logger.handlers):
        logger.addHandler(ProtocolLogHandler())
    return logger
