---
name: img3d-architecture
description: Architecture and authoritative REST/WebSocket API contract of the Img3D image-to-3D app (FastAPI backend + React frontend + TRELLIS.2 engine adapter). Use before adding/changing endpoints, schemas, backend modules, the engine seam, or the frontend API layer in E:\14-OCT-2026\Img3D.
---

# Img3D architecture

The full contract is in [api-contract.md](api-contract.md) — read it before changing anything that crosses a
layer boundary (API ↔ frontend, services ↔ inference engine, storage layout).

Layering (dependencies point downward only):

```
api (FastAPI routers, deps, ws)
  └─ services (reference/job/result/system/auth services)
       ├─ workers (JobQueue, GenerationWorker — single GPU thread)
       │    └─ pipelines (ImageTo3DPipeline, ReferenceStrategy)
       │         └─ inference (InferenceEngine seam → trellis/ adapter)   ← only place torch/trellis2 are imported
       ├─ storage (RuntimePaths, ReferenceStore, JobStore — JSON on disk under backend/runtime)
       └─ processing (image validation/normalization, mesh stats)
core (settings-aware logging, exceptions, events, container, lifecycle, security, middleware)
config (pydantic-settings)   models (domain enums/dataclasses)   schemas (Pydantic API models)   utils
```

Auto Mesh Tuner: `launcher/` (Blender runtime detection/selection/download/process runner) is a standalone,
stdlib-only lower-level package used by services and `scripts/auto_mesh_tuner.py`; it never imports backend modules.
`blender_scripts/` runs only inside Blender's Python (via `launcher.BlenderProcessRunner`) and is never imported by
the backend.

Seam files (stable, shared): `backend/models/enums.py`, `backend/core/exceptions.py`, `backend/inference/base.py`.
