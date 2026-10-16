"""Atomic JSON persistence helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.exceptions import StorageError
from utils.files import atomic_write_bytes, retry_os


def write_json_atomic(path: Path, data: Any) -> None:
    """Serialize ``data`` as UTF-8 JSON and write it atomically (temp file + ``os.replace``)."""
    try:
        payload = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False).encode("utf-8")
        atomic_write_bytes(path, payload)
    except (OSError, TypeError, ValueError) as exc:
        raise StorageError(f"Could not write {path.name}", details={"path": path.name}) from exc


def read_json(path: Path) -> Any:
    """Read a JSON file. Raises ``FileNotFoundError`` if missing and ``StorageError`` if corrupt."""

    def _read() -> bytes:
        return path.read_bytes()

    raw = retry_os(_read)
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StorageError(f"Corrupt metadata file {path.name}", details={"path": path.name}) from exc
