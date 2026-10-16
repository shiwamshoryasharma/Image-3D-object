"""Slice S4: reference-image provenance (part-aware DESIGN.md sections 1.2 S-f and 6.7).

Uploaded images keep the pre-part-aware ``set.json`` shape (no ``source`` key) and answer ``source: null``; images
imported from a text-to-image candidate carry :class:`models.reference.ImageSource`.
"""

from __future__ import annotations

import io
import json

import pytest
from PIL import Image

from models.enums import ReferenceSlot
from models.reference import ImageSource, ReferenceImage, ReferenceSet
from schemas.references import ImageSourceOut, ReferenceImageOut
from utils.timeutil import utcnow

LEGACY_IMAGE_KEYS = frozenset(
    {
        "id",
        "set_id",
        "slot",
        "label",
        "original_filename",
        "mime_type",
        "width",
        "height",
        "has_alpha",
        "size_bytes",
        "sha256",
        "is_primary",
        "created_at",
        "file_name",
        "thumbnail_name",
    }
)


def _png(size: tuple[int, int] = (256, 256)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, (120, 40, 40)).save(out, format="PNG")
    return out.getvalue()


def _source(**overrides: object) -> ImageSource:
    values: dict[str, object] = {
        "kind": "t2i",
        "image_generation_id": "ig_0123456789ab",
        "candidate_index": 1,
        "model_id": "z_image_turbo",
        "model_revision": "f332072aa78be7aecdf3ee76d5c247082da564a6",
        "license_id": "apache-2.0",
        "seed": 42,
        "final_prompt_sha256": "c0ffee" * 10 + "abcd",
    }
    values.update(overrides)
    return ImageSource.model_validate(values)


def _image(source: ImageSource | None) -> ReferenceImage:
    return ReferenceImage(
        id="img_0123456789ab",
        set_id="rs_0123456789ab",
        slot=ReferenceSlot.FRONT,
        mime_type="image/png",
        width=256,
        height=256,
        has_alpha=False,
        size_bytes=10,
        sha256="0" * 64,
        is_primary=True,
        created_at=utcnow(),
        file_name="img_0123456789ab.png",
        thumbnail_name="img_0123456789ab_thumb.webp",
        source=source,
    )


def test_upload_keeps_set_json_key_identical_and_answers_null_source(client, harness):
    set_id = client.post("/api/v1/reference-sets", json={"name": "plain"}).json()["id"]
    response = client.post(
        f"/api/v1/reference-sets/{set_id}/images",
        files={"file": ("a.png", _png(), "image/png")},
        data={"slot": "front"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["image"]["source"] is None
    assert client.get(f"/api/v1/reference-sets/{set_id}").json()["images"][0]["source"] is None
    stored = json.loads((harness.settings.runtime_dir / "uploads" / set_id / "set.json").read_text(encoding="utf-8"))
    assert set(stored["images"][0]) == LEGACY_IMAGE_KEYS


def test_domain_model_omits_none_source_and_round_trips_a_t2i_source():
    plain = _image(None)
    assert "source" not in plain.model_dump(mode="json")
    assert "source" not in json.loads(plain.model_dump_json())
    generated = _image(_source())
    dumped = generated.model_dump(mode="json")
    assert dumped["source"]["final_prompt_sha256"].startswith("c0ffee")
    assert ReferenceImage.model_validate(dumped).source == generated.source
    ref_set = ReferenceSet(id="rs_0123456789ab", created_at=utcnow(), updated_at=utcnow(), images=[plain, generated])
    images = ref_set.model_dump(mode="json")["images"]
    assert "source" not in images[0] and images[1]["source"]["kind"] == "t2i"


def test_legacy_set_json_without_source_still_loads():
    legacy = _image(None).model_dump(mode="json")
    assert ReferenceImage.model_validate(legacy).source is None


def test_api_shape_hides_the_prompt_hash():
    out = ReferenceImageOut.from_domain(_image(_source())).model_dump(mode="json")
    assert out["source"] == {
        "kind": "t2i",
        "image_generation_id": "ig_0123456789ab",
        "candidate_index": 1,
        "model_id": "z_image_turbo",
        "model_revision": "f332072aa78be7aecdf3ee76d5c247082da564a6",
        "license_id": "apache-2.0",
        "seed": 42,
    }
    assert set(ImageSourceOut.model_fields) == set(out["source"])
    assert ReferenceImageOut.from_domain(_image(None)).model_dump(mode="json")["source"] is None


def test_image_source_rejects_unknown_kinds():
    with pytest.raises(ValueError):
        _source(kind="upload")


async def _upload_with_source(service, set_id: str, source: ImageSource):
    return await service.upload_image(set_id, data=_png(), filename="x.png", slot=ReferenceSlot.FRONT, source=source)


def test_reference_service_stores_the_source_keyword(harness):
    service = harness.container.reference_service
    ref_set = service.create_set("generated")
    image, updated = harness.client.portal.call(_upload_with_source, service, ref_set.id, _source())
    assert image.source == _source()
    stored = json.loads(
        (harness.settings.runtime_dir / "uploads" / ref_set.id / "set.json").read_text(encoding="utf-8")
    )
    assert stored["images"][0]["source"]["image_generation_id"] == "ig_0123456789ab"
    assert updated.get_image(image.id).source == _source()
