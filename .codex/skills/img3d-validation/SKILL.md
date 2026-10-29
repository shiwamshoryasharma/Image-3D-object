---
name: img3d-validation
description: Run or select Img3D tests and real model-generation validation using test/images, isolated test state and the shared GPU lock. Use for backend/frontend checks, engine smoke tests, part-analysis acceptance or end-to-end verification.
---

# Img3D testing and generation validation

Read [memory](../../MEMORY.md), then
[commands.md](references/commands.md) for the applicable test track.
Every testing layer, script, configuration, cache, log, build and output stays
under E:/14-OCT-2026/Img3D/test.

## Select the least expensive sufficient check

- API/services/storage changes: relevant test/backend tests with test doubles.
- Inference seam/adapter changes: relevant test/engine unit and parity tests.
- Environment/native changes: targeted extension or auxiliary checks; inspect
  scripts before running because some checks load models or execute GPU kernels.
- Frontend state/UI changes: relevant unit tests and existing review gates;
  test browser behavior for interactive changes.
- Real generation/part/workflow validation: existing smoke scripts and real local
  weights, using test/images input and test-only runtime directories.

Passing mocked tests is not proof of real GPU generation. Choose real tests when
the requested change needs them; do not start long model runs for a docs-only task.

## Input and runtime isolation

Use test/images/ALICE/ALICE_full_front_view.png as the default. The second
character fixture is test/images/ANIME_CHARACTER_01/Alice.png.
Do not generate substitute images or use production job inputs.

test/part_aware/fixtures.json indexes reusable meshes produced from these images.
Treat fixture artifacts as read-only; copy into an isolated test runtime before
a writer or analysis uses them. If absent, regenerate from the allowed images.

Inspect script defaults, output destinations, imports and side effects first.
Use test/.runtime, test/.logs, test/outputs, test/.pytest_tmp and test/.hf_home.
Redirect pre-existing cache environment variables as well as application settings;
the TRELLIS environment uses setdefault for some caches. Do not purge production
caches to force a cold run. Check child-process flags too: a parent Python -B
flag is not inherited by a separately launched interpreter.

## GPU ownership

Wrap any real-weight/GPU command in test/part_aware/gpu_lock.py. Check for an
already-running backend using the GPU before starting another process; the lock
cannot serialize an existing process that never acquired it. Do not terminate a
user server without authorization.

For E2E, a server that owns the GPU holds the lock; the HTTP-only client does not
reacquire it. Parts/workflow smoke scripts launch their own test server, so put
the wrapper around the whole script. Avoid nested acquisition of the GPU lock.

## Acceptance and reporting

Real generation must produce a non-empty GLB with faces, UVs and preview output.
Part analysis must preserve original model.glb/preview.png/job.json, validate
the manifest and exact reassembly, keep semantic unlabelled ratio <= 0.10 and
face-weighted cut consistency >= 0.90. Components-only analysis reports those
label metrics without enforcing semantic gates.

Report exact commands, actual outcomes, input image, settings and artifact paths.
Distinguish current results from the historical baselines in ../../memory/status.md.
For model runs, record elapsed time and VRAM observations when available.

## Generation-quality regression workflow

Read [the generation-quality checkpoint](../../memory/generation-quality.md)
before diagnosing eyes, holes, stray facets or export shading. Use the dedicated
cleanup/hole/GPU regressions and replay captured decoded geometry for comparisons.
Exact collinear boundary loops are T-junction seams, not missing surface area.
A UV-split mesh needs authored smooth normals retained through topology edits.
Zero open boundary edges alone does not establish anatomical accuracy or
manifoldness.
