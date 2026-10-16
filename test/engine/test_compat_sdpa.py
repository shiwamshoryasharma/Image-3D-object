"""Numerical equivalence of the SDPA sparse-attention fallback (``inference.trellis.compat``).

Every upstream call signature of ``sparse_scaled_dot_product_attention`` is checked against a naive float32
softmax attention computed per sequence, for several sequence-length mixes (equal lengths → batched SDPA path,
mixed lengths → per-sequence path), using trellis2's own ``VarLenTensor`` / ``SparseTensor`` containers.
"""

from __future__ import annotations

import importlib
import math

import pytest

torch = pytest.importorskip("torch")
if not torch.cuda.is_available():  # pragma: no cover - depends on the machine
    pytest.skip("CUDA is required for the SDPA fallback tests", allow_module_level=True)

from trellis2.modules.sparse import SparseTensor, VarLenTensor  # noqa: E402

from inference.trellis import compat  # noqa: E402

DEVICE = torch.device("cuda")
BF16_TOL = {"atol": 2e-2, "rtol": 2e-2}
FP32_TOL = {"atol": 2e-4, "rtol": 2e-4}

SEQLEN_CASES = [
    [1],
    [37],
    [64, 64, 64],          # equal lengths -> batched path
    [7, 33, 128],          # mixed -> per-sequence path
    [300, 1, 1000],
    [2, 515],
]
HEAD_CASES = [(4, 64), (12, 128)]  # (heads, head_dim); 12x128 is what the TRELLIS.2 DiTs use


# --------------------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------------------


def _rand(*shape: int, dtype: torch.dtype = torch.bfloat16, seed: int = 0) -> torch.Tensor:
    generator = torch.Generator(device="cpu").manual_seed(seed)
    return torch.randn(*shape, generator=generator).to(device=DEVICE, dtype=dtype)


def naive_attention(
    q: torch.Tensor, k: torch.Tensor, v: torch.Tensor, q_seqlen: list[int], kv_seqlen: list[int]
) -> torch.Tensor:
    """float32 reference on packed [T, H, C] tensors (block-diagonal attention)."""
    outs = []
    qs = ks = 0
    for lq, lk in zip(q_seqlen, kv_seqlen, strict=True):
        qi, ki, vi = q[qs : qs + lq].float(), k[ks : ks + lk].float(), v[ks : ks + lk].float()
        logits = torch.einsum("qhc,khc->hqk", qi, ki) / math.sqrt(qi.shape[-1])
        outs.append(torch.einsum("hqk,khd->qhd", logits.softmax(dim=-1), vi))
        qs += lq
        ks += lk
    return torch.cat(outs, dim=0)


def make_varlen(feats: torch.Tensor, seqlens: list[int]) -> VarLenTensor:
    return VarLenTensor(feats, VarLenTensor.layout_from_seqlen(seqlens))


def make_sparse(feats: torch.Tensor, seqlens: list[int], seed: int = 0) -> SparseTensor:
    generator = torch.Generator(device="cpu").manual_seed(seed)
    batch = torch.repeat_interleave(torch.arange(len(seqlens)), torch.tensor(seqlens))
    xyz = torch.randint(0, 64, (int(sum(seqlens)), 3), generator=generator)
    coords = torch.cat([batch[:, None], xyz], dim=1).int().to(DEVICE)
    return SparseTensor(feats=feats, coords=coords)


def make_container(kind: str, feats: torch.Tensor, seqlens: list[int]) -> VarLenTensor:
    return make_sparse(feats, seqlens) if kind == "sparse" else make_varlen(feats, seqlens)


def other_seqlens(seqlens: list[int]) -> list[int]:
    """A different (but same batch size) set of KV lengths."""
    return [max(1, (n * 3) // 2 + 5) for n in seqlens]


# --------------------------------------------------------------------------------------------------
# call signatures
# --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("heads,dim", HEAD_CASES)
@pytest.mark.parametrize("seqlens", SEQLEN_CASES, ids=str)
@pytest.mark.parametrize("kind", ["varlen", "sparse"])
def test_qkv_packed(kind: str, seqlens: list[int], heads: int, dim: int) -> None:
    qkv = _rand(sum(seqlens), 3, heads, dim)
    x = make_container(kind, qkv, seqlens)
    out = compat.sparse_sdpa_attention(x)
    assert type(out) is type(x)
    assert out.layout == x.layout
    assert out.feats.shape == (sum(seqlens), heads, dim)
    assert out.feats.dtype == torch.bfloat16
    ref = naive_attention(qkv[:, 0], qkv[:, 1], qkv[:, 2], seqlens, seqlens)
    torch.testing.assert_close(out.feats.float(), ref, **BF16_TOL)


@pytest.mark.parametrize("seqlens", SEQLEN_CASES, ids=str)
def test_q_kv_both_varlen(seqlens: list[int]) -> None:
    kv_lens = other_seqlens(seqlens)
    q = _rand(sum(seqlens), 4, 64, seed=1)
    kv = _rand(sum(kv_lens), 2, 4, 64, seed=2)
    out = compat.sparse_sdpa_attention(make_sparse(q, seqlens), make_varlen(kv, kv_lens))
    assert isinstance(out, SparseTensor)
    ref = naive_attention(q, kv[:, 0], kv[:, 1], seqlens, kv_lens)
    torch.testing.assert_close(out.feats.float(), ref, **BF16_TOL)


@pytest.mark.parametrize("seqlens", SEQLEN_CASES, ids=str)
def test_q_varlen_kv_dense(seqlens: list[int]) -> None:
    """Cross-attention as used by the SLat DiTs without qk-norm: sparse queries, dense image tokens."""
    batch, ctx = len(seqlens), 41
    q = _rand(sum(seqlens), 4, 64, seed=3)
    kv = _rand(batch, ctx, 2, 4, 64, seed=4)
    out = compat.sparse_sdpa_attention(make_sparse(q, seqlens), kv)
    assert isinstance(out, SparseTensor)
    flat = kv.reshape(batch * ctx, 2, 4, 64)
    ref = naive_attention(q, flat[:, 0], flat[:, 1], seqlens, [ctx] * batch)
    torch.testing.assert_close(out.feats.float(), ref, **BF16_TOL)


@pytest.mark.parametrize("seqlens", SEQLEN_CASES, ids=str)
def test_q_dense_kv_varlen(seqlens: list[int]) -> None:
    batch, length = len(seqlens), 23
    q = _rand(batch, length, 4, 64, seed=5)
    kv = _rand(sum(seqlens), 2, 4, 64, seed=6)
    out = compat.sparse_sdpa_attention(q, make_varlen(kv, seqlens))
    assert isinstance(out, torch.Tensor)
    assert out.shape == (batch, length, 4, 64)
    ref = naive_attention(q.reshape(batch * length, 4, 64), kv[:, 0], kv[:, 1], [length] * batch, seqlens)
    torch.testing.assert_close(out.reshape(batch * length, 4, 64).float(), ref, **BF16_TOL)


@pytest.mark.parametrize("seqlens", SEQLEN_CASES, ids=str)
def test_q_k_v_varlen(seqlens: list[int]) -> None:
    kv_lens = other_seqlens(seqlens)
    q = _rand(sum(seqlens), 4, 64, seed=7)
    k = _rand(sum(kv_lens), 4, 64, seed=8)
    v = _rand(sum(kv_lens), 4, 64, seed=9)
    out = compat.sparse_sdpa_attention(make_sparse(q, seqlens), make_varlen(k, kv_lens), make_varlen(v, kv_lens))
    assert isinstance(out, SparseTensor)
    torch.testing.assert_close(out.feats.float(), naive_attention(q, k, v, seqlens, kv_lens), **BF16_TOL)


@pytest.mark.parametrize("seqlens", SEQLEN_CASES, ids=str)
def test_q_varlen_k_v_dense(seqlens: list[int]) -> None:
    """Cross-attention with qk RMS-norm (the path TRELLIS.2 actually uses): sparse q, dense k and v."""
    batch, ctx = len(seqlens), 1029  # DINOv3 at 512px: 1024 patches + 5 register/cls tokens
    q = _rand(sum(seqlens), 4, 64, seed=10)
    k = _rand(batch, ctx, 4, 64, seed=11)
    v = _rand(batch, ctx, 4, 64, seed=12)
    out = compat.sparse_sdpa_attention(make_sparse(q, seqlens), k, v)
    assert isinstance(out, SparseTensor)
    ref = naive_attention(
        q, k.reshape(batch * ctx, 4, 64), v.reshape(batch * ctx, 4, 64), seqlens, [ctx] * batch
    )
    torch.testing.assert_close(out.feats.float(), ref, **BF16_TOL)


@pytest.mark.parametrize("seqlens", SEQLEN_CASES, ids=str)
def test_q_dense_k_v_varlen(seqlens: list[int]) -> None:
    batch, length = len(seqlens), 17
    q = _rand(batch, length, 4, 64, seed=13)
    k = _rand(sum(seqlens), 4, 64, seed=14)
    v = _rand(sum(seqlens), 4, 64, seed=15)
    out = compat.sparse_sdpa_attention(q, make_varlen(k, seqlens), make_varlen(v, seqlens))
    assert out.shape == (batch, length, 4, 64)
    ref = naive_attention(q.reshape(batch * length, 4, 64), k, v, [length] * batch, seqlens)
    torch.testing.assert_close(out.reshape(batch * length, 4, 64).float(), ref, **BF16_TOL)


def test_value_dim_differs_from_key_dim() -> None:
    seqlens = [5, 70]
    q = _rand(sum(seqlens), 4, 64, seed=16)
    k = _rand(sum(seqlens), 4, 64, seed=17)
    v = _rand(sum(seqlens), 4, 32, seed=18)
    out = compat.sparse_sdpa_attention(make_varlen(q, seqlens), make_varlen(k, seqlens), make_varlen(v, seqlens))
    assert out.feats.shape == (sum(seqlens), 4, 32)
    torch.testing.assert_close(out.feats.float(), naive_attention(q, k, v, seqlens, seqlens), **BF16_TOL)


def test_keyword_arguments_match_positional() -> None:
    seqlens = [9, 31]
    qkv = _rand(sum(seqlens), 3, 4, 64, seed=19)
    x = make_varlen(qkv, seqlens)
    torch.testing.assert_close(
        compat.sparse_sdpa_attention(qkv=x).feats, compat.sparse_sdpa_attention(x).feats, atol=0, rtol=0
    )
    q = make_varlen(qkv[:, 0].contiguous(), seqlens)
    kv = make_varlen(qkv[:, 1:].contiguous(), seqlens)
    torch.testing.assert_close(
        compat.sparse_sdpa_attention(q, kv=kv).feats, compat.sparse_sdpa_attention(q, kv).feats, atol=0, rtol=0
    )


@pytest.mark.parametrize("seqlens", [[64, 64], [3, 200, 17]], ids=str)
def test_float32_is_tight(seqlens: list[int]) -> None:
    qkv = _rand(sum(seqlens), 3, 4, 64, dtype=torch.float32, seed=20)
    out = compat.sparse_sdpa_attention(make_varlen(qkv, seqlens))
    assert out.feats.dtype == torch.float32
    torch.testing.assert_close(
        out.feats, naive_attention(qkv[:, 0], qkv[:, 1], qkv[:, 2], seqlens, seqlens), **FP32_TOL
    )


def test_invalid_calls_raise() -> None:
    seqlens = [4, 4]
    qkv = _rand(8, 3, 2, 16)
    with pytest.raises(TypeError):
        compat.sparse_sdpa_attention()
    with pytest.raises(TypeError):
        compat.sparse_sdpa_attention(qkv)  # dense qkv is not allowed (upstream asserts VarLenTensor)
    with pytest.raises(TypeError):
        compat.sparse_sdpa_attention(_rand(2, 4, 2, 16), _rand(2, 4, 2, 2, 16))  # dense q + dense kv
    with pytest.raises(ValueError):
        compat.sparse_sdpa_attention(make_varlen(qkv[:, :2].contiguous(), seqlens))  # not 3 packed tensors
    with pytest.raises(ValueError):
        compat.sparse_sdpa_attention(make_varlen(qkv[:, 0], seqlens), make_varlen(qkv[:, 1:], [8]))  # batch


# --------------------------------------------------------------------------------------------------
# installation into trellis2 + module-level equivalence
# --------------------------------------------------------------------------------------------------


def _naive_sparse_attention(*args, **kwargs):
    q, k, v, q_seqlen, kv_seqlen, sparse_like, dense_shape = compat._parse_attention_args(args, kwargs)
    out = naive_attention(q, k, v, q_seqlen, kv_seqlen).to(q.dtype)
    if sparse_like is not None:
        return sparse_like.replace(out)
    n, length, heads = dense_shape
    return out.reshape(n, length, heads, -1)


@pytest.fixture
def installed_fallback():
    full_attn = importlib.import_module("trellis2.modules.sparse.attention.full_attn")
    attn_modules = importlib.import_module("trellis2.modules.sparse.attention.modules")
    sparse_config = importlib.import_module("trellis2.modules.sparse.config")
    original = full_attn.sparse_scaled_dot_product_attention
    previous_attn = sparse_config.ATTN
    patched = compat.install_sparse_sdpa_fallback()
    try:
        yield patched
    finally:
        compat.uninstall_sparse_sdpa_fallback()
        assert full_attn.sparse_scaled_dot_product_attention is original
        assert attn_modules.sparse_scaled_dot_product_attention is original
        assert sparse_config.ATTN == previous_attn


def test_install_rebinds_every_binding(installed_fallback: list[str]) -> None:
    sparse_pkg = importlib.import_module("trellis2.modules.sparse")
    sparse_attention_pkg = importlib.import_module("trellis2.modules.sparse.attention")
    full_attn = importlib.import_module("trellis2.modules.sparse.attention.full_attn")
    attn_modules = importlib.import_module("trellis2.modules.sparse.attention.modules")
    sparse_config = importlib.import_module("trellis2.modules.sparse.config")

    fallback = compat.sparse_sdpa_attention
    assert full_attn.sparse_scaled_dot_product_attention is fallback
    assert attn_modules.sparse_scaled_dot_product_attention is fallback
    assert sparse_attention_pkg.sparse_scaled_dot_product_attention is fallback
    assert sparse_pkg.sparse_scaled_dot_product_attention is fallback  # lazy package accessor
    assert sparse_config.ATTN == "sdpa"
    assert compat.sdpa_fallback_installed()
    assert "trellis2.modules.sparse.attention.modules.sparse_scaled_dot_product_attention" in installed_fallback
    # idempotent
    assert compat.install_sparse_sdpa_fallback() == installed_fallback

    import sys

    for name, module in list(sys.modules.items()):
        if name.startswith("trellis2") and module is not None:
            for attr, value in vars(module).items():
                assert not (
                    callable(value)
                    and getattr(value, "__name__", "") == "sparse_scaled_dot_product_attention"
                    and getattr(value, "__module__", "") == full_attn.__name__
                ), f"{name}.{attr} still references the upstream function"


@pytest.mark.parametrize("qk_rms_norm,use_rope", [(False, False), (True, True)])
def test_sparse_self_attention_module(installed_fallback, monkeypatch, qk_rms_norm: bool, use_rope: bool) -> None:
    from trellis2.modules.sparse.attention import modules as attn_modules

    torch.manual_seed(0)
    layer = attn_modules.SparseMultiHeadAttention(
        channels=256, num_heads=4, type="self", attn_mode="full", qk_rms_norm=qk_rms_norm, use_rope=use_rope
    ).to(device=DEVICE, dtype=torch.bfloat16)
    seqlens = [129, 7, 300]
    x = make_sparse(_rand(sum(seqlens), 256, seed=21), seqlens)
    with torch.no_grad():
        out = layer(x)
        monkeypatch.setattr(attn_modules, "sparse_scaled_dot_product_attention", _naive_sparse_attention)
        ref = layer(x)
    torch.testing.assert_close(out.feats.float(), ref.feats.float(), atol=3e-2, rtol=3e-2)


@pytest.mark.parametrize("qk_rms_norm", [False, True])
def test_sparse_cross_attention_module(installed_fallback, monkeypatch, qk_rms_norm: bool) -> None:
    from trellis2.modules.sparse.attention import modules as attn_modules

    torch.manual_seed(0)
    layer = attn_modules.SparseMultiHeadAttention(
        channels=256, num_heads=4, ctx_channels=128, type="cross", attn_mode="full", qk_rms_norm=qk_rms_norm
    ).to(device=DEVICE, dtype=torch.bfloat16)
    seqlens = [50, 260]
    x = make_sparse(_rand(sum(seqlens), 256, seed=22), seqlens)
    context = _rand(len(seqlens), 77, 128, seed=23)
    with torch.no_grad():
        out = layer(x, context)
        monkeypatch.setattr(attn_modules, "sparse_scaled_dot_product_attention", _naive_sparse_attention)
        ref = layer(x, context)
    torch.testing.assert_close(out.feats.float(), ref.feats.float(), atol=3e-2, rtol=3e-2)


def test_large_single_sequence_uses_memory_efficient_kernel() -> None:
    """A 49k-token self-attention (the cascade token cap) must not materialize the L x L matrix."""
    tokens, heads, dim = 49_152, 12, 128
    qkv = _rand(tokens, 3, heads, dim, seed=24)
    torch.cuda.synchronize()
    torch.cuda.reset_peak_memory_stats()
    before = torch.cuda.memory_allocated()
    out = compat.sparse_sdpa_attention(make_varlen(qkv, [tokens]))
    torch.cuda.synchronize()
    peak_extra = torch.cuda.max_memory_allocated() - before
    assert torch.isfinite(out.feats).all()
    # The dense score matrix alone would be heads * L^2 * 2 bytes = 58 GB.
    assert peak_extra < 2 * 1024**3, f"attention used {peak_extra / 1024**3:.1f} GB extra"
