"""Application exception hierarchy.

Every error that crosses the API boundary is an :class:`AppError` (or is converted into one by the exception
handlers in :mod:`core.error_handlers`). The response body shape is::

    {"error": {"code": str, "message": str, "details": Any | None, "request_id": str | None}}
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base class for all expected application errors."""

    status_code: int = 500
    code: str = "internal_error"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"{type(self).__name__}(code={self.code!r}, status={self.status_code}, message={self.message!r})"


class ValidationAppError(AppError):
    status_code = 422
    code = "validation_error"


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class PayloadTooLargeError(AppError):
    status_code = 413
    code = "payload_too_large"


class UnsupportedMediaTypeError(AppError):
    status_code = 415
    code = "unsupported_media_type"


class AuthenticationError(AppError):
    status_code = 401
    code = "unauthorized"


class QueueFullError(AppError):
    status_code = 429
    code = "queue_full"


class EngineUnavailableError(AppError):
    """The inference engine is not loaded, failed to load, or its runtime is missing."""

    status_code = 503
    code = "engine_unavailable"


class EngineError(AppError):
    """The inference engine failed while generating."""

    status_code = 500
    code = "engine_error"


class JobCancelledError(AppError):
    """Raised inside the worker/engine to abort a job whose cancellation was requested."""

    status_code = 409
    code = "job_cancelled"

    def __init__(self, message: str = "Job was cancelled", **kwargs: Any) -> None:
        super().__init__(message, **kwargs)


class StorageError(AppError):
    status_code = 500
    code = "storage_error"
