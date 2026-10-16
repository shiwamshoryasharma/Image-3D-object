"""Shared pydantic building blocks for domain models and API schemas."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, PlainSerializer

from utils.timeutil import isoformat_z


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


UtcDateTime = Annotated[
    datetime,
    AfterValidator(_ensure_utc),
    PlainSerializer(isoformat_z, return_type=str, when_used="json"),
]
"""Timezone-aware UTC datetime that serializes to JSON as ISO-8601 with a ``Z`` suffix (millisecond precision)."""


class DomainModel(BaseModel):
    """Base class for persisted domain models (JSON on disk)."""

    model_config = ConfigDict(extra="ignore", validate_assignment=False, use_enum_values=False)
