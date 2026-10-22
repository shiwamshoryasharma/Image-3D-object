"""``GET /features`` and the model registry API (part-aware DESIGN.md sections 4.8 and 6.1; slice S2).

* ``/features`` per section 6.1 with the real on-disk model states of this machine (executors are overridden with
  inert doubles so the pipeline codes do not depend on which run slices have merged);
* derivation rules: nothing approved, flags off, the static VRAM rule with a resident engine, refine nodes hidden,
  ``catalog_etag`` equal to the catalog built from the same node states, models equal to ``GET /models/{id}``;
* ``/models`` filters and errors; load/unload with a fake provider (CPU ``load()``, ``warm()``/``offload()``/
  ``unload()`` on the ``gpu-worker`` thread inside the torch-globals guard) and the 404/409/503 cases.
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fakes import FakeEngine
from starlette.testclient import TestClient
from test_model_registry import FakeModelProvider

from config.settings import Settings
from core.part_aware_container import PartProviderOverrides
from inference.providers import registry as registry_module
from inference.providers.catalog import CATALOG, GROUNDING_DINO_BASE, Z_IMAGE_TURBO
from schemas.features import PAUSED_REASON
from services.workflow_catalog import build_catalog, resolve_node_states
from workers.generation_worker import GPU_THREAD_NAME
from workers.run_worker import RunWorker

API = "/api/v1"
REAL_MODEL_ROOT = Path(__file__).resolve().parents[2] / "backend" / ".model"
needs_real_models = pytest.mark.skipif(
    not (REAL_MODEL_ROOT / "t2i" / "Z-Image-Turbo").is_dir(), reason="model weights not installed"
)
VISIBLE_IDS = [spec.id for spec in CATALOG if not spec.hidden_by_default]
REFINE_NODES = {"part_refine", "mesh_tune", "eye_correction", "rig_correction"}


class InertExecutor:
    """Stands in for a merged run executor (``/features`` only checks that one is available)."""

    kind = "inert"
    prefix = "xx"


@pytest.fixture
def guard_entries(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace the torch-globals guard with a recorder (keeps torch out of the backend test process)."""
    entries: list[str] = []

    @contextlib.contextmanager
    def recording_guard() -> Iterator[None]:
        entries.append(threading.current_thread().name)
        yield

    monkeypatch.setattr(registry_module, "torch_globals_guard", recording_guard)
    return entries


@pytest.fixture
def start_app(
    make_settings: Callable[..., Settings], guard_entries: list[str]
) -> Iterator[Callable[..., tuple[TestClient, Any]]]:
    stack = contextlib.ExitStack()

    def factory(
        *, providers: dict[str, Any] | None = None, engine: Any = None, executors: bool = True, **overrides: Any
    ) -> tuple[TestClient, Any]:
        from main import create_app

        # The tuner tests opt in to IMG3D_MESH_TUNING_ENABLED (test/conftest.py); discovery is tested at the app default.
        settings = make_settings(**{"device": "cpu", "mesh_tuning_enabled": False, **overrides})
        part_providers = PartProviderOverrides(
            providers=providers or {},
            executors={kind: InertExecutor() for kind in ("image_generation", "segmentation", "part_analysis")}
            if executors
            else {},
        )
        app = create_app(settings, engine=engine or FakeEngine(), part_providers=part_providers)
        client = stack.enter_context(TestClient(app))
        return client, app.state.container.part_aware

    with stack:
        yield factory


def _wait_for_state(client: TestClient, model_id: str, state: str, timeout: float = 10.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    body: dict[str, Any] = {}
    while time.monotonic() < deadline:
        body = client.get(f"{API}/models/{model_id}").json()
        if body["state"] == state:
            return body
        time.sleep(0.02)
    raise AssertionError(f"{model_id} never reached {state!r}; last: {body.get('state')} {body.get('reason')}")


def _capability(body: dict[str, Any], name: str) -> tuple[bool, str]:
    entry = body["pipeline"][name]
    return entry["available"], entry["code"]


# --------------------------------------------------------------------------------------------------
# /features
# --------------------------------------------------------------------------------------------------


@needs_real_models
def test_features_reports_the_real_states_of_this_machine(start_app):
    client, part_aware = start_app()
    response = client.get(f"{API}/features")
    assert response.status_code == 200
    body = response.json()
    assert body["schema"] == "img3d.features/1" and body["api"]["version"] == "1.1.0"
    assert body["flags"] == {
        "part_aware": True,
        "image_generation": True,
        "segmentation": True,
        "part_analysis": True,
        "workflows": True,
        "mesh_tuning": False,
        "part_refinement": False,
        "persist_generation_intermediates": False,
    }
    states = {model["id"]: (model["state"], model["reason_code"]) for model in body["models"]}
    assert states == {
        "z_image_turbo": ("installed", "ok"),
        "grounding_dino_base": ("installed", "ok"),
        "sam2_1_hiera_large": ("installed", "ok"),
        "florence2_large": ("installed", "ok"),
        "see_through_l2d": ("installed", "ok"),  # S6: vendored submodules + local weights
        "sapiens2_seg_0_4b": ("installed", "ok"),
        "raster_lifter": ("installed", "ok"),
    }
    assert all(not model["local_path"] or not Path(model["local_path"]).is_absolute() for model in body["models"])
    sapiens = next(model for model in body["models"] if model["id"] == "sapiens2_seg_0_4b")
    assert sapiens["license"]["conditions"] == [
        "No biometric identification",
        "No deepfakes or impersonation",
        "No military use",
    ]
    for model in body["models"]:
        assert client.get(f"{API}/models/{model['id']}").json() == model

    assert _capability(body, "image_to_3d") == (True, "ok")
    prompt = body["pipeline"]["prompt_to_3d"]
    assert (prompt["available"], prompt["code"], prompt["default_model_id"]) == (True, "ok", "z_image_turbo")
    assert prompt["default_model_state"] == "installed" and prompt["cold_start_s"] == 337
    assert prompt["sizes"] == [[1024, 1024], [896, 1152], [1152, 896]] and prompt["max_images"] == 4
    assert prompt["supports_negative_prompt"] is False and prompt["selection_modes"] == ["auto_first"]
    segmentation = body["pipeline"]["segmentation"]
    assert (segmentation["available"], segmentation["code"]) == (True, "ok")
    assert [(p["id"], p["available"], p["code"]) for p in segmentation["profiles"]] == [
        ("baseline", True, "ok"),
        ("anime", True, "ok"),  # S6: See-through and Sapiens2 are vendored and installed
        ("realistic", True, "ok"),
    ]
    assert segmentation["profiles"][1]["reason"] is None
    parts = body["pipeline"]["part_extraction"]
    assert [(s["id"], s["available"], s["code"]) for s in parts["strategies"]] == [
        ("components", True, "ok"),
        ("semantic_regions", True, "ok"),
        ("semantic_parts", True, "ok"),
        ("per_part_regeneration", False, "not_implemented"),
    ]
    assert parts["default_strategy"] == "semantic_parts" and parts["default_views"] == 8
    assert [g["id"] for g in parts["separable_groups"]] == [
        "eyes", "eyebrows", "eyelashes", "hair", "clothes", "accessories", "detachable"
    ]
    assert [g["id"] for g in parts["region_groups"]] == ["face", "body"]

    nodes = {node["type"]: node for node in body["nodes"]}
    for node_type in REFINE_NODES:
        assert nodes[node_type]["hidden_by_default"] is True and nodes[node_type]["locked"] is True
        assert nodes[node_type]["code"] == "not_approved"
    assert (nodes["segmentation_3d"]["code"], nodes["segmentation_3d"]["hidden_by_default"]) == ("unsupported", True)
    assert nodes["prompt"]["available"] and nodes["part_extraction"]["available"]
    pipeline = part_aware.feature_service.features().pipeline
    capabilities = {
        name: getattr(pipeline, name) for name in ("image_to_3d", "prompt_to_3d", "segmentation", "part_extraction")
    }
    expected_etag = build_catalog(resolve_node_states(capabilities), graph_max_bytes=65536).etag
    assert body["workflow"]["catalog_etag"] == expected_etag
    assert body["workflow"]["catalog_url"] == "/api/v1/workflows/catalog"
    assert body["workflow"]["code"] in {"ok", "not_implemented"}  # the workflow compiler merges with S11

    assert body["mesh_tuning"] == {"enabled": False, "reason": PAUSED_REASON}
    assert body["part_refinement"] == {"enabled": False, "reason": PAUSED_REASON}
    assert body["taxonomy"]["version"] == 1 and body["taxonomy"]["classes"]
    assert body["queues"] == {"runs": {"pending": 0, "running_id": None, "running_kind": None, "max_size": 8}}
    assert body["limits"]["max_parts"] == 256 and body["limits"]["prompt_max_chars"] == 2000


def test_mesh_tuning_toggle_follows_both_settings(start_app):
    client, _part_aware = start_app(mesh_tuning_enabled=True, blender_enabled=True)
    body = client.get(f"{API}/features").json()
    assert body["flags"]["mesh_tuning"] is True and body["mesh_tuning"] == {"enabled": True, "reason": None}
    assert body["part_refinement"]["enabled"] is False


def test_nothing_approved_leaves_only_geometry_components(start_app):
    client, _part_aware = start_app(approved_models=[])
    body = client.get(f"{API}/features").json()
    assert _capability(body, "prompt_to_3d") == (False, "not_approved")
    segmentation = body["pipeline"]["segmentation"]
    assert segmentation["available"] is False
    assert {p["code"] for p in segmentation["profiles"]} == {"not_approved"}
    strategies = {s["id"]: (s["available"], s["code"]) for s in body["pipeline"]["part_extraction"]["strategies"]}
    assert strategies["components"] == (True, "ok")
    assert strategies["semantic_parts"] == (False, "not_approved")
    assert body["pipeline"]["part_extraction"]["default_strategy"] == "components"
    approved = {model["id"]: model["approved"] for model in body["models"]}
    assert approved["raster_lifter"] is True and not any(v for k, v in approved.items() if k != "raster_lifter")


def test_flags_off_are_reported_as_disabled(start_app):
    client, _part_aware = start_app(segmentation_enabled=False)
    body = client.get(f"{API}/features").json()
    assert body["flags"]["segmentation"] is False
    assert _capability(body, "segmentation") == (False, "disabled")
    assert {m["reason_code"] for m in body["models"] if m["kind"] == "segmentation"} == {"disabled"}
    response = client.post(f"{API}/models/grounding_dino_base/load")
    assert response.status_code == 503 and response.json()["error"]["code"] == "feature_disabled"


def test_master_switch_off_keeps_discovery_readable(start_app):
    client, _part_aware = start_app(part_aware_enabled=False)
    body = client.get(f"{API}/features").json()
    assert body["flags"]["part_aware"] is False and body["flags"]["workflows"] is False
    for name in ("prompt_to_3d", "segmentation", "part_extraction"):
        assert _capability(body, name) == (False, "disabled")
    assert body["workflow"]["code"] == "disabled"
    assert client.get(f"{API}/models").status_code == 200


def test_resident_engine_makes_z_image_insufficient_vram(start_app):
    providers = {Z_IMAGE_TURBO.id: FakeModelProvider(Z_IMAGE_TURBO)}
    client, _part_aware = start_app(providers=providers, low_vram=False, engine=FakeEngine(ready=True))
    body = client.get(f"{API}/features").json()
    assert _capability(body, "prompt_to_3d") == (False, "insufficient_vram")
    model = client.get(f"{API}/models/z_image_turbo").json()
    assert (model["state"], model["reason_code"]) == ("unavailable", "insufficient_vram")


def test_missing_executor_reports_not_implemented(start_app):
    client, part_aware = start_app(executors=False)
    body = client.get(f"{API}/features").json()
    for kind, capability in (("segmentation", "segmentation"), ("part_analysis", "part_extraction")):
        availability = part_aware.executors.availability(kind)
        if availability.code == "not_implemented":
            assert _capability(body, capability) == (False, "not_implemented")


# --------------------------------------------------------------------------------------------------
# /models
# --------------------------------------------------------------------------------------------------


def test_models_listing_filters_and_errors(start_app):
    client, _part_aware = start_app()
    assert [m["id"] for m in client.get(f"{API}/models").json()["items"]] == VISIBLE_IDS
    everything = client.get(f"{API}/models", params={"include_hidden": "true"}).json()["items"]
    assert len(everything) == len(CATALOG)
    hidden = [m for m in everything if m["hidden_by_default"]]
    assert {m["reason_code"] for m in hidden} == {"unsupported"} and all(m["install"]["hint"] for m in hidden)
    segmentation = client.get(f"{API}/models", params={"kind": "segmentation"}).json()["items"]
    assert {m["kind"] for m in segmentation} == {"segmentation"} and len(segmentation) == 5
    bad_kind = client.get(f"{API}/models", params={"kind": "teleportation"})
    assert bad_kind.status_code == 422
    missing = client.get(f"{API}/models/no_such_model")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "not_found"
    for path in ("load", "unload"):
        assert client.post(f"{API}/models/no_such_model/{path}").status_code == 404


def test_load_warm_and_unload_with_a_fake_provider(start_app, guard_entries):
    fake = FakeModelProvider(GROUNDING_DINO_BASE)
    client, _part_aware = start_app(providers={GROUNDING_DINO_BASE.id: fake})
    response = client.post(f"{API}/models/grounding_dino_base/load")
    assert response.status_code == 202 and response.json()["state"] == "loading"
    loaded = _wait_for_state(client, "grounding_dino_base", "loaded")
    assert loaded["loaded_device"] == "cpu" and loaded["last_used_at"] is not None
    assert [name for name, _thread in fake.calls] == ["load", "warm", "offload"]
    threads = dict(fake.calls)
    assert not threads["load"].startswith(GPU_THREAD_NAME)
    assert threads["warm"].startswith(GPU_THREAD_NAME) and threads["offload"].startswith(GPU_THREAD_NAME)
    assert guard_entries and guard_entries[0].startswith(GPU_THREAD_NAME)
    features_models = {m["id"]: m for m in client.get(f"{API}/features").json()["models"]}
    assert features_models["grounding_dino_base"]["state"] == "loaded"

    response = client.post(f"{API}/models/grounding_dino_base/unload")
    assert response.status_code == 202
    _wait_for_state(client, "grounding_dino_base", "installed")
    assert fake.calls[-1][0] == "unload" and fake.calls[-1][1].startswith(GPU_THREAD_NAME)


def test_load_conflicts_while_loading_and_while_a_run_uses_the_kind(start_app, monkeypatch):
    gate = threading.Event()
    fake = FakeModelProvider(GROUNDING_DINO_BASE, warm_gate=gate)
    client, _part_aware = start_app(providers={GROUNDING_DINO_BASE.id: fake})
    assert client.post(f"{API}/models/grounding_dino_base/load").status_code == 202
    assert fake.warm_started.wait(10)
    assert client.get(f"{API}/models/grounding_dino_base").json()["state"] == "loading"
    for path in ("load", "unload"):
        conflict = client.post(f"{API}/models/grounding_dino_base/{path}")
        assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "conflict"
    gate.set()
    _wait_for_state(client, "grounding_dino_base", "loaded")

    monkeypatch.setattr(RunWorker, "running_kind", property(lambda self: "part_analysis"))
    monkeypatch.setattr(RunWorker, "running_id", property(lambda self: "pa_000000000001"))
    busy = client.post(f"{API}/models/grounding_dino_base/unload")
    assert busy.status_code == 409 and busy.json()["error"]["details"]["run_id"] == "pa_000000000001"
    assert client.post(f"{API}/models/z_image_turbo/unload").status_code == 202  # not used by a part analysis


def test_load_refuses_unusable_models_and_reports_load_errors(start_app, monkeypatch):
    # S6 vendored See-through for real; stub its source tree as missing so the refusal path stays covered.
    monkeypatch.setattr(registry_module, "_has_python_sources", lambda root: False)
    fake = FakeModelProvider(GROUNDING_DINO_BASE, fail_load=RuntimeError("weights are corrupt"))
    client, _part_aware = start_app(providers={GROUNDING_DINO_BASE.id: fake})
    for model_id, reason_code in (("p3sam", "unsupported"), ("see_through_l2d", "vendored_source_missing")):
        response = client.post(f"{API}/models/{model_id}/load")
        assert response.status_code == 503
        error = response.json()["error"]
        assert error["code"] == "model_unavailable" and error["details"]["reason_code"] == reason_code
    assert client.post(f"{API}/models/grounding_dino_base/load").status_code == 202  # override: no disk check
    failed = _wait_for_state(client, "grounding_dino_base", "error")
    assert failed["reason_code"] == "error" and "weights are corrupt" in failed["reason"]
    assert client.post(f"{API}/models/grounding_dino_base/unload").status_code == 202  # clears the error
    _wait_for_state(client, "grounding_dino_base", "installed")


def test_load_and_unload_answer_503_while_shutting_down(start_app):
    client, part_aware = start_app(providers={GROUNDING_DINO_BASE.id: FakeModelProvider(GROUNDING_DINO_BASE)})
    part_aware.model_registry_service._stopping = True
    for path in ("load", "unload"):
        response = client.post(f"{API}/models/grounding_dino_base/{path}")
        assert response.status_code == 503 and response.json()["error"]["code"] == "engine_unavailable"
