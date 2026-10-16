"""Upload validation by actually decoding the image with Pillow (never trusting the extension or MIME type)."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from typing import Final

from PIL import Image, UnidentifiedImageError

from core.exceptions import AppError, PayloadTooLargeError, UnsupportedMediaTypeError, ValidationAppError

# Pillow format name -> (mime type, stored file extension)
ALLOWED_FORMATS: Final[dict[str, tuple[str, str]]] = {
    "PNG": ("image/png", ".png"),
    "JPEG": ("image/jpeg", ".jpg"),
    "WEBP": ("image/webp", ".webp"),
}
ALLOWED_MIME_TYPES: Final[tuple[str, ...]] = tuple(mime for mime, _ in ALLOWED_FORMATS.values())
PILLOW_FORMATS: Final[tuple[str, ...]] = tuple(ALLOWED_FORMATS)
"""Passed to ``Image.open(formats=...)`` so untrusted bytes are only ever parsed by these three decoders."""

_EXIF_ORIENTATION_TAG: Final = 0x0112
_ROTATED_ORIENTATIONS: Final = frozenset({5, 6, 7, 8})


@dataclass(frozen=True, slots=True)
class ImageInfo:
    """Facts about a validated image. ``width``/``height`` are display dimensions (EXIF orientation applied)."""

    format: str
    mime_type: str
    extension: str
    width: int
    height: int
    has_alpha: bool
    mode: str


def configure_pillow(max_image_pixels: int) -> None:
    """Apply the global Pillow decompression-bomb limit (Pillow errors at 2x this value)."""
    Image.MAX_IMAGE_PIXELS = max_image_pixels


def validate_image_bytes(data: bytes, *, min_side: int, max_pixels: int) -> ImageInfo:
    """Validate an uploaded image.

    Raises:
        UnsupportedMediaTypeError: not decodable, corrupt/truncated, or not PNG/JPEG/WEBP (415).
        PayloadTooLargeError: pixel count above ``max_pixels`` (413).
        ValidationAppError: smaller than ``min_side`` on either side (422).
    """
    if not data:
        raise UnsupportedMediaTypeError("The uploaded file is empty")
    try:
        with Image.open(BytesIO(data), formats=PILLOW_FORMATS) as probe:
            fmt = (probe.format or "").upper()
            if fmt not in ALLOWED_FORMATS:  # pragma: no cover - guaranteed by formats=, kept as a safety net
                raise UnsupportedMediaTypeError(
                    f"Unsupported image format '{fmt or 'unknown'}'. Allowed formats: PNG, JPEG, WEBP",
                    details={"format": fmt or None, "allowed_mime_types": list(ALLOWED_MIME_TYPES)},
                )
            raw_width, raw_height = probe.size
            if raw_width * raw_height > max_pixels:
                raise PayloadTooLargeError(
                    f"Image is too large ({raw_width}x{raw_height} pixels; max {max_pixels} pixels)",
                    details={"width": raw_width, "height": raw_height, "max_pixels": max_pixels},
                )
            probe.verify()

        # verify() leaves the image unusable; reopen and fully decode to catch truncated pixel data.
        with Image.open(BytesIO(data), formats=PILLOW_FORMATS) as img:
            img.load()
            orientation = _exif_orientation(img)
            has_alpha = bool(img.has_transparency_data)
            mode = img.mode
            width, height = img.size
    except AppError:
        raise
    except Image.DecompressionBombError as exc:
        raise PayloadTooLargeError(
            "Image has too many pixels", details={"max_pixels": max_pixels}
        ) from exc
    except UnidentifiedImageError as exc:
        raise UnsupportedMediaTypeError(
            "The uploaded file is not a supported image. Allowed formats: PNG, JPEG, WEBP",
            details={"allowed_mime_types": list(ALLOWED_MIME_TYPES)},
        ) from exc
    except (OSError, SyntaxError, ValueError, EOFError, MemoryError) as exc:
        raise UnsupportedMediaTypeError("The uploaded image is corrupt or truncated") from exc

    if orientation in _ROTATED_ORIENTATIONS:
        width, height = height, width
    if min(width, height) < min_side:
        raise ValidationAppError(
            f"Image is too small ({width}x{height}); each side must be at least {min_side} pixels",
            details={"width": width, "height": height, "min_image_side": min_side},
        )

    mime_type, extension = ALLOWED_FORMATS[fmt]
    return ImageInfo(
        format=fmt,
        mime_type=mime_type,
        extension=extension,
        width=width,
        height=height,
        has_alpha=has_alpha,
        mode=mode,
    )


def _exif_orientation(img: Image.Image) -> int:
    try:
        return int(img.getexif().get(_EXIF_ORIENTATION_TAG, 1) or 1)
    except Exception:  # noqa: BLE001 - malformed EXIF must not reject an otherwise valid image
        return 1
