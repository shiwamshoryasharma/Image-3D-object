"""Tuning code gate (part-aware DESIGN.md §8.2 G1/G2): Auto Mesh Tuner runs stay disabled until the user approves.

``test/conftest.py`` opts the tuner tests in with ``IMG3D_MESH_TUNING_ENABLED=true``; these tests pass the setting
explicitly (init kwargs beat the environment) to check both sides of the gate.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fakes_blender import FakeTunerRunner, TunerHarness, start_tuner_app
from helpers import assert_error, create_job, create_set_with_image, wait_for_job

from config.settings import Settings


@pytest.fixture
def make_gate_app(make_settings: Callable[..., Settings], runtime_dir: Path) -> Iterator[Callable[..., TunerHarness]]:
    stack = contextlib.ExitStack()

    def factory(**overrides: object) -> TunerHarness:
        return start_tuner_app(stack, make_settings(**overrides), runtime_dir, runner=FakeTunerRunner("ok"))

    with stack:
        yield factory


def test_code_default_is_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("IMG3D_MESH_TUNING_ENABLED", raising=False)
    assert Settings(_env_file=None).mesh_tuning_enabled is False


def test_disabled_gate_rejects_new_runs_but_keeps_history_readable(make_gate_app) -> None:
    h = make_gate_app(mesh_tuning_enabled=False)
    assert_error(h.client.post("/api/v1/jobs/job_000000000000/tunings", json={}), 404, "not_found")

    ref_set, _ = create_set_with_image(h.client)
    h.engine.hold()
    job = create_job(h.client, ref_set["id"]).json()
    wait_for_job(h.client, job["id"], {"running"})
    assert_error(h.client.post(f"/api/v1/jobs/{job['id']}/tunings", json={}), 409, "conflict")
    h.engine.release()
    wait_for_job(h.client, job["id"], {"completed"})

    error = assert_error(h.client.post(f"/api/v1/jobs/{job['id']}/tunings", json={}), 503, "feature_disabled")
    assert error["details"]["feature"] == "mesh_tuning"
    assert error["details"]["flag"] == "IMG3D_MESH_TUNING_ENABLED"

    listed = h.client.get(f"/api/v1/jobs/{job['id']}/tunings")
    assert listed.status_code == 200 and listed.json()["items"] == []
    assert h.client.get("/api/v1/tunings/tn_000000000000").status_code == 404


def test_enabled_gate_accepts_runs(make_gate_app) -> None:
    h = make_gate_app(mesh_tuning_enabled=True)
    ref_set, _ = create_set_with_image(h.client)
    job = create_job(h.client, ref_set["id"]).json()
    wait_for_job(h.client, job["id"], {"completed"})
    response = h.client.post(f"/api/v1/jobs/{job['id']}/tunings", json={})
    assert response.status_code == 202, response.text
