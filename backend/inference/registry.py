"""Engine factory.

The concrete engine module is imported lazily so the API can start even when torch, CUDA or the native
TRELLIS.2 extensions are broken: in that case an :class:`~inference.unavailable.UnavailableEngine` reports the
import error through ``GET /system``.
"""

from __future__ import annotations

import logging
import traceback
from typing import TYPE_CHECKING

from inference.base import InferenceEngine
from inference.unavailable import UnavailableEngine

if TYPE_CHECKING:  # pragma: no cover
    from config.settings import Settings

logger = logging.getLogger(__name__)

SUPPORTED_ENGINES = ("trellis2",)


def create_engine(settings: Settings) -> InferenceEngine:
    """Instantiate the engine selected by ``settings.engine`` (never raises)."""
    name = settings.engine
    if name != "trellis2":
        reason = f"Unknown engine '{name}'. Supported engines: {', '.join(SUPPORTED_ENGINES)}"
        logger.error(reason)
        return UnavailableEngine(name, reason)

    try:
        from inference.trellis.engine import TrellisEngine
    except BaseException as exc:  # noqa: BLE001 - includes DLL load errors surfaced as OSError/ImportError
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        reason = f"{type(exc).__name__}: {exc}"
        logger.error(
            "Could not import the TRELLIS.2 engine; generation is unavailable: %s\n%s",
            reason,
            "".join(traceback.format_exception(exc)).rstrip(),
        )
        return UnavailableEngine(name, reason, details={"import_error": reason})

    try:
        engine = TrellisEngine(settings)
    except Exception as exc:  # noqa: BLE001
        reason = f"{type(exc).__name__}: {exc}"
        logger.exception("Could not construct the TRELLIS.2 engine")
        return UnavailableEngine(name, reason, details={"init_error": reason})
    logger.info("Inference engine '%s' created (%s)", engine.name, type(engine).__name__)
    return engine
