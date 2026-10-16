"""UTC time helpers. All timestamps in the app are timezone-aware UTC and serialize as ISO-8601 ``...Z``."""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    """Current UTC time, truncated to milliseconds (keeps JSON timestamps compact and stable)."""
    now = datetime.now(UTC)
    return now.replace(microsecond=(now.microsecond // 1000) * 1000)


def isoformat_z(value: datetime | None) -> str | None:
    """Serialize a datetime as ISO-8601 with a ``Z`` suffix (naive values are assumed to be UTC)."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    value = value.astimezone(UTC)
    text = value.isoformat(timespec="milliseconds")
    return text.replace("+00:00", "Z")


def utcnow_iso() -> str:
    """Current UTC time as ISO-8601 ``...Z`` string."""
    return isoformat_z(utcnow())  # type: ignore[return-value]


def parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp (accepts a ``Z`` suffix). Returns an aware UTC datetime."""
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
