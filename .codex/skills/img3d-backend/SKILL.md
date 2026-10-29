---
name: img3d-backend
description: Modify Img3D FastAPI endpoints, services, job and run scheduling, TRELLIS inference integration, or semantic-part assets while preserving its existing reconstruction contract and GPU/import boundaries.
---

# Img3D backend changes

Read [memory](../../MEMORY.md) and the relevant section of the existing
[API contract](../../../.claude/skills/img3d-architecture/api-contract.md).
Contract sections 7-8 cover original REST/WebSocket behavior; section 11 covers
part-aware resources. Compare the actual router/schema before editing.

## Locate the smallest seam

- backend/main.py is the application entry point.
- backend/core/container.py wires the original app; part_aware_container.py wires
  additive providers, run services and executors.
- api -> services -> workers/pipelines -> inference. Stores own disk layout;
  processing and parts own torch-free image/geometry work.
- Original reconstruction goes through inference/base.py and inference/trellis.
  Preserve the seven reference slots, primary choice, JobStage values, progress
  calculation, existing system payload and original model.glb.
- Part-aware resources use ig_/sg_/pa_/wf_ IDs and separate stores/runs. They read
  finished reconstruction jobs; they do not rewrite original artifacts.

## Keep execution and imports correct

All new CUDA operations use GenerationWorker.submit_gpu_task on the existing
gpu-worker. RunWorker has one run consumer, dedicated CPU phases and provider
VRAM/offload guards. Workflows orchestrate child services on the event loop
without executing GPU work themselves.

Do not block the event loop with model loads, geometry work or subprocess waits.
On cancel/timeout, await the actual worker callable before cleanup or a terminal
state; cancelling an asyncio Future does not stop native work.

Heavy model imports belong in inference implementations. API/services/workers/
pipelines/parts/processing/storage/core must not import torch, diffusers,
transformers or native model modules. Use the lazy provider registry and
inference/providers/vendor_import.py for added vendored providers. The original
TRELLIS adapter has its existing runtime source setup; do not generalize it to
new providers.

backend/launcher is standalone stdlib code; backend/blender_scripts executes
inside Blender and is never imported into the API process. Part-aware code
does not depend on the tuner.

## Part semantics

Use the frozen taxonomy and img3d.parts/1 manifest. Preserve seam records,
relationships, shared atlas and exact reassembly. Continuous skin/face/body
stays semantic; eyebrow/lash geometry and hidden-surface completion are deferred.
The accepted cut consistency gate is face-weighted >= 0.90; preserve the
accepted weaker-vote partner-wall rule.

## Validate the affected behavior

Use [the validation skill](../img3d-validation/SKILL.md). Select relevant API,
run, parts, import-boundary and legacy-invariance tests. Protected backend paths
are listed in test/backend/protected_files.sha256. Do not re-pin hashes merely
to make a guard pass; consult the contract and current user authorization before
intentionally changing a protected behavior.

Keep fake providers and fixtures under test. Test data and artifacts must not
touch backend/runtime/jobs. Do not enable mesh tuning to test part-awareness.
