"""Slice S4: ``/image-generations`` API, executor and readiness check (part-aware DESIGN.md sections 4.4 and 6.2).

The real service, run worker, ``GenerationWorker`` GPU seam and ``ImageGenerationExecutor`` run end to end; only the
model registry and the text-to-image provider are doubles (``fakes_imagegen``).
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

import numpy as np
import pytest
from fakes import FakeEngine
from fakes_imagegen import (
    T2I_MODEL_ID,
    T2I_REVISION,
    FakeImageGenRegistry,
    FakeTextToImageProvider,
    insufficient_vram_error,
)
from fastapi import FastAPI
from PIL import Image
from starlette.testclient import TestClient

from config.settings import Settings
from core.part_aware_container import PartProviderOverrides
from pipelines.prompt_templates import PART_HINT_CLAUSE, prompt_sha256
from processing.imagegen_readiness import assess_readiness

API = "/api/v1/image-generations"
PROMPT = "a knight with a red cape and a sword"


@dataclass
class T2IHarness:
    app: FastAPI
    client: TestClient
    registry: FakeImageGenRegistry
    settings: Settings

    @property
    def provider(self) -> FakeTextToImageProvider:
        return self.registry.t2i

    @property
    def part_aware(self) -> Any:
        return self.app.state.container.part_aware

    def create(self, **body: Any) -> dict[str, Any]:
        payload = {"prompt": PROMPT, "randomize_seed": False, "seed": 7, **body}
        response = self.client.post(API, json=payload)
        assert response.status_code == 202, response.text
        return response.json()

    def wait(self, ig_id: str, statuses: set[str], timeout: float = 15.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        body: dict[str, Any] = {}
        while time.monotonic() < deadline:
            body = self.client.get(f"{API}/{ig_id}").json()
            if body["status"] in statuses:
                return body
            time.sleep(0.02)
        raise AssertionError(f"{ig_id} stayed {body.get('status')} (wanted {statuses})")


@pytest.fixture
def t2i_app(make_settings: Callable[..., Settings]) -> Iterator[Callable[..., T2IHarness]]:
    from main import create_app

    stack = contextlib.ExitStack()

    def factory(registry: FakeImageGenRegistry | None = None, **overrides: Any) -> T2IHarness:
        settings = make_settings(**overrides)
        registry = registry if registry is not None else FakeImageGenRegistry()
        app = create_app(settings, engine=FakeEngine(), part_providers=PartProviderOverrides(registry=registry))
        client = stack.enter_context(TestClient(app))
        return T2IHarness(app=app, client=client, registry=registry, settings=settings)

    with stack:
        yield factory


def _error(response: Any) -> dict[str, Any]:
    return response.json()["error"]


# ==================================================================================================
# Create -> run -> candidates
# ==================================================================================================


def test_generation_runs_end_to_end_with_effective_options_and_candidate_files(t2i_app):
    h = t2i_app()
    created = h.create(num_images=2, style="anime")
    assert created["id"].startswith("ig_") and created["kind"] == "image_generation"
    assert created["status"] in {"queued", "running", "completed"}
    assert [s["id"] for s in created["steps"]] == ["load_model", "generate", "check", "save"]

    done = h.wait(created["id"], {"completed", "failed"})
    assert done["status"] == "completed", done["error"]
    options = done["options"]
    assert options["model_id"] == T2I_MODEL_ID
    assert (options["width"], options["height"]) == (896, 1152)  # character preset default
    assert (options["steps"], options["guidance"], options["seed"]) == (9, 0.0, 7)
    assert options["effective_prompt"].startswith(f"A full-body 3D character reference image of {PROMPT}. ")
    assert options["effective_prompt"].endswith(PART_HINT_CLAUSE)
    assert options["final_prompt_sha256"] == prompt_sha256(options["effective_prompt"])
    assert options["expected_terms"] == ["object.held", "clothing.outer"]
    assert done["models"] == [{"id": T2I_MODEL_ID, "revision": T2I_REVISION, "license_id": "apache-2.0"}]
    assert done["progress"]["percent"] == 100.0
    assert {s["id"]: s["status"] for s in done["steps"]} == {
        "load_model": "done",
        "generate": "done",
        "check": "done",
        "save": "done",
    }

    candidates = done["result"]["candidates"]
    assert [(c["index"], c["seed"], c["width"], c["height"]) for c in candidates] == [
        (0, 7, 896, 1152),
        (1, 8, 896, 1152),
    ]
    assert all(c["readiness"] == {"ok": True, "issues": [], "method": "border_color"} for c in candidates)
    assert candidates[0]["url"] == f"{API}/{created['id']}/candidates/0"
    assert set(done["result"]["timings"]) == {"load_model", "generate", "check", "save", "total"}

    png = h.client.get(candidates[1]["url"])
    assert png.status_code == 200 and png.headers["content-type"] == "image/png"
    assert png.headers["cache-control"] == "private, max-age=3600"
    thumb = h.client.get(candidates[1]["thumbnail_url"])
    assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/webp"
    from io import BytesIO

    assert max(Image.open(BytesIO(thumb.content)).size) <= 384

    # Threads: load on the CPU worker, generate on the single GPU thread inside the registry guard.
    assert [t.split("_")[0] for t in h.provider.threads_of("load")] == ["parts-cpu"]
    assert all(t.startswith("gpu-worker") for t in h.provider.threads_of("generate"))
    events = [e for e, _thread, _ids in h.registry.events if e != "provider"]
    assert events == ["guard_enter", "ensure_vram", "offload", "guard_exit"]
    request = h.provider.requests[0]
    assert request.seeds == (7, 8) and request.steps == 9 and request.negative_prompt is None


def test_second_run_skips_the_resident_model_and_seeds_wrap(t2i_app):
    h = t2i_app()
    first = h.wait(h.create()["id"], {"completed", "failed"})
    assert first["status"] == "completed"
    second = h.wait(h.create(seed=2147483647, num_images=2)["id"], {"completed", "failed"})
    load = next(s for s in second["steps"] if s["id"] == "load_model")
    assert load["status"] == "skipped" and load["message"] == "Model already in memory"
    assert second["result"]["timings"]["load_model"] == 0.0
    assert [c["seed"] for c in second["result"]["candidates"]] == [2147483647, 0]
    assert len(h.provider.threads_of("load")) == 1  # loaded once


def test_random_seed_is_fixed_at_creation_and_object_preset_uses_square_size(t2i_app):
    h = t2i_app()
    response = h.client.post(API, json={"prompt": "a wooden chair", "preset": "object"})
    assert response.status_code == 202
    options = response.json()["options"]
    assert 0 <= options["seed"] <= 2147483647
    assert (options["width"], options["height"]) == (1024, 1024)
    assert options["effective_prompt"].startswith("A product-style 3D reference image of a wooden chair. ")


def test_readiness_issues_become_candidate_issues_and_run_warnings(t2i_app):
    h = t2i_app(FakeImageGenRegistry(FakeTextToImageProvider(layout="edge")))
    done = h.wait(h.create()["id"], {"completed", "failed"})
    assert done["status"] == "completed"
    readiness = done["result"]["candidates"][0]["readiness"]
    assert readiness["ok"] is False and "t2i_touches_border" in readiness["issues"]
    warning = next(w for w in done["warnings"] if w["code"] == "t2i_touches_border")
    assert warning["message"].startswith("Candidate 0: ") and warning["step_id"] == "check"
    assert next(s for s in done["steps"] if s["id"] == "check")["status"] == "warning"


def test_provider_failure_fails_the_run_with_generation_failed(t2i_app):
    provider = FakeTextToImageProvider()
    provider.fail_with = RuntimeError("decoder exploded")
    h = t2i_app(FakeImageGenRegistry(provider))
    done = h.wait(h.create()["id"], {"completed", "failed"})
    assert done["status"] == "failed"
    assert done["error"]["code"] == "generation_failed" and "decoder exploded" in done["error"]["message"]
    assert next(s for s in done["steps"] if s["id"] == "generate")["status"] == "failed"
    assert provider.on_gpu is False


# ==================================================================================================
# Validation and availability
# ==================================================================================================


@pytest.mark.parametrize(
    ("body", "field", "kind"),
    [
        ({"width": 512, "height": 512}, "width", "unsupported_by_model"),
        ({"width": 896}, "height", "conflicting_fields"),
        ({"negative_prompt": "blurry"}, "negative_prompt", "unsupported_by_model"),
        ({"steps": 51}, "steps", "unsupported_by_model"),
        ({"guidance": 3.5}, "guidance", "unsupported_by_model"),
        ({"model_id": "flux_klein"}, "model_id", "unsupported_by_model"),
    ],
)
def test_unsupported_values_are_422_with_the_field_and_type(t2i_app, body, field, kind):
    h = t2i_app()
    response = h.client.post(API, json={"prompt": PROMPT, **body})
    assert response.status_code == 422, response.text
    error = _error(response)
    assert error["code"] == "validation_error"
    assert any(d["loc"] == ["body", field] and d["type"] == kind for d in error["details"]), error["details"]


def test_size_issue_lists_the_allowed_sizes(t2i_app):
    response = t2i_app().client.post(API, json={"prompt": PROMPT, "width": 512, "height": 512})
    detail = _error(response)["details"][0]
    assert detail["ctx"]["allowed"] == [[1024, 1024], [896, 1152], [1152, 896]]


def test_prompt_hygiene_and_schema_limits(t2i_app):
    h = t2i_app(prompt_max_chars=10)
    bad_chars = h.client.post(API, json={"prompt": "a knight\x07"})
    assert bad_chars.status_code == 422
    assert _error(bad_chars)["details"][0]["type"] == "invalid_characters"
    too_long = h.client.post(API, json={"prompt": "a knight with a red cape"})
    assert too_long.status_code == 422
    assert _error(too_long)["details"][0]["type"] == "string_too_long"
    assert h.client.post(API, json={"prompt": "   "}).status_code == 422
    assert h.client.post(API, json={"prompt": "ok", "num_images": 5}).status_code == 422
    assert h.client.post(API, json={"prompt": "ok", "unknown": 1}).status_code == 422
    assert h.client.post(API, json={"prompt": "ok\n\tfine"}).status_code == 202


def test_disabled_flag_is_503_for_mutations_but_reads_work(t2i_app):
    h = t2i_app(image_generation_enabled=False)
    response = h.client.post(API, json={"prompt": PROMPT})
    assert response.status_code == 503
    assert _error(response)["code"] == "feature_disabled"
    assert _error(response)["details"]["flag"] == "IMG3D_IMAGE_GENERATION_ENABLED"
    assert h.client.get(API).json() == {"items": []}


def test_no_usable_model_is_capability_unavailable_and_explicit_model_is_model_unavailable(t2i_app):
    h = t2i_app(FakeImageGenRegistry(usable=False, state="unavailable", reason_code="not_approved"))
    response = h.client.post(API, json={"prompt": PROMPT})
    assert response.status_code == 503
    error = _error(response)
    assert error["code"] == "capability_unavailable"
    assert error["details"]["reasons"][0]["reason_code"] == "not_approved"
    explicit = h.client.post(API, json={"prompt": PROMPT, "model_id": T2I_MODEL_ID})
    assert explicit.status_code == 503
    assert _error(explicit)["code"] == "model_unavailable"
    expected = {"model_id": T2I_MODEL_ID, "state": "unavailable", "reason_code": "not_approved"}
    assert _error(explicit)["details"] == expected


def test_static_vram_check_refuses_with_capability_unavailable(t2i_app):
    h = t2i_app(FakeImageGenRegistry(vram_error=insufficient_vram_error()))
    response = h.client.post(API, json={"prompt": PROMPT})
    assert response.status_code == 503
    assert _error(response)["details"]["reasons"][0]["reason_code"] == "insufficient_vram"


def test_full_run_queue_is_429(t2i_app):
    provider = FakeTextToImageProvider()
    provider.block_generate = threading.Event()
    h = t2i_app(FakeImageGenRegistry(provider), run_queue_max_size=1)
    running = h.create()
    assert provider.generate_started.wait(5)
    h.create()  # fills the queue
    response = h.client.post(API, json={"prompt": PROMPT})
    assert response.status_code == 429 and _error(response)["code"] == "queue_full"
    provider.block_generate.set()
    h.wait(running["id"], {"completed"})


# ==================================================================================================
# List, cancel, delete, candidate routes
# ==================================================================================================


def test_list_is_newest_first_with_limit_and_status_filter(t2i_app):
    h = t2i_app()
    first = h.wait(h.create()["id"], {"completed"})
    second = h.wait(h.create()["id"], {"completed"})
    items = h.client.get(API).json()["items"]
    assert [i["id"] for i in items] == [second["id"], first["id"]]
    assert [i["id"] for i in h.client.get(API, params={"limit": 1}).json()["items"]] == [second["id"]]
    assert h.client.get(API, params={"status": "failed"}).json() == {"items": []}
    assert h.client.get(API, params={"limit": 0}).status_code == 422
    assert h.client.get(API, params={"limit": 101}).status_code == 422
    assert h.client.get(API, params={"status": "bogus"}).status_code == 422


def test_cancel_running_then_terminal_conflicts_and_delete_rules(t2i_app):
    provider = FakeTextToImageProvider()
    provider.block_generate = threading.Event()
    h = t2i_app(FakeImageGenRegistry(provider))
    run = h.create()
    assert provider.generate_started.wait(5)

    busy_delete = h.client.delete(f"{API}/{run['id']}")
    assert busy_delete.status_code == 409 and _error(busy_delete)["details"]["reason"] == "active"
    assert h.client.get(f"{API}/{run['id']}/candidates/0").status_code == 409

    cancel = h.client.post(f"{API}/{run['id']}/cancel")
    assert cancel.status_code == 202 and cancel.json()["progress"]["message"] == "Cancelling..."
    done = h.wait(run["id"], {"cancelled", "failed", "completed"})
    assert done["status"] == "cancelled"
    assert provider.on_gpu is False
    again = h.client.post(f"{API}/{run['id']}/cancel")
    assert again.status_code == 409 and _error(again)["details"]["reason"] == "terminal"

    run_dir = h.part_aware.layout.imagegen_dir(run["id"])
    assert run_dir.is_dir()
    assert h.client.delete(f"{API}/{run['id']}").status_code == 204
    assert not run_dir.exists()
    assert h.client.get(f"{API}/{run['id']}").status_code == 404
    assert h.client.delete(f"{API}/{run['id']}").status_code == 404


def test_cancel_queued_run(t2i_app):
    provider = FakeTextToImageProvider()
    provider.block_generate = threading.Event()
    h = t2i_app(FakeImageGenRegistry(provider))
    running = h.create()
    assert provider.generate_started.wait(5)
    queued = h.create()
    cancel = h.client.post(f"{API}/{queued['id']}/cancel")
    assert cancel.status_code == 202 and cancel.json()["status"] == "cancelled"
    provider.block_generate.set()
    assert h.wait(running["id"], {"completed"})["status"] == "completed"
    assert len(provider.requests) == 1


def test_delete_guard_refuses_runs_in_use(t2i_app):
    h = t2i_app()
    done = h.wait(h.create()["id"], {"completed"})
    h.part_aware.image_generation_service.add_delete_guard(lambda run: "Used by workflow wf_0123456789ab")
    response = h.client.delete(f"{API}/{done['id']}")
    assert response.status_code == 409 and _error(response)["details"]["reason"] == "in_use"


@pytest.mark.parametrize("index", ["4", "x", "01", "-1"])
def test_candidate_index_outside_the_pattern_is_404(t2i_app, index):
    h = t2i_app()
    done = h.wait(h.create()["id"], {"completed"})
    assert h.client.get(f"{API}/{done['id']}/candidates/{index}").status_code == 404


def test_unknown_runs_and_missing_candidates_are_404(t2i_app):
    h = t2i_app()
    done = h.wait(h.create()["id"], {"completed"})
    assert h.client.get(f"{API}/ig_000000000000").status_code == 404
    assert h.client.get(f"{API}/not-an-id").status_code == 404
    assert h.client.get(f"{API}/{done['id']}/candidates/1").status_code == 404  # only one candidate
    assert h.client.get(f"{API}/{done['id']}/candidates/3/thumbnail").status_code == 404


# ==================================================================================================
# Use a candidate
# ==================================================================================================


def test_use_candidate_creates_a_set_with_provenance_and_records_the_use(t2i_app):
    h = t2i_app()
    done = h.wait(h.create()["id"], {"completed"})
    response = h.client.post(f"{API}/{done['id']}/candidates/0/use", json={})
    assert response.status_code == 201, response.text
    body = response.json()
    image, ref_set = body["image"], body["set"]
    assert ref_set["name"] == f"Prompt: {PROMPT}"
    assert image["slot"] == "front" and image["is_primary"] is True
    assert image["label"] == f"Prompt: {PROMPT}"
    assert image["original_filename"] == f"{done['id']}_0.png"
    assert image["source"] == {
        "kind": "t2i",
        "image_generation_id": done["id"],
        "candidate_index": 0,
        "model_id": T2I_MODEL_ID,
        "model_revision": T2I_REVISION,
        "license_id": "apache-2.0",
        "seed": 7,
    }
    assert ref_set["images"][0]["source"]["image_generation_id"] == done["id"]

    stored = json.loads(
        (h.settings.runtime_dir / "uploads" / ref_set["id"] / "set.json").read_text(encoding="utf-8")
    )
    assert stored["images"][0]["source"]["final_prompt_sha256"] == done["options"]["final_prompt_sha256"]

    after = h.client.get(f"{API}/{done['id']}").json()
    assert after["result"]["candidates"][0]["used_as"] == [{"reference_set_id": ref_set["id"], "image_id": image["id"]}]

    # The image is a regular reference image: it can be fetched and used by POST /jobs.
    file = h.client.get(image["urls"]["file"])
    assert file.status_code == 200 and file.headers["content-type"] == "image/png"


def test_use_candidate_into_an_existing_set_with_slot_and_label(t2i_app):
    h = t2i_app()
    done = h.wait(h.create()["id"], {"completed"})
    set_id = h.client.post("/api/v1/reference-sets", json={"name": "mine"}).json()["id"]
    response = h.client.post(
        f"{API}/{done['id']}/candidates/0/use",
        json={"reference_set_id": set_id, "slot": "detail", "make_primary": False, "label": "close-up"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["set"]["id"] == set_id and body["image"]["slot"] == "detail" and body["image"]["label"] == "close-up"


def test_use_candidate_errors(t2i_app):
    provider = FakeTextToImageProvider()
    provider.block_generate = threading.Event()
    h = t2i_app(FakeImageGenRegistry(provider))
    run = h.create()
    assert provider.generate_started.wait(5)
    assert h.client.post(f"{API}/{run['id']}/candidates/0/use", json={}).status_code == 409
    provider.block_generate.set()
    h.wait(run["id"], {"completed"})
    sets_before = h.client.get("/api/v1/reference-sets").json()["items"]
    assert h.client.post(f"{API}/{run['id']}/candidates/2/use", json={}).status_code == 404
    assert (
        h.client.post(f"{API}/{run['id']}/candidates/0/use", json={"reference_set_id": "rs_000000000000"}).status_code
        == 404
    )
    assert h.client.post(f"{API}/{run['id']}/candidates/0/use", json={"slot": "nowhere"}).status_code == 422
    assert h.client.post(f"{API}/{run['id']}/candidates/0/use", json={"extra": True}).status_code == 422
    assert h.client.get("/api/v1/reference-sets").json()["items"] == sets_before


def test_prompt_text_is_never_logged(t2i_app, caplog):
    secret = "a zebra-striped teapot named qwertyuiop"
    h = t2i_app()
    with caplog.at_level("INFO"):
        h.wait(h.create(prompt=secret)["id"], {"completed"})
    assert "qwertyuiop" not in caplog.text
    assert "sha256=" in caplog.text


# ==================================================================================================
# Readiness check (processing/imagegen_readiness.py)
# ==================================================================================================


def _canvas(width: int = 400, height: int = 500, color: tuple[int, int, int] = (205, 205, 205)) -> np.ndarray:
    return np.full((height, width, 3), color, dtype=np.uint8)


def test_readiness_ok_for_one_centred_figure():
    image = _canvas()
    image[100:400, 150:250] = (40, 30, 90)
    report = assess_readiness(image, preset="character")
    assert report.ok and report.issues == ()
    assert report.metrics["subjects"] == 1 and report.metrics["bbox"] == [150, 100, 249, 399]
    assert report.method == "border_color"


def test_readiness_flags_border_multiple_subjects_and_aspect():
    image = _canvas()
    image[150:350, 0:120] = (40, 30, 90)
    image[150:350, 200:390] = (90, 20, 20)
    report = assess_readiness(image, preset="character")
    assert set(report.issues) == {"t2i_touches_border", "t2i_multiple_subjects", "t2i_aspect_mismatch"}
    assert assess_readiness(image, preset="object").issues == ("t2i_touches_border", "t2i_multiple_subjects")


def test_readiness_coverage_bounds_and_noise_robustness():
    blank = _canvas()
    assert assess_readiness(blank, preset="object").issues == ("t2i_coverage_low",)
    full = _canvas()
    full[12:488, 12:388] = (30, 30, 30)
    assert "t2i_coverage_high" in assess_readiness(full, preset="object").issues
    speckled = _canvas()
    speckled[100:400, 150:250] = (40, 30, 90)
    speckled[30, 30] = speckled[460, 370] = (0, 0, 0)  # single-pixel specks are removed by the 3 px open
    assert assess_readiness(speckled, preset="character").ok
    faint = _canvas()
    faint[100:400, 150:250] = (200, 200, 200)  # Delta-E below 12: background
    assert assess_readiness(faint, preset="object").issues == ("t2i_coverage_low",)
