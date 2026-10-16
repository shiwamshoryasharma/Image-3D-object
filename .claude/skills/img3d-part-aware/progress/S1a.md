# S1a Frozen interfaces: progress report (2026-10-16)

Status: **done**. This is the data/interface half of `DESIGN.md` slice S1; the runtime plumbing half is S1b. This run
resumed an earlier one that the user's token budget stopped partway. Its files were read, checked against
`DESIGN.md` and kept where correct. The changes this run made are listed under "This run".

## Built
- **Taxonomy v1** `backend/parts/taxonomy.py` (§2.2-§2.3; data only, stdlib only):
  - `TAXONOMY_VERSION = 1` and the 10 groups (`GROUPS`, with the UI notes of §2.2).
  - `SEPARABLE_GROUP_IDS` / `DEFAULT_SEPARATE` (all seven) and `REGION_GROUP_IDS`.
  - 48 `PartClass(value, id, group, label, default_kind, attach_to, symmetric_with, sided, instance_split, aliases,
    z_priority)` records with the frozen values of §2.3. Side wildcards are resolved per class (`body.hand.left` →
    `body.arm.left`) and `*` wildcards are expanded left, then right.
  - `TAXONOMY`, `BY_VALUE`, `classes_for_groups`, `to_out()`.
  - `match_terms`: whole-word, case-insensitive matching with hyphen/underscore normalisation and an optional plural
    `s`/`es`. The longest alias wins, so "cat ears" maps to `appendage.animal_ears` only. Results come front-most
    first.
  - `classes_for_term`: falls back to `object.held`.
  - Part-id rules: the regex, 80 characters at most, and no Windows reserved first segment. `part_id_problem` /
    `validate_part_id` check them; `instance_part_id` and `component_part_id` build ids.
- **Shared vocabulary** `backend/parts/types.py` (§3.1-§3.10, numpy only under `TYPE_CHECKING`):
  - Every manifest literal: `Kind`, `Side`, groups, strategies, segmentation source/profile/subject, conditioning
    mode, frame method, geometry status/source/tag, frame/placement/provenance methods, refinement status/needs,
    relation types/sources, buffer dtypes.
  - Format constants: `img3d.parts/1`, `_FEATURE_ID_0`, the asset directory names, `FACE_PART_NONE`, the
    `face_flags` bits.
  - The §3.3 buffer table (`BUFFER_SPECS`, `vmap_buffer_spec`).
  - The §3.4 `MeshNode` record.
- **Manifest** `backend/parts/manifest.py` (§3.5-§3.11, pydantic):
  - `PartsManifest` (field `schema_id`, alias `schema`) with every sub-record: generator, source/origin, frames,
    views, strategy, label table, meshes, `PartRecord`, seams, relations, buffers, artifacts, expectations,
    provenance, refinement, reassembly, summary, warnings.
  - Unknown fields are ignored everywhere (A9). The schema major is checked.
  - Paths must be safe and relative: no absolute path, drive, backslash, empty, `.` or `..` segment.
  - Part, seam, relation, analysis and job ids are validated. Ids, mesh indices, buffer ids/files and label values
    must be unique.
  - `byte_length` must equal count × components × itemsize.
  - `expected_missing` relations start at `asset`.
  - Lookups: `part`, `part_index`, `buffer`, `mesh`, `relations_of`, `to_json_dict`.
  - Cross-checks against disk are left to `parts/validate.py` (S3b).
- **Provider seam** `backend/inference/providers/{__init__,base}.py` (§5.1): `ModelKind`, `CommercialUse`,
  `ModelUiState`, `ReasonCode` (the one code vocabulary), `LicenseInfo`, `DependencySpec`, `ModelSpec`,
  `ProviderStatus`, `ModelProvider` ABC, `StepProgress`.
- **Role ABCs** (§5.2; files and plain dataclasses only cross the seam):
  - `backend/inference/imagegen/{__init__,base}.py`: `ImageGenRequest`, `ImageGenOutput`, `TextToImageProvider`.
  - `backend/inference/segmentation/{__init__,base}.py`: `Box2D`, `Mask2D`, `Detector2D`, `Grounder2D`,
    `MaskRefiner`, `Parser2D`.
  - `backend/inference/parts/{__init__,base}.py`: `MeshArrays`, `MeshHandle`, `CameraSpec`, `CameraFit`,
    `CameraGrid`, `RasterOut`, `LabelLifter`, and the future seams `MeshSegmenter3D`, `AmodalCompleter`,
    `PartGenerator`.
- **Wire schemas** (§4.4.1-§4.7.5, §6). Requests use `RequestModel` (`extra="forbid"`); responses use `ApiModel`
  (`extra="ignore"`):
  - `schemas/features.py`: `FeaturesOut` (`schema` alias), every capability entry, `PartGroupOut`
    (`separable_groups()`, `region_groups()`), `TaxonomyOut.current()`.
  - `schemas/model_registry.py`: `ModelOut`, `ModelList`.
  - `schemas/image_generation.py`: `ImageGenerationRequest` (strip, then `invalid_characters`; 8000-character hard
    cap), `WorkflowImageGenerationRequest` (`num_images` ≤ 1), `UseCandidateRequest`, `ImageGenerationOut.from_run`.
  - `schemas/segmentation.py`: `SegmentationOptions` (partial objects, `unknown_class`, deduplicated terms and
    classes), `SegmentationRequest` (discriminated source), `SegmentationOut.from_run`.
  - `schemas/parts.py`: `PartExportOptions`, `PartExtractionOptions` (the part_extraction node params),
    `PartAnalysisOptions`, `PartAnalysisOut.from_run` with every §4.6.7 URL and nullability rule.
  - `schemas/workflows.py`: `WorkflowGraph` (`schema` and `from` aliases), node params models, the simple-form
    sources, `WorkflowRequest`, `WorkflowOut.from_domain` (`exists` computed at read time), `StepFailedDetails`,
    `GraphIssueOut`, `ValidationReport`, `CatalogOut`.
  - `WorkflowRequest` enforces: `graph` excludes the simple-form keys; source or graph is required;
    `reconstruction` is required for image/prompt sources and null for a job source; `segmentation` requires
    `parts`; `parts.segmentation_id` must be null; there is no top-level `export`. Each rule yields the §4.7.1 `loc`
    and type (checked through FastAPI: `["body","segmentation"]`, `["body","parts","segmentation_id"]`, `["body"]`).
- **Domain records**, each subclassing `models.runs.RunRecord` (S1b):
  - `models/image_generation.py`: `ImageGenerationRun`, candidates, readiness, `used_as`.
  - `models/segmentation.py`: `SegmentationRun` with source, `job_id`, `source_pixels_sha256`, result.
  - `models/part_analysis.py`: `PartAnalysisRun`, result, `PartSummary`.
  - `models/workflow.py`: `WorkflowRecord`, steps, resources, outputs, `child_ids()`.
- **Node catalog** `backend/services/workflow_catalog.py` (§4.7.2 / §6.5):
  - 14 node types in `/features` order with ports, params models, `params_schema()` (always with `properties`),
    `defaults()`, `compiles_to`, `requires` and `max_instances = 1`.
  - The 4 refine nodes are locked (`not_approved`, "Paused until approved", `hidden_by_default`). The 3 future 3D
    nodes are `unsupported` and hidden.
  - The 5 templates and an exact `LEGACY_JOB_NODE_MAP` that covers every `JobStage`.
  - `resolve_node_states`, `feature_nodes`, `catalog_etag`, `build_catalog` (ETag = sha256 of the canonical JSON).
- **Workflow store stub** `backend/storage/workflow_store.py` (§7.2):
  - The frozen API, `list_terminal() -> []`, `delete(wf_id) -> False`, `active_child_ids() -> frozenset()`, plus the
    `load()` / `recover_interrupted()` / `layout` that the S1b container calls.
  - It never touches the disk. The constructor `WorkflowStore(paths)` is frozen; S11 implements the store behind
    the same signatures.

### This run
- Checked every owned file against `DESIGN.md` §2, §3, §4.3-§4.7, §5.1-§5.2, §6, §7.2 and §8.1. The design examples
  were validated programmatically: the full §6.1 `FeaturesOut` example validates with no diff; `ModelOut`,
  §4.4.6, §4.5.6 `result`, §4.6.7 `result` and §4.7.5 validate, and their only extra keys are those listed under
  Deviations.
- Fix: `WorkflowImageGenerationRequest` and `WorkflowPartAnalysisOptions` now set `from_attributes=True`.
  - Before, server code (the S11 compiler, services) that passed a plain `ImageGenerationRequest` /
    `PartAnalysisOptions` into a `WorkflowRequest` failed with `model_type`.
  - Now such instances are re-validated, so the workflow rules still apply. JSON bodies are unaffected.
- New tests (below). They pin the response builders and the `/features` building blocks against the design
  examples.

## Files (all new; paths relative to `backend/` unless they start with `test/`)
`parts/{__init__,types,taxonomy,manifest}.py`, `inference/providers/{__init__,base}.py`,
`inference/imagegen/{__init__,base}.py`, `inference/segmentation/{__init__,base}.py`,
`inference/parts/{__init__,base}.py`,
`schemas/{features,model_registry,image_generation,segmentation,parts,workflows}.py`,
`models/{image_generation,segmentation,part_analysis,workflow}.py`, `services/workflow_catalog.py`,
`storage/workflow_store.py`, `test/backend/test_taxonomy_manifest.py`.

## Tests
- `test/backend/test_taxonomy_manifest.py`: **99 passed** (45 functions). The §10.2 row is covered:
  - taxonomy values frozen and unique, symmetric pairs mutual, groups = 7 separable + face/body;
  - Windows reserved names rejected;
  - `match_terms`: katana→`object.held`, twin tails→`hair`, cat ears→`appendage.animal_ears`;
  - the verbatim §3.11 example round-trips (dict and JSON text);
  - unknown fields ignored, a wrong schema major rejected, absolute/`..`/backslash paths and duplicate ids rejected.
- The same file also pins:
  - the request rules of every wire schema and the response shapes against §4.4.6, §4.5.6, §4.6.7, §4.7.5 and §6.1;
  - the catalog (node order, locked/unsupported nodes, `params_schema.properties`, the §6.5 segmentation node
    entry, templates, ETag stability and change), `LEGACY_JOB_NODE_MAP`;
  - the store stub signatures, the run records, and that the provider ABCs are abstract.
- Import check: importing every S1a module loads none of torch, diffusers, transformers, nvdiffrast, cumesh,
  trellis2 or numpy. `model_json_schema()` works for all 152 models, and a FastAPI app with the request/response
  models generates OpenAPI.
- Full suite `backend\python\python.exe -s -m pytest -c test\pytest.ini test\backend test\aux_models -q
  -p no:cacheprovider`: **361 passed, 2 skipped**. That is 182 baseline plus the S0, S1a and in-flight S1b tests;
  there were no failures.

## Deviations from DESIGN.md (with reasons)
1. `ModelProvider` has a constructor `__init__(spec)` and a re-entrant `self.lock = threading.RLock()`, where §4.4.4
   / §5.1 say "one `threading.Lock`". A run may call `load()` while it already holds the provider lock.
2. `ImageGenOutput.timings` and `.metadata` default to `{}` (additive).
3. `MeshNode` (§3.4) lives in `parts/types.py`, so S3a (partition) and S3b (export, reader) share one record.
4. `MeshHandle` (left undefined in §5.2) is an opaque frozen dataclass `(handle_id, vertex_count, face_count)`.
5. `AmodalCompleter.complete(part: PartGeometry, …)` imports `parts.reader.PartGeometry` only under
   `TYPE_CHECKING`. §3.13 puts `PartGeometry` in S3b's `parts/reader.py`.
6. The future-seam methods (`segment_mesh`, `complete`, `generate_parts`) are abstract. There are zero
   implementations, so nothing changes.
7. Additive response keys (each is in F1's TS types or ignored by it):
   - `ModelOut.license.attribution` (from `LicenseInfo.attribution`, null when unset);
   - `SegmentationOut.source` (a typed copy of the source) and `source_url` (`GET /segmentations/{id}/source`; the
     UI never builds paths itself, §6.9);
   - `WorkflowStepOut.resource.reference_set_id` is always present (null except for `reference_image`; the §4.7.5
     example omits it). The abridged §4.7.5 steps also gain their defaults (`message`, `progress`,
     `warning_count`).
8. Names in `WorkflowRequest`:
   - `source.generation` is `WorkflowImageGenerationRequest` and `parts` is `WorkflowPartAnalysisOptions`. Both are
     subclasses of the §4.7.1 models that carry the workflow rules (`num_images` ≤ 1 → `less_than_equal`; non-null
     `segmentation_id` → `conflicting_fields`).
   - The part_extraction node params model is `PartExtractionOptions` ("PartAnalysisOptions minus segmentation_id
     and export"); `PartAnalysisOptions` subclasses it.
9. Request validation choices the design leaves open:
   - `SegmentationOptions.classes = []` is refused (at least 1; `null` means the whole taxonomy).
   - `ImageGenerationRequest.model_id` must match `^[a-z0-9_]{2,40}$`; a well-formed unknown id is the service's
     503.
10. `PartProvenance.votes` is a float. The example shows integers, which validate and compare equal; votes are
    weighted sums (§4.6.5).
11. `taxonomy_version` accepts any value ≥ 1, because values are never reused. Only the manifest `schema` major is a
    hard gate (A9).

## Requests for other slices
- **S1b / integrator (`test_import_boundaries.py`):** `schemas/{features,model_registry,image_generation,workflows}.py`
  and `services/workflow_catalog.py` import the torch-free vocabulary of `inference.providers.base` (`ReasonCode`,
  `ModelUiState`, `ModelKind`, `CommercialUse`, `LoadedDevice`, `MODEL_ID_PATTERN`). §5.1 defines it there, and the
  §6 wire models must use the same literals. The AST layering rule has to allow `schemas/* → inference.providers.base`
  (torch-free; I7 holds).
- **S2:**
  - Build `/features.nodes` with `workflow_catalog.resolve_node_states(<pipeline capabilities>)` and then
    `feature_nodes(states)`.
  - Build the catalog with `build_catalog(states, graph_max_bytes=settings.workflow_graph_max_bytes)` and take its
    `.etag` for `features.workflow.catalog_etag`.
  - Groups come from `PartGroupOut.separable_groups()` / `region_groups()` and the taxonomy from
    `TaxonomyOut.current()`. Strategy labels are `schemas.parts.STRATEGY_LABELS`.
- **S3a:** import `MeshNode`, `BUFFER_SPECS`, `vmap_buffer_spec` and the format constants from `parts.types`; do not
  redefine `MeshNode` in `partition.py`.
- **S3b:**
  - Define `PartGeometry` (and its `SeamLoops`) in `parts/reader.py`, as `inference/parts/base.py` expects.
  - Build manifests with `parts.manifest.PartsManifest` and write `to_json_dict()`.
  - `validate.py` adds the disk cross-checks that the pydantic model leaves out.
- **S10:** `PartAnalysisResult` / `PartSummary` carry `has_overlay`, `previews`, `has_mask` and `thumbnail`;
  `PartAnalysisResultOut.from_domain` derives every URL and null from them.
- **S11:** compile to `WorkflowRequest` (plain option instances are accepted now), keep the `WorkflowStore(paths)`
  constructor and the §7.2 signatures, and add the rest of the store (create/get/list/save) behind them.
- **S0 (contract §11):** record the additive keys of Deviation 7.

## Open issues
- None blocking. Two lines of the test file exceed 120 characters because they belong to the verbatim §3.11 example.
