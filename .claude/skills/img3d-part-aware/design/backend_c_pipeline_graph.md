# Backend proposal C — pipeline-graph-first part-aware architecture

Date: 2026-10-16. Status: PROPOSAL (design only; no project code changed). Angle: **pipeline graph first** — every
generation is a typed step plan compiled from a graph; the Node UI graph, the Default/Mobile/Tablet simple forms and
legacy requests all compile to the same server-side `JobPlan`.

Inputs read in full before writing: `.claude/CLAUDE.md`, `img3d-architecture/api-contract.md`, and every file in
`img3d-part-aware/research/` (contract map, generation map, TRELLIS.2 internals, part-aware 3D research,
segmentation research, text-to-image research, environment audit, frontend map, UI-modes research). Code facts were
re-checked in `pipelines/{base,progress,image_to_3d}.py`, `models/job.py`, `schemas/jobs.py`,
`workers/generation_worker.py`, `inference/base.py`, `core/container.py`, `services/job_service.py`,
`config/settings.py`, `storage/paths.py`, `test/backend/test_health_system.py`.

---------------------------------------------------------------------------------------------------
## 0. Summary of decisions

1. **One unit of GPU work stays the `Job`.** No second GPU queue, no second worker. A job either has
   `plan = null` (legacy image-to-3D, executed by today's `ImageTo3DPipeline.run` untouched) or a `JobPlan` (an
   ordered list of typed steps executed by a new `PlanRunner` on the same `gpu-worker` thread).
2. **Typed steps:** `source_image`, `source_job`, `text_to_image`, `preprocess`, `segmentation_2d`, `reconstruct`,
   `lift_labels`, `part_extraction`, `export`; reserved and **locked**: `part_refine`, `tune` (no executors exist).
3. **Graph → plan compiler on the server.** The client sends either the legacy/simple body (`reference_set_id` or
   `source`, `settings`, optional `settings.parts`) or an executable graph (`pipeline`, node types from
   `GET /pipeline/catalog`). Both compile through `PlanCompiler` to one canonical `JobPlan`. A graph that is exactly
   "input image → image-to-3D → export" with no parts compiles to `plan = null`, i.e. the byte-identical legacy path.
4. **Job kinds:** `image_to_3d` (default), `prompt_to_3d`, `part_decomposition` (derived re-run on a completed job,
   no TRELLIS sampling).
5. **Progress:** the frozen `JobStage` list, `STAGE_TABLE` and `compute_percent` are untouched. Every step maps to a
   legacy stage (`preprocessing` before reconstruction, the engine's own stages during it, `finalizing` after it),
   so `progress` stays contract-exact. Step-level state, the plan percent and the plan ETA live in the new
   `Job.plan` object delivered through the existing `job.updated` event.
6. **Provider registry with honest states.** Every model-backed engine (text-to-image, 2D segmentation, lifting,
   future 3D segmenters/completers/part generators) is a `ProviderSpec` with licence metadata and a runtime state:
   `disabled | not_approved | missing_dependencies | not_installed | license_required | unsupported | available |
   loading | ready | error`. Model providers are usable only when the user has approved them
   (`IMG3D_PROVIDERS_APPROVED`), their packages are importable and their weights exist. Nothing is faked.
7. **Single GPU thread scheduling** through a `GpuResidencyManager`: aux models load lazily into CPU RAM, move to the
   GPU for their step, and are offloaded right after; TRELLIS keeps its own low-VRAM behaviour; an optional,
   additive `InferenceEngine.release_vram()` covers `low_vram=false`. Aux steps never run inside
   `engine.generate`, so the TRELLIS RNG/parity contract is untouched.
8. **Part representation:** TRELLIS has no native parts (internals report §2). v1 lifts 2D semantic masks onto the
   exported `model.glb` faces (single-view face-ID projection + graph propagation), keeps continuous face/body as
   **regions** (per-face labels + `_PART_ID`), and cuts **physical** parts (eyes, hair, clothes, accessories,
   detachable objects) into separate nodes of one multi-node GLB sharing the original atlas, with seam loops and
   weld ids so reassembly is lossless. Eyebrows and eyelashes are `physical_deferred` (mask + region now, geometry
   later). No new geometry is generated, nothing is re-baked, `model.glb` is never modified.
9. **Tuning stays off:** a new exposure flag `IMG3D_FEATURE_MESH_TUNING=false` (reported by `/features`, hides all
   tuner UI) plus `IMG3D_PART_REFINEMENT_ENABLED=false` (locks refine nodes; a `PartConsumer` registry refuses
   registration). Zero tuning/refinement algorithms are added. Existing tuner endpoints keep their tested behaviour;
   a hard runtime off is the user-local `.env` line `IMG3D_BLENDER_ENABLED=false` (needs the user's go-ahead).
10. **Model choices are not made here.** The catalog lists every candidate from the research with licence and size;
    the plan works with any approved subset: with no new model at all, `geometry_components` part extraction still
    runs on real output; with a segmentation provider, semantic parts work; with a T2I provider, prompt-to-3D works.

---------------------------------------------------------------------------------------------------
## 1. Hard constraints and how this design honours each

| Constraint | Mechanism |
|---|---|
| Image-to-3D path and `model.glb` byte-for-byte unchanged when part features are off | `PipelineRouter.run` delegates `plan is None` jobs to the existing `ImageTo3DPipeline.run(job, reporter, cancel)` with the same arguments; `settings.parts` is dropped from the stored settings when absent/disabled; no TRELLIS adapter change is needed for v1 parts (the reconstruct step calls the same pipeline). Test: `test_pipeline_router.py` spies the call; `test_domain_compat.py` asserts identical `settings` dicts. |
| Pinned `JobStage` list, percent formula, `/system` keys | No new `JobStage`; `compute_percent` untouched; plan progress lives in `Job.plan`; feature discovery lives at `GET /features` and `GET /pipeline/catalog`; `EngineCapabilities.to_dict()` untouched. |
| Additive, backward-compatible API; request bodies reject unknown fields | All new request fields optional with defaults; new response keys additive; new endpoints only. `CreateJobRequest.reference_set_id` becomes optional only with a model validator that keeps every legacy body valid and every legacy-invalid body invalid. |
| Single GPU worker thread; sequential model loading with offload | All steps run inside `GenerationPipeline.run` on `gpu-worker`; `GpuResidencyManager`; aux model `load()/unload()` also only on that thread (idle unload is queued through `GenerationWorker.submit_maintenance`). |
| No new heavy native deps | v1 uses trimesh, numpy, scipy, Pillow, nvdiffrast, cumesh (installed). Segmentation uses transformers-4.57.6-native classes or vendored pure-torch modules. T2I needs only `diffusers==0.39.0` + `accelerate==1.15.0` (pure wheels) and only after approval. |
| Never upgrade transformers / huggingface-hub | `backend/constraints.txt` (new) pins them; providers whose code needs transformers 5 are `unsupported` (sidecar is an open question). |
| Tuning disabled, feature-flagged, no new tuning algorithms | §9. |
| No fake/mock generation data in the app | Unavailable providers report a reason and the request is rejected (422) or the optional step is `skipped` with a warning. Test doubles live under `test/` only. |
| Model choices pending | §6.4 catalog + approval list; every slice works with fakes in tests; real smoke tests are skipped unless the provider is installed and approved. |
| Blender-side Python out of scope | Not touched. |

---------------------------------------------------------------------------------------------------
## 2. Architecture overview

```
HTTP / WS (api/v1)                      pipeline.py  features.py  providers.py  parts.py  jobs.py(+kind,+rebuild)
   │
services/                               JobService.create_job ──► PlanCompiler ──► Job(kind, source, plan)
   │                                    PipelineService (catalog, validate)  FeaturesService  ProviderService
   │                                    PartFiles (whitelisted file serving)  PromptImageImporter (loop-side hook)
   ▼
workers/GenerationWorker (unchanged loop; +requires_engine gate; +submit_maintenance)
   │  gpu-worker thread
   ▼
pipelines/plan/router.py  PipelineRouter(GenerationPipeline)
   ├── plan is None ─────────────► pipelines/image_to_3d.py ImageTo3DPipeline.run   (legacy, untouched)
   └── plan present ─────────────► pipelines/plan/runner.py PlanRunner
                                      for step in plan (topological, deterministic):
                                        StepExecutor (pipelines/plan/steps/<type>.py)
                                          ├── reconstruct ─► ImageTo3DPipeline.run (same engine.generate)
                                          ├── text_to_image / segmentation_2d / lift_labels
                                          │      └─► inference/providers/<kind>/<impl>.py  (torch lives here only)
                                          └── part_extraction / export ─► parts/ (torch-free geometry + glTF)
                                      GpuResidencyManager (inference/providers/residency.py)
storage                                 runtime/jobs/<job>/{job.json, inputs/, model.glb, preview.png,
                                                            source/, steps/<step_id>/, parts/}
```

Layering rules (extend the existing ones):
- `api/`, `services/`, `pipelines/`, `parts/`, `models/`, `schemas/` never import torch, diffusers, transformers or
  nvdiffrast. Only `inference/trellis/**` and `inference/providers/<kind>/<impl>.py` do, and only lazily.
- `inference/providers/{base,specs,catalog,registry,residency,probes,unavailable}.py` are torch-free (same rule as
  `inference/base.py`). The registry imports implementation modules lazily and substitutes an
  `UnavailableProvider` on `ImportError`, mirroring `inference/registry.py`.
- `pipelines/plan/` depends on providers only through structural `Protocol`s defined in
  `pipelines/plan/capabilities.py`, so `inference/` never imports `pipelines/`.

---------------------------------------------------------------------------------------------------
## 3. Step model

### 3.1 Port / artifact types

| Type | UI-visible port | Produced by | Files (relative to the job dir) |
|---|---|---|---|
| `image_set` | yes | `source_image`, `text_to_image`, `source_job` | `inputs/<img>.png` (+ `source/generated.png` for prompts) |
| `conditioning` | no (internal) | `preprocess` | `steps/preprocess/conditioning.png` (RGBA tight square crop), `steps/preprocess/conditioning.json` |
| `segmentation` | yes | `segmentation_2d`, `source_job` (when the parent has one) | `steps/segmentation_2d/{labels.png, overlay.png, segmentation.json, masks/<value>.png}` |
| `mesh` | yes | `reconstruct`, `source_job` | `model.glb`, `preview.png` (canonical, unchanged names) |
| `labels_3d` | no (internal) | `lift_labels` | `steps/lift_labels/{face_labels.u16, face_confidence.f16, lift.json, views/*.png}` |
| `part_set` | yes | `part_extraction` (draft), finalised by `export` | `steps/part_extraction/{partition.npz, manifest.draft.json}` → `parts/{manifest.json, model_parts.glb, face_labels.u16, weld_map.u32, seams/*.u32, masks/<pt>.png, <pt>/model.glb}` |

### 3.2 Step types (`models/plan.py: StepType`, a `StrEnum`)

| Step type | Inputs (port: type) | Outputs | Provider kind | Legacy stage used for `progress` | Default weight | Failure policy | GPU |
|---|---|---|---|---|---|---|---|
| `source_image` | – | `images: image_set` | – | `preprocessing` | 0 | hard | no |
| `source_job` | – | `images`, `mesh`, optional `masks: segmentation` | – | `preprocessing` | 0 | hard | no |
| `text_to_image` | – (params = prompt) | `images: image_set` | `t2i` | `preprocessing` | 6 | hard | yes |
| `preprocess` | `images` | `conditioning` | engine `prepare_conditioning` or torch-free crop | `preprocessing` | 1 | soft (downstream masks skipped) | BiRefNet only when the image has no alpha |
| `segmentation_2d` | `conditioning`, `images` | `masks: segmentation` | `segmentation_2d` | `preprocessing` | 4 | soft | yes |
| `reconstruct` | `images` | `mesh` | the existing `InferenceEngine` | engine stages, then `finalizing` | 100 | hard | yes |
| `lift_labels` | `mesh`, `conditioning`, `masks` | `labels_3d` | `lifting` | `finalizing` | 10 (single view) / 30 (multi view) | soft | yes (nvdiffrast) |
| `part_extraction` | `mesh`, optional `labels_3d` | `parts: part_set` | `part_extraction` (`shared_atlas` or `geometry_components`) | `finalizing` | 4 | soft | no (CPU) |
| `export` | `mesh` or `part_set` | – (sink) | – | `finalizing` | 6 | soft | no (CPU) |
| `part_refine`, `tune` | `part_set` | `part_set` | `refine` | – | – | – | **locked**: rejected by the compiler with `step_locked` |

"Soft" means: the step's failure marks the step `failed`, appends a `JobWarning`, skips dependants, and the job
still completes with `model.glb` (the user's working result is never lost because a part step failed). "Hard" fails
the job (existing error mapping). Cancellation always aborts the job.

### 3.3 Graph node types (what the Node UI edits) and their expansion

The Node UI edits **nodes**; the compiler expands nodes into steps. In v1 every node type has `max_instances = 1`,
so step ids equal step types (`steps/<step_type>/` folders, no numbering).

| Node type | Category | Inputs | Outputs | Params model | Expands to | Requires feature |
|---|---|---|---|---|---|---|
| `input_image` | input | – | `images: image_set` | `InputImageParams` | `source_image` | `image_to_3d` |
| `prompt` | input | – | `images: image_set` | `PromptParams` | `text_to_image` | `prompt_to_3d` |
| `job_result` | input | – | `images: image_set`, `mesh: mesh`, `masks: segmentation` (only if the parent has one) | `JobResultParams` | `source_job` | `part_rebuild` |
| `segmentation` | analyze | `images: image_set` (required) | `masks: segmentation` | `SegmentationParams` | `preprocess` (inserted once) + `segmentation_2d` | `segmentation_2d` |
| `image_to_3d` | generate | `images: image_set` (required) | `mesh: mesh` | `ImageTo3DParams` (= `GenerationSettings` without `parts`) | `reconstruct` | `image_to_3d` |
| `part_extraction` | parts | `mesh: mesh` (required), `masks: segmentation` (optional) | `parts: part_set` | `PartExtractionParams` | `lift_labels` (only when `masks` is connected) + `part_extraction` | `part_extraction` |
| `export` | output | `asset: mesh \| part_set` (required, single) | – | `ExportParams` | `export` | always |
| `part_refine`, `mesh_tune`, `eye_correction`, `rig_correction` | refine | `parts: part_set` | `parts: part_set` | none | – | **locked** (`available:false, locked:true, reason:"Paused until approved"`) |
| `part_completion`, `part_generation`, `segmentation_3d` | parts | `parts`/`mesh` | `parts` | none | – | `available:false` (`unsupported`: needs native deps or a sidecar) |

Deterministic step order: `source_* | text_to_image` → `preprocess` → `segmentation_2d` → `reconstruct` →
`lift_labels` → `part_extraction` → `export`. The compiler records `node_id` on every step so the UI can light the
node that owns it.

### 3.4 Node parameter models (`schemas/pipeline_params.py`, all `RequestModel`, `extra="forbid"`)

```python
PartGroup = Literal["eyes", "eyebrows", "eyelashes", "hair", "clothes", "accessories", "detachable"]
RegionGroup = Literal["face", "body"]
Subject = Literal["auto", "anime_character", "realistic_human", "object"]

class InputImageParams(RequestModel):
    reference_set_id: str                       # validated with is_valid_id(_, "rs")
    primary_image_id: str | None = None         # validated with is_valid_id(_, "img")

class PromptParams(RequestModel):               # identical to schemas/source.py PromptSourceIn minus "type"
    prompt: str = Field(min_length=1, max_length=2000)        # upper bound also <= settings.max_prompt_chars
    negative_prompt: str | None = Field(default=None, max_length=1000)
    preset: Literal["character", "object", "raw"] = "character"
    style: Literal["none", "realistic", "stylized", "anime"] = "none"
    provider: str = "auto"                      # provider id or "auto"
    image_seed: int = Field(default=0, ge=0, le=2_147_483_647)
    randomize_image_seed: bool = True
    width: int | None = Field(default=None, ge=256, le=2048)   # multiple of 16, checked vs provider caps
    height: int | None = Field(default=None, ge=256, le=2048)
    steps: int | None = Field(default=None, ge=1, le=100)
    guidance: float | None = Field(default=None, ge=0, le=20)
    part_hint: bool = True                      # append the "parts clearly distinct" clause (t2i research §8.3)

class JobResultParams(RequestModel):
    job_id: str                                 # completed job, is_valid_id(_, "job")
    reuse_segmentation: bool = True

class SegmentationParams(RequestModel):
    provider: str = "auto"
    subject: Subject = "auto"
    vocabulary_extra: list[Annotated[str, StringConstraints(min_length=1, max_length=40)]] = Field(default_factory=list, max_length=16)
    head_pass: bool = True                      # second pass on a head crop of the ORIGINAL upload
    min_confidence: float = Field(default=0.35, ge=0, le=1)

class ImageTo3DParams(GenerationSettings): ...  # same fields/bounds as today, no "parts"

class PartExtractionParams(RequestModel):
    separate: list[PartGroup] = Field(default_factory=lambda: list(ALL_PART_GROUPS))   # physical assets
    regions: list[RegionGroup] = Field(default_factory=lambda: ["face", "body"])         # semantic regions
    method: Literal["auto", "single_view", "multi_view", "voxel", "components"] = "auto"
    views: int = Field(default=0, ge=0, le=24)  # extra views for multi_view; 0 = input view only
    smoothing_iterations: int = Field(default=2, ge=0, le=10)
    min_part_faces: int = Field(default=200, ge=0, le=1_000_000)

class ExportParams(RequestModel):
    assembled_glb: bool = True                  # parts/model_parts.glb
    per_part_files: bool = False                # parts/<pt>/model.glb (each embeds the full atlas; off by default)
    face_labels: bool = True                    # parts/face_labels.u16 sidecar
    masks: bool = True                          # parts/masks/<pt>.png
```

### 3.5 Simple form ⇄ graph (bijection)

`settings.parts` (simple form used by Default/Mobile/Tablet) is exactly the union of the part node params:

```json
"parts": {
  "enabled": true,
  "separate": ["eyes","eyebrows","eyelashes","hair","clothes","accessories","detachable"],
  "regions": ["face","body"],
  "segmentation": {"provider":"auto","subject":"auto","vocabulary_extra":[],"head_pass":true,"min_confidence":0.35},
  "extraction": {"method":"auto","views":0,"smoothing_iterations":2,"min_part_faces":200},
  "export": {"assembled_glb":true,"per_part_files":false,"face_labels":true,"masks":true}
}
```
- `segmentation: null` ⇒ no Segmentation node ⇒ `method` must be `components` or `auto` (auto resolves to
  `components`).
- `enabled: false` or `parts` absent ⇒ no part nodes ⇒ (for image sources) the trivial graph ⇒ `plan = null`.
- The compiler always writes the normalized `settings.parts` back into `Job.settings`, also for graph requests, so
  the frontend's legacy *retry* (it resends `job.settings` with `reference_set_id`) rebuilds the same plan without
  the graph. `PlanCompiler.from_simple(request)` and `PlanCompiler.from_graph(spec)` must return equal plans for
  equivalent inputs (tested).

---------------------------------------------------------------------------------------------------
## 4. Domain model additions (all optional with defaults; `DomainModel` is `extra="ignore"`)

### 4.1 `models/plan.py` (new)

```python
class JobKind(StrEnum):      image_to_3d, prompt_to_3d, part_decomposition
class StepType(StrEnum):     source_image, source_job, text_to_image, preprocess, segmentation_2d, reconstruct,
                             lift_labels, part_extraction, export, part_refine, tune
class StepStatus(StrEnum):   pending, running, done, warning, skipped, failed, cancelled
class PortType(StrEnum):     image_set, conditioning, segmentation, mesh, labels_3d, part_set

class ArtifactFile(DomainModel):
    role: str                     # "image", "labels", "overlay", "manifest", ...
    path: str                     # POSIX path relative to the job dir; never absolute, never ".."
    media_type: str
    size_bytes: int = 0

class StepOutput(DomainModel):
    port: str
    type: PortType
    files: list[ArtifactFile] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)

class StepError(DomainModel):
    code: str
    message: str
    details: Any | None = None

class PlanStep(DomainModel):
    id: str                       # == type in v1; regex ^[a-z][a-z0-9_]{0,31}$
    type: StepType
    node_id: str | None = None    # graph node that owns this step
    label: str
    provider: str | None = None   # resolved provider id (None for source/export steps)
    params: dict[str, Any] = Field(default_factory=dict)      # normalized node params relevant to this step
    inputs: dict[str, str] = Field(default_factory=dict)      # port -> "<step_id>.<port>"
    legacy_stage: JobStage
    weight: float
    failure_policy: Literal["hard", "soft"]
    status: StepStatus = StepStatus.PENDING
    progress: float = 0.0         # 0..1 inside the step
    message: str | None = None
    started_at: UtcDateTime | None = None
    finished_at: UtcDateTime | None = None
    duration_s: float | None = None
    outputs: list[StepOutput] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error: StepError | None = None
    skip_reason: str | None = None   # "provider_unavailable" | "upstream_skipped" | "upstream_failed" | "not_needed"

class JobPlan(DomainModel):
    schema_: Literal["img3d.plan/1"] = Field(default="img3d.plan/1", alias="schema")
    kind: JobKind
    spec: dict[str, Any]          # normalized PipelineSpec JSON the plan was compiled from (always graph form)
    steps: list[PlanStep]
    current_step: str | None = None
    percent: float = 0.0          # plan-weighted 0..100 (independent of the frozen progress.percent)
    eta_seconds: float | None = None
    requires_engine: bool = True  # whether the worker must wait for the reconstruction engine
```

### 4.2 `models/source.py` (new)

```python
class PromptProvenance(DomainModel):
    provider: str; repo_id: str | None; revision: str | None; license_id: str
    rendered_prompt: str          # template-expanded prompt actually sent to the model
    image_seed: int; width: int; height: int; steps: int; guidance: float | None
    readiness: dict[str, Any] = {}  # 3D-readiness check results (bbox touches border, components, area)

class JobSource(DomainModel):
    type: Literal["image", "prompt", "job"]
    prompt: str | None = None
    negative_prompt: str | None = None
    preset: str | None = None
    style: str | None = None
    provider: str | None = None
    image_seed: int | None = None          # resolved at creation
    generated_image_id: str | None = None  # the reserved img_ id (prompt jobs)
    parent_job_id: str | None = None       # type == "job"
    provenance: PromptProvenance | None = None   # filled by the text_to_image step
```

### 4.3 `models/parts.py` (new)

```python
class PartKind(StrEnum):  physical, physical_deferred, region
class PartsSummaryItem(DomainModel):
    id: str; taxonomy_id: str; label: str; category: str; kind: PartKind
    parent: str | None; node_name: str | None; side: Literal["left","right","center"] | None
    faces: int; confidence: float | None; source: str
    has_mesh_file: bool; has_mask: bool
class PartsSummary(DomainModel):
    schema_: str = Field(default="img3d.parts/1", alias="schema")
    counts: dict[str, int]              # by PartKind + "unknown"
    items: list[PartsSummaryItem]
    has_assembled_glb: bool; has_face_labels: bool; has_segmentation: bool
    warnings: list[str] = []
```

### 4.4 Changes to existing domain files

| File | Change |
|---|---|
| `models/job.py` | `Job.kind: JobKind = image_to_3d`, `Job.source: JobSource \| None = None`, `Job.plan: JobPlan \| None = None`, `Job.parent_job_id: str \| None = None`, `Job.warnings: list[JobWarning] = []` where `JobWarning(code: str, message: str, step_id: str \| None = None)`; `JobResultData.parts: PartsSummary \| None = None`. `STAGE_TABLE`, `STAGE_COUNT`, `JobProgress` unchanged. |
| `models/reference.py` | `ReferenceImage.provenance: dict[str, Any] \| None = None` (prompt-generated images). |
| `core/pipeline_errors.py` (new; does not edit `core/exceptions.py`) | `CapabilityUnavailableError(ValidationAppError, code="capability_unavailable")`, `InvalidPipelineError(…, "invalid_pipeline")`, `StepLockedError(…, "step_locked")`, `FeatureDisabledError(…, "feature_disabled")`, `ConflictingFieldsError(…, "conflicting_fields")`, `PartsNotAvailableError(NotFoundError, "parts_not_available")`, `ProviderNotFoundError(NotFoundError, "provider_not_found")`, `ProviderUnavailableError(EngineUnavailableError, "provider_unavailable")`, `ProviderError(EngineError, "provider_error")`, `InsufficientVramError(EngineUnavailableError, "insufficient_vram")`, `ConditioningUnavailableError(ValidationAppError, "conditioning_unavailable")`. |

Legacy `job.json` files load unchanged (`kind` defaults to `image_to_3d`, everything else `None`/empty). New legacy
jobs gain `"kind": "image_to_3d", "source": null, "plan": null, "parent_job_id": null, "warnings": []` in
`job.json`; `settings` stays byte-identical (no `parts` key).

---------------------------------------------------------------------------------------------------
## 5. Plan compiler, runner, progress

### 5.1 `pipelines/plan/catalog.py`

```python
@dataclass(frozen=True, slots=True)
class PortDef:        id: str; label: str; accepts: tuple[PortType, ...]; required: bool; multiple: bool = False
@dataclass(frozen=True, slots=True)
class OutPortDef:     id: str; label: str; type: PortType
@dataclass(frozen=True, slots=True)
class NodeTypeDef:
    type: str; version: int; category: Literal["input","analyze","generate","parts","output","refine"]
    title: str; inputs: tuple[PortDef, ...]; outputs: tuple[OutPortDef, ...]
    params_model: type[RequestModel] | None; expands_to: tuple[StepType, ...]
    requires: tuple[str, ...]           # feature ids from /features
    max_instances: int = 1; locked: bool = False; lock_reason: str | None = None
@dataclass(frozen=True, slots=True)
class StepTypeDef:
    type: StepType; label: str; legacy_stage: JobStage; weight: float
    failure_policy: Literal["hard","soft"]; provider_kind: ProviderKind | None; gpu: bool
    executor: str                       # "pipelines.plan.steps.segmentation_2d:Segmentation2DStep" (lazy)

NODE_TYPES: Mapping[str, NodeTypeDef]        # §3.3
STEP_TYPES: Mapping[StepType, StepTypeDef]   # §3.2
TEMPLATES: Mapping[str, PipelineSpecDict]    # image_to_3d, image_to_3d_parts, prompt_to_3d, prompt_to_3d_parts, rebuild_parts
LEGACY_JOB_NODE_MAP = {"queued": "image_to_3d", "waiting_for_model": "image_to_3d", "preprocessing": "input_image",
                       "conditioning": "image_to_3d", "sparse_structure": "image_to_3d", "shape": "image_to_3d",
                       "texture": "image_to_3d", "decoding": "image_to_3d", "exporting": "export",
                       "finalizing": "export", "done": "export"}   # for plan == null jobs in the Node UI
def build_catalog(features: FeaturesView, providers: CapabilityView) -> CatalogOut   # pure; ETag = sha256 of JSON
```

### 5.2 `pipelines/plan/compiler.py`

```python
@dataclass(frozen=True, slots=True)
class CompileContext:
    settings: Settings
    capabilities: CapabilityView                # provider resolution (structural Protocol, §6.2)
    engine_caps: EngineCapabilities
    engine_features: EngineFeatures             # supports_prepare_conditioning, supports_capture_latents
    lookup_set: Callable[[str], ReferenceSetView | None]   # has_alpha of the primary, image ids
    lookup_job: Callable[[str], Job | None]

@dataclass(frozen=True, slots=True)
class CompileResult:
    kind: JobKind
    plan: JobPlan | None                        # None == legacy trivial image plan
    source: JobSource | None
    settings: dict[str, Any]                    # normalized GenerationSettings (+ "parts" only when enabled)
    reference_set_id: str | None                # None for prompt jobs (JobService creates the set)
    primary_image_id: str | None
    parent_job_id: str | None
    issues: list[GraphIssue]                    # warnings only when compile succeeds

class PlanCompiler:
    def __init__(self, ctx: CompileContext) -> None
    def compile_request(self, request: CreateJobRequest) -> CompileResult      # raises InvalidPipelineError & co.
    def validate(self, request: CreateJobRequest) -> ValidationReport          # never raises for graph issues
    def from_simple(self, request: CreateJobRequest) -> PipelineSpecDict       # simple form -> graph form
    def from_graph(self, spec: PipelineSpec) -> CompileResult
```

Algorithm (pure, no I/O except the two lookups, runs on the event loop):
1. **Normalize the body.** If `pipeline` is present, `reference_set_id`, `primary_image_id`, `source` and
   `settings` must be absent → else `conflicting_fields`. Otherwise convert the simple form into a graph with
   `from_simple` (template + params). `source.type=="image"` and top-level `reference_set_id` together →
   `conflicting_fields`.
2. **Structural validation** → `GraphIssue[]` with codes: `unknown_node_type`, `unknown_port`, `type_mismatch`,
   `missing_required_input`, `port_already_connected`, `multiple_sources`, `no_source`, `cycle` (Kahn leftover),
   `too_many_instances`, `node_locked`, `unsupported_topology` (e.g. `job_result` + `image_to_3d`; two consumers
   of different sources), `invalid_param` (pydantic errors re-located to `nodes[i].params.<field>`), `no_sink`
   (warning; an implicit `export` is added).
3. **Feature checks** → `feature_disabled` (settings master switches), `node_locked` (refine), and per node
   `capability_unavailable` with `details.candidates = [{id, state, reason}]` from the registry (provider "auto"
   resolves via `settings.provider_order[kind]` then catalog order; an explicit id must be usable).
4. **Kind:** `prompt` + `image_to_3d` → `prompt_to_3d`; `input_image` + `image_to_3d` → `image_to_3d`;
   `job_result` without `image_to_3d` → `part_decomposition`.
5. **Expansion** to steps (§3.3), port wiring `inputs = {"images": "source_image.images", ...}`, weights from
   `STEP_TYPES` (provider `weight_hint` overrides), method resolution for `lift_labels` (`auto` → `single_view`
   when masks connected, else `components` and no `lift_labels` step), conditioning source for `preprocess`
   (`alpha_crop`/`untouched` torch-free when the primary image has alpha or `remove_background=false`; otherwise
   engine `prepare_conditioning`; if neither is possible → `conditioning_unavailable` issue).
6. **Canonicalization:** if the plan's step types are exactly `[source_image, reconstruct]` (+ an `export` with
   default params and no part input) → `plan = None`, kind `image_to_3d` (byte-identical legacy path).
7. **requires_engine** = any step in {`reconstruct`, `preprocess` with engine conditioning, `lift_labels` with
   `voxel`}.

Errors raised by `compile_request` (all HTTP 422, standard envelope): `invalid_pipeline` (`details.issues`),
`conflicting_fields`, `feature_disabled`, `step_locked`, `capability_unavailable`, `conditioning_unavailable`,
plus the existing settings 422 (`unsupported_by_engine`, `validation_error`).

### 5.3 `pipelines/plan/router.py` and the `GenerationPipeline` addition

```python
class GenerationPipeline(ABC):                          # pipelines/base.py — additive
    @abstractmethod
    def run(self, job: Job, reporter: ProgressReporter, cancel: CancellationToken) -> JobResultData: ...
    def requires_engine(self, job: Job) -> bool:        # NEW, default keeps today's behaviour
        return True

class PipelineRouter(GenerationPipeline):
    def __init__(self, image_to_3d: ImageTo3DPipeline, runner: PlanRunner) -> None
    def run(self, job, reporter, cancel):
        if job.plan is None:
            return self._image_to_3d.run(job, reporter, cancel)     # legacy: identical call
        return self._runner.run(job, reporter, cancel)
    def requires_engine(self, job): return job.plan is None or job.plan.requires_engine
```

`GenerationWorker._process` change (only one branch): `engine_ready = self._engine_usable() or not
self._pipeline.requires_engine(job)`; `_wait_for_engine` is skipped when the plan does not need TRELLIS (derived
part jobs never trigger a 16 GB model load). Everything else in the worker (status transitions, cancel, finish,
error mapping) is unchanged.

### 5.4 `pipelines/plan/runner.py`

```python
class PlanRunner:
    def __init__(self, *, executors: StepExecutorTable, artifacts: ArtifactPaths, providers: ProviderRegistry,
                 residency: GpuResidencyManager, engine: InferenceEngine, image_to_3d: ImageTo3DPipeline,
                 settings: Settings, hooks: StepHooks) -> None
    def run(self, job: Job, reporter: ProgressReporter, cancel: CancellationToken) -> JobResultData
```

Execution contract (GPU thread):
1. `tracker = PlanProgressTracker(reporter, job.plan)` — requires the reporter to implement
   `PlanAwareReporter` (`mutate`, `update_plan`, `stage`, `step`); `JobProgressReporter` does.
2. For each step in order: `cancel.raise_if_cancelled()`; skip if any required input comes from a skipped/failed
   step (`skip_reason = upstream_skipped|upstream_failed`); `tracker.start(step)` (unthrottled publish +
   persist); `ctx = StepContext(...)` with a fresh `work_dir = runtime/temp/plan/<job>/<step_id>/`;
   `executor.run(ctx)`; move `work_dir` → `jobs/<job>/steps/<step_id>/` with `os.replace` (same volume); record
   outputs/duration; `tracker.finish(step, status)`.
3. Exceptions: `JobCancelledError` → step `cancelled`, re-raise. Other exception in a **soft** step → step
   `failed` (+`StepError`), `JobWarning(code=f"{step.type}_failed")`, continue. In a **hard** step → step `failed`,
   re-raise (the worker maps it as today).
4. After `reconstruct`: hold its `JobResultData`. At the end: `result.parts = PartsSummary` if `export` produced
   `parts/manifest.json`; `result.timings["total"]` = whole plan duration (stage keys from the engine unchanged).
   For `part_decomposition` the result is built from the copied `model.glb` (`compute_glb_stats`) and the parent's
   `seed/resolution/effective_resolution/texture_size/polygon_target`.
5. `residency.release_all_aux()` in `finally` (aux models offloaded to CPU on every exit path).

Invariant tests: no step writes `model.glb`/`preview.png` except `reconstruct` (hash check), and `output_dir` passed to
`engine.generate` is only ever the job dir via `ImageTo3DPipeline` (base model never overwritten).

### 5.5 `pipelines/plan/steps/base.py`

```python
@dataclass(slots=True)
class StepContext:
    job: Job                                  # snapshot at step start (read-only)
    step: PlanStep
    inputs: Mapping[str, StepOutput]          # resolved from previous steps (absolute paths via ctx.abs())
    job_dir: Path; work_dir: Path; out_dir: Path
    reporter: StepReporter
    cancel: CancellationToken
    providers: ProviderRegistry
    residency: GpuResidencyManager
    engine: InferenceEngine
    image_to_3d: ImageTo3DPipeline
    settings: Settings
    hooks: StepHooks
    def abs(self, file: ArtifactFile) -> Path   # resolves job-relative paths, rejects anything outside job_dir

@dataclass(slots=True)
class StepResult:
    outputs: list[StepOutput]
    message: str | None = None
    warnings: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    result: JobResultData | None = None        # reconstruct only

class StepExecutor(ABC):
    step_type: ClassVar[StepType]
    @abstractmethod
    def run(self, ctx: StepContext) -> StepResult: ...

class StepHooks(Protocol):                    # implemented loop-side by services (S4 wiring, S6 importer)
    def on_step_finished(self, job_id: str, step: PlanStep) -> None   # must only schedule work (call_soon_threadsafe)
```

`steps/__init__.py` holds a **lazy table** `EXECUTORS: dict[StepType, str]` of dotted paths for every step type; a
missing module (slice not merged yet) makes the catalog report the node `available:false, reason:"not
implemented"`. This keeps step modules in disjoint files owned by different slices.

`steps/reconstruct.py` (owned by S2) — the only reconstruct implementation:
```python
class ReconstructStep(StepExecutor):
    step_type = StepType.RECONSTRUCT
    def run(self, ctx):
        ctx.residency.prepare_for_reconstruction()          # offload aux models; maybe engine.restore
        result = ctx.image_to_3d.run(ctx.job, ctx.reporter.engine_reporter(), ctx.cancel)
        return StepResult(outputs=[StepOutput(port="mesh", type=PortType.MESH, files=[
            ArtifactFile(role="model", path="model.glb", media_type="model/gltf-binary"),
            ArtifactFile(role="preview", path="preview.png", media_type="image/png")])], result=result)
```

### 5.6 Progress: frozen stages + plan percent

`pipelines/progress.py` gains one additive method on `JobProgressReporter`:
```python
def update_plan(self, fn: Callable[[Job], None]) -> None:
    """Apply fn under the lock and publish throttled (droppable) — for in-step plan fraction updates."""
    with self._lock:
        if self._closed: return
        fn(self._job)
    self._emit()
```

`PlanProgressTracker` (`pipelines/plan/tracker.py`):
- `start(step)`: `mutate` → `status=running`, `started_at`, `plan.current_step`, then
  `reporter.stage(step.legacy_stage, message=step.label)` for non-reconstruct steps. Pre-reconstruct steps report
  under `preprocessing` (index 1), post-reconstruct steps under `finalizing` (index 8). The engine's own stage
  calls pass straight through during `reconstruct`. `progress.percent` therefore always equals the contract
  formula (`max(old, new)` keeps it monotonic), `stage_index` never decreases, `stage_count` stays 9.
- `StepReporter.begin(message, total)` → `reporter.stage(legacy_stage, message, total_steps=total)`;
  `advance(i, total, message)` → `reporter.step(i, total, message)` + `update_plan(fraction)`.
- `engine_reporter()` (reconstruct only) wraps the job reporter: forwards `stage/step` unchanged and sets
  `step.progress = compute_percent(stage, step, total) / 100` via `update_plan`.
- `plan.percent = 100 * (Σ_done w + w_current * progress_current) / Σ w` over non-skipped steps, monotonic;
  `plan.eta_seconds = elapsed_since_first_running_step * (100 - p) / p` for `p ≥ 1`.

Worked example (`prompt_to_3d` + parts; weights 6/1/4/100/10/4/6 = 131): reconstruct at legacy 52.3 % →
`plan.percent = (6+1+4 + 100*0.523)/131*100 = 48.3`, while `progress.percent = 52.3`, `progress.stage = "shape"`.

### 5.7 State machines

Plan step:
```
pending ──start──► running ──ok──► done | warning (done with warnings)
   │                  ├──soft error──► failed ──► dependants: skipped(upstream_failed)
   │                  ├──hard error──► failed ──► job failed (remaining steps stay pending)
   │                  └──cancel──────► cancelled ──► job cancelled (remaining stay pending)
   └──provider unavailable at run time (optional/soft) ──► skipped(provider_unavailable)
```
Job status/stage machine: unchanged (`queued → running → completed|failed|cancelled`; startup recovery marks
`interrupted`). A completed job can carry failed soft steps; `JobOut.warnings` explains them.

---------------------------------------------------------------------------------------------------
## 6. Provider registry

### 6.1 Static metadata (`inference/providers/specs.py`, torch-free)

```python
class ProviderKind(StrEnum): t2i, segmentation_2d, lifting, part_extraction,
                             segmentation_3d, completion, part_generation, refine
class ProviderState(StrEnum): disabled, not_approved, missing_dependencies, not_installed, license_required,
                              unsupported, available, loading, ready, error
USABLE_STATES = {available, loading, ready}

@dataclass(frozen=True, slots=True)
class LicenseInfo:
    id: str                    # SPDX-like: "apache-2.0", "mit", "openrail++", "sam-license", "sapiens2-license", "cc-by-nc-4.0"
    name: str; url: str
    commercial: Literal["allowed", "use_restricted", "revenue_capped", "non_commercial", "research_only"]
    gated: bool = False; requires_acceptance: bool = False; notes: str | None = None
    restricted: bool = False   # NC/research: hidden unless IMG3D_PROVIDERS_ALLOW_RESTRICTED=true

@dataclass(frozen=True, slots=True)
class ModelSpec:
    key: str                   # "transformer", "sam2", "gdino", ...
    repo_id: str | None; revision: str | None
    local_dir: str             # relative to provider_model_root, or overridden by IMG3D_PROVIDER_PATHS
    required_files: tuple[str, ...]; size_bytes: int; optional: bool = False

@dataclass(frozen=True, slots=True)
class ProviderSpec:
    id: str; kind: ProviderKind; display_name: str; description: str
    impl: str | None           # "inference.providers.t2i.diffusers_provider:ZImageTurboProvider"; None = unsupported
    builtin: bool              # algorithmic, no new weights -> implicitly approved
    models: tuple[ModelSpec, ...]; python_packages: tuple[str, ...]   # "diffusers==0.39.0"
    license: LicenseInfo; vram_estimate_mb: int; weight_hint: float | None
    uses_reconstruction_engine: bool; capabilities: Mapping[str, Any]
    unsupported_reason: str | None = None
```

### 6.2 Runtime (`inference/providers/base.py`, `registry.py`)

```python
@dataclass(frozen=True, slots=True)
class ProviderStatus:
    id: str; kind: ProviderKind; state: ProviderState; reason: str | None
    approved: bool; installed: bool; loaded: bool; on_gpu: bool
    missing_packages: tuple[str, ...]; missing_files: tuple[str, ...]
    error: str | None; last_used_at: datetime | None

class ModelProvider(ABC):
    spec: ProviderSpec
    def status(self) -> ProviderStatus                  # cheap, thread-safe
    @abstractmethod
    def load(self, on_status: Callable[[ProviderStatus], None] | None = None) -> None  # GPU thread; weights -> CPU RAM
    @abstractmethod
    def unload(self) -> None
    def to_device(self) -> None: ...                    # GPU thread; default no-op
    def offload(self) -> None: ...                      # GPU thread; move to CPU + empty_cache; default no-op
    def vram_estimate_mb(self, params: Mapping[str, Any]) -> int: return self.spec.vram_estimate_mb

class TextToImageProvider(ModelProvider):
    @abstractmethod
    def generate(self, req: TextToImageRequest, reporter: StepReporter, cancel: CancellationToken) -> TextToImageResult
@dataclass(frozen=True, slots=True)
class TextToImageRequest:  prompt: str; negative_prompt: str | None; width: int; height: int
                           steps: int | None; guidance: float | None; seed: int; out_path: Path
@dataclass(frozen=True, slots=True)
class TextToImageResult:   image_path: Path; seed: int; width: int; height: int; steps: int
                           guidance: float | None; metadata: Mapping[str, Any]

class Segmentation2DProvider(ModelProvider):
    @abstractmethod
    def segment(self, req: SegmentationRequest, reporter, cancel) -> SegmentationResult
@dataclass(frozen=True, slots=True)
class SegmentationRequest: image_path: Path              # RGBA conditioning crop (≤1024²)
                           original_path: Path           # full-resolution upload (head pass)
                           crop: CropTransform           # conditioning.json
                           vocabulary: Sequence[VocabularyEntry]   # taxonomy ids + aliases + prompt nouns
                           subject: str; head_pass: bool; min_confidence: float; out_dir: Path
@dataclass(frozen=True, slots=True)
class SegmentationResult:  labels_png: Path              # uint16 label map, conditioning space
                           overlay_png: Path; masks: Mapping[int, Path]; labels: Sequence[LabelRecord]
                           metadata: Mapping[str, Any]

class LabelLifter(ModelProvider):
    @abstractmethod
    def lift(self, req: LiftRequest, reporter, cancel) -> LiftResult
@dataclass(frozen=True, slots=True)
class LiftRequest:  glb_path: Path; conditioning_png: Path; conditioning_json: Path
                    labels_png: Path; segmentation_json: Path; views: int
                    segmenter: Segmentation2DProvider | None   # multi_view re-segments renders
                    smoothing_iterations: int; out_dir: Path
@dataclass(frozen=True, slots=True)
class LiftResult:   face_labels: Path               # uint16 LE, len == faces of model.glb (trimesh process=False order)
                    face_confidence: Path           # float16
                    camera: Mapping[str, Any]       # yaw/pitch/fov/silhouette_iou/accepted
                    metrics: Mapping[str, Any]

# Interfaces only (no implementation in this milestone; catalog entries are `unsupported`):
class PartSegmenter3D(ModelProvider): def segment_mesh(...) -> LiftResult
class AmodalCompleter(ModelProvider): def complete(...) -> ...
class PartGenerator(ModelProvider):   def generate_parts(...) -> ...
```

```python
class ProviderRegistry:                                # implements pipelines/plan/capabilities.py CapabilityView
    def __init__(self, settings: Settings, specs: Sequence[ProviderSpec], *, probe: EnvironmentProbe,
                 on_change: Callable[[], None]) -> None
    def specs(self) -> list[ProviderSpec]
    def statuses(self) -> list[ProviderStatus]         # cached; recomputed by rescan()
    def status(self, provider_id: str) -> ProviderStatus
    def resolve(self, kind: ProviderKind, requested: str = "auto") -> Resolution   # chosen id | None + candidates
    def get(self, provider_id: str) -> ModelProvider   # lazy import; UnavailableProvider on ImportError
    def rescan(self) -> list[ProviderStatus]           # re-probe packages/weights (cheap, no imports of torch)
    def loaded(self) -> list[ModelProvider]

class EnvironmentProbe(Protocol):
    def package_version(self, name: str) -> str | None   # importlib.metadata, never imports the package
    def files_present(self, root: Path, files: Sequence[str]) -> tuple[str, ...]   # returns missing
    def hf_token_present(self) -> bool
```

State resolution order (first match wins): feature switch off → `disabled`; `impl is None` → `unsupported`;
restricted licence and not allowed → `disabled` (reason "restricted licence"); not builtin and id not in
`providers_approved` → `not_approved`; missing package → `missing_dependencies`; missing required file →
`not_installed`; `requires_acceptance` and no record in `runtime/providers/license_acceptances.json` →
`license_required`; else the live state (`available`/`loading`/`ready`/`error`). Every non-usable state carries a
human-readable `reason`, e.g. `"Not approved: add 'gdino_sam21' to IMG3D_PROVIDERS_APPROVED"`,
`"Missing packages: diffusers==0.39.0, accelerate==1.15.0"`,
`"Weights not found in backend/.model/providers/z_image_turbo (transformer/…)"`.

### 6.3 Residency (`inference/providers/residency.py`, torch-free)

```python
class GpuResidencyManager:
    def __init__(self, engine: InferenceEngine, *, device_memory: Callable[[], tuple[int, int] | None],
                 margin_mb: int) -> None          # device_memory() -> (free_mb, total_mb), via inference/device.py
    def prepare_for(self, provider: ModelProvider, params: Mapping[str, Any]) -> None   # GPU thread only
    def prepare_for_reconstruction(self) -> None
    def done_with(self, provider: ModelProvider) -> None      # offload unless the next step uses it
    def release_all_aux(self) -> None
```

Policy (GPU thread only):
1. `prepare_for(p)`: offload every other loaded aux provider that is `on_gpu`; ensure `p` is loaded (lazy `load()`
   reports "Loading <model>" through the step reporter); if `free < p.vram_estimate_mb + margin` call
   `engine.release_vram()` (additive optional `InferenceEngine` method, default returns `False`; TRELLIS
   implements it in S9 by moving its modules to CPU when `low_vram=False`); if still short → raise
   `InsufficientVramError` (job error `insufficient_vram`). Then `p.to_device()`.
2. `done_with(p)`: `p.offload()` (CPU) + `empty_cache` inside the provider, unless the next step declared it
   (e.g. the segmentation provider reused by `multi_view` lifting).
3. `prepare_for_reconstruction()`: offload all aux providers. With the default `low_vram=True`, TRELLIS already
   holds no VRAM between runs (`engine.py:312-326`), so steps never contend.
4. Idle unload: `ProviderIdleReaper` (loop task, every 60 s) submits `provider.unload()` through
   `GenerationWorker.submit_maintenance(fn) -> asyncio.Future` for providers idle longer than
   `provider_idle_unload_s`; maintenance queues behind a running job on the single executor, so it never overlaps.
5. Shutdown: lifecycle submits `registry.unload_all()` to the GPU executor before the worker stops.
6. RNG: providers use private `torch.Generator`s; nothing runs inside `engine.generate`, and TRELLIS re-seeds at
   conditioning (`generation.py:202`), so same-seed reconstruction is unaffected by preceding steps.

GPU timeline for a `prompt_to_3d` + parts job (one `gpu-worker` thread):
```
[t2i: load→GPU→sample→offload] [preprocess: BiRefNet via engine.prepare_conditioning] [seg: load→GPU→run→offload]
[reconstruct: engine.generate (TRELLIS low-VRAM, unchanged)] [lift: nvdiffrast face-ID raster] [parts/export: CPU]
```

### 6.4 Provider catalog (`inference/providers/catalog.py`) — candidates, none approved by default

| id | kind | builtin | impl module (slice) | licence (commercial) | weights / packages | default state on this machine |
|---|---|---|---|---|---|---|
| `z_image_turbo` | t2i | no | `t2i/diffusers_provider.py:ZImageTurboProvider` (S6) | Apache-2.0 (allowed) | `Tongyi-MAI/Z-Image-Turbo@f332072a…` 32.8 GB; diffusers 0.39.0, accelerate 1.15.0 | `not_approved` |
| `flux2_klein_4b` | t2i | no | `t2i/diffusers_provider.py:Flux2KleinProvider` (S6) | Apache-2.0 (allowed) | `black-forest-labs/FLUX.2-klein-4B@e7b7dc27…` 16.0 GB (diffusers sub-folders only) | `not_approved` |
| `animagine_xl_4` | t2i | no | `t2i/diffusers_provider.py:SdxlSingleFileProvider` (S6) | OpenRAIL++-M (use_restricted) | single file; path via `IMG3D_PROVIDER_PATHS={"animagine_xl_4": "D:/10-SEP-2026/T2I/.models/animagine-xl-4.0/animagine-xl-4.0.safetensors"}`; diffusers 0.39.0 | `not_approved` (file exists outside the project) |
| `gdino_sam21` | segmentation_2d | no | `segmentation/gdino_sam2.py:GroundedSam2Provider` (S7) | Apache-2.0 (+ Florence-2 MIT, optional) | `IDEA-Research/grounding-dino-base` 0.93 GB + `facebook/sam2.1-hiera-large` 0.90 GB (+ optional `florence-community/Florence-2-large` 1.55 GB for prompt-noun grounding); no new packages | `not_approved` |
| `seethrough_l2d` | segmentation_2d | no | `segmentation/seethrough.py:SeeThroughParserProvider` (S7; vendored `SemanticSam` modules under `third_party/`) | Apache-2.0 | `24yearsold/l2d_sam_iter2` 1.29 GB (+ SAM 2.1 refine weights from `gdino_sam21` if present) | `not_approved` |
| `sam3` | segmentation_2d | no | `segmentation/sam3_vendored.py:Sam3Provider` (S7; vendored repo, needs `ftfy`, `iopath` with `--no-deps`) | SAM Licence (use_restricted, gated, requires_acceptance) | `facebook/sam3` 3.44 GB; HF token | `not_approved` |
| `sapiens2_seg` | segmentation_2d | no | `segmentation/sapiens2.py:Sapiens2Provider` (S7; vendored) | Sapiens2 Licence (use_restricted) | `sapiens2-seg-0.4b` 1.63 GB | `not_approved` |
| `face_parsing_nc` | segmentation_2d | no | `segmentation/face_parsing.py` (S7, optional) | NVIDIA SegFormer + CelebAMask-HQ (non_commercial, restricted) | 0.34 GB | `disabled` (restricted) |
| `single_view` | lifting | yes | `lifting/single_view.py:SingleViewLifter` (S8) | own code; inherits nvdiffrast NC licence like export | none | `available` once S8 lands |
| `multi_view` | lifting | yes | `lifting/multi_view.py:MultiViewLifter` (S8) | as above | needs a usable `segmentation_2d` provider | `available` if a segmenter is usable, else `not_installed`-style reason |
| `voxel_lift` | lifting | yes | `lifting/voxel.py:VoxelLifter` (later; needs S9 latents capture) | MIT code path (cumesh BVH) | needs `capture_latents=true` jobs | `unsupported` until implemented |
| `shared_atlas` | part_extraction | yes | `parts/` via `steps/part_extraction.py` (S5) | own | none | `available` |
| `geometry_components` | part_extraction | yes | `parts/components.py` (S5) | own | none | `available` |
| `p3sam`, `xpart`, `partfield`, `geosam2`, `holopart`, `omnipart` | segmentation_3d / completion / part_generation | no | `None` | per research table | spconv / torch_scatter / Linux sidecar | `unsupported` (reason names the blocking dependency) |

Approval is the user's: `IMG3D_PROVIDERS_APPROVED=gdino_sam21,z_image_turbo` (example). Installing weights is done
by an explicit CLI that prints the licence and requires `--accept-license` (§8.6); the app never downloads model
weights on its own in this milestone.

---------------------------------------------------------------------------------------------------
## 7. Step executors in detail

### 7.1 `text_to_image` (S6)
- Resolves `PromptParams`; renders the template from `pipelines/plan/prompting.py` (character/object presets,
  style clauses and the part-aware hint from the t2i research §8). Distilled models (Z-Image-Turbo, klein) get
  positive-only prompts; `negative_prompt` is rejected at compile time (`invalid_param`) when the provider's
  `capabilities.supports_negative_prompt` is false.
- Size defaults: character 896×1152, object 1024×1024 (multiples of 16, checked against provider caps).
- Calls `TextToImageProvider.generate` with a private generator seeded with `source.image_seed`; diffusers
  `callback_on_step_end` → `reporter.advance(i+1, steps)`; cancel sets `pipe._interrupt = True` then raises
  `JobCancelledError`.
- Writes `source/generated.png`, then `inputs/<reserved img id>.png` through `processing/image_ops.normalize_to_png`
  (the same function job creation uses), and `source/t2i.json` (provenance). Updates `Job.source.provenance` via
  `mutate`.
- 3D-readiness check (cheap, CPU, on the generated image after `preprocess` alpha exists; otherwise on luminance
  heuristics): bbox touching the border, more than one large component, foreground area outside 8–70 %. Results
  become `JobWarning`s (`t2i_cropped_subject`, `t2i_multiple_subjects`, `t2i_subject_size`), never silent
  regeneration.
- `on_step_finished` hook → `PromptImageImporter` (loop) imports the image into the job's prompt reference set with
  the reserved id (`ReferenceService.import_image`), so the set shows the generated image and a legacy retry works.

### 7.2 `preprocess` (S9)
Produces the exact square crop TRELLIS conditions on, with alpha and the crop transform that upstream discards:
- Image has a real alpha (or `remove_background=false`): torch-free `processing/conditioning.py`
  (`prepare_conditioning_from_alpha(path) -> PreparedConditioning`) re-implements the ≤1024 downscale, the
  `alpha > 0.8·255` bbox, the zero-margin square crop and premultiply; `untouched` mode returns the full image with
  an identity transform.
- Otherwise: `engine.prepare_conditioning(path, remove_background=True, out_dir) -> PreparedConditioning` (new
  optional `InferenceEngine` method, default raises `NotImplementedError`). The TRELLIS adapter implements it with
  its loaded BiRefNet and the same crop math, without touching `TrellisGenerationRun._preprocess` (the parity test
  keeps guarding the generate path).
- `conditioning.json`: `{"schema":"img3d.conditioning/1","mode":"alpha_crop|background_removed|untouched",
  "source_size":[w,h],"scale":0.66,"crop_box_src":[x0,y0,x1,y1],"size":[s,s],"alpha_threshold":0.8,
  "premultiplied":true}`.

### 7.3 `segmentation_2d` (S7)
- Vocabulary = taxonomy aliases for every class reachable from `separate`/`regions` + `vocabulary_extra` + prompt
  nouns (prompt jobs; recorded as `expected`).
- Provider output is mapped onto the taxonomy (§10.1) by `segmentation/fusion.py` (numpy only: z-priority argmax,
  minimum area/confidence gates → `unknown`, left/right from geometry, eye sub-split and eyelash band heuristics
  from the segmentation research §5).
- Files: `labels.png` (uint16), `overlay.png`, `masks/<value>.png`, `segmentation.json`:
```json
{"schema":"img3d.seg2d/1","space":"conditioning","size":[1024,1024],"subject":"anime_character",
 "provider":{"id":"gdino_sam21","models":[{"key":"gdino","repo_id":"IDEA-Research/grounding-dino-base","revision":"…"}]},
 "labels":[{"value":3,"taxonomy_id":"hair","score":0.81,"area_px":123456,"bbox":[120,40,880,610],
            "side":null,"source":"gdino_sam21","mask":"masks/3.png"}],
 "derived":[{"taxonomy_id":"eyelash.l","method":"heuristic","lid_curve":[[412,388],[430,380]],"confidence":0.55}],
 "expected":["sword"],"missing_expected":[],"warnings":[]}
```

### 7.4 `lift_labels` (S8)
- `single_view`: estimate the input camera against `model.glb` (front = +Z in glTF, image-right = +X; grid search
  yaw/pitch/FOV seeded at front, silhouette IoU after applying the same tight square crop as `conditioning.json`;
  accept when IoU ≥ 0.6, else soft failure `camera_fit_rejected`), render a face-ID buffer with nvdiffrast
  (handle IDs above 2²⁴ with the bit-decode rule), vote per face, propagate over the welded face-adjacency graph
  (scipy sparse, colour/normal affinity, `smoothing_iterations`), 1-ring majority filter, per-label small-component
  cleanup. Camera helpers re-implement `extrinsics_look_at`/`intrinsics_from_fov_xy` (no `utils3d`).
- `multi_view`: additionally renders `views` textured views (baked base colour via `dr.texture`), runs the same
  segmentation provider on each (residency keeps it on the GPU across views), weights votes by
  visibility × |cos| × score, input view weighted highest.
- Output `face_labels.u16` aligned with the faces of `model.glb` as loaded by `trimesh.load(..., process=False)`
  (single mesh, primitive order), `face_confidence.f16`, `lift.json` `{camera, coverage, label_histogram, method}`.
- `voxel`: later (needs S9 latents capture); catalog `unsupported` until then.

### 7.5 `part_extraction` (S5, torch-free)
- Inputs: `model.glb`, optional `face_labels.u16` (else `geometry_components`: connected components on the welded
  mesh → `component_<n>` parts, taxonomy `unknown`, kind `physical`, which is honest geometry-only splitting).
- Policy (`parts/policy.py`): for each taxonomy class: in `separate` group → its default kind (`physical` or
  `physical_deferred`); not in `separate` → `region`; region groups not in `regions` → merged into `body`.
- Partition: weld by position (`weld_map.u32`), split physical labels into face sets, chain boundary edges into
  ordered seam loops (`seams/<sm>.u32` weld ids), compute bbox/area/adjacency/contact length, relations
  (`attached_to` from taxonomy + contact, `symmetric_with` pairs, `layered_over` for clothing, `held_by` for
  objects), `occluded_geometry_missing` for cuts that leave a hole in the parent. No hole filling, remeshing,
  smoothing or re-baking (that would be refinement).
- Writes `partition.npz` (`face_part: int32[F]`, `weld_map`, seam index) and `manifest.draft.json`.

### 7.6 `export` (S5, torch-free)
- With a part set: `parts/model_parts.glb` = one `trimesh.Scene`: root node `asset` → node `body` (all region faces,
  `_PART_ID` vertex attribute with vertices duplicated only where a vertex borders two regions, plus
  `EXT_mesh_features`-style `extras`) → one child node per physical part named `part_<taxonomy_id with . → _>`
  (e.g. `part_eye_l`), each `TextureVisuals.face_subset` of the original mesh referencing the **same**
  `PBRMaterial` object (trimesh dedupes it by hash → one atlas in the file). Node `extras.img3d =
  {part_id, taxonomy_id, kind, parent, seam_ids}`; scene `extras.img3d = {schema, job_id}`. Identity transforms
  (shared unit-cube frame) → lossless reassembly.
- Optional `parts/<pt>/model.glb` (off by default: each embeds the full atlas), `parts/face_labels.u16`,
  `parts/masks/<pt>.png` (copied from segmentation, keyed by part id), final `parts/manifest.json`.
- Written into `runtime/temp/plan/<job>/export/parts/` then moved to `jobs/<job>/parts/` with `os.replace`.
- Without a part set: no-op `done` (the base `model.glb` is the export).

---------------------------------------------------------------------------------------------------
## 8. API contract additions (prefix `/api/v1`; JSON snake_case; standard error envelope)

### 8.1 Endpoint table

| Method & path | Auth | Request | Success | Errors |
|---|---|---|---|---|
| `GET /features` | Bearer | – | 200 `FeaturesOut` | 401 |
| `GET /pipeline/catalog` | Bearer | `If-None-Match` optional | 200 `CatalogOut` + `ETag`; 304 when unchanged | 401 |
| `POST /pipeline/validate` | Bearer | same body as `POST /jobs` | 200 `ValidationReport` (graph problems are reported, not raised) | 401, 422 only for a malformed body |
| `POST /jobs` (extended) | Bearer | `CreateJobRequest` (§8.2) | 202 `JobOut` | 404 set/image/parent job (`not_found`), 409 parent not completed (`conflict`), 422 `validation_error` / `unsupported_by_engine` / `invalid_pipeline` / `conflicting_fields` / `feature_disabled` / `step_locked` / `capability_unavailable` / `conditioning_unavailable`, 429 `queue_full`, 503 queue closed |
| `GET /jobs?kind=&status=&limit=` | Bearer | `kind ∈ image_to_3d\|prompt_to_3d\|part_decomposition` | 200 `{items}` | 422 bad `kind` |
| `POST /jobs/{job_id}/parts/rebuild` | Bearer | `RebuildPartsRequest {parts: PartOptions, reuse_segmentation: bool = true}` | 202 `JobOut` (new `part_decomposition` job) | 404, 409 (parent not completed), 422, 429 |
| `GET /jobs/{job_id}/parts/manifest` | File | – | 200 `application/json` (manifest §10.3) | 404 job, 409 not completed, 404 `parts_not_available` |
| `GET /jobs/{job_id}/parts/model.glb` | File | – | 200 `model/gltf-binary` inline | 404, 409, 404 `parts_not_available` |
| `GET /jobs/{job_id}/export/parts-glb` | File | – | 200 attachment `img3d_<job>_parts.glb` | as above |
| `GET /jobs/{job_id}/parts/{part_id}/model.glb` | File | `part_id` must match `^pt_[0-9a-f]{12}$` | 200 | 422 bad id, 404 unknown part or file not exported, 409 |
| `GET /jobs/{job_id}/parts/{part_id}/mask` | File | | 200 `image/png` | 422, 404, 409 |
| `GET /jobs/{job_id}/parts/face-labels` | File | | 200 `application/octet-stream` (uint16 LE, header `X-Img3d-Face-Count`) | 404, 409 |
| `GET /jobs/{job_id}/steps/{step_id}/files/{name}` | File | `step_id ^[a-z][a-z0-9_]{0,31}$`; `name ^[a-z0-9_]+(/[a-z0-9_]+)?\.(png\|json\|glb\|u16\|f16\|u32\|npz)$` | 200 (available as soon as the step is `done`, also while the job runs) | 422 bad names, 404 |
| `GET /jobs/{job_id}/source/image` | File | – | 200 `image/png` (the generated image) | 404 (not a prompt job / not generated yet) |
| `GET /providers` | Bearer | – | 200 `{items: ProviderOut[]}` | 401 |
| `GET /providers/{provider_id}` | Bearer | – | 200 `ProviderOut` | 404 `provider_not_found` |
| `POST /providers/rescan` | Bearer | – | 200 `{items: ProviderOut[]}` (re-probes packages/weights, publishes `features.updated`) | 401 |
| `POST /providers/{provider_id}/unload` | Bearer | – | 202 `ProviderOut` (queued on the GPU thread) | 404, 409 when the provider is in use by the running job |

Route collisions: none (`/jobs/{id}/tunings`, `/jobs/{id}/model.glb|export/glb|preview` stay as they are). Every id
is validated with `is_valid_id` before any filesystem access; file names are whitelisted by regex; served paths are
resolved and checked to stay inside the job dir. All file routes use `FileAuthGuard` (`?token=` allowed).

### 8.2 `CreateJobRequest` (extended, `schemas/jobs.py`)

```json
{
  "reference_set_id": "rs_…",            // optional now; required unless "source" or "pipeline" is given
  "primary_image_id": null,
  "settings": { …GenerationSettings…, "parts": PartOptions | null },     // "parts" optional
  "source": null
         | {"type":"image","reference_set_id":"rs_…","primary_image_id":null}
         | {"type":"prompt", …PromptParams…},
  "pipeline": null | PipelineSpec
}
```
Validator rules (model-level, 422 `validation_error` / `conflicting_fields`):
- exactly one input form: top-level `reference_set_id`, or `source`, or `pipeline`;
- `pipeline` excludes `reference_set_id`, `primary_image_id`, `source`, `settings`;
- `source.type == "image"` excludes top-level `reference_set_id`/`primary_image_id`.
Every legacy body (`reference_set_id` + `settings`) parses to the same values as today; a legacy body missing
`reference_set_id` is still 422.

`PipelineSpec` (`schemas/pipeline.py`):
```json
{"schema":"img3d.pipeline","version":1,
 "nodes":[{"id":"n_input","type":"input_image","params":{"reference_set_id":"rs_…","primary_image_id":null}},
          {"id":"n_seg","type":"segmentation","params":{"provider":"auto","subject":"auto"}},
          {"id":"n_recon","type":"image_to_3d","params":{"resolution":1536,"texture_size":2048,"polygon_target":1000000}},
          {"id":"n_parts","type":"part_extraction","params":{"separate":["eyes","hair","clothes"],"method":"auto"}},
          {"id":"n_export","type":"export","params":{"assembled_glb":true}}],
 "edges":[{"from":{"node":"n_input","port":"images"},"to":{"node":"n_seg","port":"images"}},
          {"from":{"node":"n_input","port":"images"},"to":{"node":"n_recon","port":"images"}},
          {"from":{"node":"n_recon","port":"mesh"},"to":{"node":"n_parts","port":"mesh"}},
          {"from":{"node":"n_seg","port":"masks"},"to":{"node":"n_parts","port":"masks"}},
          {"from":{"node":"n_parts","port":"parts"},"to":{"node":"n_export","port":"asset"}}]}
```
Limits: node id `^[a-z][a-z0-9_]{0,31}$`, ≤ 32 nodes, ≤ 64 edges, params validated per node type. Layout (positions,
colours, viewport) is **not** part of the spec; it stays client-side.

### 8.3 `JobOut` additions (additive keys; existing keys and semantics unchanged)

```json
{ …existing JobOut…,
  "kind": "prompt_to_3d",
  "parent_job_id": null,
  "source": {"type":"prompt","prompt":"a knight with a cape and a sword","preset":"character","style":"stylized",
             "provider":"z_image_turbo","image_seed":918273,"generated_image_id":"img_…",
             "provenance":{"provider":"z_image_turbo","repo_id":"Tongyi-MAI/Z-Image-Turbo","revision":"f332072a…",
                           "license_id":"apache-2.0","rendered_prompt":"A full-body 3D character reference image of …",
                           "image_seed":918273,"width":896,"height":1152,"steps":9,"guidance":0.0,"readiness":{…}},
             "image_url":"/api/v1/jobs/job_…/source/image"},
  "plan": {
    "schema":"img3d.plan/1","kind":"prompt_to_3d","current_step":"reconstruct","percent":48.3,"eta_seconds":212.0,
    "requires_engine":true,
    "spec": PipelineSpec,
    "steps":[
      {"id":"text_to_image","type":"text_to_image","node_id":"n_prompt","label":"Generating image",
       "provider":"z_image_turbo","legacy_stage":"preprocessing","weight":6,"failure_policy":"hard",
       "status":"done","progress":1.0,"message":"Generated 896×1152 image","started_at":"…Z","finished_at":"…Z",
       "duration_s":7.4,"warnings":[],"error":null,"skip_reason":null,
       "artifacts":[{"role":"image","name":"generated.png","media_type":"image/png","size_bytes":812345,
                     "url":"/api/v1/jobs/job_…/source/image"}]},
      {"id":"segmentation_2d","type":"segmentation_2d","node_id":"n_seg","status":"done", …,
       "artifacts":[{"role":"overlay","name":"overlay.png","media_type":"image/png",
                     "url":"/api/v1/jobs/job_…/steps/segmentation_2d/files/overlay.png"}]},
      {"id":"reconstruct","type":"reconstruct","node_id":"n_recon","status":"running","progress":0.523, …}
    ]},
  "warnings":[{"code":"t2i_subject_size","message":"The subject fills 74% of the frame","step_id":"text_to_image"}],
  "result": { …existing JobResultOut…, "parts": PartsResultOut | null }
}
```
`JobOut.plan` is `null` for legacy jobs (the Node UI uses `catalog.legacy_job_node_map`). `kind` is always present.

`PartsResultOut`:
```json
{"schema":"img3d.parts/1",
 "manifest_url":"/api/v1/jobs/job_…/parts/manifest",
 "model_url":"/api/v1/jobs/job_…/parts/model.glb",
 "download_url":"/api/v1/jobs/job_…/export/parts-glb",
 "face_labels_url":"/api/v1/jobs/job_…/parts/face-labels",
 "segmentation":{"overlay_url":"/api/v1/jobs/job_…/steps/segmentation_2d/files/overlay.png"} ,
 "counts":{"physical":6,"physical_deferred":4,"region":9,"unknown":1},
 "items":[{"id":"pt_3f9a…","taxonomy_id":"hair","label":"Hair","category":"hair","kind":"physical",
           "parent":"pt_0b12…","node_name":"part_hair","side":null,"faces":210334,"confidence":0.81,
           "source":"lifted","model_url":null,"mask_url":"/api/v1/jobs/job_…/parts/pt_3f9a…/mask"}],
 "warnings":[]}
```

### 8.4 `FeaturesOut` (`GET /features`; also the payload of `features.updated`)

```json
{"schema":"img3d.features/1",
 "features":{
   "image_to_3d":    {"enabled":true, "available":true, "reason":null},
   "prompt_to_3d":   {"enabled":true, "available":false, "reason":"No text-to-image provider is approved and installed",
                      "providers":["z_image_turbo","flux2_klein_4b","animagine_xl_4"]},
   "segmentation_2d":{"enabled":true, "available":false, "reason":"No 2D segmentation provider is approved and installed",
                      "providers":["gdino_sam21","seethrough_l2d","sam3","sapiens2_seg"]},
   "part_extraction":{"enabled":true, "available":true, "reason":null, "methods":["components"]},
   "part_rebuild":   {"enabled":true, "available":true, "reason":null},
   "node_pipelines": {"enabled":true, "available":true, "reason":null, "spec_version":1},
   "mesh_tuning":    {"enabled":false,"available":false,"reason":"Auto Mesh Tuner is paused until approved"},
   "part_refinement":{"enabled":false,"available":false,"reason":"Part refinement is paused until approved"}},
 "providers":[{"id":"gdino_sam21","kind":"segmentation_2d","state":"not_approved","reason":"…"}],
 "taxonomy_version":"img3d.taxonomy/1",
 "catalog_etag":"sha256-…"}
```
`available` = enabled and at least one usable provider (or builtin). `methods` lists usable extraction methods
(`components` always; `single_view`/`multi_view` when a segmenter and the lifter are usable). `/system` is not
changed; clients call `/features` after every WS `hello` and on `features.updated`.

### 8.5 `CatalogOut` (`GET /pipeline/catalog`) and `ProviderOut`

```json
{"schema":"img3d.catalog/1","etag":"sha256-…",
 "port_types":[{"id":"image_set","label":"Image"},{"id":"segmentation","label":"Masks"},
               {"id":"mesh","label":"Mesh"},{"id":"part_set","label":"Parts"}],
 "node_types":[{"type":"segmentation","version":1,"category":"analyze","title":"Segmentation",
                "inputs":[{"id":"images","label":"Image","accepts":["image_set"],"required":true,"multiple":false}],
                "outputs":[{"id":"masks","label":"Masks","type":"segmentation"}],
                "params_schema":{…pydantic model_json_schema() of SegmentationParams…},
                "defaults":{"provider":"auto","subject":"auto","vocabulary_extra":[],"head_pass":true,"min_confidence":0.35},
                "provider_kind":"segmentation_2d","expands_to":["preprocess","segmentation_2d"],
                "max_instances":1,"requires":["segmentation_2d"],"available":false,"locked":false,
                "reason":"No 2D segmentation provider is approved and installed"}],
 "step_types":[{"type":"segmentation_2d","label":"Segmenting parts","legacy_stage":"preprocessing","weight":4,
                "failure_policy":"soft","provider_kind":"segmentation_2d","implemented":true}],
 "providers":[ProviderOut…],
 "templates":[{"id":"image_to_3d","title":"Image to 3D","spec":PipelineSpec},
              {"id":"image_to_3d_parts","title":"Image to 3D with parts","spec":PipelineSpec},
              {"id":"prompt_to_3d","title":"Prompt to 3D","spec":PipelineSpec},
              {"id":"prompt_to_3d_parts","title":"Prompt to 3D with parts","spec":PipelineSpec},
              {"id":"rebuild_parts","title":"Rebuild parts of a job","spec":PipelineSpec}],
 "legacy_job_node_map":{"preprocessing":"input_image","conditioning":"image_to_3d", "…":"…","finalizing":"export"},
 "taxonomy":{"version":"img3d.taxonomy/1",
             "groups":[{"id":"eyes","label":"Eyes","kind":"physical","members":["eye.l","eye.r"]},
                       {"id":"eyebrows","label":"Eyebrows","kind":"physical_deferred","members":["eyebrow.l","eyebrow.r"]},
                       {"id":"face","label":"Face","kind":"region","members":["body.head.face","body.head.nose","…"]}],
             "classes":[{"id":"eye.l","label":"Left eye","group":"eyes","kind":"physical","parent":"body.head",
                         "attach_to":"body.head.eye_socket.l","symmetric_with":"eye.r"}]},
 "limits":{"max_nodes":32,"max_edges":64,"max_prompt_chars":2000,"max_views":24}}
```
`ProviderOut`:
```json
{"id":"z_image_turbo","kind":"t2i","display_name":"Z-Image-Turbo","description":"…","builtin":false,
 "state":"not_approved","reason":"Not approved: add 'z_image_turbo' to IMG3D_PROVIDERS_APPROVED",
 "approved":false,"installed":false,"loaded":false,"on_gpu":false,
 "missing_packages":["diffusers==0.39.0","accelerate==1.15.0"],"missing_files":["transformer/…"],
 "models":[{"key":"pipeline","repo_id":"Tongyi-MAI/Z-Image-Turbo","revision":"f332072aa78be7aecdf3ee76d5c247082da564a6",
            "size_bytes":32800000000,"optional":false}],
 "license":{"id":"apache-2.0","name":"Apache License 2.0","url":"https://huggingface.co/Tongyi-MAI/Z-Image-Turbo",
            "commercial":"allowed","gated":false,"requires_acceptance":false,"restricted":false,"notes":null},
 "vram_estimate_mb":15000,"uses_reconstruction_engine":false,
 "capabilities":{"supports_negative_prompt":false,"default_steps":9,"sizes":[[1024,1024],[896,1152]]},
 "error":null}
```

### 8.6 Model installation (no HTTP download in this milestone)
`backend/scripts/install_provider.py --provider <id> --accept-license [--revision <sha>]` (embedded Python, `-s`):
prints licence/size, refuses restricted licences unless `--allow-restricted`, refuses non-approved ids, downloads
with `snapshot_download(allow_patterns=…)` into `backend/.model/providers/<id>/`, records the acceptance in
`runtime/providers/license_acceptances.json`, then the user calls `POST /providers/rescan`. Package installs use
`backend/constraints.txt` (`uv pip install -c constraints.txt …`). Both only after the user approves (open
question Q1–Q3).

### 8.7 WebSocket
- `job.updated` (existing, job-scoped): `JobOut` now carries `kind`, `source`, `plan`, `warnings`. Step status
  transitions are unthrottled; in-step plan fraction updates are throttled with the existing ≤5/s progress path.
  No new job-scoped event type.
- `features.updated` (new, **not** job-scoped, never dropped, debounced 100 ms): `data = FeaturesOut`. Sent on
  provider state changes (load/unload/error), rescan and settings-driven changes. Existing clients ignore unknown
  types (`ws.ts` default branch).
- `hello` unchanged.

### 8.8 New error codes
| code | HTTP | meaning |
|---|---|---|
| `invalid_pipeline` | 422 | graph has errors; `details.issues = GraphIssue[]` |
| `conflicting_fields` | 422 | more than one input form, or `pipeline` with `settings`/`source` |
| `feature_disabled` | 422 | a master switch is off (`parts_enabled`, `prompt_to_3d_enabled`, `node_pipelines_enabled`) |
| `step_locked` | 422 | refine/tune node present (paused until approval) |
| `capability_unavailable` | 422 | no usable provider; `details = {provider_kind, requested, candidates:[{id,state,reason}]}` |
| `conditioning_unavailable` | 422 | segmentation requested but neither alpha nor engine conditioning is available |
| `parts_not_available` | 404 | completed job has no parts |
| `provider_not_found` | 404 | unknown provider id |
| runtime job/step codes | – | `provider_unavailable`, `provider_load_failed`, `provider_error`, `insufficient_vram`, `t2i_failed`, `segmentation_failed`, `camera_fit_rejected`, `lifting_failed`, `part_extraction_failed`, `export_failed` (plus existing `out_of_memory`, `engine_error`, `interrupted`) |

`GraphIssue` = `{severity: "error"|"warning", code, message, node_id, port, edge_index, details}`.

---------------------------------------------------------------------------------------------------
## 9. Tuning / refinement gate (backend side)

| Layer | Setting / mechanism | Default | Effect |
|---|---|---|---|
| Exposure | `feature_mesh_tuning` (`IMG3D_FEATURE_MESH_TUNING`) | `false` | `/features.mesh_tuning.enabled = feature_mesh_tuning and blender_enabled`; all four UI modes hide every tuner mount point from this one flag; the frontend makes no `/blender` or `/tunings` calls when it is off. |
| Hard runtime off (existing switch) | `IMG3D_BLENDER_ENABLED=false` in the user-local `backend/.env` | not set (true) | Every mutating tuner endpoint returns 503 `blender_unavailable`, no Blender detection or process; tests unaffected (they ignore `.env`). Requires the user's go-ahead because it edits their `.env`. |
| Graph | refine node types (`part_refine`, `mesh_tune`, `eye_correction`, `rig_correction`) | locked | Compiler raises `step_locked`; catalog shows them `locked:true` with the reason. |
| Part consumers | `part_refinement_enabled` (`IMG3D_PART_REFINEMENT_ENABLED`) | `false` | `pipelines/plan/consumers.py`: `PartConsumer` protocol + `PartConsumerRegistry.register()` raises `FeatureDisabledError` while false; **zero implementations**; no executor module exists for `part_refine`/`tune`. |
| Isolation | import rule | – | `pipelines/plan/**`, `parts/**` never import `services.mesh_tuner_service`, `workers.tuning_worker`, `launcher`, `blender_scripts`. |

What future refinement can consume (interface only):
```python
class PartConsumerRequest(Protocol):
    job_id: str; manifest_path: Path; parts_glb: Path; part_ids: Sequence[str]
    seams_dir: Path; face_labels: Path; out_dir: Path
class PartConsumer(Protocol):
    id: str
    kind: Literal["refine", "tune", "complete"]
    def accepts(self, part: Mapping[str, Any]) -> bool
    def run(self, request: PartConsumerRequest, reporter: StepReporter, cancel: CancellationToken) -> Mapping[str, Any]
```
The manifest's per-part `refinement: {"eligible": true, "status": "untouched", "locked": true}` and seam weld ids
are what such a consumer would need; nothing reads them yet.

---------------------------------------------------------------------------------------------------
## 10. Part-aware asset system

### 10.1 Taxonomy and default policy (`parts/taxonomy.py`, `parts/policy.py`)

Ids follow the segmentation research §7 taxonomy. Groups map the user's list to classes:

| Group (`separate`/`regions`) | Classes | Default kind when the group is selected | Why |
|---|---|---|---|
| `eyes` | `eye.l`, `eye.r` (sub-regions `sclera`, `iris`, `pupil`, `highlight` stored as region attributes) | `physical` | user asked for separation; TRELLIS eyes are shallow patches → `occluded_geometry_missing:true` |
| `eyebrows` | `eyebrow.l`, `eyebrow.r` | `physical_deferred` | usually texture-only in TRELLIS output; mask + region now, geometry later |
| `eyelashes` | `eyelash.l`, `eyelash.r` | `physical_deferred` | no parser has an eyelash class; heuristic mask + `lid_curve`; cards later |
| `hair` | `hair` (`.front`, `.back`, `.side`, `.ahoge`, `.ponytail`), `hair.facial` | `physical` | separate in all DCC pipelines |
| `clothes` | `clothing.top/outer/bottom/dress/legwear/neckwear`, `clothing.handwear.{l,r}`, `clothing.footwear.{l,r}` | `physical` (`legwear` that is skin-tight → `region` with `physical_candidate`) | layered over body |
| `accessories` | `accessory.headwear/eyewear/earwear/jewelry/belt/bag`, `appendage.tail/wings/animal_ears` | `physical` | swappable |
| `detachable` | `object.held.{l,r}`, `object.detached` | `physical` | detachable |
| `face` (region) | `body.head.face`, `.nose`, `.ear.{l,r}`, `.eyelid.{l,r}`, `.eye_socket.{l,r}`, `.mouth.*` | `region` | continuous head surface |
| `body` (region) | `body.neck`, `body.torso`, `body.arm.*`, `body.hand.*`, `body.leg.*`, `body.foot.*`, `body.skin_other` | `region` | continuous body surface |
| – | `unknown` | `region` | unassigned foreground |

A class whose group is not selected in `separate` becomes a `region` on its host; region classes whose group is not
in `regions` are merged into `body` without a label.

### 10.2 What "physical" means in v1
A physical part is the set of `model.glb` faces with that label, moved to its own node in `model_parts.glb`, keeping
the original UVs and the shared material. It is an **open surface patch** with recorded seam loops, because
TRELLIS fuses touching parts (internals §2.1). Watertight/amodal parts (scalp under hair, body under clothes,
eyeballs, lash cards) belong to future `completion`/`part_generation` providers, which are `unsupported` now.

### 10.3 Manifest `img3d.parts/1` (`parts/manifest.json`)

```json
{"schema":"img3d.parts/1","job_id":"job_…","source_job_id":null,"created_at":"…Z",
 "base":{"model":"model.glb","sha256":"…","vertices":1697772,"faces":1895691,
         "frame":{"up":"+Y","front":"+Z","units":"unit_cube","aabb":[[-0.5,-0.5,-0.5],[0.5,0.5,0.5]]}},
 "pipeline":{"plan_kind":"image_to_3d","steps":["preprocess","segmentation_2d","reconstruct","lift_labels","part_extraction","export"],
             "providers":{"segmentation_2d":{"id":"gdino_sam21","models":[…],"license":"apache-2.0"},
                          "lifting":{"id":"single_view"},"part_extraction":{"id":"shared_atlas"}},
             "options":{"separate":["eyes","eyebrows","eyelashes","hair","clothes","accessories","detachable"],"regions":["face","body"]}},
 "camera":{"method":"silhouette_grid","yaw_deg":180.0,"pitch_deg":2.0,"fov_deg":30.0,"silhouette_iou":0.91,"accepted":true},
 "taxonomy_version":"img3d.taxonomy/1",
 "labels":[{"value":3,"taxonomy_id":"hair","part_id":"pt_3f9a…"}],
 "parts":[
   {"id":"pt_0b12…","taxonomy_id":"body","label":"Body","category":"body","kind":"physical","parent":null,
    "node_name":"body","feature_id":0,
    "geometry":{"faces":1402233,"vertices":801220,"bbox":[[…],[…]],"area":1.84,"open_boundary":true,
                "watertight":false,"occluded_geometry_missing":true},
    "regions":["pt_77aa…","pt_81cc…"],"files":{"mesh":null,"mask":null},"seams":["sm_1d…"],
    "provenance":{"source":"remainder","methods":[],"confidence":null},
    "transform":[1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1],"refinement":{"eligible":true,"status":"untouched","locked":true}},
   {"id":"pt_3f9a…","taxonomy_id":"hair","label":"Hair","category":"hair","kind":"physical","parent":"pt_0b12…",
    "attach_to":"body.head","side":null,"symmetric_with":null,"node_name":"part_hair","feature_id":3,
    "geometry":{"faces":210334,"vertices":118220,"bbox":[[…],[…]],"area":0.12,"open_boundary":true,
                "watertight":false,"occluded_geometry_missing":true},
    "files":{"mesh":"parts/pt_3f9a…/model.glb","mask":"parts/masks/pt_3f9a….png"},"seams":["sm_1d…"],
    "provenance":{"source":"lifted","mask_values":[3],"methods":["gdino_sam21","single_view"],"confidence":0.81,"expected":false},
    "transform":[1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1],"refinement":{"eligible":true,"status":"untouched","locked":true}},
   {"id":"pt_77aa…","taxonomy_id":"body.head.face","label":"Face","category":"face","kind":"region","host":"pt_0b12…",
    "feature_id":7,"geometry":{"faces":81220,"area":0.05},"files":{"mask":"parts/masks/pt_77aa….png"},
    "provenance":{"source":"lifted","methods":["gdino_sam21","single_view"],"confidence":0.74}},
   {"id":"pt_9e01…","taxonomy_id":"eyelash.l","label":"Left eyelashes","category":"eyelashes","kind":"physical_deferred",
    "host":"pt_0b12…","feature_id":12,"side":"left","symmetric_with":"pt_9e02…",
    "deferred":{"reason":"texture_only_in_reconstruction","lid_curve_px":[[412,388],[430,380]],"space":"conditioning"},
    "files":{"mask":"parts/masks/pt_9e01….png"},"provenance":{"source":"heuristic","confidence":0.55}}],
 "seams":[{"id":"sm_1d…","parts":["pt_3f9a…","pt_0b12…"],"loops":[{"file":"parts/seams/sm_1d…_0.u32","closed":true,"length":1.84}]}],
 "relations":[{"type":"attached_to","a":"pt_3f9a…","b":"pt_0b12…","via":"sm_1d…","contact_length":1.84},
              {"type":"symmetric_with","a":"pt_9e01…","b":"pt_9e02…"},
              {"type":"layered_over","a":"pt_c0…","b":"pt_0b12…"},
              {"type":"held_by","a":"pt_d4…","b":"pt_0b12…","region":"body.hand.r"}],
 "artifacts":{"assembled":"parts/model_parts.glb","face_labels":"parts/face_labels.u16","weld_map":"parts/weld_map.u32"},
 "warnings":[]}
```
Rules: all paths relative to the job dir; part ids `pt_<12hex>`, seam ids `sm_<12hex>`; the manifest is validated
by `parts/manifest.py: validate_manifest(dict) -> list[str]` before it is written and before it is served.

Reassembly invariant (tested): concatenating every node of `model_parts.glb` and welding by `weld_map` yields the
same face set as `model.glb` (identical positions, Hausdorff 0).

### 10.4 Storage layout

```
runtime/jobs/<job_id>/
  job.json                         (+ kind, source, plan, parent_job_id, warnings)
  inputs/<img>.png                 (prompt jobs: written by text_to_image)
  preview.png  model.glb           (unchanged names and semantics; written only by reconstruct / copied for derived jobs)
  source/generated.png  source/t2i.json                         (prompt jobs)
  steps/preprocess/conditioning.png  conditioning.json
  steps/segmentation_2d/labels.png  overlay.png  segmentation.json  masks/<value>.png
  steps/reconstruct/latents.npz    (only when capture_latents=true; S9, later)
  steps/lift_labels/face_labels.u16  face_confidence.f16  lift.json  views/<n>.png
  steps/part_extraction/partition.npz  manifest.draft.json
  parts/manifest.json  model_parts.glb  face_labels.u16  weld_map.u32  seams/<sm>_<k>.u32  masks/<pt>.png  <pt>/model.glb
runtime/temp/plan/<job_id>/<step_id>/   (step work dirs; purged at startup like all of temp/)
runtime/providers/license_acceptances.json
backend/.model/providers/<provider_id>/  (weights; never committed)
```
Job deletion and retention remove the whole job dir, so all new artifacts are cleaned automatically. Derived jobs
get their own `model.glb` (hard link on the same volume, copy fallback) and copies of `inputs/`, `preview.png`,
`steps/preprocess/*` and, with `reuse_segmentation`, `steps/segmentation_2d/*`, made at creation time (same
isolation rule as reference snapshots), so deleting the parent never breaks them.

---------------------------------------------------------------------------------------------------
## 11. Job kinds end to end

### 11.1 `image_to_3d`
- No parts → `plan = null` → legacy path (identical `job.json.settings`, identical engine call, identical
  `model.glb`).
- With parts → plan `[source_image, preprocess?, segmentation_2d?, reconstruct, lift_labels?, part_extraction,
  export]`.

### 11.2 `prompt_to_3d`
Creation (`JobService.create_job`, event loop):
1. Compile; `capability_unavailable` (422) if no usable `t2i` provider.
2. `ReferenceService.create_set(name=f"Prompt: {prompt[:48]}")`; reserve `img_id = new_image_id()`.
3. `Job(kind=prompt_to_3d, reference_set_id=<new set>, primary_image_id=img_id,
   references=[JobReference(image_id=img_id, slot=front, is_primary=True)], source=JobSource(type="prompt",
   image_seed=<resolved>, generated_image_id=img_id, …), plan=…)`; no `_prepare_inputs` (nothing to copy).
Run: `text_to_image` writes `inputs/<img_id>.png` → `PromptImageImporter` imports it into the set
(`ReferenceService.import_image(set_id, image_id=img_id, path, slot=front, label="Generated", make_primary=True,
provenance=…)`, under the set lock, Pillow-validated, thumbnail made) → remaining steps as for images.
Failure/cancel before the image exists → the importer's `on_job_finished` deletes the set if still empty.
Legacy retry (`reference_set_id + primary_image_id + settings`) becomes a deterministic image-to-3D re-run on the
generated image; "regenerate from prompt" sends `source` (or `pipeline = job.plan.spec`) again.

### 11.3 `part_decomposition`
`POST /jobs/{id}/parts/rebuild` or a graph with a `job_result` node. Parent must be `completed` (409 otherwise).
Plan `[source_job, preprocess?, segmentation_2d?, lift_labels?, part_extraction, export]`;
`requires_engine=false` unless conditioning needs BiRefNet and the parent has no `steps/preprocess/`. Reference
fields copy the parent's; `settings` = parent settings + new `parts`; `seed` = parent seed.

### 11.4 Sequence (prompt + parts)
```
Client           API/JobService                 GenerationWorker (loop)        gpu-worker: PlanRunner
  │ POST /jobs ──► compile → create set, reserve img → save job.json → queue.put → 202 JobOut(plan: all pending)
  │                                              get job → status running ───► text_to_image (provider t2i)
  │ ◄─ job.updated (step text_to_image running, progress.stage=preprocessing) ◄───┤ writes inputs/<img>.png
  │                PromptImageImporter ◄── hook ─────────────────────────────────┤
  │ ◄─ job.updated (text_to_image done) ...                                       ├ preprocess, segmentation_2d
  │ ◄─ job.updated (reconstruct running, progress.stage=shape …)                  ├ reconstruct = ImageTo3DPipeline.run
  │ ◄─ job.updated (lift_labels / part_extraction / export under finalizing)     ├ lift → partition → export
  │ ◄─ job.updated (completed, plan.percent 100, result.parts)  ◄── _finish ─────┘
```

---------------------------------------------------------------------------------------------------
## 12. Settings (`config/settings.py`, all new; `/system` untouched; `safe_summary` includes them, no secrets)

| field | env | default | meaning |
|---|---|---|---|
| `parts_enabled` | `IMG3D_PARTS_ENABLED` | `true` | master switch for part steps (`feature_disabled` when false) |
| `prompt_to_3d_enabled` | `IMG3D_PROMPT_TO_3D_ENABLED` | `true` | master switch for prompt jobs |
| `node_pipelines_enabled` | `IMG3D_NODE_PIPELINES_ENABLED` | `true` | accept `pipeline` bodies |
| `providers_approved` | `IMG3D_PROVIDERS_APPROVED` | `[]` (comma list or JSON) | model providers the user approved |
| `providers_allow_restricted` | `IMG3D_PROVIDERS_ALLOW_RESTRICTED` | `false` | allow non-commercial/research licences |
| `provider_model_root` | `IMG3D_PROVIDER_MODEL_ROOT` | `backend/.model/providers` | weights root |
| `provider_paths` | `IMG3D_PROVIDER_PATHS` | `{}` (JSON) | per-provider weight path overrides |
| `provider_order` | `IMG3D_PROVIDER_ORDER` | `{}` (JSON kind → ids) | resolution order for `"auto"` |
| `provider_idle_unload_s` | `IMG3D_PROVIDER_IDLE_UNLOAD_S` | `900` | unload idle aux models from RAM |
| `provider_vram_margin_mb` | `IMG3D_PROVIDER_VRAM_MARGIN_MB` | `1536` | residency headroom |
| `capture_latents` | `IMG3D_CAPTURE_LATENTS` | `false` | persist reconstruct latents (S9, voxel lifting later) |
| `max_prompt_chars` | `IMG3D_MAX_PROMPT_CHARS` | `2000` | prompt cap (≤ schema max) |
| `lifting_max_views` | `IMG3D_LIFTING_MAX_VIEWS` | `24` | cap for `views` |
| `feature_mesh_tuning` | `IMG3D_FEATURE_MESH_TUNING` | `false` | tuner exposure/approval flag |
| `part_refinement_enabled` | `IMG3D_PART_REFINEMENT_ENABLED` | `false` | part consumer registry gate |

---------------------------------------------------------------------------------------------------
## 13. What the frontend needs (shared state/API layer for all four UI modes)

1. **Types** in `frontend/src/api/types.ts` mirroring §8: `JobKind`, `JobSource`, `JobPlan`, `PlanStep`,
   `StepStatus`, `StepArtifact`, `JobWarning`, `PartsResultOut`, `PartItem`, `PartOptions`, `PipelineSpec`,
   `PipelineNode`, `PipelineEdge`, `GraphIssue`, `ValidationReport`, `FeaturesOut`, `FeatureFlag`, `CatalogOut`,
   `NodeTypeDef`, `ProviderOut`, `LicenseInfo`, `TaxonomyOut`. `JobStage` stays the closed 11-value union;
   `PIPELINE_STAGES` stays as is.
2. **API modules:** `api/features.ts` (`getFeatures`), `api/pipeline.ts` (`getCatalog` with ETag, `validate`),
   `api/providers.ts` (`listProviders`, `rescan`), `api/parts.ts` (URL helpers: manifest, parts GLB, part mask,
   face labels, step files, source image; `rebuildParts(jobId, body)`), `api/jobs.ts` (`createJob` accepts the
   extended body; `listJobs({kind})`). Downloads go through the existing `useGlbDownload` path.
3. **Realtime:** handle `features.updated` (replace the features store) and re-fetch `/features` + the catalog
   after every `hello`; refetch the catalog when `catalog_etag` changes. `job.updated` already carries the plan.
4. **Gating (single source of truth = `/features`):**
   - "Image | Prompt" switch: Prompt enabled iff `features.prompt_to_3d.available`; disabled state shows `reason`.
   - "Parts" toggle iff `features.part_extraction.available`; the group checkboxes come from
     `catalog.taxonomy.groups`; when `segmentation_2d.available` is false, offer only
     `segmentation: null, extraction.method: "components"` ("Geometry split, unnamed components").
   - Tuner UI only when `features.mesh_tuning.enabled`; Node UI refine nodes rendered locked from the catalog.
5. **Submit:** Default/Mobile/Tablet build the simple form (`reference_set_id` or `source`, `settings` with
   optional `settings.parts`); the Node UI compiles its graph to `PipelineSpec` (node types, ports, params schemas
   and templates all from `/pipeline/catalog`; layout stays client-side), calls `POST /pipeline/validate`
   (debounced) to show issues, and runs `POST /jobs {pipeline}`. Both go through one `submitJob(request)`.
   The default Node template with no part nodes must produce a body that compiles to `plan: null` (the backend
   canonicalizes anyway).
6. **Progress UI:** when `job.plan` is non-null, list `plan.steps` (label, status, progress, message, duration)
   and use `plan.percent`/`plan.eta_seconds` for the overall bar; otherwise keep today's stage list. The Node UI
   lights `plan.steps[].node_id`; for `plan: null` it uses `catalog.legacy_job_node_map[progress.stage]`. Step
   artifacts (`overlay.png`, generated image) are node thumbnails (the viewer singleton stays the only 3D view).
7. **Results UI:** `result.parts` drives a Parts outliner (physical vs region vs deferred, counts, confidence,
   per-part mask thumbnails). The viewer loads `result.parts.model_url` instead of `result.model_url` when parts
   exist and toggles visibility by node name (`items[].node_name`) or `extras.img3d.part_id`; regions are faces
   with `_PART_ID` values on node `body`. "Download parts GLB" → `download_url`. "Rebuild parts" →
   `POST /jobs/{id}/parts/rebuild`.
8. **History:** `GET /jobs?kind=` filter chips; prompt jobs show `source.prompt` and `source.image_url`;
   derived jobs link `parent_job_id`. Retry: legacy jobs as today; plan jobs resend `pipeline: job.plan.spec`.
9. **Mock:** `test/frontend/mock_backend.mjs` implements every endpoint/event in §8 with `--parts-scenario
   none|components|semantic|fail` and `--t2i-scenario none|ok|fail`, so UI work proceeds before models are
   approved (mock lives under `test/`).

---------------------------------------------------------------------------------------------------
## 14. Test plan (all under `test/`; the existing 173 + 9 backend tests and the engine tests stay green)

| File (new) | Slice | Asserts |
|---|---|---|
| `test/backend/test_domain_compat.py` | S1 | legacy `job.json` fixture (`test/backend/fixtures/legacy_job.json`) loads: `kind=image_to_3d`, `plan/source=None`; `JobResultData` without `parts` loads; `PlanStep`/`JobPlan` JSON round-trip; manifest path fields reject absolute and `..` paths |
| `test/backend/test_schemas_pipeline.py` | S1 | `PipelineSpec` limits and id regex; node params unknown field → error; `PartOptions` bounds; `PromptParams` lengths |
| `test/backend/test_plan_compiler.py` | S2 | each template compiles; simple form ≡ graph form (equal plans); trivial graph → `plan None`; cycle, type mismatch, multiple sources, missing input, locked node, `job_result`+`image_to_3d` → exact issue codes; `capability_unavailable` details with a fake `CapabilityView`; deterministic step order; `requires_engine` flags; `settings.parts` written back normalized |
| `test/backend/test_plan_runner.py` | S2 | with `test/backend/fakes_plan.py` executors: status transitions + unthrottled publishes; `progress.stage` only from `STAGE_ORDER ∪ {queued, waiting_for_model}` and `stage_index` monotonic; `progress.percent` equals `compute_percent` values; `plan.percent` monotonic, 100 at end; soft failure → job completed + warning + dependants skipped; hard failure → job failed; cancel mid-step → `cancelled`; work dirs moved atomically; `model.glb` hash unchanged by post steps |
| `test/backend/test_pipeline_router.py` | S2 | `plan None` → `ImageTo3DPipeline.run` called once with the same `(job, reporter, cancel)` objects; plan → runner; `requires_engine` |
| `test/backend/test_provider_registry.py` | S3 | state resolution order with a fake `EnvironmentProbe` (`not_approved`, `missing_dependencies`, `not_installed`, `license_required`, `disabled` restricted, `unsupported`, `available`); lazy import failure → `UnavailableProvider` with reason; `resolve("auto")` honours `provider_order`; rescan triggers `on_change` |
| `test/backend/test_residency.py` | S3 | offload ordering between two fake providers; `release_vram` called only when short; `InsufficientVramError` when still short; `release_all_aux` on exit |
| `test/backend/test_settings_parts.py` | S3 | defaults of every new setting; comma/JSON parsing of list/map settings; `safe_summary` has no secrets |
| `test/backend/test_parts_taxonomy.py` | S1 | groups → classes; policy for every combination of `separate`/`regions`; symmetric pairs |
| `test/backend/test_parts_geometry.py` | S5 | on synthetic meshes (two touching boxes, capped cylinder, mesh with UV seams): weld map, split, seam loop order/closure, reassembly invariant, connected components |
| `test/backend/test_parts_gltf.py` | S5 | `model_parts.glb` has 1 material and 2 images, node names, node/scene `extras`, `_PART_ID` round-trip through trimesh, `compute_glb_stats` sums nodes |
| `test/backend/test_parts_manifest.py` | S5 | `validate_manifest` accepts the §10.3 example and rejects bad ids/paths/kinds |
| `test/backend/test_features_api.py` | S4 | `/features` keys; `mesh_tuning.enabled` false by default and with `blender_enabled=False`; `/system` response still equals the pinned shape |
| `test/backend/test_pipeline_api.py` | S4 | catalog shape, ETag + 304; validate returns issues without creating jobs |
| `test/backend/test_jobs_kinds.py` | S4 | legacy POST → `plan null`, `settings` without `parts`, identical to pre-change payload; `settings.parts.enabled=false` → legacy; conflicting fields 422; prompt without t2i → 422 `capability_unavailable`; parts job with fake providers completes with `result.parts`; `?kind=` filter; rebuild 404/409/202 and file copies; worker skips engine wait for `requires_engine=false` |
| `test/backend/test_part_files_api.py` | S4 | manifest/GLB/mask/face-label/step-file routes: 409 before completion, 404 `parts_not_available`, 422 bad ids/names, path traversal rejected, `?token=` works with auth enabled |
| `test/backend/test_ws_features.py` | S4 | `features.updated` delivered to all connections (also with `?job_id=`); `job.updated` carries `plan` |
| `test/backend/test_prompt_jobs.py` | S6 | fake T2I provider (`test/backend/fakes_t2i.py`, writes a real PNG): set created, reserved id imported with provenance, `source.provenance` filled, readiness warnings, empty set deleted on failure, legacy retry body works |
| `test/backend/test_prompt_templates.py` | S6 | presets/styles render; negative prompt rejected for distilled providers |
| `test/backend/test_segmentation_fusion.py` | S7 | numpy fusion: z-priority, gates → `unknown`, left/right split, eyelash band and eye sub-split heuristics on synthetic masks |
| `test/backend/test_segmentation_step.py` | S7 | fake segmenter (`fakes_segmentation.py`): files and `segmentation.json` shape; vocabulary includes prompt nouns; soft failure path |
| `test/backend/test_lift_propagation.py` | S8 | pure label propagation and majority filter on a synthetic face graph |
| `test/engine/test_lifting_raster.py` | S8 | GPU-only (skip without CUDA): face-ID decode above 2²⁴, front camera orientation (image-right = +X), silhouette IoU on a known mesh |
| `test/engine/test_conditioning_crop.py` | S9 | `processing/conditioning.py` equals upstream `preprocess_image` RGB output for alpha inputs (pixel-exact) |
| `test/engine/test_capture_parity.py` | S9 | existing parity test still green; with capture enabled the call sequence and RNG fingerprints are identical and `model.glb` matches the capture-off run in the mechanics harness |
| `test/engine/test_provider_smoke.py` | S6/S7 | real providers, **skipped unless installed and approved**: T2I one image; segmentation on `test/images/{ALICE,ANIME_CHARACTER_01}`; afterwards the TRELLIS import probe still passes |
| `test/frontend/pipeline_contract_smoke.mjs` | S10 | mock endpoints match §8 shapes (keys, status codes, events) |

---------------------------------------------------------------------------------------------------
## 15. Incremental implementation plan (slices with disjoint file ownership)

Rules for every slice: embedded Python only (`backend\python\python.exe -s`), tests only under `test/`, no edits
outside the listed files, contract first, all existing tests green at merge, no tuning code, no model downloads.

| Slice | Owns (create/modify only these) | Depends on | Acceptance criteria |
|---|---|---|---|
| **S0 Contract** (lead) | `.claude/skills/img3d-architecture/api-contract.md` (new §11 Pipeline/Parts/Prompt/Features; §2 settings rows incl. the drift fixes listed in the contract map §6.1.10; §4 error codes; §7 new endpoints; §8 `features.updated`), `.claude/skills/img3d-part-aware/DESIGN.md` | – | user approves the contract before code; no code changes |
| **S1 Domain, taxonomy & schema files** | `backend/models/plan.py`, `models/source.py`, `models/parts.py`, `models/job.py` (additive fields only), `models/reference.py` (`provenance`), `backend/core/pipeline_errors.py`, `backend/parts/__init__.py`, `parts/taxonomy.py`, `parts/policy.py` (pure data + policy, no geometry), `backend/schemas/pipeline.py`, `schemas/pipeline_params.py`, `schemas/parts.py`, `schemas/source.py`, `schemas/features.py`, `schemas/providers.py`; tests `test_domain_compat.py`, `test_schemas_pipeline.py`, `test_parts_taxonomy.py`, `fixtures/legacy_job.json` | S0 | 182 tests green; new tests green; no behaviour change (no router/service wiring) |
| **S3 Providers, settings, residency** | `backend/config/settings.py` (new fields only), `backend/inference/providers/{__init__,specs,base,catalog,registry,residency,probes,unavailable}.py`, `backend/scripts/install_provider.py`, `backend/constraints.txt`; tests `test_provider_registry.py`, `test_residency.py`, `test_settings_parts.py` | S1 | registry reports the honest states of §6.4 on this machine with no installs (all model providers `not_approved`, builtins `available` or `not implemented`); no torch import at module import time (asserted by importing with `sys.modules` guard) |
| **S2 Plan core** | `backend/pipelines/base.py` (`requires_engine`), `pipelines/progress.py` (`update_plan`), `pipelines/plan/{__init__,catalog,capabilities,compiler,issues,runner,tracker,context,artifacts,router,consumers}.py`, `pipelines/plan/steps/{__init__,base,reconstruct}.py`; tests `test_plan_compiler.py`, `test_plan_runner.py`, `test_pipeline_router.py`, `fakes_plan.py` | S1, S3 (protocol only) | compiler/runner tests green; `compute_percent`, `STAGE_TABLE` untouched (diff check); refine nodes locked; nodes whose executor module is not merged yet report `available:false, reason:"not implemented"` |
| **S5 Parts geometry & export** | `backend/parts/{manifest,weld,split,seams,relations,components,gltf_export,labels_io}.py`, `backend/pipelines/plan/steps/part_extraction.py`, `pipelines/plan/steps/export.py`; tests `test_parts_geometry.py`, `test_parts_gltf.py`, `test_parts_manifest.py` | S1, S2 (step base) | reassembly invariant; 1 shared material; manifest validator; `geometry_components` works on a real `model.glb` copied from `runtime/jobs` into a temp dir in a manual check (no runtime job modified) |
| **S4 Services, API, worker wiring** | `backend/schemas/jobs.py`, `schemas/events.py`, `backend/services/{job_service,result_service}.py`, new `services/{pipeline_service,features_service,provider_service,part_files}.py`, `backend/api/v1/{jobs,pipeline,features,providers,parts}.py`, `api/router.py`, `backend/core/{container,lifecycle}.py`, `backend/workers/generation_worker.py`, `backend/main.py` (inject `provider_registry`), `test/backend/conftest.py`; tests `test_features_api.py`, `test_pipeline_api.py`, `test_jobs_kinds.py`, `test_part_files_api.py`, `test_ws_features.py` | S1, S2, S3, S5 | **first user-visible milestone**: legacy requests byte-identical; `/features`, `/pipeline/catalog`, `/pipeline/validate`, `/providers` live; an image job with `settings.parts = {segmentation: null, extraction: {method: "components"}}` produces a real `model_parts.glb` + manifest from a real generation (smoke run by the user); `/system` unchanged |
| **S9 Reconstruction seam extensions** | `backend/inference/base.py` (optional `prepare_conditioning`, `release_vram`, `GenerationRequest.capture`), `backend/inference/trellis/{engine,generation,imaging}.py`, `backend/processing/conditioning.py`, `backend/pipelines/image_to_3d.py` (`run(..., capture=None)` keyword only), `backend/pipelines/plan/steps/preprocess.py`; tests `test/engine/test_conditioning_crop.py`, `test/engine/test_capture_parity.py` | S2 | `test_generation_parity.py` unchanged and green; capture off ⇒ no new files, identical `model.glb`; `prepare_conditioning` crop equals `preview.png` RGB within 1 LSB on a real run |
| **S7 2D segmentation** | `backend/inference/providers/segmentation/**`, `backend/pipelines/plan/steps/segmentation_2d.py`, vendored code under `backend/third_party/<name>/` only for user-approved providers; tests `test_segmentation_fusion.py`, `test_segmentation_step.py`, `fakes_segmentation.py` | S3, S2, S9 (conditioning) | with fakes: full semantic pipeline completes; with an approved+installed provider: smoke on both sample images produces labels for hair/face/eyes (manual review), peak VRAM recorded in step metrics |
| **S8 Lifting** | `backend/inference/providers/lifting/**`, `backend/pipelines/plan/steps/lift_labels.py`; tests `test_lift_propagation.py`, `test/engine/test_lifting_raster.py`, `fakes_lifting.py` | S3, S2, S5, S7 (interface) | single-view lifting labels ≥ 90 % of front-visible faces on a real job with fake masks derived from the preview alpha; camera IoU reported; soft failure path verified |
| **S6 Prompt-to-3D** | `backend/inference/providers/t2i/**`, `backend/pipelines/plan/steps/text_to_image.py`, `pipelines/plan/prompting.py`, `backend/services/reference_service.py` (`import_image`, `delete_if_empty`), new `services/prompt_import.py`, `backend/schemas/references.py` (`provenance`), `backend/requirements.txt` (diffusers/accelerate rows, **only after approval**); tests `test_prompt_jobs.py`, `test_prompt_templates.py`, `fakes_t2i.py` | S3, S2, S4 | with the fake provider: full prompt job; with an approved provider: one real prompt → image → GLB; `uv pip check` clean; TRELLIS runtime probe and real-generation smoke unchanged |
| **S10 Frontend contract mock** | `test/frontend/mock_backend.mjs`, `test/frontend/pipeline_contract_smoke.mjs` | S0 | smoke passes; existing `mesh_tuner_mock_smoke.mjs` still passes |

Merge order: S0 → S1 → S3 → S2 → S5 → S4 → S9 → (S7 ∥ S8) → S6; S10 any time after S0. S7, S8 and S6 can be
developed in parallel against fakes; their real smoke tests wait for the user's model approval.

---------------------------------------------------------------------------------------------------
## 16. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Base model overwrite by a second `engine.generate` | Only `ReconstructStep` calls `ImageTo3DPipeline.run`; runner hash test; per-part reconstruction is not in this milestone |
| Remeshed double shell; inner wall never visible | Labels propagate across the welded face graph; `voxel` lifting (raw single-layer mesh via latents) is the planned upgrade |
| Camera fit fails for non-frontal inputs | IoU gate → soft failure with warning; job still completes; `multi_view` improves coverage |
| Anime eyebrow/eyelash detection weak | `physical_deferred` + heuristics; never invented geometry; confidence in manifest |
| VRAM contention | single thread, residency manager, offload after each step, `insufficient_vram` instead of OOM where predictable |
| Dependency drift (diffusers pulling hub 1.x) | `constraints.txt`; install only with `-c`; TRELLIS probe after install |
| Long plans tie up the single GPU thread with CPU work | accepted in v1 (sequential queue anyway); later: CPU-only steps could move to a CPU executor |
| Licence exposure (public GPL-3.0 repo) | weights never committed; restricted licences hidden by default; THIRD_PARTY_NOTICES rows per approved provider; nvdiffrast NC already noted |
| Frontend stage coupling | no new `JobStage`; plan progress is separate |

---------------------------------------------------------------------------------------------------
## 17. Open questions for the user (genuinely undecided)

1. **Text-to-image model:** approve Z-Image-Turbo (32.8 GB), FLUX.2-klein-4B (16 GB), Animagine XL 4.0 (already on
   disk in `D:\10-SEP-2026\T2I`, anime only, OpenRAIL++), several, or none for now? Approving any of them also
   means installing `diffusers==0.39.0` + `accelerate==1.15.0` with the constraints file.
2. **Segmentation stack:** approve the baseline Grounding DINO + SAM 2.1 (+ optional Florence-2) (~1.8–3.4 GB,
   Apache/MIT, zero new packages)? Add See-through `l2d_sam_iter2` for anime (vendored modules)? SAM 3 (gated SAM
   licence, vendored, needs `ftfy` + `iopath`)? Sapiens2 (custom licence)?
3. **Model installation route:** CLI script only (this proposal), or also an in-app download endpoint with a
   licence dialog?
4. **Hard-disable the paused tuner now** by adding `IMG3D_BLENDER_ENABLED=false` to your local `backend/.env`?
5. **Physical part semantics for this milestone:** are open surface patches with recorded seams (no hole filling,
   no amodal completion) acceptable for eyes/hair/clothes until a refinement/completion layer is approved?
6. **Prompt review step:** is one-shot prompt → 3D enough now, or do you want an "image only, review first" job
   type (needs a non-GLB job result shape in the contract)?
7. **Out-of-process sidecar** for models that need transformers 5 / spconv (SAM 3 via HF, See-through LayerDiff,
   P3-SAM/X-Part): allowed later as an exception to "embedded Python only", or never?
8. **Multi-view lifting budget:** acceptable extra time per job (estimate tens of seconds to ~2 minutes for 16–24
   views) — default `views=0` (input view only) or on?
