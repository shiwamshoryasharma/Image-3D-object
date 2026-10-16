---
name: img3d-architecture
description: Architecture and authoritative REST/WebSocket API contract of the Img3D image-to-3D app (FastAPI backend + React frontend + TRELLIS.2 engine adapter). Use before adding/changing endpoints, schemas, backend modules, the engine seam, the part-aware layer (runs, providers, parts, workflows), or the frontend API layer in E:\14-OCT-2026\Img3D.
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

## Part-aware layer (contract section 11; milestone docs in `.claude/skills/img3d-part-aware/`)

Additive packages, same downward direction (`api → services → {pipelines, parts, processing, storage, models,
schemas, core}`):

```
api/part_routes.py + api/v1/{features,model_registry,image_generations,segmentations,part_analyses,workflows}.py
  └─ services/{feature,model_registry,image_generation,segmentation,part_analysis,workflow}_service.py,
     workflow_catalog/compiler, part_files, run_events, run_retention_service       (torch-free)
       ├─ workers/run_worker.py   RunWorker: one run at a time; GPU phases → GenerationWorker.submit_gpu_task
       │                          (the same single gpu-worker thread), CPU phases → "parts-cpu" thread
       ├─ pipelines/runs/*        run framework (executor protocol, reporter, context) + ig_/sg_/pa_ executors
       ├─ parts/                  torch-free asset library (numpy, scipy, Pillow, json, struct): taxonomy, manifest,
       │                          geometry, buffers, export, validate, publish, reader; never imports services/api/workers
       ├─ processing/             + conditioning, imagegen_readiness, seg2d/ (numpy/opencv/Pillow only)
       ├─ storage/                + run_store, run_layout, workflow_store (storage/paths.py stays protected)
       └─ inference/providers/    torch-free registry, catalog, states, vram guard, vendor_import
            └─ inference/{imagegen,segmentation,parts}/   base.py = torch-free role ABCs; implementation modules below
```
Workflows (`wf_`) run on the event loop only: they create children through the services (`JobService.create_job`
for the reconstruction) and never touch the GPU.

**Torch import rule.** Only `inference/imagegen/zimage.py`, `inference/segmentation/{gdino,sam2,florence2,
see_through,sapiens2}.py`, `inference/parts/{raster_lifter,cameras}.py` and the existing `inference/trellis/**` may
import torch, diffusers, transformers, nvdiffrast, cumesh or vendored model code. The registry loads them lazily
(`importlib.import_module` inside a function, after `configure_hf_environment`) and turns any import error into an
`UnavailableProvider`. `inference/providers/vram.py` may import torch inside function bodies only.
`api/ services/ pipelines/ parts/ processing/ storage/ models/ schemas/ core/ workers/` never import them, and
part-aware code never imports the tuner (`services.mesh_tuner_service`, `workers.tuning_worker`, `launcher`,
`blender_scripts`). Vendored code is imported only through `inference/providers/vendor_import.py` (never via
`sys.path`). `test/backend/test_import_boundaries.py` enforces this with an AST scan.

**Protected files.** The image-to-3D path (`DESIGN.md` §1.3) is sha256-pinned in `test/backend/protected_files.sha256`
(`test_protected_files.py`); a change needs the user's approval and a re-pin in the same commit.
