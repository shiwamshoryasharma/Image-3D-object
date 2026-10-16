"""Legacy invariance of the image-to-3D path (part-aware milestone, DESIGN.md §0.4 I1-I4 and §10.1).

The part-aware layer is additive: it never edits ``job.json``, ``JobOut``, ``CreateJobRequest`` or the files of a
generation job. These tests pin that from the outside:

* **legacy load** - the committed pre-change fixture ``fixtures/legacy_job_v1/job.json`` (built from the Job model as
  it was before the milestone, fake values only; ``model.glb``/``preview.png``/inputs are written at test time)
  loads at startup and serves ``GET /jobs/{id}`` and ``GET /jobs/{id}/result`` unchanged;
* **retry round-trip** - re-posting a stored ``settings`` dict with its ``reference_set_id``/``primary_image_id``
  (what the frontend's "retry" sends) passes ``extra="forbid"`` and stores an equal dict;
* **byte and key identity** - an image job run with ``part_aware_enabled`` true and false keeps the ``job.json`` key
  sets of the fixture, the pinned ``JobOut`` and ``/system`` key sets, the legacy WS stage order, the engine's
  ``model.glb``/``preview.png`` bytes and a job folder without extra entries;
* **part analyses** - a legacy job lists no part analyses, and a part analysis never touches ``model.glb``,
  ``preview.png`` or ``job.json`` (I2; un-skipped by slice S10 when the route exists).
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any

import pytest
from fakes import FakeEngine, write_test_glb
from fastapi import FastAPI
from fastapi.routing import APIRoute
from helpers import create_job, create_set_with_image, png_bytes, wait_for, wait_for_job, ws_collect_until, ws_receive
from PIL import Image

from config.settings import Settings

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "legacy_job_v1"
FIXTURE_JOB_JSON = FIXTURE_DIR / "job.json"
LEGACY_JOB: dict[str, Any] = json.loads(FIXTURE_JOB_JSON.read_text(encoding="utf-8"))

LEGACY_JOB_KEYS = frozenset(LEGACY_JOB)
"""Top-level ``job.json`` keys before the milestone (taken from the fixture)."""
LEGACY_SETTINGS_KEYS = frozenset(LEGACY_JOB["settings"])
LEGACY_PROGRESS_KEYS = frozenset(LEGACY_JOB["progress"])
LEGACY_RESULT_KEYS = frozenset(LEGACY_JOB["result"])
LEGACY_STATS_KEYS = frozenset(LEGACY_JOB["result"]["stats"])
LEGACY_REFERENCE_KEYS = frozenset(LEGACY_JOB["references"][0])

LEGACY_JOB_OUT_KEYS = frozenset(
    {
        "id",
        "status",
        "engine",
        "created_at",
        "started_at",
        "finished_at",
        "reference_set_id",
        "primary_image_id",
        "references",
        "settings",
        "seed",
        "queue_position",
        "progress",
        "error",
        "result",
    }
)
"""``JobOut`` keys of contract §7.4 (pinned literally: a later schema change must fail here)."""
LEGACY_JOB_RESULT_OUT_KEYS = frozenset(
    {
        "model_url",
        "download_url",
        "preview_url",
        "stats",
        "timings",
        "seed",
        "resolution",
        "effective_resolution",
        "texture_size",
        "polygon_target",
    }
)
LEGACY_SYSTEM_KEYS = frozenset({"app", "engine", "device", "queue", "limits", "defaults", "auth_required"})
LEGACY_STAGE_ORDER = (
    "queued",
    "waiting_for_model",
    "preprocessing",
    "conditioning",
    "sparse_structure",
    "shape",
    "texture",
    "decoding",
    "exporting",
    "finalizing",
    "done",
)
LEGACY_JOB_DIR_ENTRIES = frozenset({"job.json", "inputs", "model.glb", "preview.png"})
TERMINAL = {"completed", "failed", "cancelled"}
PART_ANALYSES_LIST_ROUTE = "/api/v1/jobs/{}/part-analyses"


# --------------------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------------------


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _install_legacy_job(runtime_dir: Path) -> Path:
    """Place the fixture job folder in ``runtime_dir`` before the app starts; returns the job directory.

    ``job.json`` is copied byte for byte; ``model.glb`` comes from :func:`fakes.write_test_glb` and the preview and
    input PNGs are generated, so the fixture carries no binary data and no real user data.
    """
    job_dir = runtime_dir / "jobs" / LEGACY_JOB["id"]
    inputs = job_dir / "inputs"
    inputs.mkdir(parents=True)
    shutil.copyfile(FIXTURE_JOB_JSON, job_dir / "job.json")
    write_test_glb(job_dir / "model.glb")
    Image.new("RGB", (96, 96), (180, 140, 90)).save(job_dir / "preview.png", format="PNG")
    for reference in LEGACY_JOB["references"]:
        Image.new("RGBA", (128, 96), (10, 120, 200, 255)).save(inputs / f"{reference['image_id']}.png", format="PNG")
    return job_dir


def _require_setting(name: str) -> None:
    """Skip while :class:`Settings` lacks a part-aware field (slice S1 adds them, DESIGN.md §8.1)."""
    if name not in Settings.model_fields:
        pytest.skip(f"Settings.{name} does not exist yet (added by slice S1, DESIGN.md §8.1)")


def _has_route(app: FastAPI, method: str, path_pattern: str) -> bool:
    """True when ``app`` serves ``method`` on ``path_pattern`` (``{}`` matches any path parameter)."""
    for route in app.routes:
        if isinstance(route, APIRoute) and method in route.methods:
            if re.sub(r"\{[^}]+\}", "{}", route.path) == path_pattern:
                return True
    return False


def _stage_sequence(updates: list[dict[str, Any]]) -> list[str]:
    """Distinct consecutive stages of a ``job.updated`` stream."""
    sequence: list[str] = []
    for update in updates:
        stage = update["progress"]["stage"]
        if not sequence or sequence[-1] != stage:
            sequence.append(stage)
    return sequence


def _is_subsequence(items: list[str], order: tuple[str, ...]) -> bool:
    remaining = iter(order)
    return all(item in remaining for item in items)


# --------------------------------------------------------------------------------------------------
# Legacy load
# --------------------------------------------------------------------------------------------------


def test_fixture_is_a_completed_pre_change_job():
    assert LEGACY_JOB["status"] == "completed"
    assert LEGACY_JOB["id"] not in {"job_269a864ac597", "job_fa0c4771ec03"}
    assert "parts" not in LEGACY_JOB_KEYS and "parts" not in LEGACY_SETTINGS_KEYS and "parts" not in LEGACY_RESULT_KEYS
    assert LEGACY_JOB["progress"]["stage"] == "done" and LEGACY_JOB["progress"]["stage_index"] == 9
    assert LEGACY_JOB["result"]["has_preview"] is True


def test_legacy_job_loads_and_serves_results(make_app, runtime_dir):
    job_dir = _install_legacy_job(runtime_dir)
    job_id = LEGACY_JOB["id"]
    before = {name: _sha256(job_dir / name) for name in ("job.json", "model.glb", "preview.png")}

    h = make_app()
    response = h.client.get(f"/api/v1/jobs/{job_id}")
    assert response.status_code == 200, response.text
    job = response.json()
    assert set(job) == LEGACY_JOB_OUT_KEYS
    assert job["status"] == "completed"
    assert job["engine"] == LEGACY_JOB["engine"]
    assert job["settings"] == LEGACY_JOB["settings"]
    assert job["seed"] == LEGACY_JOB["seed"]
    assert job["references"] == LEGACY_JOB["references"]
    assert job["progress"]["percent"] == 100.0 and job["progress"]["stage_index"] == 9
    assert job["queue_position"] is None and job["error"] is None

    result = job["result"]
    assert set(result) == LEGACY_JOB_RESULT_OUT_KEYS
    assert result["model_url"] == f"/api/v1/jobs/{job_id}/model.glb"
    assert result["download_url"] == f"/api/v1/jobs/{job_id}/export/glb"
    assert result["preview_url"] == f"/api/v1/jobs/{job_id}/preview"
    assert result["stats"] == LEGACY_JOB["result"]["stats"]
    assert result["timings"] == LEGACY_JOB["result"]["timings"]

    served = h.client.get(f"/api/v1/jobs/{job_id}/result")
    assert served.status_code == 200, served.text
    assert served.json() == result
    model = h.client.get(result["model_url"])
    assert model.status_code == 200 and model.content == (job_dir / "model.glb").read_bytes()
    assert h.client.get(result["preview_url"]).status_code == 200
    assert job_id in [item["id"] for item in h.client.get("/api/v1/jobs").json()["items"]]

    # Loading and serving never rewrites the legacy files.
    after = {name: _sha256(job_dir / name) for name in before}
    assert after == before
    assert {entry.name for entry in job_dir.iterdir()} == LEGACY_JOB_DIR_ENTRIES


def test_legacy_job_lists_no_part_analyses(make_app, runtime_dir):
    _install_legacy_job(runtime_dir)
    h = make_app()
    if not _has_route(h.app, "GET", PART_ANALYSES_LIST_ROUTE):
        pytest.skip("GET /api/v1/jobs/{job_id}/part-analyses is not mounted yet (slice S10)")
    response = h.client.get(f"/api/v1/jobs/{LEGACY_JOB['id']}/part-analyses")
    assert response.status_code == 200, response.text
    assert response.json() == {"items": []}


# --------------------------------------------------------------------------------------------------
# Retry round-trip
# --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("source", ["legacy_fixture", "completed_job"])
def test_retry_round_trip_keeps_the_stored_settings(make_app, runtime_dir, source):
    """The frontend retry re-posts ``reference_set_id`` + ``primary_image_id`` + the stored ``settings`` dict."""
    if source == "legacy_fixture":
        _install_legacy_job(runtime_dir)
    h = make_app()
    ref_set, image = create_set_with_image(h.client, data=png_bytes((128, 128), mode="RGBA"))

    if source == "legacy_fixture":
        stored = LEGACY_JOB["settings"]
    else:
        submitted = {
            "resolution": 512,
            "seed": 42,
            "randomize_seed": False,
            "polygon_target": 100_000,
            "texture_size": 1024,
            "remove_background": False,
            "advanced": {"shape": {"steps": 8, "guidance_strength": 5.0}},
        }
        first = create_job(h.client, ref_set["id"], submitted)
        assert first.status_code == 202, first.text
        stored = wait_for_job(h.client, first.json()["id"], TERMINAL)["settings"]
        assert stored["advanced"]["shape"] == {
            "steps": 8,
            "guidance_strength": 5.0,
            "guidance_rescale": None,
            "rescale_t": None,
        }

    retry = create_job(h.client, ref_set["id"], stored, primary_image_id=image["id"])
    assert retry.status_code == 202, retry.text
    retried = retry.json()
    assert retried["settings"] == stored
    assert retried["primary_image_id"] == image["id"]
    final = wait_for_job(h.client, retried["id"], TERMINAL)
    assert final["status"] == "completed", final
    assert final["settings"] == stored
    on_disk = json.loads((runtime_dir / "jobs" / retried["id"] / "job.json").read_text(encoding="utf-8"))
    assert on_disk["settings"] == stored


# --------------------------------------------------------------------------------------------------
# Byte and key identity of image jobs with part-awareness on and off
# --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("part_aware_enabled", [True, False], ids=["part_aware_on", "part_aware_off"])
def test_image_job_bytes_and_keys_are_unchanged(make_app, runtime_dir, tmp_runtime, part_aware_enabled):
    _require_setting("part_aware_enabled")
    h = make_app(engine=FakeEngine(step_delay=0.02), part_aware_enabled=part_aware_enabled)
    client = h.client

    system = client.get("/api/v1/system").json()
    assert set(system) == LEGACY_SYSTEM_KEYS

    ref_set, image = create_set_with_image(client, data=png_bytes((256, 256), mode="RGBA"))
    with client.websocket_connect("/api/v1/ws") as ws:
        assert ws_receive(ws)["type"] == "hello"
        response = create_job(client, ref_set["id"], LEGACY_JOB["settings"])
        assert response.status_code == 202, response.text
        job_id = response.json()["id"]
        messages = ws_collect_until(
            ws,
            lambda m: m["type"] == "job.updated" and m["data"]["id"] == job_id and m["data"]["status"] in TERMINAL,
        )

    # WebSocket: legacy stage order, monotonic progress, final 100 % at stage index 9.
    updates = [m["data"] for m in messages if m["type"] == "job.updated" and m["data"]["id"] == job_id]
    assert all(set(update) == LEGACY_JOB_OUT_KEYS for update in updates)
    stages = _stage_sequence(updates)
    assert _is_subsequence(stages, LEGACY_STAGE_ORDER), stages
    assert stages[-1] == "done"
    indexes = [u["progress"]["stage_index"] for u in updates]
    percents = [u["progress"]["percent"] for u in updates]
    assert indexes == sorted(indexes) and percents == sorted(percents)
    assert updates[-1]["status"] == "completed"
    assert percents[-1] == 100.0 and indexes[-1] == 9

    # REST: JobOut and JobResultOut key sets; settings stored exactly as submitted, without a "parts" key.
    job = client.get(f"/api/v1/jobs/{job_id}").json()
    assert set(job) == LEGACY_JOB_OUT_KEYS
    assert set(job["result"]) == LEGACY_JOB_RESULT_OUT_KEYS
    assert set(job["progress"]) == LEGACY_PROGRESS_KEYS
    assert job["settings"] == LEGACY_JOB["settings"] and "parts" not in job["settings"]
    assert set(client.get("/api/v1/system").json()) == LEGACY_SYSTEM_KEYS

    # job.json: the fixture's key sets at every level (engine metadata contents are engine-defined).
    job_dir = runtime_dir / "jobs" / job_id
    stored = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    assert set(stored) == LEGACY_JOB_KEYS
    assert set(stored["settings"]) == LEGACY_SETTINGS_KEYS and "parts" not in stored["settings"]
    assert set(stored["progress"]) == LEGACY_PROGRESS_KEYS
    assert set(stored["result"]) == LEGACY_RESULT_KEYS
    assert set(stored["result"]["stats"]) == LEGACY_STATS_KEYS
    assert all(set(reference) == LEGACY_REFERENCE_KEYS for reference in stored["references"])

    # Bytes: model.glb is exactly what the engine wrote, preview.png the engine's copy of the primary input, and
    # nothing else appears inside the job folder.
    expected_glb = write_test_glb(tmp_runtime / "expected_model.glb")
    assert (job_dir / "model.glb").read_bytes() == expected_glb.read_bytes()
    assert (job_dir / "preview.png").read_bytes() == (job_dir / "inputs" / f"{image['id']}.png").read_bytes()
    assert {entry.name for entry in job_dir.iterdir()} == LEGACY_JOB_DIR_ENTRIES


# --------------------------------------------------------------------------------------------------
# A part analysis never touches the job's files (I2)
# --------------------------------------------------------------------------------------------------


@pytest.mark.skip(reason="un-skipped by S10")
def test_part_analysis_leaves_the_job_files_untouched(make_app, runtime_dir):
    job_dir = _install_legacy_job(runtime_dir)
    job_id = LEGACY_JOB["id"]
    protected = ("model.glb", "preview.png", "job.json")
    before = {name: _sha256(job_dir / name) for name in protected}

    h = make_app()
    response = h.client.post(f"/api/v1/jobs/{job_id}/part-analyses", json={"strategy": "components"})
    assert response.status_code == 202, response.text
    analysis_id = response.json()["id"]

    def finished() -> dict[str, Any] | None:
        body = h.client.get(f"/api/v1/part-analyses/{analysis_id}").json()
        return body if body["status"] in TERMINAL else None

    analysis = wait_for(finished, timeout=60, message=f"part analysis {analysis_id} finished")
    assert analysis["status"] == "completed", analysis

    assert {name: _sha256(job_dir / name) for name in protected} == before
    assert {entry.name for entry in job_dir.iterdir()} == LEGACY_JOB_DIR_ENTRIES | {"parts"}
    assert [entry.name for entry in (job_dir / "parts").iterdir()] == [analysis_id]
    assert h.client.get(f"/api/v1/jobs/{job_id}").json()["settings"] == LEGACY_JOB["settings"]
