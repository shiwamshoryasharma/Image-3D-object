"""Structured logging.

* Console: human-readable (colored on a TTY) or JSON (``log_json_console``).
* ``logs/img3d.log``: rotating JSON lines (10 MB x 5).
* ``logs/error.log``: rotating, WARNING and above, human-readable with tracebacks.
* Context variables ``request_id`` / ``job_id`` are attached to every record.
* Known secret values (HF token, UI password, auth secret) are redacted from every formatted line.
* Uvicorn's loggers propagate into the root logger (their own handlers are removed). ``uvicorn.access`` is
  silenced because the request middleware writes its own access log without query strings (tokens).
"""

from __future__ import annotations

import contextlib
import json
import logging
import logging.handlers
import os
import re
import sys
import traceback
from collections.abc import Iterable, Iterator
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:  # pragma: no cover
    from config.settings import Settings

request_id_var: ContextVar[str | None] = ContextVar("img3d_request_id", default=None)
job_id_var: ContextVar[str | None] = ContextVar("img3d_job_id", default=None)

APP_LOG_FILE = "img3d.log"
ERROR_LOG_FILE = "error.log"
_MAX_BYTES = 10 * 1024 * 1024
_BACKUP_COUNT = 5
_HANDLER_MARK = "_img3d_handler"

_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.asgi")
_NOISY_LOGGERS = {
    "uvicorn.access": logging.WARNING,
    "httpx": logging.WARNING,
    "httpcore": logging.WARNING,
    "PIL": logging.WARNING,
    "multipart": logging.WARNING,
    "python_multipart": logging.WARNING,
    "watchfiles": logging.WARNING,
    "asyncio": logging.WARNING,
    "websockets": logging.WARNING,
    "trimesh": logging.WARNING,
    "urllib3": logging.WARNING,
    "huggingface_hub": logging.WARNING,
}

# Attributes present on every LogRecord; anything else came from ``extra=`` and is emitted as a field.
_STANDARD_RECORD_ATTRS = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", None, None)).keys()
    | {"message", "asctime", "request_id", "job_id", "taskName", "color_message"}  # color_message: uvicorn
)


# --------------------------------------------------------------------------------------------------
# Secret redaction
# --------------------------------------------------------------------------------------------------


REDACTED = "***"

# Bearer tokens travel in ``?token=`` for <img>/GLTFLoader/WebSocket URLs (contract 7.1). Uvicorn logs the WebSocket
# handshake line *with* the query string, so token values are scrubbed from every formatted line.
_TOKEN_QUERY_RE = re.compile(r"(?i)([?&](?:token|access_token)=)[^&#\s\"'\\]+")
_BEARER_RE = re.compile(r"(?i)(\bbearer\s+)[A-Za-z0-9._~+/=-]{8,}")


class SecretRegistry:
    """Process-wide set of secret strings that must never appear in log output."""

    _secrets: ClassVar[set[str]] = set()

    @classmethod
    def register(cls, values: Iterable[str | None]) -> None:
        for value in values:
            if value and len(value) >= 4:
                cls._secrets.add(value)
                # JSON log lines escape quotes/backslashes/control characters; redact that spelling too.
                escaped = json.dumps(value, ensure_ascii=False)[1:-1]
                if escaped != value:
                    cls._secrets.add(escaped)

    @classmethod
    def clear(cls) -> None:
        cls._secrets.clear()

    @classmethod
    def redact(cls, text: str) -> str:
        # Longest first, so a secret that contains another registered secret is removed as a whole.
        for secret in sorted(cls._secrets, key=len, reverse=True):
            if secret in text:
                text = text.replace(secret, REDACTED)
        text = _TOKEN_QUERY_RE.sub(rf"\1{REDACTED}", text)
        return _BEARER_RE.sub(rf"\1{REDACTED}", text)


# --------------------------------------------------------------------------------------------------
# Filters & formatters
# --------------------------------------------------------------------------------------------------


class ContextFilter(logging.Filter):
    """Attach ``request_id`` and ``job_id`` from context variables (unless given via ``extra``)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not getattr(record, "request_id", None):
            record.request_id = request_id_var.get()
        if not getattr(record, "job_id", None):
            record.job_id = job_id_var.get()
        return True


def _extra_fields(record: logging.LogRecord) -> dict[str, Any]:
    return {k: v for k, v in record.__dict__.items() if k not in _STANDARD_RECORD_ATTRS and not k.startswith("_")}


class JsonFormatter(logging.Formatter):
    """One JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", None),
            "job_id": getattr(record, "job_id", None),
            "thread": record.threadName,
        }
        extra = _extra_fields(record)
        if extra:
            payload["extra"] = extra
        if record.exc_info:
            payload["exception"] = "".join(traceback.format_exception(*record.exc_info)).rstrip()
        elif record.exc_text:
            payload["exception"] = record.exc_text
        if record.stack_info:
            payload["stack"] = record.stack_info
        text = json.dumps(payload, ensure_ascii=False, default=str)
        return SecretRegistry.redact(text)


class ConsoleFormatter(logging.Formatter):
    """Compact human-readable format, optionally colored."""

    _COLORS: ClassVar[dict[int, str]] = {
        logging.DEBUG: "\x1b[2m",
        logging.INFO: "\x1b[36m",
        logging.WARNING: "\x1b[33m",
        logging.ERROR: "\x1b[31m",
        logging.CRITICAL: "\x1b[1;31m",
    }
    _RESET = "\x1b[0m"
    _DIM = "\x1b[2m"

    def __init__(self, *, use_colors: bool) -> None:
        super().__init__()
        self.use_colors = use_colors

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created).strftime("%H:%M:%S.%f")[:-3]
        level = record.levelname
        context = []
        if getattr(record, "request_id", None):
            context.append(f"rid={record.request_id}")
        if getattr(record, "job_id", None):
            context.append(f"job={record.job_id}")
        extra = _extra_fields(record)
        if extra:
            context.extend(f"{k}={v}" for k, v in extra.items())
        suffix = f" ({', '.join(context)})" if context else ""
        message = record.getMessage()
        if self.use_colors:
            color = self._COLORS.get(record.levelno, "")
            line = f"{self._DIM}{ts}{self._RESET} {color}{level:<8}{self._RESET} {self._DIM}[{record.name}]{self._RESET} {message}{self._DIM}{suffix}{self._RESET}"
        else:
            line = f"{ts} {level:<8} [{record.name}] {message}{suffix}"
        if record.exc_info:
            line += "\n" + "".join(traceback.format_exception(*record.exc_info)).rstrip()
        elif record.exc_text:
            line += "\n" + record.exc_text
        return SecretRegistry.redact(line)


class PlainFileFormatter(logging.Formatter):
    """Human-readable format with full timestamps for ``error.log``."""

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        context = []
        if getattr(record, "request_id", None):
            context.append(f"rid={record.request_id}")
        if getattr(record, "job_id", None):
            context.append(f"job={record.job_id}")
        suffix = f" ({', '.join(context)})" if context else ""
        line = f"{ts} {record.levelname:<8} [{record.name}] [{record.threadName}] {record.getMessage()}{suffix}"
        if record.exc_info:
            line += "\n" + "".join(traceback.format_exception(*record.exc_info)).rstrip()
        elif record.exc_text:
            line += "\n" + record.exc_text
        return SecretRegistry.redact(line)


# --------------------------------------------------------------------------------------------------
# Setup
# --------------------------------------------------------------------------------------------------


def _stream_supports_color(stream: Any) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    isatty = getattr(stream, "isatty", None)
    if not callable(isatty) or not isatty():
        return False
    if sys.platform == "win32":
        try:
            import colorama  # optional (installed with uvicorn[standard] on Windows)

            colorama.just_fix_windows_console()
        except Exception:  # noqa: BLE001 - modern Windows terminals support ANSI anyway
            pass
    return True


def _mark(handler: logging.Handler) -> logging.Handler:
    setattr(handler, _HANDLER_MARK, True)
    handler.addFilter(ContextFilter())
    return handler


def _remove_own_handlers(root: logging.Logger) -> None:
    for handler in list(root.handlers):
        if getattr(handler, _HANDLER_MARK, False):
            root.removeHandler(handler)
            with contextlib.suppress(Exception):
                handler.close()


def configure_logging(settings: Settings, *, level: str | None = None) -> None:
    """(Re)configure process logging from ``settings``. Idempotent; safe to call more than once."""
    log_level = (level or settings.log_level).upper()
    log_dir: Path = settings.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)

    SecretRegistry.register(
        secret.get_secret_value() if secret is not None else None
        for secret in (settings.hf_token, settings.ui_password, settings.auth_secret)
    )

    root = logging.getLogger()
    _remove_own_handlers(root)
    root.setLevel(log_level)

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(
        JsonFormatter() if settings.log_json_console else ConsoleFormatter(use_colors=_stream_supports_color(sys.stderr))
    )
    root.addHandler(_mark(console))

    app_file = logging.handlers.RotatingFileHandler(
        log_dir / APP_LOG_FILE, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8", delay=True
    )
    app_file.setFormatter(JsonFormatter())
    root.addHandler(_mark(app_file))

    error_file = logging.handlers.RotatingFileHandler(
        log_dir / ERROR_LOG_FILE, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8", delay=True
    )
    error_file.setLevel(logging.WARNING)
    error_file.setFormatter(PlainFileFormatter())
    root.addHandler(_mark(error_file))

    for name in _UVICORN_LOGGERS:
        uv_logger = logging.getLogger(name)
        uv_logger.handlers.clear()
        uv_logger.propagate = True
        uv_logger.setLevel(logging.NOTSET)
    root_level = logging.getLevelNamesMapping()[log_level]
    for name, noisy_level in _NOISY_LOGGERS.items():
        noisy = logging.getLogger(name)
        noisy.handlers.clear()
        noisy.propagate = True
        noisy.setLevel(max(noisy_level, root_level))

    logging.captureWarnings(True)


def flush_logging() -> None:
    """Flush every handler on the root logger."""
    for handler in logging.getLogger().handlers:
        with contextlib.suppress(Exception):
            handler.flush()


def shutdown_logging() -> None:
    """Flush and close the handlers installed by :func:`configure_logging`."""
    flush_logging()
    _remove_own_handlers(logging.getLogger())


@contextlib.contextmanager
def bind_job_id(job_id: str | None) -> Iterator[None]:
    """Temporarily set the ``job_id`` logging context (works in worker threads too)."""
    token = job_id_var.set(job_id)
    try:
        yield
    finally:
        job_id_var.reset(token)
