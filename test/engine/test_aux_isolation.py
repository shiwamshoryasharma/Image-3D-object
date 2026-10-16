"""Auxiliary GPU work cannot change a same-seed reconstruction (part-aware DESIGN.md section 4.9; slice S2).

Runs on torch's CPU build path (no CUDA needed). The fake TRELLIS.2 pipeline of ``test_generation_parity.py`` runs twice
with the same seed on the real single ``gpu-worker`` thread of :class:`workers.generation_worker.GenerationWorker`;
between the runs a callable submitted through ``submit_gpu_task`` flips **every** global guarded by
:func:`inference.providers.vram.torch_globals_guard` (default dtype float64, both reduced-precision flags off, one
intra-op thread, TF32/cuDNN/SDP/determinism/grad switches). With the guard, the call sequences and RNG fingerprints
are identical, every global equals its snapshot and the ``[torch_guard] restored`` record lists exactly the flipped
names. A negative control runs the second reconstruction with the flipped globals still in place and shows the
fingerprint change.

The recording pipeline additionally fingerprints the default-dtype noise each stage draws, like upstream's CPU
``torch.randn`` sampling: that is the channel through which a leaked global would alter a reconstruction.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

torch = pytest.importorskip("torch")

import test_generation_parity as parity  # noqa: E402 - the fake pipeline and fingerprint recipe of the parity test

from inference.base import CancellationToken  # noqa: E402
from inference.providers.vram import (  # noqa: E402
    GUARDED_GLOBAL_NAMES,
    RESTORED_LOG_PREFIX,
    snapshot_torch_globals,
    torch_globals_guard,
)
from inference.trellis import generation as generation_module  # noqa: E402
from inference.trellis.generation import TrellisGenerationRun  # noqa: E402
from workers.generation_worker import GPU_THREAD_NAME, GenerationWorker  # noqa: E402

SEED = 123456
GUARD_LOGGER = "inference.providers.vram"


class NoiseRecordingPipeline(parity.RecordingPipeline):
    """The parity test double plus a fingerprint of the default-dtype noise drawn at every stage."""

    def _record(self, name: str, *args: Any) -> None:
        super()._record(name, *args)
        if name == "preprocess_image":
            return
        noise = torch.randn(4)
        digest = hashlib.sha1(noise.numpy().tobytes()).hexdigest()[:16]
        self.calls.append((f"{name}:noise", parity._rng_fingerprint(), digest, str(noise.dtype)))


@pytest.fixture(autouse=True)
def fake_export(monkeypatch: pytest.MonkeyPatch) -> None:
    def export(mesh, pipeline, resolution, params, out_path, reporter, cancel):  # noqa: ANN001
        cancel.raise_if_cancelled()
        Path(out_path).write_bytes(b"glTF-test-double")
        reporter.step(2, 2, "written")
        return {"vertices": 1, "faces": 1, "file_size_bytes": 16}

    monkeypatch.setattr(generation_module, "export_glb", export)


def _reconstruct(out_dir: Path, prior_seed: int) -> tuple[list[tuple[Any, ...]], str]:
    """One same-seed TRELLIS.2 generation with the recording pipeline (runs on the calling thread)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    pipeline = NoiseRecordingPipeline()
    torch.manual_seed(prior_seed)  # unrelated prior state: the generation reseeds at conditioning
    request = parity._request(out_dir, parity._image(out_dir), resolution=512, seed=SEED, remove_background=True)
    TrellisGenerationRun(pipeline, request, parity.RecordingReporter(), CancellationToken()).execute()
    return pipeline.calls, threading.current_thread().name


def _flip_every_guarded_global() -> None:
    """What a careless diffusers/transformers provider could leave behind on the shared GPU thread."""
    backends = torch.backends
    torch.set_default_dtype(torch.float64)
    precision = torch.get_float32_matmul_precision()
    # also flips torch.backends.cuda.matmul.allow_tf32 (the legacy flag follows the precision)
    torch.set_float32_matmul_precision("medium" if precision != "medium" else "highest")
    backends.cuda.matmul.allow_fp16_reduced_precision_reduction = False
    backends.cuda.matmul.allow_bf16_reduced_precision_reduction = False
    backends.cudnn.benchmark = not backends.cudnn.benchmark
    backends.cudnn.deterministic = not backends.cudnn.deterministic
    backends.cudnn.allow_tf32 = not backends.cudnn.allow_tf32
    torch.set_num_threads(1 if torch.get_num_threads() != 1 else 2)
    backends.cuda.enable_flash_sdp(not backends.cuda.flash_sdp_enabled())
    backends.cuda.enable_mem_efficient_sdp(not backends.cuda.mem_efficient_sdp_enabled())
    backends.cuda.enable_math_sdp(not backends.cuda.math_sdp_enabled())
    torch.use_deterministic_algorithms(not torch.are_deterministic_algorithms_enabled(), warn_only=True)
    torch.set_grad_enabled(not torch.is_grad_enabled())


def _changed_names(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    return sorted(name for name in GUARDED_GLOBAL_NAMES if before[name] != after[name])


def _guarded_flip() -> list[str]:
    """The auxiliary GPU callable: flips everything inside the guard, returns the names it changed."""
    with torch_globals_guard():
        before = snapshot_torch_globals()
        _flip_every_guarded_global()
        return _changed_names(before, snapshot_torch_globals())


def _unguarded_reconstruct(out_dir: Path, prior_seed: int) -> tuple[list[tuple[Any, ...]], str]:
    """Negative control: the second reconstruction sees the flipped globals (the outer guard only cleans up)."""
    with torch_globals_guard():
        _flip_every_guarded_global()
        return _reconstruct(out_dir, prior_seed)


async def _on_gpu_thread(*steps: tuple[Any, ...]) -> list[Any]:
    """Submit each ``(fn, *args)`` through ``GenerationWorker.submit_gpu_task`` in order (FIFO, one thread)."""
    worker = GenerationWorker(
        engine=SimpleNamespace(),  # only the gpu-worker executor is used here
        pipeline=None,  # type: ignore[arg-type]
        job_store=None,  # type: ignore[arg-type]
        queue=None,  # type: ignore[arg-type]
        events=None,  # type: ignore[arg-type]
        bus=None,  # type: ignore[arg-type]
        on_system_changed=lambda: None,
    )
    try:
        return [await worker.submit_gpu_task(fn, *args) for fn, *args in steps]
    finally:
        worker._executor.shutdown(wait=True)


def test_guarded_auxiliary_task_leaves_same_seed_reconstructions_unchanged(tmp_path: Path, caplog):
    with caplog.at_level(logging.INFO, logger=GUARD_LOGGER):
        first, before, flipped, second, after = asyncio.run(
            _on_gpu_thread(
                (_reconstruct, tmp_path / "first", 42),
                (snapshot_torch_globals,),
                (_guarded_flip,),
                (_reconstruct, tmp_path / "second", 7),
                (snapshot_torch_globals,),
            )
        )
    first_calls, first_thread = first
    second_calls, second_thread = second
    assert first_thread.startswith(GPU_THREAD_NAME) and second_thread.startswith(GPU_THREAD_NAME)
    assert flipped == sorted(GUARDED_GLOBAL_NAMES)  # every guarded global really was flipped
    assert second_calls == first_calls  # identical call sequence, RNG and noise fingerprints
    assert any(len(call) > 1 and call[1] is not None for call in first_calls)
    assert after == before
    records = [r.getMessage() for r in caplog.records if r.name == GUARD_LOGGER and RESTORED_LOG_PREFIX in r.getMessage()]
    assert records == [f"{RESTORED_LOG_PREFIX} {flipped}"]


def test_negative_control_without_the_guard_changes_the_fingerprints(tmp_path: Path):
    first, before, second, after = asyncio.run(
        _on_gpu_thread(
            (_reconstruct, tmp_path / "first", 42),
            (snapshot_torch_globals,),
            (_unguarded_reconstruct, tmp_path / "second", 42),
            (snapshot_torch_globals,),
        )
    )
    assert second[0] != first[0]
    noise_dtypes = {call[3] for call in second[0] if str(call[0]).endswith(":noise")}
    assert noise_dtypes == {"torch.float64"}
    assert after == before  # the clean-up guard restored everything for the next test


def test_guard_logs_nothing_when_nothing_changed(caplog):
    def untouched() -> None:
        with torch_globals_guard():
            torch.randn(2)

    with caplog.at_level(logging.INFO, logger=GUARD_LOGGER):
        asyncio.run(_on_gpu_thread((untouched,)))
    assert not [r for r in caplog.records if r.name == GUARD_LOGGER]


def test_guard_restores_globals_when_the_callable_raises():
    before = snapshot_torch_globals()
    with pytest.raises(RuntimeError, match="provider failed"):
        with torch_globals_guard():
            torch.set_default_dtype(torch.float64)
            torch.set_num_threads(1 if torch.get_num_threads() != 1 else 2)
            raise RuntimeError("provider failed")
    assert snapshot_torch_globals() == before
