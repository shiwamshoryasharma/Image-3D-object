---
name: img3d-environment
description: Maintain or diagnose Img3D's embedded Windows Python, pinned model dependencies, CUDA extensions and local model assets. Use before package changes, extension builds, or investigating model import and DLL failures.
---

# Img3D environment

Use backend/python/python.exe -s for backend work. Python 3.14.2 runs isolated,
so script-directory imports and PYTHONPATH cannot be assumed. main.py and test
entry points bootstrap their paths. -s prevents user-site packages shadowing
the embedded stack; use -B for read-only inspection.

Read local backend/constraints.txt and requirements.txt before package changes.
The existing detailed
[environment skill](../../../.claude/skills/img3d-environment/SKILL.md) and
[build patches](../../../backend/third_party/PATCHES.md) explain the machine's
non-obvious compiler/runtime constraints.

## Preserve the pinned stack

Recorded pins: torch 2.14.1+cu130, torchvision 0.29.1+cu130, transformers 4.57.6,
huggingface-hub 0.36.2, diffusers 0.39.0. Newer diffusers can force an incompatible
Hub upgrade. Do not reinstall torch as a generic troubleshooting step or install
a second OpenCV distribution.

When a package change is actually required, use embedded uv pip with
--python targeting the embedded interpreter, --link-mode=copy,
--only-binary ":all:", and -c backend/constraints.txt. Determine the real package
and version from the task; do not run an unconstrained bulk upgrade.
Native extension builds use the existing build script instead of wheel-only
package installation.

## Compiler and attention findings

The project's working build combination is local CUDA nvcc 13.4 with MSVC 14.51.
Do not install a system CUDA toolkit or modify machine settings to work around
the existing project toolchain. Historical failed combinations: nvcc 13.0 with
MSVC 14.51 crashes in cudafe++; MSVC 14.29 lacks the needed C++20 support.

backend/scripts/build_extensions.cmd owns CuMesh, FlexGEMM, nvdiffrast and
o_voxel builds and refuses replacement while extensions are loaded. Sources and
patches live under backend/third_party. The documented build targets sm_120.

The Windows stack uses torch SDPA plus the adapter's sparse-attention fallback.
Do not add flash-attn or xformers merely because upstream suggests them; the
documented Windows/sm_120 stack did not have usable kernels.

## Assets, cache and readiness

Reuse weights in backend/.model. Check completeness rather than assuming a
directory exists or repeating an old gated-download failure. Do not redownload
existing TRELLIS.2 weights. Required local auxiliary assets include DINOv3,
BiRefNet (or the configured background remover) and the TRELLIS v1 SS decoder.

Provider imports are lazy and occur after Hugging Face environment configuration.
Inspect readiness and provider errors before making environment changes.
For anonymous calls to public Hub repositories, the project records token=False
to avoid a stale cached OAuth token; never print credentials from backend/.env.

Keep production Triton/FlexGEMM caches intact. Validation uses separate caches
under test; use [the validation skill](../img3d-validation/SKILL.md).
Directory presence was inspected on 2026-10-29, but native imports, GPU readiness
and weight integrity were not revalidated during knowledge setup.
