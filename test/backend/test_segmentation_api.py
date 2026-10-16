"""Segmentation runs (``sg_``) through the API with fake providers (part-aware DESIGN.md sections 4.5 and 6.3)."""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable, Iterator
from io import BytesIO
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from fakes import FakeEngine
from fakes_segmentation import EYEBROW_Y, FakeSegmentationRegistry, character_image, png_of
from helpers import assert_error, create_job, create_set_with_image, wait_for, wait_for_job
from PIL import Image
from starlette.testclient import TestClient

from core.part_aware_container import PartProviderOverrides
from parts.taxonomy import TAXONOMY

API = "/api/v1/segmentations"


@pytest.fixture
def seg_app(make_settings: Callable[..., Any]) -> Iterator[Callable[..., SimpleNamespace]]:
    from main import create_app

    stack = contextlib.ExitStack()

    def factory(registry: Any = None, engine: Any = None, **overrides: Any) -> SimpleNamespace:
        settings = make_settings(**overrides)
        registry = registry if registry is not None else FakeSegmentationRegistry()
        engine = engine if engine is not None else FakeEngine()
        app = create_app(settings, engine=engine, part_providers=PartProviderOverrides(registry=registry))
        client = stack.enter_context(TestClient(app))
        return SimpleNamespace(app=app, client=client, registry=registry, engine=engine, settings=settings)

    with stack:
        yield factory


def _reference(client: TestClient, *, rgba: bool = False) -> tuple[dict, dict]:
    return create_set_with_image(client, data=png_of(character_image(rgba=rgba)))


def _create(client: TestClient, source: dict, **options: Any):
    body: dict[str, Any] = {"source": source}
    if options:
        body["options"] = options
    return client.post(API, json=body)


def _wait(client: TestClient, sg_id: str, statuses: set[str] | None = None, timeout: float = 30.0) -> dict:
    wanted = statuses or {"completed", "failed", "cancelled"}

    def poll() -> dict | None:
        body = client.get(f"{API}/{sg_id}").json()
        return body if body["status"] in wanted else None

    return wait_for(poll, timeout=timeout, message=f"segmentation {sg_id} did not reach {wanted}")


def _label(result: dict, class_id: str) -> dict | None:
    return next((label for label in result["labels"] if label["category"] == class_id), None)


# ==================================================================================================
# Reference images
# ==================================================================================================


def test_reference_image_baseline_finds_hair_face_eyes_and_rejects_the_tiara_eyebrow(seg_app):
    app = seg_app()
    client = app.client
    ref_set, image = _reference(client)
    response = _create(client, {"kind": "reference_image", "reference_set_id": ref_set["id"], "image_id": image["id"]})
    assert response.status_code == 202, response.text
    created = response.json()
    assert created["kind"] == "segmentation" and created["status"] == "queued"
    assert created["source"] == {
        "kind": "reference_image", "reference_set_id": ref_set["id"], "image_id": image["id"], "job_id": None,
    }

    run = _wait(client, created["id"])
    assert run["status"] == "completed", run
    result = run["result"]
    assert result["space"] == "source" and result["frame"] is None  # opaque input: BiRefNet decides the crop
    assert result["subject"] == "anime_character"
    assert result["profile"] == "baseline"  # the anime parser is unavailable -> honest degradation
    codes = {w["code"] for w in run["warnings"]}
    assert {"frame_unverified", "profile_degraded"} <= codes
    for class_id in ("hair", "body.head.face", "eye.left", "eye.right", "accessory.headwear"):
        assert _label(result, class_id) is not None, f"{class_id} missing: {[l['category'] for l in result['labels']]}"
    # Character-relative sides: the image-right eye is the character's left eye.
    assert _label(result, "eye.left")["bbox"][0] > _label(result, "eye.right")["bbox"][0]
    # No eyebrow on the tiara: every eyebrow label lies just above the eyes.
    brows = [label for label in result["labels"] if label["category"].startswith("eyebrow.")]
    assert len(brows) == 2
    for brow in brows:
        assert EYEBROW_Y[0] - 3 <= brow["bbox"][1] <= EYEBROW_Y[1] + 3, brow
    steps = {s["id"]: s for s in run["steps"]}
    assert [s["id"] for s in run["steps"]] == ["prepare", "frame", "parse", "detect", "refine", "fuse"]
    assert steps["parse"]["status"] == "skipped"
    assert steps["detect"]["details"]["gdino_batches"] >= 1
    assert steps["detect"]["details"]["head_box"] is not None
    rejected = steps["fuse"]["details"]["rejected"]
    assert any(r["reason"] == "eyebrow_not_above_eye" for r in rejected)
    assert {m["id"] for m in run["models"]} == {"grounding_dino_base", "sam2_1_hiera_large", "florence2_large"}
    assert all(m["revision"] == "fake-rev" for m in run["models"])
    assert run["progress"]["percent"] == 100

    # Threads: load() on the CPU thread, the role calls on the single GPU thread.
    registry = app.registry
    assert {name.split("_")[0] for name in registry.threads("load")} == {"parts-cpu"}
    gpu_threads = registry.threads("detect") | registry.threads("refine")
    assert len(gpu_threads) == 1 and next(iter(gpu_threads)).startswith("gpu-worker")
    assert [e[0] for e in registry.events][:2] == ["guard", "ensure_vram"]

    # Files.
    sg_id = run["id"]
    labels = client.get(result["labels_url"])
    assert labels.status_code == 200 and labels.headers["content-type"] == "image/png"
    label_img = Image.open(BytesIO(labels.content))
    assert label_img.mode.startswith("I") and label_img.size == (400, 400)
    values = np.asarray(label_img)
    assert TAXONOMY["eye.left"].value in values and TAXONOMY["hair"].value in values
    overlay = client.get(result["overlay_url"])
    assert overlay.status_code == 200 and Image.open(BytesIO(overlay.content)).mode == "RGBA"
    eye = _label(result, "eye.left")
    mask = client.get(eye["mask_url"])
    assert mask.status_code == 200 and Image.open(BytesIO(mask.content)).mode == "L"
    assert client.get(run["source_url"]).status_code == 200
    assert_error(client.get(f"{API}/{sg_id}/masks/61"), 404, "not_found")
    assert client.get(f"{API}/{sg_id}/masks/abc").status_code in (404, 422)


def test_real_alpha_reference_is_segmented_in_conditioning_space(seg_app):
    app = seg_app()
    client = app.client
    ref_set, image = _reference(client, rgba=True)
    created = _create(client, {"kind": "reference_image", "reference_set_id": ref_set["id"], "image_id": image["id"]})
    run = _wait(client, created.json()["id"])
    assert run["status"] == "completed", run
    result = run["result"]
    assert result["space"] == "conditioning"
    frame = result["frame"]
    assert frame["mode"] == "alpha_crop" and frame["method"] == "recompute" and frame["verified"] is True
    assert frame["residual"] == 0.0
    assert "frame_unverified" not in {w["code"] for w in run["warnings"]}
    assert _label(result, "eye.left") is not None and _label(result, "eye.right") is not None
    stored = app.app.state.container.part_aware.stores["segmentation"].get(run["id"])
    assert stored.source_pixels_sha256 is not None and len(stored.source_pixels_sha256) == 64


def test_classes_extra_terms_and_prompt_terms_are_honoured(seg_app):
    app = seg_app()
    client = app.client
    ref_set, image = _reference(client)
    created = _create(
        client,
        {"kind": "reference_image", "reference_set_id": ref_set["id"], "image_id": image["id"]},
        classes=["eyebrow.left", "eyebrow.right", "hair"],
        extra_terms=["magic wand"],
        head_pass=False,
        subject="object",
    )
    assert created.status_code == 202, created.text
    run = _wait(client, created.json()["id"])
    assert run["status"] == "completed", run
    categories = {label["category"] for label in run["result"]["labels"]}
    assert categories <= {"eyebrow.left", "eyebrow.right", "hair"}
    assert {"eyebrow.left", "eyebrow.right", "hair"} <= categories
    sent = app.registry.detector.requests[0]
    assert {"eye.left", "body.head.face", "object.held"} <= sent  # eyebrows need the eyes; the wand is a held object
    assert "magic wand" in app.registry.grounder.grounded
    assert run["result"]["expected_terms_missing"] == ["object.held"]
    assert run["result"]["subject"] == "object" and "caption" not in {c[1] for c in app.registry.calls}


# ==================================================================================================
# Job sources
# ==================================================================================================


def test_completed_job_source_and_not_completed_conflict(seg_app):
    engine = FakeEngine()
    app = seg_app(engine=engine)
    client = app.client
    ref_set, _image = _reference(client)
    job = create_job(client, ref_set["id"]).json()
    wait_for_job(client, job["id"], {"completed"})
    created = _create(client, {"kind": "job", "job_id": job["id"]})
    assert created.status_code == 202, created.text
    body = created.json()
    assert body["job_id"] == job["id"] and body["source"]["kind"] == "job"
    run = _wait(client, body["id"])
    assert run["status"] == "completed", run
    assert run["result"]["space"] == "conditioning"
    # The fake engine records no preprocess mode, so the frame cannot be recovered: honest low-res head pass.
    assert "head_pass_low_res" in {w["code"] for w in run["warnings"]}
    listed = client.get(API, params={"job_id": job["id"]}).json()["items"]
    assert [item["id"] for item in listed] == [run["id"]]

    engine.hold()
    try:
        running = create_job(client, ref_set["id"]).json()
        assert_error(_create(client, {"kind": "job", "job_id": running["id"]}), 409, "conflict")
    finally:
        engine.release()
    assert_error(_create(client, {"kind": "job", "job_id": "job_000000000000"}), 404, "not_found")
    assert_error(_create(client, {"kind": "job", "job_id": "../etc"}), 404, "not_found")


# ==================================================================================================
# Validation, flags, capability
# ==================================================================================================


def test_validation_and_not_found(seg_app):
    client = seg_app().client
    ref_set, image = _reference(client)
    src = {"kind": "reference_image", "reference_set_id": ref_set["id"], "image_id": image["id"]}
    assert client.post(API, json={"source": src, "options": {"classes": ["not.a.class"]}}).status_code == 422
    assert client.post(API, json={"source": src, "bogus": 1}).status_code == 422
    assert client.post(API, json={"source": src, "options": {"min_confidence": 0.99}}).status_code == 422
    assert client.post(API, json={"source": {"kind": "job"}}).status_code == 422
    missing = {"kind": "reference_image", "reference_set_id": ref_set["id"], "image_id": "img_000000000000"}
    assert_error(client.post(API, json={"source": missing}), 404, "not_found")
    assert_error(client.get(f"{API}/sg_000000000000"), 404, "not_found")
    assert_error(client.get(f"{API}/job_000000000000"), 404, "not_found")
    assert client.get(API, params={"limit": 0}).status_code == 422
    assert client.get(API, params={"limit": 101}).status_code == 422
    assert client.get(API, params={"status": "nope"}).status_code == 422
    assert client.get(API).json() == {"items": []}


def test_feature_flag_off_blocks_mutations_but_not_reads(seg_app):
    client = seg_app(segmentation_enabled=False).client
    ref_set, image = _reference(client)
    src = {"kind": "reference_image", "reference_set_id": ref_set["id"], "image_id": image["id"]}
    body = assert_error(client.post(API, json={"source": src}), 503, "feature_disabled")
    assert body["details"]["flag"] == "IMG3D_SEGMENTATION_ENABLED"
    assert client.get(API).status_code == 200
    assert_error(client.post(f"{API}/sg_000000000000/cancel"), 503, "feature_disabled")


def test_no_usable_model_is_capability_unavailable(seg_app):
    registry = FakeSegmentationRegistry(detector=False, refiner=False, grounder=False)
    client = seg_app(registry=registry).client
    ref_set, image = _reference(client)
    src = {"kind": "reference_image", "reference_set_id": ref_set["id"], "image_id": image["id"]}
    body = assert_error(client.post(API, json={"source": src}), 503, "capability_unavailable")
    assert body["details"]["capability"] == "segmentation"
    reasons = {r["model_id"]: r["reason_code"] for r in body["details"]["reasons"]}
    assert reasons["see_through_l2d"] == "vendored_source_missing"
    assert reasons["grounding_dino_base"] == "not_approved"
    assert client.get(API).json() == {"items": []}


def test_grounder_missing_degrades_subject_to_object(seg_app):
    client = seg_app(registry=FakeSegmentationRegistry(grounder=False)).client
    ref_set, image = _reference(client)
    src = {"kind": "reference_image", "reference_set_id": ref_set["id"], "image_id": image["id"]}
    run = _wait(client, client.post(API, json={"source": src}).json()["id"])
    assert run["status"] == "completed", run
    assert run["result"]["subject"] == "object" and run["result"]["profile"] == "baseline"
    messages = [w["message"] for w in run["warnings"] if w["code"] == "profile_degraded"]
    assert any("florence2_large unavailable" in m for m in messages)


# ==================================================================================================
# Cancel, delete, files while running
# ==================================================================================================


def test_cancel_running_files_conflict_and_delete(seg_app):
    gate = threading.Event()
    app = seg_app(registry=FakeSegmentationRegistry(gate=gate))
    client = app.client
    ref_set, image = _reference(client)
    src = {"kind": "reference_image", "reference_set_id": ref_set["id"], "image_id": image["id"]}
    sg_id = client.post(API, json={"source": src}).json()["id"]
    assert app.registry.detector.started.wait(10)
    assert_error(client.get(f"{API}/{sg_id}/overlay"), 409, "conflict")
    assert client.get(f"{API}/{sg_id}/source").status_code == 200
    assert_error(client.delete(f"{API}/{sg_id}"), 409, "conflict")
    assert client.post(f"{API}/{sg_id}/cancel").status_code == 202
    run = _wait(client, sg_id)
    assert run["status"] == "cancelled"
    assert_error(client.post(f"{API}/{sg_id}/cancel"), 409, "conflict")
    assert client.delete(f"{API}/{sg_id}").status_code == 204
    assert_error(client.get(f"{API}/{sg_id}"), 404, "not_found")
    gate.set()


def test_queued_run_cancels_and_completed_run_deletes(seg_app):
    gate = threading.Event()
    app = seg_app(registry=FakeSegmentationRegistry(gate=gate))
    client = app.client
    ref_set, image = _reference(client)
    src = {"kind": "reference_image", "reference_set_id": ref_set["id"], "image_id": image["id"]}
    first = client.post(API, json={"source": src}).json()["id"]
    second = client.post(API, json={"source": src}).json()["id"]
    assert app.registry.detector.started.wait(10)
    cancelled = client.post(f"{API}/{second}/cancel")
    assert cancelled.status_code == 202 and cancelled.json()["status"] == "cancelled"
    gate.set()
    assert _wait(client, first)["status"] == "completed"
    items = client.get(API, params={"reference_set_id": ref_set["id"]}).json()["items"]
    assert [i["id"] for i in items] == [second, first]  # newest first
    assert [i["id"] for i in client.get(API, params={"status": "completed"}).json()["items"]] == [first]
    assert client.delete(f"{API}/{first}").status_code == 204
    assert_error(client.get(f"{API}/{first}/labels"), 404, "not_found")
