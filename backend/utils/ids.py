"""Identifier generation and validation.

IDs are ``<prefix>_<12 lowercase hex>``: ``rs_`` reference sets, ``img_`` images, ``job_`` jobs. IDs are also
used as directory/file names, so :func:`is_valid_id` must be checked before touching the filesystem with an
ID that came from a client.
"""

from __future__ import annotations

import re
import secrets
from typing import Final

REFERENCE_SET_PREFIX: Final = "rs"
IMAGE_PREFIX: Final = "img"
JOB_PREFIX: Final = "job"

_ID_HEX_LEN: Final = 12
_ID_PATTERN: Final = re.compile(r"^(?P<prefix>[a-z]+)_(?P<hex>[0-9a-f]{12})$")


def new_id(prefix: str) -> str:
    """Return a new random identifier ``<prefix>_<12 hex>``."""
    return f"{prefix}_{secrets.token_hex(_ID_HEX_LEN // 2)}"


def new_reference_set_id() -> str:
    return new_id(REFERENCE_SET_PREFIX)


def new_image_id() -> str:
    return new_id(IMAGE_PREFIX)


def new_job_id() -> str:
    return new_id(JOB_PREFIX)


def is_valid_id(value: str, prefix: str) -> bool:
    """``True`` if ``value`` is a well-formed identifier with the given prefix (safe to use in paths)."""
    match = _ID_PATTERN.match(value or "")
    return bool(match) and match.group("prefix") == prefix


def new_request_id() -> str:
    """Short random request id used for log correlation (``X-Request-ID``)."""
    return secrets.token_hex(6)
