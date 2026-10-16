"""Runtime adaptations of the vendored TRELLIS.2 code (``third_party`` is never edited).

(a) :func:`install_sparse_sdpa_fallback` — upstream sparse attention only supports xformers / flash_attn /
    flash_attn_3, none of which is available for this Windows/sm_120 stack by default. The fallback implements
    ``trellis2.modules.sparse.attention.full_attn.sparse_scaled_dot_product_attention`` with
    ``torch.nn.functional.scaled_dot_product_attention`` (batched when all sequence lengths are equal, otherwise
    one call per sequence) and rebinds every trellis2 module attribute that captured the original function.

(b) :func:`hook_sampler_steps` — wraps a sampler *instance*'s ``sample_once`` to report per-step progress and to
    check for cancellation; the original method is restored on exit.

(c) :func:`silence_upstream_output` / :func:`capture_prints` — route upstream ``print``/``tqdm`` output to logging.
"""

from __future__ import annotations

import contextlib
import importlib
import io
import logging
import sys
import threading
from collections.abc import Callable, Iterator
from typing import Any

import torch
import torch.nn.functional as F

logger = logging.getLogger(__name__)
upstream_logger = logging.getLogger("trellis2.upstream")

_FALLBACK_MARKER = "__img3d_sdpa_fallback__"
_FULL_ATTN_MODULE = "trellis2.modules.sparse.attention.full_attn"
_FUNCTION_NAME = "sparse_scaled_dot_product_attention"

_install_lock = threading.Lock()
_original_function: Callable[..., Any] | None = None
_patched_bindings: list[tuple[str, str]] = []
_previous_sparse_attn: str | None = None


# ==================================================================================================
# (a) SDPA fallback for sparse (variable-length) attention
# ==================================================================================================


def _varlen_type() -> type:
    from trellis2.modules.sparse import VarLenTensor

    return VarLenTensor


def _seqlens(x: Any) -> list[int]:
    return [int(s.stop - s.start) for s in x.layout]


def _parse_attention_args(args: tuple[Any, ...], kwargs: dict[str, Any]) -> tuple[Any, ...]:
    """Normalize every upstream call signature to packed ``[T, H, C]`` tensors.

    Returns ``(q, k, v, q_seqlen, kv_seqlen, sparse_like, dense_q_shape)`` where ``sparse_like`` is the
    VarLenTensor whose layout the output takes (``None`` when ``q`` was dense) and ``dense_q_shape`` is
    ``(N, L, H)`` of a dense ``q``.
    """
    varlen = _varlen_type()
    names = {1: ("qkv",), 2: ("q", "kv"), 3: ("q", "k", "v")}
    count = len(args) + len(kwargs)
    if count not in names:
        raise TypeError(f"sparse_scaled_dot_product_attention expects 1, 2 or 3 arguments, got {count}")
    expected = names[count]
    unknown = set(kwargs) - set(expected[len(args):])
    missing = [key for key in expected[len(args):] if key not in kwargs]
    if unknown or missing:
        raise TypeError(f"Invalid arguments: missing {missing}, unexpected {sorted(unknown)}")
    values = list(args) + [kwargs[key] for key in expected[len(args):]]

    if count == 1:
        (qkv,) = values
        if not isinstance(qkv, varlen):
            raise TypeError(f"qkv must be a VarLenTensor, got {type(qkv)}")
        if qkv.feats.ndim != 4 or qkv.feats.shape[1] != 3:
            raise ValueError(f"Invalid shape for qkv, got {tuple(qkv.shape)}, expected [N, *, 3, H, C]")
        seqlen = _seqlens(qkv)
        q, k, v = qkv.feats.unbind(dim=1)
        return q, k, v, seqlen, seqlen, qkv, None

    q_in = values[0]
    if isinstance(q_in, varlen):
        if q_in.feats.ndim != 3:
            raise ValueError(f"Invalid shape for q, got {tuple(q_in.shape)}, expected [N, *, H, C]")
        q, q_seqlen, sparse_like, dense_q_shape = q_in.feats, _seqlens(q_in), q_in, None
        batch = len(q_in.layout)
    elif isinstance(q_in, torch.Tensor):
        if q_in.ndim != 4:
            raise ValueError(f"Invalid shape for q, got {tuple(q_in.shape)}, expected [N, L, H, C]")
        n, length, heads, channels = q_in.shape
        q = q_in.reshape(n * length, heads, channels)
        q_seqlen, sparse_like, dense_q_shape, batch = [length] * n, None, (n, length, heads), n
    else:
        raise TypeError(f"q must be a VarLenTensor or torch.Tensor, got {type(q_in)}")

    if count == 2:
        kv = values[1]
        if isinstance(kv, varlen):
            if kv.feats.ndim != 4 or kv.feats.shape[1] != 2:
                raise ValueError(f"Invalid shape for kv, got {tuple(kv.shape)}, expected [N, *, 2, H, C]")
            kv_seqlen = _seqlens(kv)
            k, v = kv.feats.unbind(dim=1)
            kv_batch = len(kv.layout)
        elif isinstance(kv, torch.Tensor):
            if sparse_like is None:
                raise TypeError("Dense q requires a VarLenTensor kv (use dense attention for dense inputs)")
            if kv.ndim != 5 or kv.shape[2] != 2:
                raise ValueError(f"Invalid shape for kv, got {tuple(kv.shape)}, expected [N, L, 2, H, C]")
            n, length, _, heads, channels = kv.shape
            k, v = kv.reshape(n * length, 2, heads, channels).unbind(dim=1)
            kv_seqlen, kv_batch = [length] * n, n
        else:
            raise TypeError(f"kv must be a VarLenTensor or torch.Tensor, got {type(kv)}")
    else:
        k_in, v_in = values[1], values[2]
        if isinstance(k_in, varlen) and isinstance(v_in, varlen):
            if k_in.feats.ndim != 3 or v_in.feats.ndim != 3:
                raise ValueError("Invalid shapes for k/v, expected [N, *, H, C]")
            kv_seqlen = _seqlens(k_in)
            k, v = k_in.feats, v_in.feats
            kv_batch = len(k_in.layout)
        elif isinstance(k_in, torch.Tensor) and isinstance(v_in, torch.Tensor) and not isinstance(k_in, varlen):
            if sparse_like is None:
                raise TypeError("Dense q requires VarLenTensor k/v (use dense attention for dense inputs)")
            if k_in.ndim != 4 or v_in.ndim != 4:
                raise ValueError("Invalid shapes for k/v, expected [N, L, H, C]")
            n, length, heads, ci = k_in.shape
            k = k_in.reshape(n * length, heads, ci)
            v = v_in.reshape(n * length, heads, v_in.shape[-1])
            kv_seqlen, kv_batch = [length] * n, n
        else:
            raise TypeError(f"Invalid types for k/v: {type(k_in)} and {type(v_in)}")

    if batch != kv_batch:
        raise ValueError(f"Batch size mismatch, got {batch} and {kv_batch}")
    return q, k, v, q_seqlen, kv_seqlen, sparse_like, dense_q_shape


def varlen_sdpa(
    q: torch.Tensor,
    k: torch.Tensor,
    v: torch.Tensor,
    q_seqlen: list[int],
    kv_seqlen: list[int],
) -> torch.Tensor:
    """Block-diagonal attention on packed tensors.

    Args:
        q: ``[T_q, H, Ci]``; k: ``[T_kv, H, Ci]``; v: ``[T_kv, H, Co]``.
        q_seqlen / kv_seqlen: per-sequence lengths (sum to ``T_q`` / ``T_kv``).

    Returns:
        ``[T_q, H, Co]``. Uses ``scale = 1/sqrt(Ci)`` like flash_attn / xformers.
    """
    if len(q_seqlen) != len(kv_seqlen):
        raise ValueError("q_seqlen and kv_seqlen must have the same number of sequences")
    heads, out_channels = q.shape[1], v.shape[-1]
    batch = len(q_seqlen)
    if batch == 0:
        return q.new_empty((0, heads, out_channels))

    if len(set(q_seqlen)) == 1 and len(set(kv_seqlen)) == 1:
        lq, lkv = q_seqlen[0], kv_seqlen[0]
        qb = q.reshape(batch, lq, heads, -1).transpose(1, 2)  # [B, H, Lq, Ci]
        kb = k.reshape(batch, lkv, heads, -1).transpose(1, 2)
        vb = v.reshape(batch, lkv, heads, -1).transpose(1, 2)
        out = F.scaled_dot_product_attention(qb, kb, vb)  # [B, H, Lq, Co]
        return out.transpose(1, 2).reshape(batch * lq, heads, out_channels)

    out = q.new_empty((q.shape[0], heads, out_channels))
    q_start = kv_start = 0
    for lq, lkv in zip(q_seqlen, kv_seqlen, strict=True):
        if lq > 0:
            if lkv == 0:
                out[q_start : q_start + lq].zero_()
            else:
                qs = q[q_start : q_start + lq].unsqueeze(0).transpose(1, 2)
                ks = k[kv_start : kv_start + lkv].unsqueeze(0).transpose(1, 2)
                vs = v[kv_start : kv_start + lkv].unsqueeze(0).transpose(1, 2)
                out[q_start : q_start + lq] = F.scaled_dot_product_attention(qs, ks, vs)[0].transpose(0, 1)
        q_start += lq
        kv_start += lkv
    return out


def sparse_sdpa_attention(*args: Any, **kwargs: Any) -> Any:
    """Drop-in replacement for upstream ``sparse_scaled_dot_product_attention`` (all call signatures)."""
    q, k, v, q_seqlen, kv_seqlen, sparse_like, dense_q_shape = _parse_attention_args(args, kwargs)
    out = varlen_sdpa(q, k, v, q_seqlen, kv_seqlen)
    if sparse_like is not None:
        return sparse_like.replace(out)
    n, length, heads = dense_q_shape
    return out.reshape(n, length, heads, -1)


setattr(sparse_sdpa_attention, _FALLBACK_MARKER, True)


def _trellis_modules() -> list[tuple[str, Any]]:
    return [
        (name, module)
        for name, module in list(sys.modules.items())
        if module is not None and (name == "trellis2" or name.startswith("trellis2."))
    ]


def install_sparse_sdpa_fallback() -> list[str]:
    """Replace upstream sparse attention with :func:`sparse_sdpa_attention` everywhere it is bound.

    Idempotent. Imports the modules that bind the function by name first, then scans every loaded ``trellis2``
    module for attributes that reference the original function and rebinds them. Also sets the sparse config's
    ``ATTN`` to ``"sdpa"``. Returns the patched ``module.attribute`` names.
    """
    global _original_function, _previous_sparse_attn
    with _install_lock:
        full_attn = importlib.import_module(_FULL_ATTN_MODULE)
        for name in ("trellis2.modules.sparse", "trellis2.modules.sparse.attention",
                     "trellis2.modules.sparse.attention.modules"):
            importlib.import_module(name)
        current = getattr(full_attn, _FUNCTION_NAME)
        if _original_function is None:
            if getattr(current, _FALLBACK_MARKER, False):
                return [f"{m}.{a}" for m, a in _patched_bindings]
            _original_function = current
        original = _original_function

        for module_name, module in _trellis_modules():
            namespace = getattr(module, "__dict__", None)
            if not isinstance(namespace, dict):
                continue
            for attr, value in list(namespace.items()):
                if value is original:
                    setattr(module, attr, sparse_sdpa_attention)
                    if (module_name, attr) not in _patched_bindings:
                        _patched_bindings.append((module_name, attr))

        # The lazy package accessor (trellis2.modules.sparse.__getattr__) caches into the package globals.
        sparse_pkg = importlib.import_module("trellis2.modules.sparse")
        if sparse_pkg.__dict__.get(_FUNCTION_NAME) is not sparse_sdpa_attention:
            sparse_pkg.__dict__[_FUNCTION_NAME] = sparse_sdpa_attention
            if ("trellis2.modules.sparse", _FUNCTION_NAME) not in _patched_bindings:
                _patched_bindings.append(("trellis2.modules.sparse", _FUNCTION_NAME))

        sparse_config = importlib.import_module("trellis2.modules.sparse.config")
        if _previous_sparse_attn is None:
            _previous_sparse_attn = sparse_config.ATTN
        sparse_config.ATTN = "sdpa"

        patched = [f"{m}.{a}" for m, a in _patched_bindings]
        logger.info("Installed SDPA fallback for sparse attention (%d bindings): %s", len(patched), ", ".join(patched))
        return patched


def uninstall_sparse_sdpa_fallback() -> None:
    """Restore the original upstream function (used by tests)."""
    global _original_function, _previous_sparse_attn
    with _install_lock:
        if _original_function is None:
            return
        for module_name, attr in _patched_bindings:
            module = sys.modules.get(module_name)
            if module is not None and getattr(module, attr, None) is sparse_sdpa_attention:
                setattr(module, attr, _original_function)
        _patched_bindings.clear()
        if _previous_sparse_attn is not None:
            importlib.import_module("trellis2.modules.sparse.config").ATTN = _previous_sparse_attn
        _original_function = None
        _previous_sparse_attn = None


def sdpa_fallback_installed() -> bool:
    full_attn = sys.modules.get(_FULL_ATTN_MODULE)
    return bool(full_attn is not None and getattr(getattr(full_attn, _FUNCTION_NAME, None), _FALLBACK_MARKER, False))


# ==================================================================================================
# (b) Sampler step hooks
# ==================================================================================================


@contextlib.contextmanager
def hook_sampler_steps(
    sampler: Any,
    *,
    before_step: Callable[[], None] | None = None,
    after_step: Callable[[], None] | None = None,
) -> Iterator[None]:
    """Wrap ``sampler.sample_once`` (instance attribute) for the duration of the ``with`` block.

    ``before_step`` runs before every Euler step (use it to raise ``JobCancelledError``), ``after_step`` after it
    (progress reporting / cancellation). The previous binding is restored on exit, also on exceptions.
    """
    had_instance_attr = "sample_once" in vars(sampler)
    previous = vars(sampler).get("sample_once")
    original = sampler.sample_once

    def sample_once(*args: Any, **kwargs: Any) -> Any:
        if before_step is not None:
            before_step()
        result = original(*args, **kwargs)
        if after_step is not None:
            after_step()
        return result

    sampler.sample_once = sample_once
    try:
        yield
    finally:
        if had_instance_attr:
            sampler.sample_once = previous
        else:
            with contextlib.suppress(AttributeError):
                del sampler.sample_once


# ==================================================================================================
# (c) Upstream output → logging
# ==================================================================================================


class _LogWriter(io.TextIOBase):
    """Text stream that forwards complete lines to a logger.

    ``sys.stdout`` is process-wide, so while it is redirected (model loading takes over a minute) other threads may
    write to it too. Only writes from ``owner_thread`` are captured; everything else - and any re-entrant write
    caused by a logging handler that itself prints to ``sys.stdout`` - goes to ``passthrough`` unchanged.
    """

    def __init__(
        self,
        target: logging.Logger,
        level: int = logging.INFO,
        *,
        owner_thread: int | None = None,
        passthrough: Any = None,
    ) -> None:
        super().__init__()
        self._logger = target
        self._level = level
        self._buffer = ""
        self._owner = owner_thread
        self._passthrough = passthrough
        self._local = threading.local()

    def writable(self) -> bool:
        return True

    def _captures_current_write(self) -> bool:
        if self._owner is not None and threading.get_ident() != self._owner:
            return False
        return not getattr(self._local, "busy", False)

    def write(self, text: str) -> int:
        if not self._captures_current_write():
            if self._passthrough is not None:
                return self._passthrough.write(text)
            return len(text)
        self._local.busy = True
        try:
            self._buffer += text
            while "\n" in self._buffer:
                line, self._buffer = self._buffer.split("\n", 1)
                if line.strip():
                    self._logger.log(self._level, "%s", line.rstrip())
        finally:
            self._local.busy = False
        return len(text)

    def flush(self) -> None:
        if not self._captures_current_write():
            if self._passthrough is not None:
                self._passthrough.flush()
            return
        self._local.busy = True
        try:
            if self._buffer.strip():
                self._logger.log(self._level, "%s", self._buffer.rstrip())
            self._buffer = ""
        finally:
            self._local.busy = False


@contextlib.contextmanager
def capture_prints(target: logging.Logger | None = None) -> Iterator[None]:
    """Route the *calling thread's* ``sys.stdout`` output to ``target`` (upstream config prints, model loading).

    Output written by other threads while the redirect is active is passed through to the previous stream.
    """
    writer = _LogWriter(target or upstream_logger, owner_thread=threading.get_ident(), passthrough=sys.stdout)
    try:
        with contextlib.redirect_stdout(writer):
            yield
    finally:
        writer.flush()


def _upstream_print(*args: Any, sep: str = " ", end: str = "\n", file: Any = None, flush: bool = False) -> None:
    if file not in (None, sys.stdout, sys.stderr):
        print(*args, sep=sep, end=end, file=file, flush=flush)
        return
    text = sep.join(str(a) for a in args)
    if text.strip():
        upstream_logger.info("%s", text.rstrip())


def _silent_tqdm(iterable: Any = None, *args: Any, **kwargs: Any) -> Any:
    """``tqdm`` stand-in for upstream samplers: progress is reported through the sampler hooks instead."""
    return iterable if iterable is not None else []


_PRINT_MODULES = (
    "trellis2.pipelines.trellis2_image_to_3d",
    "trellis2.modules.sparse.basic",
    "trellis2.modules.sparse.config",
    "trellis2.modules.attention.config",
)


def silence_upstream_output() -> None:
    """Route upstream module-level ``print`` calls to logging and disable sampler tqdm bars (idempotent).

    Only the module namespaces listed here are touched (a module-global ``print`` shadows the builtin), so other
    threads' output is never captured.
    """
    for name in _PRINT_MODULES:
        module = sys.modules.get(name)
        if module is not None:
            module.print = _upstream_print  # type: ignore[attr-defined]
    flow_euler = sys.modules.get("trellis2.pipelines.samplers.flow_euler")
    if flow_euler is not None:
        flow_euler.tqdm = _silent_tqdm  # type: ignore[attr-defined]
