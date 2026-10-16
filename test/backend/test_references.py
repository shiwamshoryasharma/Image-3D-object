"""Reference sets: CRUD, upload validation, slot rules, primary selection, files."""

from __future__ import annotations

from io import BytesIO

from helpers import assert_error, create_set, image_bytes, png_bytes, upload
from PIL import Image


def test_reference_set_crud(client):
    created = create_set(client, name="  Alice  ")
    assert created["id"].startswith("rs_") and len(created["id"]) == 15
    assert created["name"] == "Alice"
    assert created["images"] == []
    assert created["primary_image_id"] is None
    assert created["slot_limits"] == {
        "front": 1, "back": 1, "left": 1, "right": 1, "face_front": 1, "face_side": 1, "detail": 8,
    }
    assert created["created_at"].endswith("Z")

    second = create_set(client, name=None)
    items = client.get("/api/v1/reference-sets").json()["items"]
    assert [s["id"] for s in items][:2] == [second["id"], created["id"]]  # newest first

    assert client.get(f"/api/v1/reference-sets/{created['id']}").json()["id"] == created["id"]
    assert client.delete(f"/api/v1/reference-sets/{created['id']}").status_code == 204
    assert_error(client.get(f"/api/v1/reference-sets/{created['id']}"), 404, "not_found")
    assert_error(client.delete(f"/api/v1/reference-sets/{created['id']}"), 404, "not_found")


def test_create_set_without_body(client):
    response = client.post("/api/v1/reference-sets")
    assert response.status_code == 201
    assert response.json()["name"] is None


def test_create_set_rejects_unknown_fields(client):
    assert_error(client.post("/api/v1/reference-sets", json={"nme": "x"}), 422, "validation_error")


def test_upload_png_jpeg_webp(client, harness):
    ref_set = create_set(client)
    cases = [
        ("front", image_bytes("PNG", (200, 100), "RGBA", (1, 2, 3, 100)), "image/png", True, ".png"),
        ("back", image_bytes("JPEG", (120, 300)), "image/jpeg", False, ".jpg"),
        ("left", image_bytes("WEBP", (90, 90)), "image/webp", False, ".webp"),
    ]
    for slot, data, mime, has_alpha, ext in cases:
        response = upload(client, ref_set["id"], data, slot=slot, filename=f"C:\\fakepath\\pic{ext}")
        assert response.status_code == 201, response.text
        image = response.json()["image"]
        assert image["mime_type"] == mime
        assert image["has_alpha"] is has_alpha
        assert image["slot"] == slot
        assert image["size_bytes"] == len(data)
        assert image["original_filename"] == f"pic{ext}"
        assert len(image["sha256"]) == 64
        assert image["urls"]["file"] == f"/api/v1/reference-sets/{ref_set['id']}/images/{image['id']}/file"
        stored = harness.settings.runtime_dir / "uploads" / ref_set["id"] / f"{image['id']}{ext}"
        assert stored.read_bytes() == data  # original bytes are stored untouched

    full = client.get(f"/api/v1/reference-sets/{ref_set['id']}").json()
    assert [img["slot"] for img in full["images"]] == ["front", "back", "left"]
    assert full["images"][0]["width"] == 200 and full["images"][0]["height"] == 100


def test_upload_rejects_non_images_with_415(client):
    ref_set = create_set(client)
    assert_error(upload(client, ref_set["id"], b"definitely not an image"), 415, "unsupported_media_type")
    assert_error(upload(client, ref_set["id"], b""), 415, "unsupported_media_type")
    # A real image in a format we do not accept (validated by decoding, not by extension/MIME).
    gif = image_bytes("GIF", (100, 100), "P", 1)
    assert_error(upload(client, ref_set["id"], gif, filename="x.png"), 415, "unsupported_media_type")
    # Truncated PNG.
    truncated = png_bytes((300, 300))[:200]
    assert_error(upload(client, ref_set["id"], truncated), 415, "unsupported_media_type")


def test_upload_too_large_413(make_app):
    h = make_app(max_upload_mb=1)
    ref_set = create_set(h.client)
    big = image_bytes("PNG", (64, 64)) + b"\0" * (1024 * 1024 + 10)
    error = assert_error(upload(h.client, ref_set["id"], big), 413, "payload_too_large")
    assert "1 MB" in error["message"]


def test_upload_too_large_streamed_without_content_length(make_app):
    h = make_app(max_upload_mb=1)
    ref_set = create_set(h.client)
    boundary = "img3dboundary"
    head = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"slot\"\r\n\r\nfront\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.png\"\r\n"
        "Content-Type: image/png\r\n\r\n"
    ).encode()

    def body():
        yield head
        for _ in range(40):  # 40 x 64 KiB = 2.5 MiB, chunked (no Content-Length)
            yield b"\0" * 65536
        yield f"\r\n--{boundary}--\r\n".encode()

    response = h.client.post(
        f"/api/v1/reference-sets/{ref_set['id']}/images",
        content=body(),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    assert_error(response, 413, "payload_too_large")


def test_upload_declared_content_length_too_large(make_app):
    h = make_app(max_upload_mb=1)
    ref_set = create_set(h.client)
    response = h.client.post(
        f"/api/v1/reference-sets/{ref_set['id']}/images",
        content=b"x",
        headers={"Content-Type": "multipart/form-data; boundary=x", "Content-Length": str(50 * 1024 * 1024)},
    )
    assert_error(response, 413, "payload_too_large")


def test_upload_bad_slot_and_missing_fields_422(client):
    ref_set = create_set(client)
    error = assert_error(upload(client, ref_set["id"], png_bytes(), slot="top"), 422, "validation_error")
    assert any(item["loc"][-1] == "slot" for item in error["details"])
    response = client.post(f"/api/v1/reference-sets/{ref_set['id']}/images", data={"slot": "front"})
    assert_error(response, 422, "validation_error")


def test_upload_too_small_422(client):
    ref_set = create_set(client)
    error = assert_error(upload(client, ref_set["id"], png_bytes((63, 500))), 422, "validation_error")
    assert error["details"]["min_image_side"] == 64


def test_upload_too_many_pixels_413(make_app):
    h = make_app(max_image_pixels=1_000_000)
    ref_set = create_set(h.client)
    assert_error(upload(h.client, ref_set["id"], png_bytes((1100, 1000))), 413, "payload_too_large")


def test_upload_to_unknown_set_404(client):
    assert_error(upload(client, "rs_000000000000", png_bytes()), 404, "not_found")


def test_single_slot_replace_keeps_primary_and_deletes_old_file(client, harness):
    ref_set = create_set(client)
    first = upload(client, ref_set["id"], png_bytes(color=(1, 1, 1)), slot="front").json()["image"]
    assert first["is_primary"] is True
    old_file = harness.settings.runtime_dir / "uploads" / ref_set["id"] / f"{first['id']}.png"
    assert old_file.exists()

    body = upload(client, ref_set["id"], png_bytes(color=(9, 9, 9)), slot="front").json()
    second = body["image"]
    assert second["id"] != first["id"]
    assert second["is_primary"] is True
    assert [img["id"] for img in body["set"]["images"]] == [second["id"]]
    assert body["set"]["primary_image_id"] == second["id"]
    assert not old_file.exists()
    assert_error(client.get(first["urls"]["file"]), 404, "not_found")


def test_detail_slot_limit(make_app):
    h = make_app(max_detail_images=2)
    ref_set = create_set(h.client)
    for _ in range(2):
        assert upload(h.client, ref_set["id"], png_bytes(), slot="detail").status_code == 201
    error = assert_error(upload(h.client, ref_set["id"], png_bytes(), slot="detail"), 422, "validation_error")
    assert error["details"]["max_detail_images"] == 2


def test_primary_rules_and_auto_promotion(client):
    ref_set = create_set(client)
    set_id = ref_set["id"]
    detail = upload(client, set_id, png_bytes(), slot="detail").json()["image"]
    assert detail["is_primary"] is True  # first upload becomes primary
    back = upload(client, set_id, png_bytes(), slot="back").json()["image"]
    face = upload(client, set_id, png_bytes(), slot="face_front", make_primary=True).json()
    assert face["set"]["primary_image_id"] == face["image"]["id"]
    assert sum(img["is_primary"] for img in face["set"]["images"]) == 1

    # PUT primary
    response = client.put(f"/api/v1/reference-sets/{set_id}/primary", json={"image_id": back["id"]})
    assert response.status_code == 200
    assert response.json()["primary_image_id"] == back["id"]
    assert_error(
        client.put(f"/api/v1/reference-sets/{set_id}/primary", json={"image_id": "img_000000000000"}),
        404,
        "not_found",
    )

    # Deleting the primary promotes by slot priority: front > face_front > left > right > back > face_side > detail
    response = client.delete(f"/api/v1/reference-sets/{set_id}/images/{back['id']}")
    assert response.status_code == 200
    assert response.json()["primary_image_id"] == face["image"]["id"]
    response = client.delete(f"/api/v1/reference-sets/{set_id}/images/{face['image']['id']}")
    assert response.json()["primary_image_id"] == detail["id"]
    response = client.delete(f"/api/v1/reference-sets/{set_id}/images/{detail['id']}")
    assert response.json()["primary_image_id"] is None
    assert response.json()["images"] == []


def test_patch_image_slot_and_label(client):
    ref_set = create_set(client)
    set_id = ref_set["id"]
    front = upload(client, set_id, png_bytes(), slot="front").json()["image"]
    left = upload(client, set_id, png_bytes(), slot="left", label="side").json()["image"]
    assert left["label"] == "side"

    url = f"/api/v1/reference-sets/{set_id}/images/{left['id']}"
    assert_error(client.patch(url, json={"slot": "front"}), 409, "conflict")
    body = client.patch(url, json={"slot": "right", "label": "  renamed "}).json()
    moved = next(img for img in body["images"] if img["id"] == left["id"])
    assert moved["slot"] == "right" and moved["label"] == "renamed"
    body = client.patch(url, json={"label": None}).json()
    assert next(img for img in body["images"] if img["id"] == left["id"])["label"] is None
    # Primary is unaffected by slot moves.
    assert body["primary_image_id"] == front["id"]
    assert_error(client.patch(url, json={"slot": "nowhere"}), 422, "validation_error")
    assert_error(client.patch(url, json={"bogus": 1}), 422, "validation_error")
    assert_error(
        client.patch(f"/api/v1/reference-sets/{set_id}/images/img_000000000000", json={"label": "x"}),
        404,
        "not_found",
    )


def test_file_and_thumbnail_endpoints(client):
    ref_set = create_set(client)
    data = image_bytes("JPEG", (1000, 500), exif_orientation=6)  # rotated 90 degrees by EXIF
    image = upload(client, ref_set["id"], data, filename="photo.jpg", content_type="image/jpeg").json()["image"]
    assert (image["width"], image["height"]) == (500, 1000)  # display (EXIF-transposed) dimensions

    response = client.get(image["urls"]["file"])
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content == data
    assert "max-age" in response.headers["cache-control"]

    response = client.get(image["urls"]["thumbnail"])
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/webp"
    thumb = Image.open(BytesIO(response.content))
    assert thumb.format == "WEBP"
    assert max(thumb.size) == 384
    assert thumb.size == (192, 384)  # EXIF orientation applied


def test_thumbnail_keeps_alpha(client):
    ref_set = create_set(client)
    image = upload(client, ref_set["id"], png_bytes((500, 500), mode="RGBA")).json()["image"]
    thumb = Image.open(BytesIO(client.get(image["urls"]["thumbnail"]).content))
    assert thumb.mode == "RGBA"


def test_reference_sets_persist_across_restart(make_app, make_settings):
    settings = make_settings()
    first = make_app(settings=settings)
    ref_set = create_set(first.client)
    upload(first.client, ref_set["id"], png_bytes())
    second = make_app(settings=settings)
    reloaded = second.client.get(f"/api/v1/reference-sets/{ref_set['id']}").json()
    assert len(reloaded["images"]) == 1
    assert reloaded["primary_image_id"] == reloaded["images"][0]["id"]
