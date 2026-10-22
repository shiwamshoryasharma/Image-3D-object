"""Model registry and catalog v1 (part-aware DESIGN.md sections 4.8 and 5.3-5.4; slice S2).

* the catalog: the six DECISIONS.md models are approved by default, the lifter needs no approval, the 3D seams are
  unsupported and hidden;
* the real on-disk states of this machine (Z-Image, GDINO, SAM 2.1, Florence-2, See-through, Sapiens2 (vendored by S6)
  and the lifter installed), resolved without importing any heavy package;
* every state rule in order, the 5 s cache of the on-disk checks, live states, lazy provider construction with the
  ``UnavailableProvider`` substitution and the HF environment order, overrides, the static and live VRAM checks,
  ``offload``, role resolution, defaults and idle providers.

:class:`FakeModelProvider` is the provider double shared with ``test_features_api.py``.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import sys
import threading
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fakes import FakeEngine

from config.settings import Settings
from core.exceptions import NotFoundError
from core.feature_errors import CapabilityUnavailableError, InsufficientVramError, ModelUnavailableError
from core.part_aware_container import PartProviderOverrides
from inference.base import CancellationToken
from inference.providers import registry as registry_module
from inference.providers.base import (
    MODEL_ID_PATTERN,
    DependencySpec,
    LicenseInfo,
    ModelProvider,
    ModelSpec,
    ProviderStatus,
)
from inference.providers.catalog import CATALOG, RASTER_LIFTER, Z_IMAGE_TURBO
from inference.providers.registry import ProviderRegistry
from inference.providers.unavailable import UnavailableProvider
from utils.timeutil import utcnow

BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"
REAL_MODEL_ROOT = BACKEND_DIR / ".model"
HEAVY = ("torch", "diffusers", "transformers", "nvdiffrast", "cumesh")


class FakeModelProvider(ModelProvider):
    """Provider double: records ``(operation, thread name)``; ``load`` makes it ``loaded`` on the CPU."""

    def __init__(
        self,
        spec: ModelSpec,
        *,
        settings: Settings | None = None,
        model_dir: Path | None = None,
        warm_gate: threading.Event | None = None,
        fail_load: BaseException | None = None,
    ) -> None:
        super().__init__(spec)
        self.settings = settings
        self.model_dir = model_dir
        self.warm_gate = warm_gate
        self.fail_load = fail_load
        self.state = "installed"
        self.device: str | None = None
        self.calls: list[tuple[str, str]] = []
        self.warm_started = threading.Event()

    def _record(self, name: str) -> None:
        self.calls.append((name, threading.current_thread().name))

    def status(self) -> ProviderStatus:
        return ProviderStatus(self.spec.id, self.state, "ok", None, loaded_device=self.device)  # type: ignore[arg-type]

    def load(self, cancel: CancellationToken) -> None:
        self._record("load")
        if self.fail_load is not None:
            raise self.fail_load
        self.state, self.device = "loaded", "cpu"

    def warm(self, cancel: CancellationToken) -> None:
        self._record("warm")
        self.warm_started.set()
        if self.warm_gate is not None and not self.warm_gate.wait(10):
            raise TimeoutError("warm gate never opened")

    def offload(self) -> None:
        self._record("offload")
        self.device = "cpu"

    def unload(self) -> None:
        self._record("unload")
        self.state, self.device = "installed", None


class BrokenProvider(ModelProvider):
    """Construction fails like a provider whose CUDA DLL cannot load."""

    def __init__(self, spec: ModelSpec, **_kwargs: Any) -> None:
        raise OSError("[WinError 126] The specified module could not be found")

    def status(self) -> ProviderStatus:  # pragma: no cover - never constructed
        raise AssertionError

    def load(self, cancel: CancellationToken) -> None:  # pragma: no cover
        raise AssertionError

    def offload(self) -> None:  # pragma: no cover
        raise AssertionError

    def unload(self) -> None:  # pragma: no cover
        raise AssertionError


def _spec(model_id: str = "test_model", **changes: Any) -> ModelSpec:
    base = ModelSpec(
        id=model_id,
        kind="segmentation",
        roles=("detector",),
        display_name=f"Test {model_id}",
        provider="fake_ok",
        repo_id="example/test",
        revision="r1",
        local_subdir=f"seg/{model_id}",
        required_files=("config.json", "model.safetensors"),
        size_bytes=10,
        vram_peak_gb=2.0,
        ram_gb=1.0,
        cold_load_s=None,
        license=LicenseInfo(id="apache-2.0", name="Apache License 2.0", url="https://example.invalid", commercial="allowed"),
        default_approved=True,
    )
    return dataclasses.replace(base, **changes)


def _install(root: Path, spec: ModelSpec) -> Path:
    model_dir = root / str(spec.local_subdir)
    for name in spec.required_files:
        path = model_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
    return model_dir


@pytest.fixture
def model_root(tmp_runtime: Path) -> Path:
    root = tmp_runtime / "models"
    root.mkdir()
    return root


@pytest.fixture
def make_registry(
    make_settings: Callable[..., Settings], model_root: Path, monkeypatch: pytest.MonkeyPatch
) -> Callable[..., ProviderRegistry]:
    monkeypatch.setattr(
        registry_module,
        "PROVIDER_PATHS",
        {
            **registry_module.PROVIDER_PATHS,
            "fake_ok": "test_model_registry:FakeModelProvider",
            "fake_broken": "test_model_registry:BrokenProvider",
            "fake_missing": "inference.segmentation.no_such_provider_module:Provider",
            "fake_noattr": "test_model_registry:NoSuchClass",
        },
    )
    for name in ("HF_HOME", "HF_MODULES_CACHE", "HF_HUB_DISABLE_TELEMETRY", "HF_HUB_DISABLE_PROGRESS_BARS"):
        monkeypatch.delenv(name, raising=False)

    def factory(
        catalog: list[ModelSpec],
        *,
        engine: Any = None,
        providers: dict[str, ModelProvider] | None = None,
        on_change: Callable[[], None] | None = None,
        **settings: Any,
    ) -> ProviderRegistry:
        values: dict[str, Any] = {"aux_model_root": model_root, "device": "cpu", "hf_home": model_root / ".hf"}
        values.update(settings)
        return ProviderRegistry(
            make_settings(**values),
            catalog,
            engine=engine or FakeEngine(ready=False),
            overrides=PartProviderOverrides(providers=providers or {}),
            on_change=on_change,
        )

    return factory


# --------------------------------------------------------------------------------------------------
# Catalog v1
# --------------------------------------------------------------------------------------------------


def test_catalog_v1_lists_the_decisions_models_and_the_future_seams():
    import re

    ids = [spec.id for spec in CATALOG]
    assert ids == [
        "z_image_turbo",
        "grounding_dino_base",
        "sam2_1_hiera_large",
        "florence2_large",
        "see_through_l2d",
        "sapiens2_seg_0_4b",
        "raster_lifter",
        "p3sam",
        "partfield",
        "holopart",
        "x_part",
    ]
    assert all(re.fullmatch(MODEL_ID_PATTERN, model_id) for model_id in ids)
    assert {spec.id for spec in CATALOG if spec.default_approved} == {
        "z_image_turbo",
        "grounding_dino_base",
        "sam2_1_hiera_large",
        "florence2_large",
        "see_through_l2d",
        "sapiens2_seg_0_4b",
    }
    assert RASTER_LIFTER.approval_required is False and RASTER_LIFTER.license.commercial == "non_commercial"
    future = [spec for spec in CATALOG if spec.kind in {"segmentation_3d", "completion", "part_generation"}]
    assert [spec.id for spec in future] == ["p3sam", "partfield", "holopart", "x_part"]
    assert all(spec.provider is None and spec.hidden_by_default and spec.unsupported_reason for spec in future)
    sapiens = next(spec for spec in CATALOG if spec.id == "sapiens2_seg_0_4b")
    assert sapiens.license.commercial == "allowed_with_conditions" and len(sapiens.license.conditions) == 3
    assert Z_IMAGE_TURBO.vram_peak_gb == 24.0 and Z_IMAGE_TURBO.cold_load_s == 337.0
    assert Z_IMAGE_TURBO.capabilities["steps"]["default"] == 9


@pytest.mark.skipif(not (REAL_MODEL_ROOT / "t2i" / "Z-Image-Turbo").is_dir(), reason="model weights not installed")
def test_real_on_disk_states_of_this_machine(make_settings: Callable[..., Settings]):
    already_loaded = {name for name in HEAVY if name in sys.modules}
    registry = ProviderRegistry(make_settings(), CATALOG, engine=FakeEngine(ready=False))
    states = {status.model_id: (status.state, status.reason_code) for status in registry.statuses()}
    assert states == {
        "z_image_turbo": ("installed", "ok"),
        "grounding_dino_base": ("installed", "ok"),
        "sam2_1_hiera_large": ("installed", "ok"),
        "florence2_large": ("installed", "ok"),
        "see_through_l2d": ("installed", "ok"),  # S6: vendored submodule + local checkpoint
        "sapiens2_seg_0_4b": ("installed", "ok"),
        "raster_lifter": ("installed", "ok"),
        "p3sam": ("unavailable", "unsupported"),
        "partfield": ("unavailable", "unsupported"),
        "holopart": ("unavailable", "unsupported"),
        "x_part": ("unavailable", "unsupported"),
    }
    assert registry.status("see_through_l2d").reason is None
    assert registry.status("sapiens2_seg_0_4b").reason is None
    # segmentation revisions come from backend/.model/seg/download_manifest.json
    assert registry.status("grounding_dino_base").revision == "12bdfa3120f3e7ec7b434d90674b3396eccf88eb"
    assert {name for name in HEAVY if name in sys.modules} == already_loaded


# --------------------------------------------------------------------------------------------------
# State rules (section 5.3)
# --------------------------------------------------------------------------------------------------


def test_rules_1_to_4_policy(make_registry, model_root):
    spec = _spec()
    _install(model_root, spec)
    unsupported = _spec("future_one", provider=None, unsupported_reason="Needs spconv", kind="segmentation_3d")
    blocked = _spec("restricted", license=dataclasses.replace(spec.license, commercial="non_commercial"))
    exempt = _spec("exempt", license=blocked.license, approval_required=False, default_approved=False)
    for item in (blocked, exempt):
        _install(model_root, item)
    catalog = [spec, unsupported, blocked, exempt]

    registry = make_registry(catalog)
    assert (registry.status("future_one").state, registry.status("future_one").reason_code) == ("unavailable", "unsupported")
    assert registry.status("future_one").reason == "Needs spconv"
    assert registry.status("test_model").reason_code == "ok"
    assert registry.status("restricted").reason_code == "license_blocked"
    assert registry.status("exempt").reason_code == "ok"
    assert make_registry(catalog, allow_restricted_licenses=True).status("restricted").reason_code == "ok"

    disabled = make_registry(catalog, segmentation_enabled=False).status("test_model")
    assert (disabled.state, disabled.reason_code) == ("unavailable", "disabled")
    assert "IMG3D_SEGMENTATION_ENABLED" in (disabled.reason or "")
    master_off = make_registry(catalog, part_aware_enabled=False).status("test_model")
    assert master_off.reason_code == "disabled" and "IMG3D_PART_AWARE_ENABLED" in (master_off.reason or "")

    nothing = make_registry(catalog, approved_models=[])
    assert nothing.status("test_model").reason_code == "not_approved"
    assert nothing.status("exempt").reason_code == "ok"  # approval_required=False
    assert not nothing.is_approved("test_model") and nothing.is_approved("exempt")
    only_restricted = make_registry(catalog, approved_models=["restricted"])
    assert only_restricted.status("test_model").reason_code == "not_approved"
    assert only_restricted.effective_approved_models() == frozenset({"restricted"})


def test_rules_5_to_8_on_disk(make_registry, model_root):
    vendored_missing = _spec("vend_missing", vendored_source="third_party/no-such-vendored-tree")
    vendored_present = _spec("vend_present", vendored_source="inference/providers")
    bad_dep = _spec("bad_dep", dependencies=(DependencySpec("packaging", "==0.0.1"),))
    absent_dep = _spec("absent_dep", dependencies=(DependencySpec("img3d-no-such-distribution", ">=1"),))
    good_dep = _spec("good_dep", dependencies=(DependencySpec("packaging", ">=20"),))
    missing = _spec("missing_files")
    t2i = _spec("t2i_new", kind="text_to_image", roles=("generator",), required_files=("model_index.json",))
    t2i_old = _spec("t2i_old", kind="text_to_image", roles=("generator",), required_files=("model_index.json",))
    for item in (vendored_missing, vendored_present, bad_dep, absent_dep, good_dep, t2i, t2i_old):
        _install(model_root, item)
    for item, version in ((t2i, "5.0.0"), (t2i_old, "4.51.0")):
        encoder = model_root / str(item.local_subdir) / "text_encoder" / "config.json"
        encoder.parent.mkdir(parents=True)
        encoder.write_text(json.dumps({"transformers_version": version}), encoding="utf-8")
    registry = make_registry([vendored_missing, vendored_present, bad_dep, absent_dep, good_dep, missing, t2i, t2i_old])

    def code(model_id: str) -> tuple[str, str]:
        status = registry.status(model_id)
        return status.state, status.reason_code

    assert code("vend_missing") == ("unavailable", "vendored_source_missing")
    assert registry.status("vend_missing").reason == (
        "Test vend_missing code is not vendored under backend/third_party/no-such-vendored-tree"
    )
    assert code("vend_present") == ("installed", "ok")
    assert code("bad_dep") == ("unavailable", "missing_dependency")
    assert "packaging==0.0.1" in (registry.status("bad_dep").reason or "")
    assert code("absent_dep") == ("unavailable", "missing_dependency")
    assert "absent" in (registry.status("absent_dep").reason or "")
    assert code("good_dep") == ("installed", "ok")
    deps = registry.dependencies("good_dep")
    assert deps[0].ok and deps[0].installed
    assert code("missing_files") == ("not_installed", "not_installed")
    assert code("t2i_new") == ("unavailable", "incompatible")
    assert "transformers 5.0.0" in (registry.status("t2i_new").reason or "")
    assert code("t2i_old") == ("installed", "ok")


def test_model_paths_override_and_partial_install(make_registry, model_root, tmp_runtime):
    spec = _spec()
    elsewhere = tmp_runtime / "elsewhere"
    (elsewhere / "config.json").parent.mkdir(parents=True)
    (elsewhere / "config.json").write_text("{}", encoding="utf-8")
    registry = make_registry([spec], model_paths={"test_model": str(elsewhere)})
    assert registry.model_dir("test_model") == elsewhere
    status = registry.status("test_model")
    assert status.state == "not_installed" and "model.safetensors" in (status.reason or "")
    (elsewhere / "model.safetensors").write_text("x", encoding="utf-8")
    assert registry.status("test_model").state == "not_installed"  # cached for 5 s
    registry.refresh()
    assert registry.status("test_model").state == "installed"


def test_static_vram_rule_follows_the_engine_residency(make_registry, model_root):
    big = _spec("big_model", vram_peak_gb=24.0)
    _install(model_root, big)
    assert make_registry([big]).status("big_model").reason_code == "ok"
    resident = make_registry([big], low_vram=False, engine=FakeEngine(ready=True))
    status = resident.status("big_model")
    assert (status.state, status.reason_code) == ("unavailable", "insufficient_vram")
    assert "24 GB" in (status.reason or "") and "11 GB" in (status.reason or "")
    with pytest.raises(CapabilityUnavailableError) as raised:
        resident.check_vram_static("big_model")
    assert raised.value.details["reasons"][0]["reason_code"] == "insufficient_vram"
    assert make_registry([big], low_vram=False, engine=FakeEngine(ready=False)).status("big_model").reason_code == "ok"
    assert make_registry([big], aux_vram_budget_gb=10).status("big_model").reason_code == "insufficient_vram"
    make_registry([big]).check_vram_static("big_model")


def test_live_states_loading_error_and_provider_state(make_registry, model_root):
    spec = _spec()
    _install(model_root, spec)
    changes: list[int] = []
    registry = make_registry([spec], on_change=lambda: changes.append(1))
    assert registry.begin_load("test_model") is True
    assert registry.begin_load("test_model") is False
    assert registry.status("test_model").state == "loading" and registry.usable("test_model")
    registry.end_load("test_model", "RuntimeError: boom")
    status = registry.status("test_model")
    assert (status.state, status.reason_code, status.reason) == ("error", "error", "RuntimeError: boom")
    assert not registry.usable("test_model")
    registry.after_unload("test_model")
    assert registry.status("test_model").state == "installed"
    provider = registry.provider("test_model")
    provider.load(CancellationToken())
    loaded = registry.status("test_model")
    assert (loaded.state, loaded.loaded_device) == ("loaded", "cpu")
    assert len(changes) >= 3
    with pytest.raises(NotFoundError):
        registry.status("no_such_model")
    assert registry.usable("no_such_model") is False


# --------------------------------------------------------------------------------------------------
# Lazy providers
# --------------------------------------------------------------------------------------------------


def test_provider_is_built_lazily_after_configuring_the_hf_cache(make_registry, model_root):
    spec = _spec()
    model_dir = _install(model_root, spec)
    registry = make_registry([spec])
    assert registry.instantiated("test_model") is None
    provider = registry.provider("test_model")
    assert isinstance(provider, FakeModelProvider)
    assert provider.model_dir == model_dir and provider.settings is not None
    assert os.environ["HF_HOME"] == str(model_root / ".hf")
    assert registry.provider("test_model") is provider
    assert registry.resolve_role("detector") is provider


@pytest.mark.parametrize(
    ("provider_key", "state", "reason_code", "reason_part"),
    [
        ("fake_broken", "error", "error", "OSError: [WinError 126]"),
        ("fake_missing", "unavailable", "not_implemented", "no_such_provider_module is not part of this build"),
        ("fake_noattr", "error", "error", "AttributeError"),
        ("not_registered", "error", "error", "No implementation is registered"),
    ],
)
def test_import_failures_substitute_an_unavailable_provider(
    make_registry, model_root, provider_key, state, reason_code, reason_part
):
    spec = _spec(provider=provider_key)
    _install(model_root, spec)
    changes: list[int] = []
    registry = make_registry([spec], on_change=lambda: changes.append(1))
    with pytest.raises(ModelUnavailableError) as raised:
        registry.provider("test_model")
    assert raised.value.details["reason_code"] == reason_code
    assert isinstance(registry.instantiated("test_model"), UnavailableProvider)
    status = registry.status("test_model")
    assert (status.state, status.reason_code) == (state, reason_code)
    assert reason_part in (status.reason or "")
    assert changes and registry.resolve_role("detector") is None
    with pytest.raises(ModelUnavailableError):
        registry.provider("test_model")
    registry.after_unload("test_model")  # forget the stand-in: the next use imports again
    assert registry.instantiated("test_model") is None and registry.status("test_model").state == "installed"


def test_overrides_skip_the_disk_rules_but_not_the_policy(make_registry):
    spec = _spec(provider="zimage")  # nothing on disk
    fake = FakeModelProvider(spec)
    registry = make_registry([spec], providers={"test_model": fake})
    assert registry.status("test_model").state == "installed"
    assert registry.provider("test_model") is fake
    blocked = make_registry([spec], providers={"test_model": FakeModelProvider(spec)}, approved_models=[])
    assert blocked.status("test_model").reason_code == "not_approved"
    with pytest.raises(ModelUnavailableError):
        blocked.provider("test_model")


# --------------------------------------------------------------------------------------------------
# VRAM, offload, roles, defaults
# --------------------------------------------------------------------------------------------------


def test_live_vram_check(make_registry, model_root, monkeypatch):
    first, second = _spec("first", vram_peak_gb=2.0), _spec("second", vram_peak_gb=3.0)
    registry = make_registry([first, second], aux_vram_margin_gb=1.5)
    monkeypatch.setattr(registry_module, "free_vram_mb", lambda device: 4096.0)
    with pytest.raises(InsufficientVramError) as raised:
        registry.ensure_vram(["first", "second"])
    assert raised.value.details == {"model_ids": ["first", "second"], "free_mb": 4096.0, "required_mb": 6656.0}
    registry.ensure_vram(["first"])  # 3.5 GB needed, 4 GB free
    registry.ensure_vram([])
    monkeypatch.setattr(registry_module, "free_vram_mb", lambda device: None)  # no CUDA: the phase reports it
    registry.ensure_vram(["first", "second"])


def test_offload_marks_use_and_skips_unbuilt_providers(make_registry, model_root):
    spec, other = _spec(), _spec("other_model")
    _install(model_root, spec)
    registry = make_registry([spec, other])
    provider = registry.provider("test_model")
    registry.offload(["test_model", "other_model"])
    assert [name for name, _thread in provider.calls] == ["offload"]
    assert registry.status("test_model").last_used_at is not None
    assert registry.instantiated("other_model") is None


def test_roles_defaults_and_preferred_models(make_registry, model_root):
    gen_a = _spec("gen_a", kind="text_to_image", roles=("generator",))
    gen_b = _spec("gen_b", kind="text_to_image", roles=("generator",))
    det_a, det_b = _spec("det_a"), _spec("det_b")
    anime = _spec("anime_parser", roles=("parser",), capabilities={"subject": "anime_character"})
    real = _spec("real_parser", roles=("parser",), capabilities={"subject": "realistic_human"})
    catalog = [gen_a, gen_b, det_a, det_b, anime, real]
    for item in (gen_b, det_b, anime, real):
        _install(model_root, item)
    registry = make_registry(catalog)
    assert registry.default_model("text_to_image") == "gen_b"  # first usable
    assert registry.default_model("part_extraction") is None
    assert registry.preferred_model_ids() == {"gen_a", "det_a", "anime_parser", "real_parser"}
    assert make_registry(catalog, t2i_default_model="gen_a").default_model("text_to_image") == "gen_a"
    assert registry.resolve_role("detector").model_id == "det_b"
    assert registry.resolve_role("detector", preferred=["det_a", "det_b"]).model_id == "det_b"
    assert registry.resolve_role("lifter") is None


def test_download_manifest_revisions(make_registry, model_root, caplog):
    spec = _spec(revision="catalog-rev")
    _install(model_root, spec)
    manifest = model_root / "seg" / "download_manifest.json"
    manifest.write_text(json.dumps({"test_model": {"repo": "example/test", "revision": "disk-rev"}}), encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="inference.providers.registry"):
        registry = make_registry([spec])
    assert "differs from the catalog revision" in caplog.text
    assert registry.status("test_model").revision == "disk-rev"


def test_idle_providers(make_registry, model_root):
    spec = _spec()
    _install(model_root, spec)
    assert make_registry([spec]).idle_providers(utcnow()) == []
    registry = make_registry([spec], aux_model_idle_unload_s=60)
    provider = registry.provider("test_model")
    provider.load(CancellationToken())
    registry.mark_used("test_model")
    assert registry.idle_providers(utcnow()) == []
    assert registry.idle_providers(utcnow() + timedelta(seconds=61)) == [provider]
