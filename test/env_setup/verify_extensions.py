"""Functional verification of the TRELLIS.2 native CUDA extensions in the embedded Python.

Run (from anywhere):

    E:\\14-OCT-2026\\Img3D\\backend\\python\\python.exe -s E:\\14-OCT-2026\\Img3D\\test\\env_setup\\verify_extensions.py

``-s`` keeps the per-user site-packages out of ``sys.path`` (it can shadow the
embedded site-packages on this machine). The script reports either way.

Checks (all on CUDA, numerically compared with independent references where possible):

* imports      - o_voxel, cumesh, flex_gemm, nvdiffrast.torch (+ versions / install locations)
* flex_gemm    - sparse_submanifold_conv3d (Triton JIT) vs a dense ``F.conv3d`` reference for
                 several algorithms / dtypes; grid_sample_3d vs the package's torch reference
* cumesh       - CuMesh init / simplify / read / normals; cuBVH distance / ray tracing / sign
* nvdiffrast   - RasterizeCudaContext: rasterize one triangle, interpolate, antialias
* o_voxel      - z-order / hilbert serialize round trip (CUDA), mesh -> flexible dual grid ->
                 mesh, and ``postprocess.to_glb`` (remesh + UV unwrap + bake) exported as GLB;
                 VXZ io round trips (SVO codec + parent/neighbor attribute filters) and the
                 boundary-edge QEF on an open mesh - the C++ paths touched by patches/o_voxel.patch
* trellis2     - the real TRELLIS.2 modules that use the extensions, imported with
                 ATTN_BACKEND=sdpa: SparseConv3d (flex_gemm backend) vs a dense reference,
                 Mesh.fill_holes / simplify (cumesh), MeshWithVoxel.query_vertex_attrs (grid_sample_3d)
* xformers     - optional: memory_efficient_attention with fmha.BlockDiagonalMask (bf16)

Test caches (Triton kernels, FlexGEMM autotune) and outputs stay under this directory.
``--cold-cache`` wipes and uses ``cache_cold/`` instead, so every Triton kernel is JIT-compiled and
autotuned from scratch (slow: minutes) - proves the Triton-on-Windows toolchain, not just a warm cache.
Exit code: 0 if every required check passes (xformers is optional), 1 otherwise.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata as md
import math
import os
import shutil
import site
import sys
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parents[1]
TRELLIS_SOURCE_DIR = PROJECT_ROOT / "backend" / "third_party" / "TRELLIS.2"
COLD_CACHE = "--cold-cache" in sys.argv[1:]
CACHE_DIR = HERE / ("cache_cold" if COLD_CACHE else "cache")
OUT_DIR = HERE / "out"

# Keep test-time caches out of the user profile. Must be set before flex_gemm / triton import.
# (flex_gemm's save_autotune_cache does not create the parent directory itself.)
if COLD_CACHE:
    shutil.rmtree(CACHE_DIR, ignore_errors=True)
(CACHE_DIR / "flex_gemm").mkdir(parents=True, exist_ok=True)
os.environ.setdefault("TRITON_CACHE_DIR", str(CACHE_DIR / "triton"))
os.environ.setdefault("FLEX_GEMM_AUTOTUNE_CACHE_PATH", str(CACHE_DIR / "flex_gemm" / "autotune_cache.json"))

import numpy as np  # noqa: E402
import torch  # noqa: E402  (torch must be imported before the extensions: it loads the CUDA DLLs)
import torch.nn.functional as F  # noqa: E402

DEVICE = torch.device("cuda")
AABB = [[-0.5, -0.5, -0.5], [0.5, 0.5, 0.5]]


# --------------------------------------------------------------------------------------------
# Tiny check harness
# --------------------------------------------------------------------------------------------
class Tee:
    """Write to several text streams at once (console + log file)."""

    def __init__(self, *streams: TextIO) -> None:
        self._streams = streams

    def write(self, data: str) -> int:
        for s in self._streams:
            s.write(data)
            s.flush()
        return len(data)

    def flush(self) -> None:
        for s in self._streams:
            s.flush()


@dataclass
class CheckResult:
    name: str
    status: str  # PASS / FAIL / SKIP
    seconds: float
    details: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    optional: bool = False


class SkipCheck(Exception):
    """Raised by a check that cannot run in this environment (optional checks only)."""


def run_check(name: str, fn: Callable[[], dict[str, Any]], optional: bool = False) -> CheckResult:
    print(f"\n=== {name} {'(optional) ' if optional else ''}===")
    t0 = time.perf_counter()
    try:
        details = fn() or {}
        torch.cuda.synchronize()
        result = CheckResult(name, "PASS", time.perf_counter() - t0, details, optional=optional)
    except SkipCheck as exc:
        result = CheckResult(name, "SKIP", time.perf_counter() - t0, error=str(exc), optional=optional)
    except Exception as exc:  # noqa: BLE001 - report every failure, keep going
        traceback.print_exc()
        result = CheckResult(name, "FAIL", time.perf_counter() - t0, error=f"{type(exc).__name__}: {exc}", optional=optional)
    for key, value in result.details.items():
        print(f"    {key}: {value}")
    suffix = f" - {result.error}" if result.error else ""
    print(f"--> {result.status} ({result.seconds:.2f}s){suffix}")
    return result


def expect(cond: bool, message: str) -> None:
    if not cond:
        raise AssertionError(message)


def rel_err(out: torch.Tensor, ref: torch.Tensor) -> float:
    """Max abs error normalised by the max magnitude of the reference."""
    out32, ref32 = out.float(), ref.float()
    return ((out32 - ref32).abs().max() / ref32.abs().max().clamp_min(1e-12)).item()


def pkg_version(dist: str) -> str:
    try:
        return md.version(dist)
    except md.PackageNotFoundError:
        return "not installed"


# --------------------------------------------------------------------------------------------
# Environment
# --------------------------------------------------------------------------------------------
def check_environment() -> dict[str, Any]:
    expect(torch.cuda.is_available(), "torch.cuda.is_available() is False")
    user_site = site.getusersitepackages()
    user_site_active = user_site in sys.path
    details: dict[str, Any] = {
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "torch": f"{torch.__version__} (cuda {torch.version.cuda}, cudnn {torch.backends.cudnn.version()})",
        "gpu": f"{torch.cuda.get_device_name(0)} sm_{''.join(map(str, torch.cuda.get_device_capability(0)))}",
        "torch arch list": " ".join(torch.cuda.get_arch_list()),
        "triton": pkg_version("triton-windows"),
        "user site on sys.path": f"{user_site_active} ({user_site})",
        "TRITON_CACHE_DIR": os.environ["TRITON_CACHE_DIR"],
        "FLEX_GEMM_AUTOTUNE_CACHE_PATH": os.environ["FLEX_GEMM_AUTOTUNE_CACHE_PATH"],
    }
    if user_site_active:
        shadowed = []
        for name in ("numpy", "huggingface_hub", "fastapi", "pydantic", "starlette", "packaging", "opencv-python-headless"):
            try:
                dist = md.distribution(name)
            except md.PackageNotFoundError:
                continue
            location = str(dist.locate_file(""))
            if location.lower().startswith(user_site.lower()):
                shadowed.append(f"{name}=={dist.version}")
        details["WARNING shadowing from user site"] = ", ".join(shadowed) or "none detected"
    return details


def check_imports() -> dict[str, Any]:
    details: dict[str, Any] = {}
    for module_name, dist in (
        ("cumesh", "cumesh"),
        ("flex_gemm", "flex_gemm"),
        ("nvdiffrast.torch", "nvdiffrast"),
        ("o_voxel", "o_voxel"),
    ):
        module = importlib.import_module(module_name)
        details[module_name] = f"{pkg_version(dist)}  {Path(module.__file__).parent}"
    import _nvdiffrast_c  # noqa: F401  - compiled nvdiffrast module
    from cumesh import _C as _cm, _cubvh, _cumesh_xatlas  # noqa: F401
    from flex_gemm.kernels import cuda as _fg  # noqa: F401
    from o_voxel import _C as _ov  # noqa: F401
    details["compiled modules"] = "cumesh._C, cumesh._cubvh, cumesh._cumesh_xatlas, flex_gemm.kernels.cuda, _nvdiffrast_c, o_voxel._C"
    return details


# --------------------------------------------------------------------------------------------
# flex_gemm
# --------------------------------------------------------------------------------------------
@torch.no_grad()
def sphere_shell_coords(res: int, batch: int) -> torch.Tensor:
    """Voxel coordinates [N, 4] (b, x, y, z) of a 1-voxel-thick sphere shell, int32 on CUDA."""
    grid = torch.stack(
        torch.meshgrid(*(torch.arange(res, device=DEVICE),) * 3, indexing="ij"), dim=-1
    ).reshape(-1, 3)
    dist = ((grid.float() - res / 2 + 0.5) ** 2).sum(-1).sqrt()
    shell = grid[(dist <= res / 2) & (dist >= res / 2 - 1.25)].int()
    coords = torch.cat(
        [torch.cat([torch.full((shell.shape[0], 1), b, dtype=torch.int32, device=DEVICE), shell], 1) for b in range(batch)]
    )
    return coords.contiguous()


@torch.no_grad()
def dense_submconv_reference(
    feats: torch.Tensor, coords: torch.Tensor, shape: torch.Size, weight: torch.Tensor, bias: torch.Tensor
) -> torch.Tensor:
    """Submanifold conv computed densely with F.conv3d (fp32, no TF32), gathered at the active sites."""
    batch, ci, w, h, d = shape
    co, kw, kh, kd, _ = weight.shape
    b, x, y, z = coords.long().unbind(1)
    dense = torch.zeros(batch, ci, w, h, d, device=DEVICE, dtype=torch.float32)
    dense[b, :, x, y, z] = feats.float()
    kernel = weight.float().permute(0, 4, 1, 2, 3).contiguous()  # [Co, Ci, Kw, Kh, Kd]
    out = F.conv3d(dense, kernel, bias.float(), padding=(kw // 2, kh // 2, kd // 2))
    return out[b, :, x, y, z]


def check_flex_gemm_spconv() -> dict[str, Any]:
    import flex_gemm
    from flex_gemm.ops.spconv import Algorithm, sparse_submanifold_conv3d

    torch.backends.cudnn.allow_tf32 = False
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.manual_seed(0)

    res, batch, ci, co = 32, 2, 64, 64
    coords = sphere_shell_coords(res, batch)
    shape = torch.Size([batch, ci, res, res, res])
    feats32 = torch.randn(coords.shape[0], ci, device=DEVICE)
    weight32 = torch.randn(co, 3, 3, 3, ci, device=DEVICE) / math.sqrt(27 * ci)
    bias32 = torch.randn(co, device=DEVICE) * 0.1
    ref = dense_submconv_reference(feats32, coords, shape, weight32, bias32)

    details: dict[str, Any] = {"active voxels": coords.shape[0], "channels": f"{ci}->{co}", "kernel": "3x3x3"}
    cases = [
        # (algorithm, dtype, tolerance) - masked_implicit_gemm_splitk is TRELLIS.2's default.
        (Algorithm.MASKED_IMPLICIT_GEMM_SPLITK, torch.float16, 1e-2),
        (Algorithm.MASKED_IMPLICIT_GEMM_SPLITK, torch.bfloat16, 3e-2),
        (Algorithm.MASKED_IMPLICIT_GEMM_SPLITK, torch.float32, 1e-2),
        (Algorithm.MASKED_IMPLICIT_GEMM, torch.float16, 1e-2),
        (Algorithm.IMPLICIT_GEMM, torch.float16, 1e-2),
        (Algorithm.IMPLICIT_GEMM_SPLITK, torch.float16, 1e-2),
        (Algorithm.EXPLICIT_GEMM, torch.float16, 1e-2),
    ]
    try:
        for algo, dtype, tol in cases:
            flex_gemm.ops.spconv.set_algorithm(algo)
            feats, weight, bias = feats32.to(dtype), weight32.to(dtype), bias32.to(dtype)
            t0 = time.perf_counter()
            out, cache = sparse_submanifold_conv3d(feats, coords, shape, weight, bias)
            torch.cuda.synchronize()
            first = time.perf_counter() - t0
            # Second call reuses the neighbour cache (TRELLIS registers it as a spatial cache).
            out2, _ = sparse_submanifold_conv3d(feats, coords, shape, weight, bias, cache)
            torch.cuda.synchronize()
            err = rel_err(out, ref)
            expect(out.shape == ref.shape and out.dtype == dtype, f"{algo}/{dtype}: bad output {tuple(out.shape)} {out.dtype}")
            expect(torch.isfinite(out.float()).all().item(), f"{algo}/{dtype}: non-finite output")
            expect(err < tol, f"{algo}/{dtype}: rel err {err:.2e} >= {tol:.0e}")
            expect(torch.equal(out, out2), f"{algo}/{dtype}: neighbour-cache reuse changed the result")
            details[f"{algo} {str(dtype).removeprefix('torch.')}"] = f"rel_err={err:.2e}  first call {first:.2f}s"
    finally:
        flex_gemm.ops.spconv.set_algorithm(Algorithm.MASKED_IMPLICIT_GEMM_SPLITK)
    return details


def check_flex_gemm_grid_sample() -> dict[str, Any]:
    from flex_gemm.ops.grid_sample import grid_sample_3d, grid_sample_3d_torch

    torch.manual_seed(1)
    res, ch = 32, 6
    coords = sphere_shell_coords(res, 1)
    shape = torch.Size([1, ch, res, res, res])
    feats = torch.rand(coords.shape[0], ch, device=DEVICE)
    # Query points around the shell (in voxel units, like TRELLIS' texture baking).
    direction = F.normalize(torch.randn(4096, 3, device=DEVICE), dim=-1)
    radius = res / 2 - 0.6 + 0.4 * torch.rand(4096, 1, device=DEVICE)
    grid = (direction * radius + res / 2).reshape(1, -1, 3).contiguous()

    details: dict[str, Any] = {"active voxels": coords.shape[0], "queries": grid.shape[1]}
    for mode in ("trilinear", "nearest"):
        out = grid_sample_3d(feats, coords, shape, grid, mode=mode)
        ref = grid_sample_3d_torch(feats, coords, shape, grid, mode)
        expect(out.shape == (1, grid.shape[1], ch), f"{mode}: bad output shape {tuple(out.shape)}")
        err = rel_err(out, ref)
        expect(err < 1e-4, f"{mode}: rel err vs torch reference {err:.2e}")
        nonzero = (out.abs().sum(-1) > 0).float().mean().item()
        expect(nonzero > 0.5, f"{mode}: only {nonzero:.0%} of queries hit active voxels")
        details[mode] = f"rel_err={err:.2e}  hit-rate={nonzero:.1%}"
    return details


# --------------------------------------------------------------------------------------------
# cumesh
# --------------------------------------------------------------------------------------------
def check_cumesh() -> dict[str, Any]:
    import cumesh
    import trimesh

    radius = 0.4
    ico = trimesh.creation.icosphere(subdivisions=5, radius=radius)
    vertices = torch.from_numpy(ico.vertices).float().to(DEVICE).contiguous()
    faces = torch.from_numpy(ico.faces).int().to(DEVICE).contiguous()

    mesh = cumesh.CuMesh()
    mesh.init(vertices, faces)
    expect(mesh.num_vertices == vertices.shape[0], f"num_vertices {mesh.num_vertices} != {vertices.shape[0]}")
    expect(mesh.num_faces == faces.shape[0], f"num_faces {mesh.num_faces} != {faces.shape[0]}")
    mesh.get_boundary_info()
    expect(mesh.num_boundaries == 0, f"closed icosphere reports {mesh.num_boundaries} boundary edges")

    target = 2000
    mesh.simplify(target)
    v2, f2 = mesh.read()
    expect(v2.is_cuda and f2.is_cuda, "read() did not return CUDA tensors")
    expect(0 < f2.shape[0] <= target, f"simplify -> {f2.shape[0]} faces (target {target})")
    expect(int(f2.max()) < v2.shape[0] and int(f2.min()) >= 0, "face indices out of range after simplify")
    norms = v2.norm(dim=-1)
    expect(torch.isfinite(v2).all().item(), "non-finite vertices after simplify")
    expect(((norms - radius).abs() < 0.02 * radius).all().item(), "simplified vertices left the sphere")
    mesh.compute_vertex_normals()
    vn = mesh.read_vertex_normals()
    expect(((vn.norm(dim=-1) - 1).abs() < 1e-3).all().item(), "vertex normals not unit length")

    # cuBVH on the original mesh
    bvh = cumesh.cuBVH(vertices, faces)
    torch.manual_seed(2)
    pts = torch.randn(10000, 3, device=DEVICE) * 0.3
    dist, face_id, uvw = bvh.unsigned_distance(pts, return_uvw=True)
    analytic = (pts.norm(dim=-1) - radius).abs()
    udf_err = (dist - analytic).abs().max().item()
    expect(udf_err < 2e-3, f"unsigned_distance max err {udf_err:.2e}")
    expect((face_id >= 0).all().item() and torch.allclose(uvw.sum(-1), torch.ones_like(dist), atol=1e-4), "bad face ids / uvw")

    dirs = F.normalize(torch.randn(4096, 3, device=DEVICE), dim=-1)
    rays_o = dirs * 1.0
    _, hit_face, depth = bvh.ray_trace(rays_o, -dirs)
    depth_err = (depth - (1.0 - radius)).abs().max().item()
    expect((hit_face >= 0).all().item(), "some rays toward the sphere centre missed")
    expect(depth_err < 2e-3, f"ray depth max err {depth_err:.2e}")

    sdf, _, _ = bvh.signed_distance(torch.tensor([[0.0, 0.0, 0.0], [0.0, 0.0, 0.9]], device=DEVICE))
    expect(sdf[0].item() * sdf[1].item() < 0, f"signed_distance inside/outside signs equal: {sdf.tolist()}")
    expect(abs(abs(sdf[0].item()) - radius) < 2e-3 and abs(abs(sdf[1].item()) - 0.5) < 2e-3, f"signed distances {sdf.tolist()}")

    return {
        "icosphere": f"{vertices.shape[0]} v / {faces.shape[0]} f",
        "simplified": f"{v2.shape[0]} v / {f2.shape[0]} f (target {target})",
        "cuBVH udf max err": f"{udf_err:.2e}",
        "cuBVH ray depth max err": f"{depth_err:.2e}",
        "cuBVH sdf (inside, outside)": [round(x, 4) for x in sdf.tolist()],
    }


# --------------------------------------------------------------------------------------------
# nvdiffrast
# --------------------------------------------------------------------------------------------
def check_nvdiffrast() -> dict[str, Any]:
    import nvdiffrast.torch as dr

    ctx = dr.RasterizeCudaContext(device=DEVICE)
    pos = torch.tensor([[[-0.8, -0.8, 0.0, 1.0], [0.8, -0.8, 0.0, 1.0], [0.0, 0.8, 0.0, 1.0]]], device=DEVICE)
    tri = torch.tensor([[0, 1, 2]], dtype=torch.int32, device=DEVICE)
    res = 256
    rast, rast_db = dr.rasterize(ctx, pos, tri, resolution=[res, res])
    expect(rast.shape == (1, res, res, 4), f"rast shape {tuple(rast.shape)}")
    tri_id = rast[0, ..., 3]
    coverage = (tri_id > 0).float().mean().item()
    expected = (1.6 * 1.6 / 2) / 4.0  # triangle area / NDC square area
    expect(tri_id[res // 2, res // 2].item() == 1.0, "centre pixel not covered by triangle 1")
    expect(tri_id[res - 1, 0].item() == 0.0 and tri_id[res - 1, res - 1].item() == 0.0, "top corners should be empty")
    expect(abs(coverage - expected) < 0.01, f"coverage {coverage:.4f} vs expected {expected:.4f}")

    colors = torch.eye(3, device=DEVICE).unsqueeze(0)  # one-hot vertex colours -> barycentrics
    img, _ = dr.interpolate(colors, rast, tri)
    inside = tri_id > 0
    bary_sum_err = (img[0][inside].sum(-1) - 1).abs().max().item()
    expect(bary_sum_err < 1e-4, f"interpolated barycentrics do not sum to 1 (err {bary_sum_err:.2e})")
    aa = dr.antialias(img.contiguous(), rast, pos, tri)
    expect(torch.isfinite(aa).all().item() and aa.shape == img.shape, "antialias output invalid")
    return {
        "resolution": f"{res}x{res}",
        "coverage": f"{coverage:.4f} (expected {expected:.4f})",
        "barycentric sum err": f"{bary_sum_err:.1e}",
    }


# --------------------------------------------------------------------------------------------
# o_voxel
# --------------------------------------------------------------------------------------------
def check_o_voxel_serialize() -> dict[str, Any]:
    import o_voxel

    torch.manual_seed(3)
    coords = torch.randint(0, 1024, (100_000, 3), device=DEVICE, dtype=torch.int32)
    details: dict[str, Any] = {}
    for mode in ("z_order", "hilbert"):
        code = o_voxel.serialize.encode_seq(coords, mode=mode)
        back = o_voxel.serialize.decode_seq(code, mode=mode)
        expect(torch.equal(back.int(), coords), f"{mode}: CUDA decode(encode(x)) != x")
        code_cpu = o_voxel.serialize.encode_seq(coords[:1000].cpu(), mode=mode)
        expect(torch.equal(code_cpu, code[:1000].cpu()), f"{mode}: CPU and CUDA codes differ")
        details[mode] = f"round trip OK ({coords.shape[0]} coords, dtype {code.dtype})"
    return details


def check_o_voxel_mesh_and_glb() -> dict[str, Any]:
    import o_voxel
    import trimesh

    res, radius = 64, 0.35
    ico = trimesh.creation.icosphere(subdivisions=4, radius=radius)
    vertices = torch.from_numpy(ico.vertices).float()
    faces = torch.from_numpy(ico.faces).long()

    # mesh -> flexible dual grid (CPU kernel in o_voxel._C), as in o-voxel/examples/mesh2ovox.py
    voxel_indices, dual_vertices, intersected = o_voxel.convert.mesh_to_flexible_dual_grid(
        vertices, faces, grid_size=res, aabb=AABB,
        face_weight=1.0, boundary_weight=0.2, regularization_weight=1e-2,
    )
    order = torch.argsort(o_voxel.serialize.encode_seq(voxel_indices.to(DEVICE))).cpu()
    voxel_indices, dual_vertices, intersected = voxel_indices[order], dual_vertices[order], intersected[order]
    dual_local = torch.clamp(dual_vertices * res - voxel_indices, 0, 1)

    # flexible dual grid -> mesh (CUDA hashmap kernels), as in TRELLIS.2's FDG VAE decoder
    coords = voxel_indices.to(DEVICE).int()
    rec_v, rec_f = o_voxel.convert.flexible_dual_grid_to_mesh(
        coords, dual_local.to(DEVICE).float(), intersected.to(DEVICE).bool(),
        split_weight=None, grid_size=res, aabb=AABB,
    )
    expect(rec_v.shape[0] > 1000 and rec_f.shape[0] > 1000, f"reconstructed mesh too small: {rec_v.shape} {rec_f.shape}")
    surf_err = (rec_v.norm(dim=-1) - radius).abs()
    expect(surf_err.mean().item() < 0.5 / res, f"reconstruction mean surface error {surf_err.mean().item():.4f}")

    # o_voxel.postprocess.to_glb with the contract's settings (remesh=True, band 1, project 0)
    unit = (coords.float() + 0.5) / res  # [0, 1]^3 -> a smooth colour field
    attr_volume = torch.cat(
        [unit, torch.zeros_like(unit[:, :1]), torch.full_like(unit[:, :1], 0.5), torch.ones_like(unit[:, :1])], dim=-1
    ).contiguous()
    attr_layout = {"base_color": slice(0, 3), "metallic": slice(3, 4), "roughness": slice(4, 5), "alpha": slice(5, 6)}
    decimation_target, texture_size = 5000, 512
    glb = o_voxel.postprocess.to_glb(
        vertices=rec_v, faces=rec_f, attr_volume=attr_volume, coords=coords, attr_layout=attr_layout,
        grid_size=res, aabb=AABB, decimation_target=decimation_target, texture_size=texture_size,
        remesh=True, remesh_band=1, remesh_project=0,
    )
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    glb_path = OUT_DIR / "verify_o_voxel_sphere.glb"
    glb.export(str(glb_path), extension_webp=False)
    expect(glb_path.is_file() and glb_path.stat().st_size > 10_000, "GLB not written")

    loaded = trimesh.load(str(glb_path))
    geoms = list(loaded.geometry.values()) if isinstance(loaded, trimesh.Scene) else [loaded]
    n_faces = sum(len(g.faces) for g in geoms)
    expect(0 < n_faces <= decimation_target, f"GLB has {n_faces} faces (target {decimation_target})")
    material = geoms[0].visual.material
    expect(getattr(material, "baseColorTexture", None) is not None, "GLB has no base colour texture")
    tex = material.baseColorTexture
    rgb =np.asarray(tex.convert("RGB"), dtype=np.float32)
    # The attribute volume is a smooth colour ramp, so the baked texture must be neither empty nor flat.
    expect(rgb.mean() > 20 and rgb.std() > 5, f"baked texture looks empty (mean {rgb.mean():.1f}, std {rgb.std():.1f})")
    return {
        "fdg voxels": voxel_indices.shape[0],
        "fdg -> mesh": f"{rec_v.shape[0]} v / {rec_f.shape[0]} f, mean surface err {surf_err.mean().item():.4f}",
        "to_glb": f"{n_faces} faces, texture {tex.size[0]}x{tex.size[1]} (mean RGB {rgb.mean():.0f}), "
        f"{glb_path.stat().st_size / 1024:.0f} KiB",
        "glb": str(glb_path),
    }


def _lexsort_rows(coords: torch.Tensor) -> torch.Tensor:
    """Permutation that sorts integer [N, 3] coordinates lexicographically (x, y, z)."""
    c = coords.long()
    span = int(c.max()) + 1
    return torch.argsort((c[:, 0] * span + c[:, 1]) * span + c[:, 2])


def check_o_voxel_io() -> dict[str, Any]:
    """VXZ write/read round trips: exercises the patched SVO codec (src/io/svo.cpp) and the
    parent / neighbor attribute filters (src/io/filter_parent.cpp, src/io/filter_neighbor.cpp)."""
    import o_voxel
    from o_voxel import _C

    torch.manual_seed(5)
    res, chunk_size = 128, 64  # -> up to 8 chunks, chunk depth 6
    flat = torch.randperm(res**3)[:30_000]
    coords = torch.stack([flat // (res * res), (flat // res) % res, flat % res], dim=1).int()
    # Spatially smooth attributes (what the filters are designed for) plus noise, as uint8.
    smooth = (coords.float() / res * 255).round()
    noise = torch.randint(-3, 4, smooth.shape).float()
    attrs = {
        "base_color": (smooth + noise).clamp(0, 255).to(torch.uint8),
        "alpha": torch.randint(0, 256, (coords.shape[0], 1), dtype=torch.uint8),
    }

    # Direct SVO codec round trip on one chunk (morton codes must come back bit-exact).
    local = coords[(coords < chunk_size).all(dim=1)]
    morton = o_voxel.serialize.encode_seq(local).cpu()
    morton = morton[torch.argsort(morton)]
    depth = int(math.log2(chunk_size))
    svo = _C.encode_sparse_voxel_octree_cpu(morton, depth)
    expect(svo.dtype == torch.uint8 and svo.dim() == 1 and svo.numel() > 0, f"bad SVO tensor {svo.dtype} {tuple(svo.shape)}")
    decoded = _C.decode_sparse_voxel_octree_cpu(svo, depth)
    expect(torch.equal(decoded.long(), morton.long()), "decode_sparse_voxel_octree_cpu(encode(...)) != morton codes")
    details: dict[str, Any] = {"svo codec": f"{local.shape[0]} voxels -> {svo.numel()} SVO bytes, exact round trip"}

    order_in = _lexsort_rows(coords)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for filt, compression in (("none", "zstd"), ("parent", "zstd"), ("neighbor", "deflate")):
        path = OUT_DIR / f"verify_o_voxel_{filt}.vxz"
        o_voxel.io.write(str(path), coords, attrs, chunk_size=chunk_size, filter=filt, compression=compression)
        info = o_voxel.io.read_vxz_info(str(path))
        coords_back, attrs_back = o_voxel.io.read(str(path))
        expect(coords_back.shape == coords.shape, f"{filt}: {coords_back.shape[0]} voxels read back, wrote {coords.shape[0]}")
        order_out = _lexsort_rows(coords_back)
        expect(torch.equal(coords_back[order_out].int(), coords[order_in]), f"{filt}: coordinates differ after round trip")
        for key, value in attrs.items():
            expect(torch.equal(attrs_back[key][order_out], value[order_in]), f"{filt}: attribute '{key}' differs after round trip")
        details[f"vxz filter={filt}"] = (
            f"{len(info['chunks'])} chunks, {compression}, ratio {info['compress_ratio']:.2f}, exact round trip"
        )
    return details


def check_o_voxel_boundary_qef() -> dict[str, Any]:
    """mesh_to_flexible_dual_grid on an OPEN mesh: exercises boundry_qef (patched literals in
    src/convert/flexible_dual_grid.cpp) incl. axis-aligned edges (dir[axis] == 0.0) and a
    zero-length boundary edge (segment_length < 1e-6)."""
    import o_voxel

    res = 64
    vs = 1.0 / res
    half, z0, n = 0.3 + 0.37 * vs, 0.1 + 0.41 * vs, 16  # off the voxel borders on purpose
    xs = torch.linspace(-half, half, n + 1)
    gx, gy = torch.meshgrid(xs, xs, indexing="ij")
    verts = torch.stack([gx.reshape(-1), gy.reshape(-1), torch.full(((n + 1) ** 2,), z0)], dim=-1)
    vid = lambda i, j: i * (n + 1) + j  # noqa: E731
    tris = [[vid(i, j), vid(i + 1, j), vid(i + 1, j + 1)] for i in range(n) for j in range(n)]
    tris += [[vid(i, j), vid(i + 1, j + 1), vid(i, j + 1)] for i in range(n) for j in range(n)]
    # A sliver triangle with two coincident vertices -> a zero-length boundary edge.
    sliver = torch.tensor([[0.0, 0.0, -0.3 + 0.3 * vs], [0.0, 0.0, -0.3 + 0.3 * vs], [0.1 + 0.2 * vs, 0.05, -0.3 + 0.3 * vs]])
    base = verts.shape[0]
    verts = torch.cat([verts, sliver])
    faces = torch.tensor(tris + [[base, base + 1, base + 2]])

    plane_z = int((z0 + 0.5) * res)
    edge_x = int((half + 0.5) * res)
    errors: dict[float, float] = {}
    for boundary_weight in (0.0, 1.0):
        vox, dual, _ = o_voxel.convert.mesh_to_flexible_dual_grid(
            verts, faces, grid_size=res, aabb=AABB,
            face_weight=1.0, boundary_weight=boundary_weight, regularization_weight=1e-2,
        )
        expect(torch.isfinite(dual).all().item(), f"boundary_weight={boundary_weight}: non-finite dual vertices")
        local = dual * res - vox
        expect(((local > -1e-4) & (local < 1 + 1e-4)).all().item(), "dual vertices outside their voxels")
        world = dual + torch.tensor(AABB[0])  # dual vertices are relative to the aabb minimum
        on_plane = vox[:, 2] == plane_z
        expect(on_plane.all().item(), "open square voxelised outside its plane")
        expect((world[:, 2] - z0).abs().max().item() < 1e-3 * vs, "dual vertices left the plane z = z0")
        edge = (vox[:, 0] == edge_x) & (vox[:, 1] > int((-half + 0.5) * res)) & (vox[:, 1] < edge_x)
        expect(edge.sum().item() > 10, "no voxels along the x = +half boundary")
        errors[boundary_weight] = (world[edge, 0] - half).abs().max().item() / vs
    # The boundary QEF must pull the dual vertices of boundary voxels onto the boundary line.
    expect(errors[1.0] < 0.05, f"boundary QEF inactive: max |x - half| = {errors[1.0]:.3f} voxel")
    expect(errors[0.0] > 10 * errors[1.0], f"boundary term had no effect ({errors[0.0]:.3f} vs {errors[1.0]:.3f} voxel)")
    return {
        "open square": f"{faces.shape[0]} faces incl. 1 zero-length boundary edge, {int(on_plane.sum())} voxels",
        "boundary-voxel |x - edge| (voxels)": f"without boundary QEF {errors[0.0]:.3f}, with {errors[1.0]:.4f}",
    }


# --------------------------------------------------------------------------------------------
# TRELLIS.2 modules on top of the extensions
# --------------------------------------------------------------------------------------------
def check_trellis2_integration() -> dict[str, Any]:
    """Import the real TRELLIS.2 modules that use the extensions (ATTN_BACKEND=sdpa, as the engine
    runs them) and run the extension-backed code paths of those modules on CUDA."""
    import cumesh
    import trimesh
    from flex_gemm.ops.grid_sample import grid_sample_3d_torch

    expect("trellis2" not in sys.modules, "trellis2 was imported before ATTN_BACKEND could be set")
    os.environ["ATTN_BACKEND"] = "sdpa"  # must be set BEFORE trellis2 is imported (read at import)
    if str(TRELLIS_SOURCE_DIR) not in sys.path:
        sys.path.insert(0, str(TRELLIS_SOURCE_DIR))

    details: dict[str, Any] = {}
    for name in ("trellis2.representations.mesh.base", "trellis2.modules.sparse.conv.conv_flex_gemm", "o_voxel.postprocess"):
        t0 = time.perf_counter()
        module = importlib.import_module(name)
        details[f"import {name}"] = f"OK {time.perf_counter() - t0:.2f}s  {module.__file__}"
    from trellis2.modules.attention import config as attn_config
    from trellis2.modules.sparse import SparseTensor
    from trellis2.modules.sparse import config as sparse_config
    from trellis2.modules.sparse.conv import SparseConv3d
    from trellis2.modules.sparse.conv import config as conv_config
    from trellis2.representations.mesh.base import Mesh, MeshWithVoxel

    expect(attn_config.BACKEND == "sdpa", f"dense attention backend is {attn_config.BACKEND!r}, expected 'sdpa'")
    expect(sparse_config.CONV == "flex_gemm", f"sparse conv backend is {sparse_config.CONV!r}")
    details["backends"] = (
        f"dense attn={attn_config.BACKEND}, sparse conv={sparse_config.CONV} ({conv_config.FLEX_GEMM_ALGO}), "
        f"sparse attn={sparse_config.ATTN} (TRELLIS ignores 'sdpa' here -> engine compat shim required)"
    )

    # --- SparseConv3d (flex_gemm backend) vs dense conv, as used by the TRELLIS.2 VAEs / flows.
    torch.backends.cudnn.allow_tf32 = False
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.manual_seed(6)
    res, batch, ci, co = 32, 2, 32, 48
    coords = sphere_shell_coords(res, batch)
    conv = SparseConv3d(ci, co, 3).to(DEVICE)
    feats32 = torch.randn(coords.shape[0], ci, device=DEVICE)
    with torch.no_grad():
        for dtype, tol in ((torch.float16, 1e-2), (torch.bfloat16, 3e-2)):
            conv = conv.to(dtype)
            x = SparseTensor(feats32.to(dtype), coords)
            shape = torch.Size([*x.shape, *x.spatial_shape])
            ref = dense_submconv_reference(feats32, coords, shape, conv.weight.float(), conv.bias.float())
            y = conv(x)
            neighbour_keys = [k for k in x.get_spatial_cache() if k.startswith("SubMConv3d_neighbor_cache")]
            expect(len(neighbour_keys) == 1, f"SparseConv3d did not register its neighbour map: {list(x.get_spatial_cache())}")
            y2 = conv(x)  # second call must reuse the registered neighbour map
            err = rel_err(y.feats, ref)
            expect(y.feats.shape == (coords.shape[0], co) and y.feats.dtype == dtype, f"{dtype}: bad output {tuple(y.feats.shape)}")
            expect(err < tol, f"SparseConv3d {dtype}: rel err {err:.2e} >= {tol:.0e}")
            expect(torch.equal(y.feats, y2.feats), f"SparseConv3d {dtype}: cached neighbour map changed the result")
            expect(torch.equal(y.coords, x.coords), "SparseConv3d changed the coordinates")
            details[f"SparseConv3d {str(dtype).removeprefix('torch.')}"] = (
                f"{coords.shape[0]} voxels {ci}->{co}, rel_err={err:.2e}, cached '{neighbour_keys[0]}'"
            )

    # --- Mesh.fill_holes / Mesh.simplify (cumesh) as called by Trellis2ImageTo3DPipeline / example.py
    radius = 0.4
    ico = trimesh.creation.icosphere(subdivisions=5, radius=radius)
    keep = torch.ones(len(ico.faces), dtype=torch.bool)
    keep[torch.tensor([10, 500, 5000, 12000])] = False  # four small holes
    kept_faces = ico.faces[keep.numpy()]
    edges = np.sort(kept_faces[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2), axis=1)
    expected_boundary = int((np.unique(edges, axis=0, return_counts=True)[1] == 1).sum())
    mesh = Mesh(torch.from_numpy(ico.vertices).float().to(DEVICE), torch.from_numpy(kept_faces).int().to(DEVICE))

    def boundary_edges(m: Mesh) -> int:
        cm = cumesh.CuMesh()
        cm.init(m.vertices.contiguous(), m.faces.contiguous())
        cm.get_edges()
        cm.get_boundary_info()
        return int(cm.num_boundaries)

    holes_before, faces_before = boundary_edges(mesh), mesh.faces.shape[0]
    expect(holes_before == expected_boundary > 0, f"CuMesh reports {holes_before} boundary edges, numpy {expected_boundary}")
    mesh.fill_holes(max_hole_perimeter=0.2)
    holes_after, faces_filled = boundary_edges(mesh), mesh.faces.shape[0]
    expect(holes_after == 0, f"fill_holes left {holes_after} boundary edges")
    expect(faces_filled >= faces_before + 4, f"fill_holes added {faces_filled - faces_before} faces for 4 holes")
    target = 3000
    mesh.simplify(target)
    expect(0 < mesh.faces.shape[0] <= target, f"Mesh.simplify -> {mesh.faces.shape[0]} faces (target {target})")
    expect(((mesh.vertices.norm(dim=-1) - radius).abs() < 0.02 * radius).all().item(), "simplified mesh left the sphere")
    details["Mesh.fill_holes"] = f"boundary edges {holes_before} -> {holes_after}, faces {faces_before} -> {faces_filled}"
    details["Mesh.simplify"] = f"-> {mesh.vertices.shape[0]} v / {mesh.faces.shape[0]} f (target {target})"

    # --- MeshWithVoxel.query_vertex_attrs (flex_gemm grid_sample_3d) as in decode_latent / to_glb
    vres = 64
    grid = torch.stack(torch.meshgrid(*(torch.arange(vres, device=DEVICE),) * 3, indexing="ij"), dim=-1).reshape(-1, 3)
    centers = (grid.float() + 0.5) / vres - 0.5
    band = (centers.norm(dim=-1) - radius).abs() < 3.0 / vres  # a 6-voxel-thick shell around the surface
    vcoords = grid[band].int().contiguous()
    vattrs = torch.cat([centers[band], torch.ones_like(centers[band][:, :1])], dim=-1).contiguous()  # attr = voxel centre
    mwv = MeshWithVoxel(
        mesh.vertices, mesh.faces, origin=[-0.5, -0.5, -0.5], voxel_size=1 / vres,
        coords=vcoords, attrs=vattrs, voxel_shape=torch.Size([1, vattrs.shape[1], vres, vres, vres]),
        layout={"xyz": slice(0, 3), "one": slice(3, 4)},
    )
    queried = mwv.query_vertex_attrs()
    grid_pts = ((mwv.vertices - mwv.origin) / mwv.voxel_size).reshape(1, -1, 3)
    ref = grid_sample_3d_torch(
        vattrs, torch.cat([torch.zeros_like(vcoords[:, :1]), vcoords], dim=-1), mwv.voxel_shape, grid_pts, "trilinear"
    )[0]
    expect(queried.shape == (mwv.vertices.shape[0], vattrs.shape[1]), f"query_vertex_attrs shape {tuple(queried.shape)}")
    q_err = rel_err(queried, ref)
    expect(q_err < 1e-4, f"query_vertex_attrs vs torch reference rel err {q_err:.2e}")
    # Interpolating a linear field (voxel centres) at the vertices must give back ~the vertex positions.
    pos_err = (queried[:, :3] - mwv.vertices).abs().max().item() * vres
    expect(pos_err < 0.6, f"query_vertex_attrs position error {pos_err:.3f} voxel")
    expect(((queried[:, 3] - 1).abs() < 1e-4).all().item(), "constant attribute not preserved by trilinear sampling")
    details["MeshWithVoxel.query_vertex_attrs"] = (
        f"{vcoords.shape[0]} voxels, {mwv.vertices.shape[0]} vertices, rel_err vs torch={q_err:.1e}, "
        f"linear-field error {pos_err:.3f} voxel"
    )
    return details


# --------------------------------------------------------------------------------------------
# xformers (optional attention backend)
# --------------------------------------------------------------------------------------------
def check_xformers() -> dict[str, Any]:
    try:
        import xformers
        import xformers.ops as xops
    except ImportError as exc:
        raise SkipCheck(f"xformers not importable ({exc}); engine will use its SDPA fallback") from exc

    torch.manual_seed(4)
    heads, head_dim = 16, 64
    details: dict[str, Any] = {"xformers": xformers.__version__}

    def reference(q, k, v, q_lens, kv_lens):
        outs, qs, ks = [], 0, 0
        for ql, kl in zip(q_lens, kv_lens):
            qi = q[0, qs:qs + ql].transpose(0, 1).float()
            ki = k[0, ks:ks + kl].transpose(0, 1).float()
            vi = v[0, ks:ks + kl].transpose(0, 1).float()
            outs.append(F.scaled_dot_product_attention(qi, ki, vi).transpose(0, 1))
            qs, ks = qs + ql, ks + kl
        return torch.cat(outs).unsqueeze(0)

    # Self-attention (sparse full attn) and cross-attention (q vs. kv lengths) as TRELLIS.2 uses them.
    for label, q_lens, kv_lens in (
        ("self  ", [37, 128, 301], [37, 128, 301]),
        ("cross ", [64, 200, 17], [1029, 1029, 1029]),
    ):
        q = torch.randn(1, sum(q_lens), heads, head_dim, device=DEVICE, dtype=torch.bfloat16)
        k = torch.randn(1, sum(kv_lens), heads, head_dim, device=DEVICE, dtype=torch.bfloat16)
        v = torch.randn(1, sum(kv_lens), heads, head_dim, device=DEVICE, dtype=torch.bfloat16)
        mask = xops.fmha.BlockDiagonalMask.from_seqlens(q_lens, kv_lens)
        out = xops.memory_efficient_attention(q, k, v, mask)
        err = (out.float() - reference(q, k, v, q_lens, kv_lens)).abs().max().item()
        expect(out.shape == q.shape[:3] + (head_dim,), f"{label}: bad shape {tuple(out.shape)}")
        expect(err < 3e-2, f"{label}: max abs err {err:.2e}")
        op = "?"
        try:  # private API, only used for reporting which kernel was dispatched
            op = xops.fmha._dispatch_fw(xops.fmha.Inputs(query=q, key=k, value=v, attn_bias=mask), False).NAME
        except Exception:  # noqa: BLE001
            pass
        details[f"BlockDiagonalMask {label.strip()} bf16"] = f"max_abs_err={err:.2e} op={op}"

    # Dense [B, L, H, D] call (TRELLIS.2 full_attn with the xformers backend)
    q, k, v = (torch.randn(2, 257, heads, head_dim, device=DEVICE, dtype=torch.bfloat16) for _ in range(3))
    out = xops.memory_efficient_attention(q, k, v)
    ref = F.scaled_dot_product_attention(*(t.transpose(1, 2).float() for t in (q, k, v))).transpose(1, 2)
    err = (out.float() - ref).abs().max().item()
    expect(err < 3e-2, f"dense: max abs err {err:.2e}")
    details["dense bf16"] = f"max_abs_err={err:.2e}"
    return details


# --------------------------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--log", type=Path, default=HERE / "verify_extensions.log", help="log file (tee'd)")
    parser.add_argument(
        "--cold-cache", action="store_true",
        help="wipe and use cache_cold/ so all Triton kernels are JIT-compiled and autotuned from scratch",
    )
    args = parser.parse_args()

    args.log.parent.mkdir(parents=True, exist_ok=True)
    with args.log.open("w", encoding="utf-8") as log:
        sys.stdout = Tee(sys.__stdout__, log)  # type: ignore[assignment]
        sys.stderr = Tee(sys.__stderr__, log)  # type: ignore[assignment]
        print(f"verify_extensions.py  {time.strftime('%Y-%m-%d %H:%M:%S')}{'  (cold cache)' if args.cold_cache else ''}")
        checks: list[tuple[str, Callable[[], dict[str, Any]], bool]] = [
            ("environment", check_environment, False),
            ("imports", check_imports, False),
            ("flex_gemm.sparse_submanifold_conv3d", check_flex_gemm_spconv, False),
            ("flex_gemm.grid_sample_3d", check_flex_gemm_grid_sample, False),
            ("cumesh (CuMesh + cuBVH)", check_cumesh, False),
            ("nvdiffrast (RasterizeCudaContext)", check_nvdiffrast, False),
            ("o_voxel.serialize", check_o_voxel_serialize, False),
            ("o_voxel convert + postprocess.to_glb", check_o_voxel_mesh_and_glb, False),
            ("o_voxel.io (SVO codec + attribute filters)", check_o_voxel_io, False),
            ("o_voxel boundary QEF (open mesh)", check_o_voxel_boundary_qef, False),
            ("trellis2 modules (ATTN_BACKEND=sdpa)", check_trellis2_integration, False),
            ("xformers memory_efficient_attention", check_xformers, True),
        ]
        results = [run_check(name, fn, optional) for name, fn, optional in checks]

        print("\n=== SUMMARY ===")
        for r in results:
            print(f"  {r.status:4}  {r.seconds:7.2f}s  {r.name}{' (optional)' if r.optional else ''}")
        failed = [r for r in results if r.status == "FAIL" and not r.optional]
        optional_failed = [r for r in results if r.status == "FAIL" and r.optional]
        verdict = "ALL REQUIRED CHECKS PASSED" if not failed else f"{len(failed)} REQUIRED CHECK(S) FAILED"
        if optional_failed:
            verdict += f"; {len(optional_failed)} optional check(s) failed"
        print(f"\n{verdict}")
        sys.stdout.flush()
        sys.stdout, sys.stderr = sys.__stdout__, sys.__stderr__
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
