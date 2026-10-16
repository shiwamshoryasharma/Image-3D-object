"""Reference sets and reference images (contract 7.3)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, File, Form, Response, UploadFile, status
from fastapi.responses import FileResponse

from api.deps import AuthGuard, ContainerDep, FileAuthGuard, ReferenceServiceDep
from api.upload_route import LimitedUploadRoute
from core.exceptions import PayloadTooLargeError
from models.enums import ReferenceSlot
from models.reference import ReferenceSet
from schemas.common import ERROR_RESPONSES
from schemas.references import (
    MAX_LABEL_LENGTH,
    CreateReferenceSetRequest,
    ReferenceImageOut,
    ReferenceSetList,
    ReferenceSetOut,
    SetPrimaryRequest,
    UpdateReferenceImageRequest,
    UploadImageResponse,
)

PREFIX = "/reference-sets"
TAGS: list[str] = ["references"]
_READ_CHUNK = 1024 * 1024
_IMMUTABLE_CACHE = "private, max-age=31536000, immutable"

router = APIRouter(prefix=PREFIX, tags=TAGS, dependencies=[AuthGuard], responses=ERROR_RESPONSES)
upload_router = APIRouter(
    prefix=PREFIX, tags=TAGS, dependencies=[AuthGuard], route_class=LimitedUploadRoute, responses=ERROR_RESPONSES
)
files_router = APIRouter(prefix=PREFIX, tags=TAGS, dependencies=[FileAuthGuard], responses=ERROR_RESPONSES)


def _out(container: ContainerDep, ref_set: ReferenceSet) -> ReferenceSetOut:
    return ReferenceSetOut.from_domain(ref_set, max_detail_images=container.settings.max_detail_images)


# --------------------------------------------------------------------------------------------------
# Sets
# --------------------------------------------------------------------------------------------------


@router.post("", response_model=ReferenceSetOut, status_code=status.HTTP_201_CREATED)
async def create_reference_set(
    container: ContainerDep,
    service: ReferenceServiceDep,
    body: Annotated[CreateReferenceSetRequest | None, Body()] = None,
) -> ReferenceSetOut:
    ref_set = service.create_set(body.name if body else None)
    return _out(container, ref_set)


@router.get("", response_model=ReferenceSetList)
async def list_reference_sets(container: ContainerDep, service: ReferenceServiceDep) -> ReferenceSetList:
    return ReferenceSetList(items=[_out(container, s) for s in service.list_sets()])


@router.get("/{set_id}", response_model=ReferenceSetOut)
async def get_reference_set(set_id: str, container: ContainerDep, service: ReferenceServiceDep) -> ReferenceSetOut:
    return _out(container, service.get_set(set_id))


@router.delete("/{set_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_reference_set(set_id: str, service: ReferenceServiceDep) -> Response:
    await service.delete_set(set_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------------------------------
# Images
# --------------------------------------------------------------------------------------------------


async def _read_limited(upload: UploadFile, max_bytes: int, max_mb: int) -> bytes:
    """Read an uploaded file in chunks, failing as soon as it exceeds ``max_bytes``."""
    chunks: list[bytes] = []
    total = 0
    while chunk := await upload.read(_READ_CHUNK):
        total += len(chunk)
        if total > max_bytes:
            raise PayloadTooLargeError(
                f"Upload exceeds the maximum size of {max_mb} MB", details={"max_upload_mb": max_mb}
            )
        chunks.append(chunk)
    return b"".join(chunks)


@upload_router.post("/{set_id}/images", response_model=UploadImageResponse, status_code=status.HTTP_201_CREATED)
async def upload_reference_image(
    set_id: str,
    container: ContainerDep,
    service: ReferenceServiceDep,
    file: Annotated[UploadFile, File(description="PNG, JPEG or WEBP image")],
    slot: Annotated[ReferenceSlot, Form()],
    label: Annotated[str | None, Form(max_length=MAX_LABEL_LENGTH)] = None,
    make_primary: Annotated[bool, Form()] = False,
) -> UploadImageResponse:
    service.get_set(set_id)  # 404 before loading the upload into memory
    settings = container.settings
    data = await _read_limited(file, settings.max_upload_bytes, settings.max_upload_mb)
    image, ref_set = await service.upload_image(
        set_id, data=data, filename=file.filename, slot=slot, label=label, make_primary=make_primary
    )
    return UploadImageResponse(image=ReferenceImageOut.from_domain(image), set=_out(container, ref_set))


@router.patch("/{set_id}/images/{image_id}", response_model=ReferenceSetOut)
async def update_reference_image(
    set_id: str,
    image_id: str,
    body: UpdateReferenceImageRequest,
    container: ContainerDep,
    service: ReferenceServiceDep,
) -> ReferenceSetOut:
    return _out(container, await service.update_image(set_id, image_id, body))


@router.delete("/{set_id}/images/{image_id}", response_model=ReferenceSetOut)
async def delete_reference_image(
    set_id: str, image_id: str, container: ContainerDep, service: ReferenceServiceDep
) -> ReferenceSetOut:
    return _out(container, await service.delete_image(set_id, image_id))


@router.put("/{set_id}/primary", response_model=ReferenceSetOut)
async def set_primary_image(
    set_id: str, body: SetPrimaryRequest, container: ContainerDep, service: ReferenceServiceDep
) -> ReferenceSetOut:
    return _out(container, await service.set_primary(set_id, body.image_id))


# --------------------------------------------------------------------------------------------------
# Files (header or ?token= auth)
# --------------------------------------------------------------------------------------------------


@files_router.get("/{set_id}/images/{image_id}/file", response_class=FileResponse)
async def get_reference_image_file(set_id: str, image_id: str, service: ReferenceServiceDep) -> FileResponse:
    stored = service.image_file(set_id, image_id)
    return FileResponse(
        stored.path,
        media_type=stored.media_type,
        filename=stored.filename,
        content_disposition_type="inline",
        headers={"Cache-Control": _IMMUTABLE_CACHE},
    )


@files_router.get("/{set_id}/images/{image_id}/thumbnail", response_class=FileResponse)
async def get_reference_image_thumbnail(set_id: str, image_id: str, service: ReferenceServiceDep) -> FileResponse:
    stored = service.thumbnail_file(set_id, image_id)
    return FileResponse(stored.path, media_type=stored.media_type, headers={"Cache-Control": _IMMUTABLE_CACHE})
