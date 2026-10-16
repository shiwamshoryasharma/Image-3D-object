"""Reference set / reference image schemas (contract 7.3)."""

from __future__ import annotations

from pydantic import Field

from models.enums import SINGLE_SLOTS, SLOT_DISPLAY_ORDER, ReferenceSlot
from models.reference import ReferenceImage, ReferenceSet
from models.types import UtcDateTime
from schemas.common import API_V1_PREFIX, ApiModel, RequestModel

MAX_NAME_LENGTH = 200
MAX_LABEL_LENGTH = 200


class ReferenceImageUrls(ApiModel):
    file: str
    thumbnail: str


class ReferenceImageOut(ApiModel):
    id: str
    set_id: str
    slot: ReferenceSlot
    label: str | None
    original_filename: str | None
    mime_type: str
    width: int
    height: int
    has_alpha: bool
    size_bytes: int
    sha256: str
    is_primary: bool
    created_at: UtcDateTime
    urls: ReferenceImageUrls

    @classmethod
    def from_domain(cls, image: ReferenceImage) -> ReferenceImageOut:
        base = f"{API_V1_PREFIX}/reference-sets/{image.set_id}/images/{image.id}"
        return cls(
            id=image.id,
            set_id=image.set_id,
            slot=image.slot,
            label=image.label,
            original_filename=image.original_filename,
            mime_type=image.mime_type,
            width=image.width,
            height=image.height,
            has_alpha=image.has_alpha,
            size_bytes=image.size_bytes,
            sha256=image.sha256,
            is_primary=image.is_primary,
            created_at=image.created_at,
            urls=ReferenceImageUrls(file=f"{base}/file", thumbnail=f"{base}/thumbnail"),
        )


class ReferenceSetOut(ApiModel):
    id: str
    name: str | None
    created_at: UtcDateTime
    updated_at: UtcDateTime
    primary_image_id: str | None
    images: list[ReferenceImageOut]
    slot_limits: dict[str, int]

    @classmethod
    def from_domain(cls, ref_set: ReferenceSet, *, max_detail_images: int) -> ReferenceSetOut:
        return cls(
            id=ref_set.id,
            name=ref_set.name,
            created_at=ref_set.created_at,
            updated_at=ref_set.updated_at,
            primary_image_id=ref_set.primary_image_id,
            images=[ReferenceImageOut.from_domain(img) for img in ref_set.ordered_images()],
            slot_limits=slot_limits(max_detail_images),
        )


def slot_limits(max_detail_images: int) -> dict[str, int]:
    return {
        str(slot): (1 if slot in SINGLE_SLOTS else max_detail_images) for slot in SLOT_DISPLAY_ORDER
    }


class ReferenceSetList(ApiModel):
    items: list[ReferenceSetOut]


class CreateReferenceSetRequest(RequestModel):
    name: str | None = Field(default=None, max_length=MAX_NAME_LENGTH)


class UpdateReferenceImageRequest(RequestModel):
    slot: ReferenceSlot | None = None
    label: str | None = Field(default=None, max_length=MAX_LABEL_LENGTH)


class SetPrimaryRequest(RequestModel):
    image_id: str


class UploadImageResponse(ApiModel):
    image: ReferenceImageOut
    set: ReferenceSetOut
