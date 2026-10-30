"""CPU-only regression for the PyTorch -> native CUDA allocator handoff.

No CUDA tensor, context, allocation or native extension is created: the exported
mesh only advertises its device and all torch.cuda entry points are recorders.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import sys

import numpy as np
import pytest
import trimesh


def _export_fixture(
    monkeypatch, tmp_path: Path, *, cuda: bool, available: bool = True,
    resolution: int = 512, requested_resolution: int | None = None,
    native_kwargs: dict | None = None, native_calls: list | None = None,
    decoded_vertex_count: int = 8,
):
    import torch
    from inference.base import GenerationParams

    events = []
    device = torch.device("cuda:1" if cuda else "cpu")
    mesh = SimpleNamespace(
        vertices=SimpleNamespace(shape=(decoded_vertex_count, 3), is_cuda=cuda, device=device),
        faces=SimpleNamespace(shape=(12, 3)), attrs=object(),
        coords=torch.tensor([[1, 2, 3], [resolution - 3, resolution - 2, resolution - 1]], dtype=torch.int32),
    )

    @contextmanager
    def device_context(selected):
        events.append(("device_enter", selected))
        try:
            yield
        finally:
            events.append(("device_exit", selected))

    monkeypatch.setattr(torch.cuda, "is_available", lambda: available)
    monkeypatch.setattr(torch.cuda, "device", device_context)
    monkeypatch.setattr(torch.cuda, "synchronize", lambda *args, **kwargs: events.append(("synchronize",)))
    monkeypatch.setattr(torch.cuda, "empty_cache", lambda: events.append(("empty_cache",)))

    def native_export(**kwargs):
        events.append(("to_glb",))
        assert kwargs["vertices"] is mesh.vertices
        if native_kwargs is not None:
            native_kwargs.update(kwargs)
        if native_calls is not None:
            native_calls.append(kwargs.copy())
        return trimesh.creation.box(extents=(0.5, 0.5, 0.5))

    def forbid_upstream_export(*args, **kwargs):
        pytest.fail("export must use the project adapter that bypasses native hole filling")
    monkeypatch.setitem(sys.modules, "o_voxel", SimpleNamespace(postprocess=SimpleNamespace(to_glb=forbid_upstream_export)))
    monkeypatch.setitem(sys.modules, "inference.trellis.postprocess", SimpleNamespace(to_glb=native_export))
    from inference.trellis import export as export_module
    monkeypatch.setattr(export_module, "to_glb", native_export, raising=False)
    # Existing adapter tests isolate the support gate; dedicated cases below
    # assert its conversion, retry and publication behavior.
    monkeypatch.setattr(export_module, "count_unsupported_vertices", lambda *args: 0, raising=False)
    monkeypatch.setattr(export_module, "count_uncovered_source", lambda *args, **kwargs: (0, 2), raising=False)
    cancel = SimpleNamespace(raise_if_cancelled=lambda: events.append(("cancel",)))
    reporter = SimpleNamespace(step=lambda *args: None)
    params = GenerationParams(
        resolution=resolution if requested_resolution is None else requested_resolution,
        polygon_target=2_000_000, texture_size=4096,
    )
    args = (mesh, SimpleNamespace(pbr_attr_layout={}), resolution, params, tmp_path / "model.glb", reporter, cancel)
    return export_module, args, events, device


def test_cuda_export_releases_cached_allocator_blocks_before_native_handoff(monkeypatch, tmp_path):
    export_module, args, events, device = _export_fixture(monkeypatch, tmp_path, cuda=True)
    stats = export_module.export_glb(*args)
    assert events == [
        ("cancel",), ("device_enter", device), ("synchronize",),
        ("empty_cache",), ("device_exit", device), ("to_glb",), ("cancel",),
    ], "native export must start only after completion and allocator-cache release on the mesh's CUDA device"
    assert args[4].is_file()
    assert stats["faces"] == 12


@pytest.mark.parametrize("available", [False, True])
def test_cpu_export_does_not_touch_cuda_allocator(monkeypatch, tmp_path, available):
    export_module, args, events, _ = _export_fixture(monkeypatch, tmp_path, cuda=False, available=available)
    export_module.export_glb(*args)
    assert events == [("cancel",), ("to_glb",), ("cancel",)]


def test_cancelled_export_checks_cancellation_before_cuda_handoff(monkeypatch, tmp_path):
    export_module, args, events, _ = _export_fixture(monkeypatch, tmp_path, cuda=True)

    class Cancelled(RuntimeError):
        pass

    def cancel():
        events.append(("cancel",))
        raise Cancelled("stop before touching native CUDA")

    args[-1].raise_if_cancelled = cancel
    with pytest.raises(Cancelled):
        export_module.export_glb(*args)
    assert events == [("cancel",)]
    assert not args[4].exists()


@pytest.mark.parametrize(
    ("resolution", "remesh_resolution"),
    [
        (512, 512),
        (1024, 1024),
        (1536, 1536),
    ],
)
def test_export_uses_full_decoded_and_attribute_resolution(
    monkeypatch, tmp_path, resolution, remesh_resolution
):
    captured = {}
    export_module, args, _, _ = _export_fixture(
        monkeypatch, tmp_path, cuda=False, resolution=resolution, native_kwargs=captured,
    )
    stats = export_module.export_glb(*args)
    assert captured["remesh"] is True
    assert captured["remesh_band"] == 2
    assert captured["remesh_resolution"] == remesh_resolution
    assert captured["remesh_project"] == 0
    # This selects a supported export path, retaining the decoded shape's
    # effective resolution and the user's full polygon / texture settings.
    assert captured["grid_size"] == resolution
    assert captured["decimation_target"] == 2_000_000
    assert captured["texture_size"] == 4096
    assert stats["export_strategy"] == "narrow_band_remesh"
    assert stats["remesh_band"] == 2
    assert stats["remesh_resolution"] == remesh_resolution
    assert args[4].is_file()


def test_export_strategy_uses_effective_resolution_after_decoder_fallback(monkeypatch, tmp_path):
    captured = {}
    export_module, args, _, _ = _export_fixture(
        monkeypatch, tmp_path, cuda=False, resolution=1024,
        requested_resolution=1536, native_kwargs=captured,
    )
    stats = export_module.export_glb(*args)
    assert captured["grid_size"] == 1024
    assert captured["remesh"] is True
    assert captured["remesh_resolution"] == 1024
    assert stats["export_strategy"] == "narrow_band_remesh"


@pytest.mark.parametrize("cuda", [False, True])
def test_unsupported_remesh_retries_original_inputs_with_smaller_remesh_grid(monkeypatch, tmp_path, cuda):
    calls = []
    export_module, args, _, _ = _export_fixture(
        monkeypatch, tmp_path, cuda=cuda, resolution=1024, native_calls=calls,
    )
    counts = iter([7, 0])
    checked = []

    def check(mesh, support, resolution):
        checked.append((mesh, np.array(support, copy=True), resolution))
        return next(counts)

    monkeypatch.setattr(export_module, "count_unsupported_vertices", check, raising=False)
    stats = export_module.export_glb(*args)
    assert len(calls) == 2, "unsupported remesh output must be replaced, never published or trimmed"
    assert [call["remesh"] for call in calls] == [True, True]
    assert [call["remesh_band"] for call in calls] == [2, 2]
    assert [call["remesh_resolution"] for call in calls] == [1024, 512]
    for call in calls:
        for key, original in (("vertices", args[0].vertices), ("faces", args[0].faces),
                              ("attr_volume", args[0].attrs), ("coords", args[0].coords)):
            assert call[key] is original, "retry must restart from the original decoded inputs"
        assert call["grid_size"] == 1024
        assert call["decimation_target"] == 2_000_000
        assert call["texture_size"] == 4096
    assert len(checked) == 2
    assert checked[0][0] is not checked[1][0]
    # The quality gate is read-only: neither candidate's unsupported faces are
    # pruned to disguise a corrupted remesh as successful output.
    assert [len(item[0].faces) for item in checked] == [12, 12]
    source = (args[0].coords.numpy().astype(float) + 0.5) / 1024 - 0.5
    expected_glb = source[:, [0, 2, 1]] * [1, 1, -1]
    for (_, support, resolution), expected_grid in zip(checked, (1024, 512)):
        np.testing.assert_allclose(support, expected_glb, atol=0, rtol=0)
        assert resolution == expected_grid
    assert stats["export_strategy"] == "narrow_band_remesh"
    assert stats["remesh_resolution"] == 512
    assert stats["remesh_band"] == 2
    assert args[4].is_file()


@pytest.mark.parametrize(("resolution", "counts", "attempts"), [(1024, [7, 5], 2), (1536, [7, 5], 2)])
def test_unsupported_second_remesh_is_rejected_without_replacing_existing_output(
    monkeypatch, tmp_path, resolution, counts, attempts
):
    calls = []
    export_module, args, _, _ = _export_fixture(
        monkeypatch, tmp_path, cuda=False, resolution=resolution, native_calls=calls,
    )
    remaining = iter(counts)
    monkeypatch.setattr(export_module, "count_unsupported_vertices", lambda *args: next(remaining), raising=False)
    args[4].write_bytes(b"previous valid export")
    with pytest.raises(ValueError, match="(?i)unsupported|source|support"):
        export_module.export_glb(*args)
    assert len(calls) == attempts
    assert calls[-1]["remesh"] is True
    assert [call["remesh_resolution"] for call in calls] == [resolution, resolution // 2]
    assert args[4].read_bytes() == b"previous valid export"
    assert not args[4].with_name(args[4].name + ".tmp").exists()


@pytest.mark.parametrize("cuda", [False, True])
def test_missing_source_surface_retries_smaller_remesh_grid_without_pruning(monkeypatch, tmp_path, cuda):
    calls = []
    export_module, args, _, device = _export_fixture(
        monkeypatch, tmp_path, cuda=cuda, resolution=1024, native_calls=calls,
    )
    results = iter([(1, 2), (0, 2)])
    checked = []

    def coverage(mesh, support, resolution, device):
        checked.append((mesh, np.array(support, copy=True), resolution, device))
        return next(results)

    monkeypatch.setattr(export_module, "count_uncovered_source", coverage, raising=False)
    stats = export_module.export_glb(*args)
    assert [call["remesh"] for call in calls] == [True, True], "missing source surface must trigger a smaller remesh grid"
    assert len(checked) == 2
    assert checked[0][0] is not checked[1][0]
    for call in calls:
        assert call["vertices"] is args[0].vertices
        assert call["faces"] is args[0].faces
        assert call["coords"] is args[0].coords
        assert call["decimation_target"] == 2_000_000
        assert call["texture_size"] == 4096
    for (mesh, support, resolution, selected), expected_grid in zip(checked, (1024, 512)):
        assert len(mesh.faces) == 12
        assert resolution == expected_grid
        assert str(selected) == str(device)
        assert support.shape == (2, 3)
    assert stats["export_strategy"] == "narrow_band_remesh"
    assert stats["remesh_resolution"] == 512
    assert args[4].is_file()


@pytest.mark.parametrize(("resolution", "results", "attempts"),
                         [(1024, [(1, 2), (1, 2)], 2), (1536, [(1, 2), (1, 2)], 2)])
def test_missing_surface_after_second_remesh_is_rejected_without_overwriting_previous_glb(
    monkeypatch, tmp_path, resolution, results, attempts
):
    calls = []
    export_module, args, _, _ = _export_fixture(
        monkeypatch, tmp_path, cuda=False, resolution=resolution, native_calls=calls,
    )
    values = iter(results)
    monkeypatch.setattr(export_module, "count_uncovered_source", lambda *args, **kwargs: next(values), raising=False)
    args[4].write_bytes(b"previous complete model")
    with pytest.raises(ValueError, match="(?i)source|coverage|surface|support"):
        export_module.export_glb(*args)
    assert len(calls) == attempts
    assert calls[-1]["remesh"] is True
    assert [call["remesh_resolution"] for call in calls] == [resolution, resolution // 2]
    assert args[4].read_bytes() == b"previous complete model"
    assert not args[4].with_name(args[4].name + ".tmp").exists()


def test_exactly_five_percent_uncovered_surface_does_not_trigger_retry(monkeypatch, tmp_path):
    calls = []
    export_module, args, _, _ = _export_fixture(
        monkeypatch, tmp_path, cuda=False, resolution=1024, native_calls=calls,
    )
    checks = []
    def coverage(*args, **kwargs):
        checks.append(True)
        return 1, 20
    monkeypatch.setattr(export_module, "count_uncovered_source", coverage, raising=False)
    stats = export_module.export_glb(*args)
    assert len(checks) == 1, "every real source-supported export must undergo the coverage gate"
    assert len(calls) == 1
    assert stats["export_strategy"] == "narrow_band_remesh"


def test_512_remesh_retry_uses_256_grid_without_altering_attribute_resolution(monkeypatch, tmp_path):
    calls = []
    export_module, args, _, _ = _export_fixture(
        monkeypatch, tmp_path, cuda=False, resolution=512, native_calls=calls,
    )
    values = iter([7, 0])
    monkeypatch.setattr(export_module, "count_unsupported_vertices", lambda *args: next(values))
    stats = export_module.export_glb(*args)
    assert [call["remesh_resolution"] for call in calls] == [512, 256]
    assert all(call["grid_size"] == 512 and call["remesh"] is True for call in calls)
    assert stats["remesh_resolution"] == 256
    assert stats["remesh_band"] == 2


@pytest.mark.parametrize(("decoded_vertices", "expected_grid"),
                         [(6_380_000, 1536), (9_130_000, 1536)])
def test_decoded_vertex_density_does_not_reduce_full_resolution_or_change_source_frame(
    monkeypatch, tmp_path, decoded_vertices, expected_grid
):
    # Only the .shape metadata advertises millions of source vertices. No large
    # tensor, model, memory allocation, or GPU is involved in this test.
    calls = []
    export_module, args, _, _ = _export_fixture(
        monkeypatch, tmp_path, cuda=False, resolution=1536,
        decoded_vertex_count=decoded_vertices, native_calls=calls,
    )
    repair_resolutions = []
    for name in ("clean_export_mesh", "fill_small_triangle_holes", "repair_small_boundary_loops"):
        original = getattr(export_module, name)
        def repair(mesh, resolution, *, _name=name, _original=original):
            repair_resolutions.append((_name, resolution))
            return _original(mesh, resolution)
        monkeypatch.setattr(export_module, name, repair)
    source_checks = []
    def support_gate(mesh, points, resolution):
        source_checks.append((np.array(points, copy=True), resolution))
        return 0
    def coverage_gate(mesh, points, resolution, device):
        source_checks.append((np.array(points, copy=True), resolution))
        return 0, len(points)
    monkeypatch.setattr(export_module, "count_unsupported_vertices", support_gate)
    monkeypatch.setattr(export_module, "count_uncovered_source", coverage_gate)
    stats = export_module.export_glb(*args)
    assert len(calls) == 1
    assert calls[0]["grid_size"] == 1536, "texture/attribute voxels must keep the original resolution"
    assert calls[0]["remesh_resolution"] == expected_grid
    assert calls[0]["vertices"] is args[0].vertices
    assert calls[0]["coords"] is args[0].coords
    assert calls[0]["decimation_target"] == 2_000_000
    assert calls[0]["texture_size"] == 4096
    assert stats["decoded_vertices"] == decoded_vertices
    assert stats["remesh_resolution"] == expected_grid
    assert repair_resolutions == [(name, expected_grid) for name in (
        "clean_export_mesh", "fill_small_triangle_holes", "repair_small_boundary_loops")]
    centers = (args[0].coords.numpy().astype(np.float64) + 0.5) / 1536 - 0.5
    expected_support = centers[:, [0, 2, 1]] * [1, 1, -1]
    assert len(source_checks) == 2
    for points, tolerance_resolution in source_checks:
        np.testing.assert_array_equal(points, expected_support)
        assert tolerance_resolution == expected_grid


def test_1536_source_retry_halves_grid_to_768_and_retains_original_inputs(monkeypatch, tmp_path):
    calls = []
    export_module, args, _, _ = _export_fixture(
        monkeypatch, tmp_path, cuda=False, resolution=1536,
        decoded_vertex_count=9_130_000, native_calls=calls,
    )
    counts = iter([7, 0])
    checked = []
    def gate(mesh, points, resolution):
        checked.append(resolution)
        return next(counts)
    monkeypatch.setattr(export_module, "count_unsupported_vertices", gate)
    stats = export_module.export_glb(*args)
    assert [call["remesh_resolution"] for call in calls] == [1536, 768]
    assert checked == [1536, 768]
    assert all(call["grid_size"] == 1536 and call["vertices"] is args[0].vertices for call in calls)
    assert stats["remesh_resolution"] == 768
