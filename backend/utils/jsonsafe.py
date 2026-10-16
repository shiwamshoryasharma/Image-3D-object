"""Coerce arbitrary (engine-provided) structures into plain, strictly JSON-compliant values."""

from __future__ import annotations

import json
import math
from typing import Any


def to_jsonable(value: Any) -> Any:
    """Round-trip through JSON, stringifying unknown objects (paths, numpy scalars, ...).

    Non-finite floats (NaN/Infinity) become ``None``: the API serializes responses with ``allow_nan=False``, so a
    single NaN coming from an engine would otherwise turn every response containing it into a 500.
    """
    if value is None:
        return None
    try:
        return json.loads(json.dumps(value, default=_default), parse_constant=lambda _constant: None)
    except (TypeError, ValueError):
        return str(value)


def finite_or(value: Any, default: float = 0.0) -> float:
    """``float(value)`` if it is a finite number, else ``default``."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _default(obj: Any) -> Any:
    item = getattr(obj, "item", None)  # numpy scalars
    if callable(item):
        try:
            return item()
        except Exception:  # noqa: BLE001
            pass
    tolist = getattr(obj, "tolist", None)  # numpy arrays
    if callable(tolist):
        try:
            return tolist()
        except Exception:  # noqa: BLE001
            pass
    if isinstance(obj, (set, frozenset, tuple)):
        return list(obj)
    return str(obj)
