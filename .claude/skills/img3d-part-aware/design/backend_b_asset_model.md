# Backend proposal B: asset-model-first, part-aware Img3D backend

Date: 2026-10-16. Status: **design proposal** for the milestone "backend part-aware architecture + four UI modes".
Nothing here has been implemented. Tuning, refinement, eye or rig correction, reference-aware optimisation and Blender
work are **out of scope** and stay disabled.

Angle of this proposal: start from the **part-aware asset data model** that future tuning, refinement and reassembly
must consume, then derive the pipeline, the inference seams, the API and the storage from it.

Evidence used (read fully): `research/backend_contract_map.md`, `research/backend_generation_map.md`,
`research/trellis2_internals.md`, `research/part_aware_3d_research.md`, `research/segmentation_models_research.md`,
`research/text_to_image_research.md`, `research/environment_audit.md`, `research/frontend_architecture_map.md`,
`research/ui_modes_research.md` (§0–5, §8–11), the authoritative `img3d-architecture/api-contract.md` and
`.claude/CLAUDE.md`. I also re-checked these code facts in this session: `pipelines/base.py`
(`GenerationPipeline.run(job, reporter, cancel)`), `pipelines/progress.py` (`compute_percent`,
`JobProgressReporter.mutate`), `models/job.py` (`STAGE_TABLE`, `Job`, `JobResultData`), `storage/paths.py`,
`utils/ids.py`, `inference/base.py` (`GenerationRequest`, `GenerationOutput`), `schemas/jobs.py`,
`core/container.py:63-140`, `config/settings.py:60-131,282-310`, `api/router.py`, `schemas/events.py`,
`api/websocket_hub.py:229`, `test/backend/conftest.py:60-110`, `test/backend/test_health_system.py:33-70`,
`test/backend/test_settings.py:28-48`, `test/backend/test_mesh_tuner_api.py:49-95`, `test/backend/fakes.py:150-193`
and `test/pytest.ini`.

Labels: **[V]** = verified in code or evidence; **[A]** = assumption that the named test must confirm.

---------------------------------------------------------------------------------------------------

## 0. Decisions at a glance

1. **The asset is the product.** A completed job is one immutable **asset revision**: the untouched base mesh
   `model.glb` plus an optional **part layer** under `jobs/<job>/parts/`, described by a versioned manifest
   `img3d.parts/1`. Everything a later consumer needs (tuning, refinement, DCC export, reassembly) is in that
   folder and addressable by stable ids.
2. **One stable face index space.** Every per-face array indexes the faces of `model.glb` in glTF index order.
   Semantic labels (`face_labels`) are stored **separately** from physical ownership (`face_owner`). Regions and
   "geometry-deferred" parts therefore never cut topology, and physical parts can always be stitched back exactly.
3. **Three part kinds plus one host.** `host` (the continuous body or object remainder), `physical` (its own mesh
   node, cut with recorded seams), `physical_deferred` (a physical asset that the user asked for, but which TRELLIS.2
   only paints; it ships now as a region with mask and guide curves, and geometry comes later), and `region`
   (a semantic area on a mesh-owning part). Eyes, hair, clothes, accessories and detachable objects become
   `physical`. Eyebrows and eyelashes become `physical_deferred`. Face and body areas stay `region`.
4. **Reversible splitting.** Physical cuts weld UV-seam duplicates, record ordered **boundary loops** (seams) in
   welded vertex ids, and store per-mesh vertex maps. Reassembly reproduces the base positions, UVs and normals
   bit-exactly. This is a tested invariant.
5. **One texture atlas, no re-bake.** All meshes reference the base material. `assembled.glb` is a multi-node scene
   (root → host → physical children). Per-part GLBs are written in the same asset frame with identity transforms.
6. **Asset revisions are jobs.** Re-decomposing an existing asset creates a **derived job** of kind
   `part_decomposition` that hard-links the parent's base files and writes a new part layer (revision n+1). One GPU
   queue and one worker stay the only GPU path. No second queue, no new worker.
7. **Job kinds** `image_to_3d` (default, legacy) · `prompt_to_3d` · `part_decomposition`. A
   `PipelineRouter` sends legacy jobs (no `parts`, no `source`) to the **unchanged** `ImageTo3DPipeline.run`, so
   that path is byte-identical.
8. **No new `JobStage`.** The frozen 9-stage `progress` keeps its formula. New work is described by an optional
   `Job.pipeline` plan (steps `prompt`, `input_image`, `segmentation`, `image_to_3d`, `part_extraction`, `export`)
   with its own `pipeline.percent`. Step ids equal Node-UI node types (`img3d.<step id>`).
9. **Decomposition before and after reconstruction.** Before: 2D segmentation of the exact conditioning crop
   (pixel-identical to what DINOv3 sees, through a new optional engine method `prepare_conditioning`). After:
   registration of the input view to the mesh, face-ID lifting of 2D labels to faces, propagation, and the
   physical/region partition on the exported mesh. Without any segmentation model, a **geometry-only** strategy
   (connected components on the welded mesh) still produces real, honest output.
10. **Provider seams plus a model registry.** Text-to-image, 2D segmentation and mesh labelling are pluggable
    `AuxEngine`s with honest states (`not_configured`, `missing_dependency`, `not_installed`, `gated`, …) and
    licence metadata. Nothing is downloaded or installed until the user approves a model. The plan works for any
    subset of the pending model choices (§14.6).
11. **Prompt-to-3D** = text-to-image (on the GPU thread, before any TRELLIS stage) → the normal image path. The
    generated image is imported into a new reference set under a reserved image id, so `Job.reference_set_id` and
    `primary_image_id` stay non-null strings and a legacy-UI retry becomes a deterministic image job.
12. **New endpoints are additive.** `GET /features`, `GET /taxonomy`, `GET /models`, part routes under
    `/jobs/{id}/parts…`, a whitelisted `GET /jobs/{id}/files/{path}`, and `POST /jobs/{id}/parts/rebuild`.
    `/system`, `limits`, `defaults`, `EngineCapabilities.to_dict()`, the stage table and the error envelope are
    untouched. All new request fields are optional.
13. **Tuning stays off, server-side.** New setting `tuning_enabled=False` makes `POST /jobs/{id}/tunings` return 503
    `feature_disabled`. `/features.mesh_tuning.enabled=false` hides tuning in all four UIs. A `PartConsumer`
    registry exists for future refinement but refuses every registration while `part_refinement_enabled=False`.
14. **Torch isolation stays.** The new `backend/parts/` library is torch-free (numpy, scipy, trimesh, PIL,
    opencv-headless). All torch, diffusers, transformers and nvdiffrast code lives under `backend/inference/`.

---------------------------------------------------------------------------------------------------

## 1. Scope, constraints and how this design meets them

| Hard constraint | How it is met |
|---|---|
| Image-to-3D path and `model.glb` unchanged when part features are off | `PipelineRouter` calls the existing `ImageTo3DPipeline.run(job, reporter, cancel)` with no overrides. `GenerationRequest` gains defaulted fields only (`prepared=None`, `capture=frozenset()`), and TRELLIS code takes the new branch only when they are set. `job.json` dump excludes default-valued new fields. Tests: §21.1. |
| Do not change `JobStage` list, percent formula, `/system` keys | No new stage. `compute_percent` untouched. New plan has its own `pipeline.percent`. Feature discovery lives at `GET /features`. |
| Additive, backward-compatible API (`extra="forbid"` requests) | Every new request field is optional with a default. `settings.parts` is excluded from the stored settings when absent (round-trip invariant for the legacy retry, `frontend/src/store/jobStore.ts:225-245`). |
| Single GPU worker thread; sequential model loading with offload | Every GPU call (T2I, segmentation, TRELLIS, mesh labelling) runs inside `pipeline.run` on the existing `gpu-worker` executor. A `GpuResidency` coordinator offloads the previous engine before the next one runs. |
| No new heavy native deps | Lifting uses nvdiffrast 0.4.0 (already built) and torch. Partition, graph and glTF use numpy, scipy, trimesh 5.1.1 (installed). No spconv, kaolin, flash-attn or xatlas. |
| Never upgrade transformers or huggingface-hub | Segmentation baselines are transformers 4.57.6-native classes. T2I, when approved, uses `diffusers==0.39.0` and `accelerate==1.15.0` installed with a constraints file (environment audit §9). The registry refuses text encoders whose config says `transformers_version >= 5`. |
| Tuning stays disabled and flagged; no new tuning algorithms | `tuning_enabled=False`, `part_refinement_enabled=False`. No refinement code ships. The manifest only **describes** per-part refinement state. |
| No fake or mock data in the app | Unavailable providers report why. Geometry-only output is real data. Test doubles live only under `test/`. |
| Model choices pending | Registry plus providers plus capability codes. §14.6 shows what each approved subset yields. |
| Blender-side Python | Out of scope. Nothing here touches `backend/blender_scripts/` or `backend/launcher/`. |

**Non-goals of this milestone:** amodal completion (hidden scalp, body under clothes, eyeballs), per-part
re-generation, per-part re-bake or re-atlas, eyelash or eyebrow geometry, rigging, a "preview segmentation only"
job, cloud APIs, sidecar Python environments.

---------------------------------------------------------------------------------------------------

## 2. Asset model: concepts and invariants

### 2.1 Concepts

| Concept | Definition | Where it lives |
|---|---|---|
| **Asset** | Everything generated from one input (image or prompt), across revisions. `asset_id` = id of the first job that produced the base mesh. | `manifest.asset.asset_id` |
| **Asset revision** | One job's output. Immutable once the job is terminal. | `jobs/<job>/` |
| **Base mesh** | `model.glb` exactly as TRELLIS export wrote it: 1 mesh, 1 material, 2 textures, glTF Y-up, unit AABB. | `jobs/<job>/model.glb` |
| **Part layer** | Manifest plus binary buffers plus derived GLBs, masks and thumbnails. Optional. | `jobs/<job>/parts/` |
| **Base face space** | Faces of the base mesh in glTF index-accessor order (`trimesh.load(..., process=False)` order). Index `f ∈ [0, F)`. | implicit |
| **Base vertex space** | Vertices of the base mesh in accessor order. It contains UV-seam duplicates (39–50 % of vertices, trellis2_internals §6.2 [V]). | implicit |
| **Welded vertex space** | Base vertices grouped by bit-identical position. `weld_map[v] = w`. Cuts, seams and adjacency use welded ids. | buffer `weld_map` |
| **Part** | A semantic unit with a stable id `pt_<12 hex>`, a taxonomy class and a kind. | `manifest.parts[]` |
| **Mesh node** | A part that owns faces: the host and every physical part. Index into `manifest.meshes[]`. | `face_owner` values |
| **Relation** | A typed edge between two parts (attached_to, symmetric_with, covers, occludes, …). | `manifest.relations[]` |
| **Seam** | The ordered boundary loops created by cutting a physical part out of its host. | `manifest.seams[]` + buffer `seam_loops` |

### 2.2 Invariants (each one has a test, §21)

| Id | Invariant |
|---|---|
| I1 Base immutability | No step ever rewrites `model.glb`. The manifest records `base.sha256`, and the export step re-hashes the file before publishing. |
| I2 Stable face space | All per-face buffers have length `F = base.faces`. Two revisions with the same `base.sha256` share the face space, so face sets can be compared across revisions (lineage by face IoU, §8). |
| I3 Complete partition | `face_owner[f]` is defined for every `f`. Each face belongs to exactly one mesh node. The sum of mesh face counts equals `F`. |
| I4 Lossless reassembly | For each mesh node, faces are the base faces with `face_owner == k` in **ascending base order**, and vertices are `vertex_map[k]` (ascending base vertex ids). Concatenating all nodes and mapping through the vertex maps reproduces the base positions, UVs, normals and face set bit-exactly. |
| I5 Labels ≠ ownership | `face_labels` (finest semantic part per face) is independent of `face_owner`. Regions and deferred parts never change `face_owner`. |
| I6 One atlas | Every mesh node references material 0 of the base. `assembled.glb` contains exactly the base number of materials and images. |
| I7 Explicit frames | Every coordinate is in the `asset` frame unless the record names another frame (§7). |
| I8 Honest provenance | Every part lists `sources[]` with method, provider and score. Unlabelled foreground is `unknown`. Nothing is invented, and nothing is dropped. |
| I9 Immutable revisions | A published `parts/` folder is never modified. Changes produce a new derived job (revision n+1). |
| I10 Refinement locked | While `part_refinement_enabled=False`, every part has `refinement.locked=true` and no consumer is registered. |
| I11 Forward-compatible format | Readers ignore unknown fields. `schema` names the major version. Breaking changes require `img3d.parts/2` plus a reader-side migration. |

---------------------------------------------------------------------------------------------------

## 3. Taxonomy `img3d.taxonomy/1`

Source: segmentation research §7 (classes and mapping) and part-aware research §6.2 (policy). Implemented as data in
`backend/parts/taxonomy.py` and served read-only by `GET /api/v1/taxonomy`.

### 3.1 Groups (what the user toggles)

| Group id | Classes | If in `parts.separate` | If not | Note shown in the UI |
|---|---|---|---|---|
| `eyes` | `eye.l`, `eye.r` (fine: `.sclera`, `.iris`, `.pupil`, `.highlight` as regions of the eye part) | physical | region | "Eyes are shallow surface patches; the hidden eyeball is not generated." |
| `eyebrows` | `eyebrow.l`, `eyebrow.r` | physical_deferred | region | "Eyebrows are painted on; geometry is deferred (mask + curve now)." |
| `eyelashes` | `eyelash.l`, `eyelash.r` | physical_deferred | region | "Eyelash geometry is deferred; mask + lid curve only." |
| `hair` | `hair` (fine: `hair.front`, `.back`, `.side`, `.ahoge`, `.tail`), `hair.facial` | physical | region | "The scalp under the hair is not generated (opening recorded)." |
| `clothes` | `clothing.top`, `.outer`, `.bottom`, `.dress`, `.legwear`, `.neckwear`, `.handwear.l/.r`, `.footwear.l/.r` (coarse: `clothing`) | physical | region | "The body under clothes is not generated (opening recorded)." |
| `accessories` | `accessory.headwear`, `.eyewear`, `.earwear.l/.r`, `.jewelry`, `.belt`, `.bag`, `.other` (coarse: `accessory`) | physical | region | – |
| `detachable` | `object.held.l`, `object.held.r`, `object.detached` (coarse: `object`) | physical | region | – |
| `appendages` | `appendage.tail`, `.wings`, `.animal_ears` | physical | region | Not in the default selection. |
| `face` (region group) | `body.head.face`, `.nose`, `.ear.l/.r`, `.eyelid.l/.r`, `.mouth.lip_upper`, `.mouth.lip_lower`, `.mouth.interior` | – | region | Always regions. |
| `body` (region group) | `body.neck`, `body.torso`, `body.arm.upper.l/.r`, `body.arm.lower.l/.r`, `body.hand.l/.r`, `body.leg.upper.l/.r`, `body.leg.lower.l/.r`, `body.foot.l/.r`, `body.skin_other` | – | region | Always regions. |

Default `parts.separate` = the user's list: `eyes, eyebrows, eyelashes, hair, clothes, accessories, detachable`.
Host classes: `body` (characters) or `object` (everything else). `unknown` covers unassigned foreground.

### 3.2 Class record

```python
@dataclass(frozen=True, slots=True)
class PartClass:
    id: str                       # "eye.l", dotted, regex ^[a-z][a-z0-9_]*(\.[a-z0-9_]+)*$
    label: str                    # "Left eye"
    group: str                    # "eyes"
    parent: str | None            # default semantic parent class ("body.head.face"); None for roots
    attach_to: str | None         # default attachment class ("body.head.eyelid.l" for eyelash.l)
    side: Literal["l", "r", "c"] | None
    symmetric_pair: str | None    # "eye.r"
    kind_if_separated: PartKind   # physical | physical_deferred | region
    kind_if_not_separated: PartKind
    geometry_expectation: Literal["surface", "shallow_surface", "texture_only_likely", "thin"]
    coarse_class: str | None      # class used for granularity="coarse"
    fine_children: tuple[str, ...]
    aliases: tuple[str, ...]      # open-vocabulary phrases ("bangs", "ponytail", …) for prompting and prompt nouns
    priority: int                 # 2D fusion z-priority, higher = in front (segmentation research §7.2)
    covers_default: tuple[str, ...]  # e.g. clothing.top → ("body.torso", "body.arm.upper.l", …)
```

`Taxonomy` API (torch-free): `get(class_id)`, `classes_for_groups(groups, granularity)`,
`kind_for(class_id, separate_groups)`, `match_phrases(text) -> list[ClassMatch]` (lower-case alias match used for
prompt nouns and `extra_terms`; no NLP model), `to_out()` for the API.

---------------------------------------------------------------------------------------------------

## 4. Physical part vs semantic region: the policy

### 4.1 Rules

1. **Kind comes from the taxonomy policy, not from the segmenter.** Segmenters and labellers produce labels. The
   partitioner decides what to cut (part-aware research §6.1 principle 3).
2. **Continuous skin stays continuous.** Face and body classes are always `region` with `host` = the host part.
3. **Physical needs geometry.** A class whose policy is `physical` is cut only if its labelled faces form at least
   `parts_min_faces` faces (default 200) after cleanup. Otherwise it is downgraded to `physical_deferred` with
   warning `too_small_for_geometry`.
4. **Texture-only classes defer.** Eyebrows and eyelashes (`geometry_expectation = texture_only_likely`) are
   `physical_deferred` when selected. They get a 2D mask, a 3D region on the host, and guide curves (`lid_curve`,
   `brow_curve`) when the 2D heuristics produce them (segmentation research §5). `geometry.status` is
   `texture_only`.
5. **Separate shells are free cuts.** With `split_by_connectivity=true`, a connected component of the welded mesh
   that is not the largest component and holds less than 25 % of the faces becomes its own physical part. Its class
   is the majority lifted label inside it, or `unknown`. No seam is created because nothing is cut. Components under
   0.05 % of faces are kept with tag `floater_candidate`; they are never deleted (I8).
6. **Cut openings are recorded, never filled.** Cutting hair or clothes leaves an opening in the host. The seam
   records it (`host_opening=true`), and the host gets `geometry.occluded_geometry_missing=true`. Filling holes would
   be refinement.
7. **The double-walled shell is cut as is.** The exported mesh is a remeshed double shell (trellis2_internals §1.3).
   A physical cut therefore yields an outer and an inner sheet. Each sheet contributes its own boundary loops to the
   same seam record.
8. **Granularity** `coarse` collapses classes to `coarse_class` (`clothing`, `accessory`, `object`, `hair`).
   `standard` uses taxonomy classes. `fine` adds `fine_children` (hair sub-parts, eye sub-regions).
9. **Part budget.** At most `parts_max_parts` parts (default 64). Extra smallest physical parts are merged into
   `unknown` regions on the host, with warning `max_parts_exceeded`.

### 4.2 Deterministic partition algorithm (`parts/policy.py` + `parts/partition.py`)

```
input : base mesh arrays, face_labels (finest class per face, 0 = none), face_scores, components (welded),
        taxonomy, options (separate, granularity, split_by_connectivity, min faces, max parts)
1. classes ← labels present in face_labels, mapped through granularity
2. for each class c:   kind ← taxonomy.kind_for(c, separate)
                       if kind == physical and faces(c) < min_faces: kind ← physical_deferred (+warning)
3. if split_by_connectivity: for each welded component K (not the largest, |K| < 0.25 F):
                       if no physical class already owns K: create physical part (class = majority label in K or unknown)
4. face_owner ← 0 (host) everywhere; for each physical part p (in a stable order: priority desc, then class id):
                       face_owner[faces(p) ∧ face_owner == 0] ← mesh_index(p)
5. regions and physical_deferred parts: host ← the mesh node that owns the majority of their faces
6. seams ← boundary edges between owner k and owner j (welded) → chained into ordered loops (§5.4)
7. enforce part budget; assign label_value (1..n) in part order; write face_labels with label_values
```

---------------------------------------------------------------------------------------------------

## 5. Geometry representation and reversible splitting

### 5.1 Binary buffers

All arrays are raw little-endian files without a header, under `parts/buffers/`. The manifest describes each one
(`dtype`, `count`, `components`, `byte_length`, `sha256`, `indexes`), in the spirit of glTF accessors. Any language
can read them; NumPy reads them with `np.fromfile(path, dtype)`.

| Buffer id | dtype × components | count | Indexes | Meaning |
|---|---|---|---|---|
| `face_labels` | uint16 | F | base faces | `label_value` of the finest part that contains the face (0 = none) |
| `face_owner` | uint16 | F | base faces | index into `meshes[]` (0 = host) |
| `face_scores` | uint8 | F | base faces | label confidence × 255 (255 for geometry-only certainty such as separate shells) |
| `weld_map` | uint32 | V | base vertices | welded vertex id |
| `vmap_<pt_id>` | uint32 | Vk | mesh-k vertices | base vertex id of each vertex of mesh node k (ascending) |
| `seam_loops` | uint32 | S | seam vertices | welded vertex ids of every loop, concatenated; loops are slices `{start, count}` |
| `guides_<pt_id>` | float32 × 3 | Gk | none | concatenated 3D polylines of part guides (asset frame); slices in `guides[]` |

The face list of mesh k is not stored: it is `np.flatnonzero(face_owner == k)` (ascending), which keeps
I4 cheap and unambiguous.

### 5.2 Welding

`weld_map = np.unique(positions.view(np.void(12)), return_inverse=True)[1]`. Bit-identical positions only. UV-seam
duplicates written by `uv_unwrap` copy the same simplified vertex (trellis2_internals §1.3 step 3 [V]), so they weld
exactly. Welded ids are used for adjacency, connected components, seams and propagation. They are never used to
reorder anything in the output meshes.

### 5.3 Splitting and reassembly

```python
# parts/partition.py
def build_mesh_nodes(base: BaseMesh, face_owner: np.ndarray) -> list[MeshNode]:
    """For each owner k: faces = flatnonzero(face_owner == k) (ascending); vertex_map = unique(base.faces[faces]);
    local faces = searchsorted(vertex_map, base.faces[faces]). Positions, normals and UVs are gathered by
    vertex_map. Nothing is welded, recomputed or reordered."""

# parts/reassemble.py
def reassemble(manifest: PartManifest, root: Path) -> BaseMesh:
    """Inverse of build_mesh_nodes using vmap_* buffers and face_owner. Used by validation and by future consumers."""
def verify_reassembly(manifest, root, base) -> ReassemblyReport  # exact equality of faces, positions, uv, normals
```

### 5.4 Seams (boundary loops)

- A **boundary edge** of mesh k is a welded edge used by exactly one face of k and by at least one face owned by
  another mesh j. Edges that are already open on the base mesh are recorded separately as `base_open`; they are not
  seams.
- Boundary edges are grouped by `(k, j)` and chained into ordered loops by walking welded vertices. A loop is
  `closed` when it returns to its start. Branch points (non-manifold junctions, 2.2k–33k edges per mesh in
  trellis2_internals §6.2) end an open chain, which is recorded with `closed=false`.
- Record: `{id: "seam_<n>", a: <physical part id>, b: <host part id>, host_opening: true, boundary_edges, length,
  loops: [{start, count, closed, length}]}`.
- A consumer that closes, stitches or re-meshes a part (future refinement) must keep the loop vertex positions
  fixed when `refinement.constraints.seam_lock` is true. That is what makes later reassembly crack-free.

### 5.5 Labels on the mesh: how the canonical store is produced

- The canonical label store is per **final** face, because that is what DCC tools, viewers and the paused tuner
  consume (part-aware research §6.1 principle 2). Voxel-space labels via persisted latents (trellis2_internals
  §2.2) are an allowed future provider. The manifest records the method (`sources[].method`), so both can coexist.
- Lifting happens in the `MeshLabeler` provider (§14.2). The torch-free post-processing in `parts/labels.py` is:
  welded face-adjacency diffusion (scipy.sparse), nearest-labelled-centroid fill within `2.5 / effective_resolution`
  for disconnected sheets such as the inner wall of the double shell (scipy `cKDTree`), two passes of 1-ring
  majority smoothing, small-island cleanup, and mirror completion for symmetric pairs when the mirror error is below
  the threshold.

### 5.6 Optional UV label texture

`parts/textures/part_id_uv.png` (16-bit, nearest sampling, base atlas size): the label of each texel, written when
the labeller returns barycentric hits. It serves texture-only parts (painted brows and lashes) that are smaller than
a triangle. Manifest key `artifacts.uv_label_map`. Default off (`parts_uv_label_map=false`) until the labeller
implements it.

---------------------------------------------------------------------------------------------------

## 6. Part graph

### 6.1 Hierarchy

`parent` is the **scene and semantic parent**. It is used for the `assembled.glb` node tree and for UI outliners.

- The host has `parent = null`.
- A physical part whose taxonomy parent chain reaches a body class has `parent = host`.
- `object.detached` parts and unknown separate shells have `parent = null`. They become siblings of the host under
  the scene root.
- A region or deferred part has `parent` = its taxonomy parent part if that part exists, else its `host`.

### 6.2 Relations

| Type | Direction (a → b) | Derived from | Metrics | Default confidence |
|---|---|---|---|---|
| `attached_to` | physical → region or part it is attached to | longest shared seam (`via` = seam id); or, for separate shells, nearest region within `contact_eps` (2 voxels) | `contact: seam\|proximity`, `shared_length`, `distance` | from labels |
| `region_of` | region / physical_deferred → host mesh part | `host` field (redundant, explicit for graph consumers) | `faces` | 1.0 |
| `adjacent_to` | region ↔ region (stored once, a < b) | welded edges between differently-labelled faces on the same mesh | `border_length`, `border_edges` | 1.0 |
| `symmetric_with` | `.l` ↔ `.r` pair | taxonomy `symmetric_pair`, both present | `plane: "x=0"`, `centroid_error`, `area_ratio`, `chamfer` | geometric |
| `covers` | clothing / hair → body region | taxonomy `covers_default` ∧ the regions exist ∧ overlap in the input view | `overlap_px` | 0.5 (`source: taxonomy_default`) |
| `occludes` | a in front of b in the input view | depth comparison along shared 2D mask borders in the registered face-ID render | `view: "input"`, `border_px`, `mean_depth_gap` | geometric |
| `held_by` | `object.held.*` → `body.hand.*` | proximity to the hand region | `distance` | geometric |
| `expected_missing` | asset → class (b is a class id, not a part) | prompt nouns or `extra_terms` not found | – | – |

Record: `{"id": "rel_<n>", "type": …, "a": <part id>, "b": <part id or class id>, "via": <seam id or null>,
"metrics": {…}, "source": "geometry|taxonomy_default|lifted|prompt", "confidence": 0..1}`.

---------------------------------------------------------------------------------------------------

## 7. Frames, transforms and views

### 7.1 Frames (manifest `frames`)

| Frame | Convention | Notes |
|---|---|---|
| `asset` | glTF: +Y up, front = +Z, normalised units, AABB `[-0.5, 0.5]³` | Frame of `model.glb` and of every part file. |
| `trellis` | Blender: +Z up, front = −Y | Internal TRELLIS frame. `to_asset` (row-major 4×4, column vectors) = `[1,0,0,0, 0,0,1,0, 0,-1,0,0, 0,0,0,1]` (x' = x, y' = z, z' = −y; trellis2_internals §1.3 step 5 [V]). Only for future latents or voxel providers. |
| `conditioning` | Image pixels of the exact conditioning crop (≤ 1024 px; origin top-left) | `source_image_id`, `source_size`, `downscale`, `crop_box` (in downscaled source pixels, may exceed the image), `alpha_threshold` 0.8, `mode` (`alpha_crop`, `background_removed`, `untouched`). Maps masks to and from the original upload for the head-crop pass. |

### 7.2 Part frame and placement

- `frame = {pivot: [x,y,z], axes: [[…],[…],[…]], method}`. Pivot is the centroid of the seam loops (attachment
  point) for cut parts, the bbox centre for separate shells, and the area-weighted centroid for regions. Axes come from
  PCA of the part's welded vertices, made right-handed and sign-stabilised towards the asset axes. The frame is
  metadata for DCC tools and future consumers.
- `placement = {transform: <16 numbers, identity>, method: "identity", error: null}`. Part vertices stay in the asset
  frame, so reassembly is exact (subtracting and re-adding a pivot in float32 would not be bit-exact). Future
  re-generated parts use `method: "registered"` with a non-identity transform and an alignment error.

### 7.3 Views

`views[]` records each camera used for lifting:

```json
{ "id": "input", "image_frame": "conditioning",
  "camera": { "model": "perspective", "yaw_deg": 0.0, "pitch_deg": 2.0, "fov_deg": 30.0, "distance": 1.93,
              "world_to_camera": [1,0,0,0, 0,1,0,0, 0,0,1,-1.93, 0,0,0,1],
              "intrinsics": {"fx": 1.866, "fy": 1.866, "cx": 0.5, "cy": 0.5, "normalized": true},
              "convention": "opencv" },
  "fit": { "method": "silhouette_grid_refine", "iou": 0.91, "accepted": true, "candidates_tested": 1332 } }
```

Yaw and pitch are in the asset frame: the camera sits at `d·(sin yaw·cos pitch, sin pitch, cos yaw·cos pitch)` and
looks at the origin, so yaw 0 is the front view. A fit is accepted when `iou ≥ 0.6` (trellis2_internals §3.2).

### 7.4 Sides

`side` is **character-relative** (`l`, `r`, `c`). It is computed from the registered input view: for an accepted
front-ish fit, the character's left is image-right and asset +X [V by trellis2_internals §3.1]. `image_side` stores
the raw image-space side. When registration is rejected, side comes from the sign of the part centroid's x with
`sources[].method = "assumed_front"` and confidence 0.5.

---------------------------------------------------------------------------------------------------

## 8. Provenance and lineage

- **Manifest provenance**: `asset_id`, `revision`, `job_id`, `parent_job_id`, `mode` (`inline` = produced by the
  generating job; `redecompose` = derived job), `created_at`, `img3d_version`.
- **Base provenance**: engine name, model (`TRELLIS.2-4B`), `pipeline_type`, `seed`, `resolution`,
  `effective_resolution`, `texture_size`, `polygon_target`, `base.sha256`. Copied from `JobResultData` and its
  metadata.
- **Source provenance**: `kind` (`image` or `prompt`), `image_id`, `image_sha256`, and for prompts the T2I model id,
  revision, licence id, final prompt hash, seed, steps and size (§15).
- **Step provenance**: for each step, `provider`, the models used (`id`, `revision`, `licence_id`), its parameters
  and duration.
- **Per part**: `sources[]` (`method`: `lifted`, `components`, `heuristic2d`, `taxonomy_default`, `prompt`,
  `assumed_front`; plus `provider`, `model`, `score`), `confidence`, `expected`.
- **Lineage across revisions**: when a derived job re-decomposes an asset, each new part gets
  `lineage.derived_from = [{part_id, job_id, face_iou}]` for parent parts with face-set IoU ≥ 0.5. This is possible
  because I2 guarantees the same face space. Part ids are new in each re-decomposition. Future refinement revisions
  (not in this milestone) will keep part ids.
- **Licences**: `provenance.licences[]` lists every model licence involved (DINOv3, TRELLIS.2 MIT, nvdiffrast
  NVIDIA NC, plus T2I and segmentation models). The UI can then show what governs an asset.

---------------------------------------------------------------------------------------------------

## 9. Refinement state and the consumer interface (description only, disabled)

Per part:

```json
"refinement": {
  "status": "untouched",
  "locked": true,
  "lock_reason": "part_refinement_disabled",
  "needs": ["occluded_geometry"],
  "constraints": {"seam_lock": true, "keep_uv_atlas": true, "keep_frame": true},
  "history": []
}
```

- `status` values reserved by the schema: `untouched` (the only value produced now), `queued`, `running`,
  `refined`, `failed`, `rejected`.
- `needs` contains **facts**, not operations: `occluded_geometry` (opening recorded), `geometry` (deferred part),
  `closure` (open boundary), `low_confidence`. These hints let a future consumer pick work without re-analysing.
- Manifest-level `refinement = {"enabled": false, "policy": "locked_until_approved", "consumers": []}`.

Backend interface (no implementations, no registrations):

```python
# backend/parts/consumers.py (torch-free)
class PartConsumer(Protocol):
    id: str
    supported_classes: frozenset[str] | None
    def accepts(self, part: PartRecord, manifest: PartManifest) -> bool: ...
    def describe(self) -> ConsumerInfo: ...

class ConsumerRegistry:
    def __init__(self, *, enabled: bool) -> None: ...
    def register(self, consumer: PartConsumer) -> None:
        """Raises FeatureDisabledError(feature="part_refinement") while disabled."""
    def list(self) -> tuple[ConsumerInfo, ...]: ...      # always () in this milestone

# backend/parts/reader.py (torch-free) — the read API future consumers will use
class AssetReader:
    def __init__(self, job_dir: Path) -> None: ...        # validates the manifest, checks base sha256 lazily
    manifest: PartManifest
    def base(self) -> BaseMesh: ...
    def part(self, part_id: str) -> PartGeometry: ...     # positions, faces, uv, normals, vertex_map, seams, frame
    def region_faces(self, part_id: str) -> np.ndarray: ...  # base face indices of a region or deferred part
    def seams(self, part_id: str) -> list[SeamLoops]: ...
```

---------------------------------------------------------------------------------------------------

## 10. Manifest `img3d.parts/1`

### 10.1 Rules

- File `parts/manifest.json`, UTF-8, written atomically (temp + `os.replace`) inside the staging folder before the
  folder is published (§12.2).
- **All `uri` values are relative to the job directory** (the asset root), for example `model.glb`,
  `parts/assembled.glb`, `parts/buffers/face_labels.bin`. The same relative layout is used inside the ZIP export, and
  `GET /jobs/{id}/files/{uri}` serves exactly these paths (§17.4).
- Pydantic models in `backend/parts/model.py` use `extra="ignore"` for reading. The `schema` key maps to the field
  `schema_id` (alias `schema`), because `schema` shadows a `BaseModel` attribute.
- Arrays never go into JSON (I11). Only buffer references do.

### 10.2 Top-level fields

| Field | Type | Meaning |
|---|---|---|
| `schema` | `"img3d.parts/1"` | format id |
| `taxonomy` | `"img3d.taxonomy/1"` | taxonomy version used |
| `asset` | `{asset_id, revision, job_id, parent_job_id, mode}` | identity and revision (§8) |
| `created_at` | ISO-8601 UTC | |
| `domain` | `character` \| `object` \| `unknown` | from the segmentation subject or prompt preset; `unknown` for geometry-only |
| `base` | `{uri, sha256, bytes, vertices, faces, materials, textures, frame, face_order: "gltf_indices", vertex_order: "gltf_accessor"}` | base mesh |
| `frames` | §7.1 | |
| `views` | §7.3 | `[]` when nothing was registered |
| `segmentation` | `{provider, subject, space: "conditioning", label_map, overlay, palette[], instances[], vocabulary[]}` or `null` | 2D layer |
| `strategy` | `{decomposition: "components" \| "lifted+components", granularity, separate[], split_by_connectivity, labeler}` | effective options |
| `meshes` | `[{index, part_id, node, uri, faces, vertices, vertex_map}]` | mesh nodes; index 0 = host |
| `parts` | `PartRecord[]` (§10.3) | |
| `relations` | §6.2 | |
| `seams` | §5.4 | |
| `buffers` | `[{id, uri, dtype, components, count, byte_length, sha256, indexes, description}]` | §5.1 |
| `artifacts` | `{assembled: {uri, sha256, bytes, nodes}, uv_label_map: …\|null}` | derived files |
| `expectations` | `{source: "prompt"\|"extra_terms"\|null, expected_classes[], missing[]}` | |
| `provenance` | §8 | |
| `refinement` | §9 | |
| `stats` | `{labelled_face_ratio, unknown_face_ratio, host_faces, physical_faces, timings{…}}` | |
| `warnings` | `[{code, message, part_id\|null}]` | codes: `registration_failed`, `segmentation_unavailable`, `segmentation_failed`, `expected_part_missing`, `too_small_for_geometry`, `max_parts_exceeded`, `low_confidence`, `charts_cross_boundary`, `alpha_from_preview` |

### 10.3 Part record

| Field | Type | Required | Meaning |
|---|---|---|---|
| `id` | `pt_<12 hex>` | yes | stable id for this revision; used in file names (validated with `is_valid_id(id, "pt")`) |
| `key` | `^[a-z][a-z0-9_.]*(#[0-9]+)?$` | yes | unique human key; glTF node name (`hair`, `eye.l`, `unknown#2`) |
| `class` | taxonomy class id | yes | |
| `group` | taxonomy group id | yes | |
| `label` | string | yes | display label |
| `kind` | `host` \| `physical` \| `physical_deferred` \| `region` | yes | |
| `side` / `image_side` | `l\|r\|c\|null` / `left\|right\|center\|null` | yes | §7.4 |
| `parent` | part id \| null | yes | §6.1 |
| `host` | part id \| null | yes | mesh node that owns the faces of a region or deferred part; null for host and physical |
| `label_value` | 1..65535 | yes | value in `face_labels` |
| `mesh_index` | int \| null | yes | index into `meshes[]` for host and physical parts |
| `faces` / `area` | int / float | yes | faces carrying this label; surface area in asset units² |
| `bbox` / `centroid` | `[[x,y,z],[x,y,z]]` / `[x,y,z]` | yes | asset frame |
| `frame` / `placement` | §7.2 | yes | |
| `geometry` | `{status: surface\|open_surface\|closed\|texture_only\|absent, watertight, open_boundary_loops, occluded_geometry_missing, components}` | yes | honest geometry facts |
| `uv` | `{material: 0, atlas: "shared", uv_bbox, charts_cross_boundary}` | yes | `charts_cross_boundary=true` for cut parts (Option A split, trellis2_internals §4.2) |
| `seams` | seam ids | yes | |
| `masks` | `{mask2d: uri\|null, thumb: uri\|null}` | yes | conditioning-space mask and a masked crop thumbnail |
| `guides` | `[{name, type: "polyline2d"\|"polyline3d", space, points?, buffer?, start?, count?}]` | yes | e.g. `lid_curve`, `brow_curve` |
| `sources` / `confidence` / `expected` / `tags` | §8 | yes | |
| `refinement` | §9 | yes | |
| `lineage` | `{derived_from: [{part_id, job_id, face_iou}]}` | yes | `[]` for inline revisions |

### 10.4 Example (abridged: one part of each kind; hashes shortened)

```json
{
  "schema": "img3d.parts/1",
  "taxonomy": "img3d.taxonomy/1",
  "asset": {"asset_id": "job_9f3c2a1b4d5e", "revision": 1, "job_id": "job_9f3c2a1b4d5e",
            "parent_job_id": null, "mode": "inline"},
  "created_at": "2026-10-20T10:04:31Z",
  "domain": "character",
  "base": {"uri": "model.glb", "sha256": "4b1d0c…", "bytes": 82113340, "vertices": 1697772, "faces": 1895691,
           "materials": 1, "textures": 2, "frame": "asset", "face_order": "gltf_indices",
           "vertex_order": "gltf_accessor"},
  "frames": {
    "asset": {"convention": "gltf", "up": "+Y", "front": "+Z", "units": "normalized",
              "aabb": [[-0.5, -0.5, -0.5], [0.5, 0.5, 0.5]]},
    "trellis": {"convention": "blender", "up": "+Z", "front": "-Y",
                "to_asset": [1, 0, 0, 0, 0, 0, 1, 0, 0, -1, 0, 0, 0, 0, 0, 1]},
    "conditioning": {"uri": "intermediates/conditioning_rgba.png", "width": 1000, "height": 1000,
                     "source_image_id": "img_7c1e0d9a2b3f", "source_size": [2048, 3072], "downscale": 0.3333333,
                     "crop_box": [-158, 12, 842, 1012], "alpha_threshold": 0.8, "mode": "alpha_crop"}
  },
  "views": [{"id": "input", "image_frame": "conditioning",
             "camera": {"model": "perspective", "yaw_deg": 0.0, "pitch_deg": 2.0, "fov_deg": 30.0, "distance": 1.93,
                        "world_to_camera": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, -1.93, 0, 0, 0, 1],
                        "intrinsics": {"fx": 1.866, "fy": 1.866, "cx": 0.5, "cy": 0.5, "normalized": true},
                        "convention": "opencv"},
             "fit": {"method": "silhouette_grid_refine", "iou": 0.91, "accepted": true, "candidates_tested": 1332}}],
  "segmentation": {"provider": "gdino_sam2", "subject": "anime_character", "space": "conditioning",
                   "label_map": {"uri": "parts/segmentation/labels.png", "dtype": "uint16"},
                   "overlay": {"uri": "parts/segmentation/overlay.webp"},
                   "palette": [{"value": 1, "class": "hair"}, {"value": 2, "class": "eye.l"},
                               {"value": 3, "class": "eyebrow.l"}, {"value": 4, "class": "body.head.face"}],
                   "instances": [{"value": 2, "class": "eye.l", "score": 0.83, "bbox": [612, 233, 670, 262],
                                  "model": "sam2.1-hiera-large", "mask": "parts/masks/pt_3c4d5e6f7a8b.png"}],
                   "vocabulary": ["hair", "eye", "eyebrow", "face", "jacket", "earring"]},
  "strategy": {"decomposition": "lifted+components", "granularity": "standard",
               "separate": ["eyes", "eyebrows", "eyelashes", "hair", "clothes", "accessories", "detachable"],
               "split_by_connectivity": true, "labeler": "nvdiffrast_lift"},
  "meshes": [
    {"index": 0, "part_id": "pt_a0b1c2d3e4f5", "node": "body", "uri": "parts/meshes/pt_a0b1c2d3e4f5.glb",
     "faces": 1402233, "vertices": 1271110, "vertex_map": "vmap_pt_a0b1c2d3e4f5"},
    {"index": 1, "part_id": "pt_1a2b3c4d5e6f", "node": "hair", "uri": "parts/meshes/pt_1a2b3c4d5e6f.glb",
     "faces": 410112, "vertices": 377020, "vertex_map": "vmap_pt_1a2b3c4d5e6f"},
    {"index": 2, "part_id": "pt_3c4d5e6f7a8b", "node": "eye.l", "uri": "parts/meshes/pt_3c4d5e6f7a8b.glb",
     "faces": 2210, "vertices": 1301, "vertex_map": "vmap_pt_3c4d5e6f7a8b"},
    {"index": 3, "part_id": "pt_9e8d7c6b5a4f", "node": "unknown#1", "uri": "parts/meshes/pt_9e8d7c6b5a4f.glb",
     "faces": 81136, "vertices": 70422, "vertex_map": "vmap_pt_9e8d7c6b5a4f"}
  ],
  "parts": [
    {"id": "pt_a0b1c2d3e4f5", "key": "body", "class": "body", "group": "body", "label": "Body", "kind": "host",
     "side": "c", "image_side": "center", "parent": null, "host": null, "label_value": 1, "mesh_index": 0,
     "faces": 1402233, "area": 1.912, "bbox": [[-0.39, -0.5, -0.19], [0.39, 0.5, 0.2]], "centroid": [0.0, -0.04, 0.0],
     "frame": {"pivot": [0.0, -0.04, 0.0], "axes": [[1, 0, 0], [0, 1, 0], [0, 0, 1]], "method": "pca"},
     "placement": {"transform": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1], "method": "identity", "error": null},
     "geometry": {"status": "open_surface", "watertight": false, "open_boundary_loops": 6,
                  "occluded_geometry_missing": true, "components": 3},
     "uv": {"material": 0, "atlas": "shared", "uv_bbox": [0.0, 0.0, 1.0, 1.0], "charts_cross_boundary": true},
     "seams": ["seam_0", "seam_1"], "masks": {"mask2d": null, "thumb": null}, "guides": [],
     "sources": [{"method": "components", "provider": "img3d", "model": null, "score": 1.0}],
     "confidence": 1.0, "expected": false, "tags": [],
     "refinement": {"status": "untouched", "locked": true, "lock_reason": "part_refinement_disabled",
                    "needs": ["occluded_geometry"], "constraints": {"seam_lock": true, "keep_uv_atlas": true,
                    "keep_frame": true}, "history": []},
     "lineage": {"derived_from": []}},
    {"id": "pt_1a2b3c4d5e6f", "key": "hair", "class": "hair", "group": "hair", "label": "Hair", "kind": "physical",
     "side": "c", "image_side": "center", "parent": "pt_a0b1c2d3e4f5", "host": null, "label_value": 2,
     "mesh_index": 1, "faces": 410112, "area": 0.611, "bbox": [[-0.31, 0.12, -0.2], [0.31, 0.5, 0.17]],
     "centroid": [0.0, 0.33, -0.02],
     "frame": {"pivot": [0.0, 0.29, 0.01], "axes": [[1, 0, 0], [0, 1, 0], [0, 0, 1]], "method": "seam_centroid"},
     "placement": {"transform": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1], "method": "identity", "error": null},
     "geometry": {"status": "open_surface", "watertight": false, "open_boundary_loops": 4,
                  "occluded_geometry_missing": false, "components": 2},
     "uv": {"material": 0, "atlas": "shared", "uv_bbox": [0.02, 0.41, 0.97, 0.99], "charts_cross_boundary": true},
     "seams": ["seam_0"], "masks": {"mask2d": "parts/masks/pt_1a2b3c4d5e6f.png",
                                    "thumb": "parts/thumbs/pt_1a2b3c4d5e6f.webp"},
     "guides": [], "sources": [{"method": "lifted", "provider": "gdino_sam2", "model": "sam2.1-hiera-large",
                                "score": 0.88}],
     "confidence": 0.88, "expected": false, "tags": [],
     "refinement": {"status": "untouched", "locked": true, "lock_reason": "part_refinement_disabled",
                    "needs": ["closure"], "constraints": {"seam_lock": true, "keep_uv_atlas": true,
                    "keep_frame": true}, "history": []},
     "lineage": {"derived_from": []}},
    {"id": "pt_5f6e7d8c9b0a", "key": "eyebrow.l", "class": "eyebrow.l", "group": "eyebrows",
     "label": "Left eyebrow", "kind": "physical_deferred", "side": "l", "image_side": "right",
     "parent": "pt_a0b1c2d3e4f5", "host": "pt_a0b1c2d3e4f5", "label_value": 4, "mesh_index": null, "faces": 1890,
     "area": 0.0012, "bbox": [[0.03, 0.27, 0.13], [0.09, 0.29, 0.16]], "centroid": [0.06, 0.28, 0.15],
     "frame": {"pivot": [0.06, 0.28, 0.15], "axes": [[1, 0, 0], [0, 1, 0], [0, 0, 1]], "method": "area_centroid"},
     "placement": {"transform": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1], "method": "identity", "error": null},
     "geometry": {"status": "texture_only", "watertight": false, "open_boundary_loops": 0,
                  "occluded_geometry_missing": false, "components": 1},
     "uv": {"material": 0, "atlas": "shared", "uv_bbox": [0.61, 0.70, 0.64, 0.71], "charts_cross_boundary": false},
     "seams": [], "masks": {"mask2d": "parts/masks/pt_5f6e7d8c9b0a.png", "thumb": "parts/thumbs/pt_5f6e7d8c9b0a.webp"},
     "guides": [{"name": "brow_curve", "type": "polyline3d", "space": "asset", "buffer": "guides_pt_5f6e7d8c9b0a",
                 "start": 0, "count": 24}],
     "sources": [{"method": "lifted", "provider": "gdino_sam2", "model": "sam2.1-hiera-large", "score": 0.61}],
     "confidence": 0.61, "expected": false, "tags": [],
     "refinement": {"status": "untouched", "locked": true, "lock_reason": "part_refinement_disabled",
                    "needs": ["geometry"], "constraints": {"seam_lock": true, "keep_uv_atlas": true,
                    "keep_frame": true}, "history": []},
     "lineage": {"derived_from": []}},
    {"id": "pt_c1d2e3f4a5b6", "key": "body.head.face", "class": "body.head.face", "group": "face",
     "label": "Face", "kind": "region", "side": "c", "image_side": "center", "parent": "pt_a0b1c2d3e4f5",
     "host": "pt_a0b1c2d3e4f5", "label_value": 6, "mesh_index": null, "faces": 51230, "area": 0.041,
     "bbox": [[-0.11, 0.17, 0.05], [0.11, 0.36, 0.19]], "centroid": [0.0, 0.26, 0.15],
     "frame": {"pivot": [0.0, 0.26, 0.15], "axes": [[1, 0, 0], [0, 1, 0], [0, 0, 1]], "method": "area_centroid"},
     "placement": {"transform": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1], "method": "identity", "error": null},
     "geometry": {"status": "surface", "watertight": false, "open_boundary_loops": 0,
                  "occluded_geometry_missing": false, "components": 1},
     "uv": {"material": 0, "atlas": "shared", "uv_bbox": [0.55, 0.62, 0.71, 0.80], "charts_cross_boundary": false},
     "seams": [], "masks": {"mask2d": "parts/masks/pt_c1d2e3f4a5b6.png", "thumb": "parts/thumbs/pt_c1d2e3f4a5b6.webp"},
     "guides": [], "sources": [{"method": "lifted", "provider": "gdino_sam2", "model": "sam2.1-hiera-large",
                                "score": 0.9}],
     "confidence": 0.9, "expected": false, "tags": [],
     "refinement": {"status": "untouched", "locked": true, "lock_reason": "part_refinement_disabled",
                    "needs": [], "constraints": {"seam_lock": true, "keep_uv_atlas": true, "keep_frame": true},
                    "history": []},
     "lineage": {"derived_from": []}}
  ],
  "relations": [
    {"id": "rel_0", "type": "attached_to", "a": "pt_1a2b3c4d5e6f", "b": "pt_a0b1c2d3e4f5", "via": "seam_0",
     "metrics": {"contact": "seam", "shared_length": 1.84}, "source": "geometry", "confidence": 0.88},
    {"id": "rel_1", "type": "region_of", "a": "pt_c1d2e3f4a5b6", "b": "pt_a0b1c2d3e4f5", "via": null,
     "metrics": {"faces": 51230}, "source": "geometry", "confidence": 1.0},
    {"id": "rel_2", "type": "occludes", "a": "pt_1a2b3c4d5e6f", "b": "pt_c1d2e3f4a5b6", "via": null,
     "metrics": {"view": "input", "border_px": 412, "mean_depth_gap": 0.006}, "source": "geometry",
     "confidence": 0.8}
  ],
  "seams": [{"id": "seam_0", "a": "pt_1a2b3c4d5e6f", "b": "pt_a0b1c2d3e4f5", "host_opening": true,
             "boundary_edges": 3122, "length": 1.84,
             "loops": [{"start": 0, "count": 1561, "closed": true, "length": 0.93},
                       {"start": 1561, "count": 1561, "closed": true, "length": 0.91}]}],
  "buffers": [
    {"id": "face_labels", "uri": "parts/buffers/face_labels.bin", "dtype": "uint16", "components": 1,
     "count": 1895691, "byte_length": 3791382, "sha256": "a1c9…", "indexes": "base_faces",
     "description": "label_value of the finest part per base face"},
    {"id": "face_owner", "uri": "parts/buffers/face_owner.bin", "dtype": "uint16", "components": 1,
     "count": 1895691, "byte_length": 3791382, "sha256": "77e0…", "indexes": "base_faces",
     "description": "mesh index per base face"},
    {"id": "weld_map", "uri": "parts/buffers/weld_map.bin", "dtype": "uint32", "components": 1,
     "count": 1697772, "byte_length": 6791088, "sha256": "0c3b…", "indexes": "base_vertices",
     "description": "welded vertex id per base vertex"},
    {"id": "seam_loops", "uri": "parts/buffers/seam_loops.bin", "dtype": "uint32", "components": 1,
     "count": 3122, "byte_length": 12488, "sha256": "5d2f…", "indexes": "welded_vertices",
     "description": "concatenated seam loops"}
  ],
  "artifacts": {"assembled": {"uri": "parts/assembled.glb", "sha256": "9b7a…", "bytes": 84522108, "nodes": 5},
                "uv_label_map": null},
  "expectations": {"source": null, "expected_classes": [], "missing": []},
  "provenance": {"img3d_version": "1.0.0",
                 "base": {"engine": "trellis2", "model": "TRELLIS.2-4B", "pipeline_type": "1536_cascade", "seed": 123456,
                          "resolution": 1536, "effective_resolution": 1536, "texture_size": 4096,
                          "polygon_target": 2000000},
                 "source": {"kind": "image", "image_id": "img_7c1e0d9a2b3f", "image_sha256": "e41a…"},
                 "steps": [{"id": "segmentation", "provider": "gdino_sam2",
                            "models": [{"id": "grounding-dino-base", "revision": "12bdfa3…", "license_id": "apache-2.0"},
                                       {"id": "sam2.1-hiera-large", "revision": "a7c2b0e…", "license_id": "apache-2.0"}],
                            "duration_s": 2.4},
                           {"id": "part_extraction", "provider": "nvdiffrast_lift", "models": [], "duration_s": 18.7}],
                 "licences": ["mit", "dinov3", "nvidia-source-code-nc", "apache-2.0"]},
  "refinement": {"enabled": false, "policy": "locked_until_approved", "consumers": []},
  "stats": {"labelled_face_ratio": 0.94, "unknown_face_ratio": 0.06, "host_faces": 1402233,
            "physical_faces": 493458, "timings": {"registration": 3.1, "lifting": 6.8, "partition": 4.2,
            "export": 9.6}},
  "warnings": [{"code": "charts_cross_boundary", "message": "UV charts of cut parts may cross part borders.",
                "part_id": null}]
}
```

---------------------------------------------------------------------------------------------------

## 11. GLB outputs

| File | Content | Written when |
|---|---|---|
| `model.glb` | Untouched base. | always (engine) |
| `parts/assembled.glb` | One scene. Root node `asset` → `body` (host mesh) → children per physical part (§6.1); detached and unknown shells are root children. Every node mesh references the **same** `PBRMaterial` object, so trimesh writes one material and one set of WebP images (dedupe by hash, trellis2_internals §4.2 [V code]). Node `extras.img3d = {part_id, key, class, kind, parent, seams[]}`. Scene `extras.img3d = {schema, asset_id, revision, job_id, manifest: "parts/manifest.json"}`. The host primitive carries `_FEATURE_ID_0` (uint16 per vertex = majority `label_value` of incident faces; approximate at region borders; the authoritative per-face labels are in `face_labels`). | `parts.export.assembled_glb` (default true) |
| `parts/meshes/<pt_id>.glb` | One mesh node per host or physical part, asset frame, identity transform, base material (each file embeds the atlas images). Scene extras carry `{schema, asset_id, revision, job_id, part_id, key, class, kind, frame: "asset"}`. | `parts.export.part_files` (default `settings.parts_write_part_files` = true) |
| `parts/masks/<pt_id>.png` | 8-bit mask in conditioning space. | when a 2D mask exists |
| `parts/thumbs/<pt_id>.webp` | Masked crop of the conditioning RGBA, max side 256. Used by Node-UI and mobile part lists (the viewer is a singleton, frontend map §2.3 [V]). | when a 2D mask exists |
| `parts/segmentation/labels.png`, `overlay.webp` | 16-bit label map and a colour overlay. | when segmentation ran |

Construction rule: the writer builds each mesh from arrays (`positions[vmap]`, remapped faces, `uv[vmap]`,
`normals[vmap]`) with `trimesh.Trimesh(process=False)` and **does not** rely on `submesh` internals, so vertex and
face order are explicit (I4). `EXT_mesh_features` is not emitted in v1 because trimesh support for primitive-level
extensions is unverified [A]. `_FEATURE_ID_0` is emitted through `vertex_attributes` (trimesh writes the `_` prefix
[V part-aware research §1]); its component type must be `UNSIGNED_SHORT`, which `test_gltf_export.py` checks [A].

Disk cost [A]: `assembled.glb` ≈ `model.glb` (41–86 MB). Per-part files add about one more `model.glb` worth of
geometry plus one atlas copy per file.

---------------------------------------------------------------------------------------------------

## 12. Storage layout

### 12.1 Job folder (everything below is removed by the existing job delete and retention, unchanged)

```
runtime/jobs/<job>/
  job.json, inputs/<img>.png, preview.png, model.glb          (unchanged; legacy jobs have only these)
  source/                    prompt jobs only
    t2i_0.png                raw T2I output (RGB or RGBA)
    t2i.json                 T2I request + metadata (model id, revision, licence id, seed, steps, size, timings)
  intermediates/             part-aware jobs only
    conditioning_rgb.png     exact RGB fed to DINOv3 (lossless PNG)
    conditioning_rgba.png    same crop with alpha
    conditioning.json        CropTransform + preprocess mode
    latents.npz              optional (settings.parts_capture_latents=false by default)
  parts/                     published atomically (§12.2)
    manifest.json
    assembled.glb
    meshes/<pt_id>.glb
    buffers/*.bin
    masks/<pt_id>.png
    thumbs/<pt_id>.webp
    segmentation/labels.png, overlay.webp, head_<n>.png
    textures/part_id_uv.png  optional
  exports/parts.zip          cached ZIP export, built on first request (§17.4)
```

Derived jobs (`part_decomposition`) create `inputs/`, `preview.png`, `model.glb`, `intermediates/*` and
`source/*` as **hard links** to the parent's files (`os.link`; same NTFS volume), falling back to a copy. Deleting the
parent never breaks the child (snapshot rule, contract §6).

### 12.2 Atomic publish

1. The pipeline creates `runtime/temp/parts/<job>_<6 hex>/` and writes everything there.
2. Validation (§13.4 export step) runs on the staging folder.
3. `os.replace(staging, jobs/<job>/parts)` publishes it. Both folders are under `runtime/` on the same volume, so the
   rename is atomic. An existing `parts/` is an error (I9).
4. On failure or cancel the staging folder is removed. `RuntimePaths.purge_temp()` at startup removes leftovers.

### 12.3 New `RuntimePaths` helpers (`storage/paths.py`)

`job_source(job_id)`, `job_intermediates(job_id)`, `job_parts(job_id)`, `job_exports(job_id)`,
`temp_parts` (`temp/parts`). Constants `JOB_SOURCE_DIR="source"`, `JOB_INTERMEDIATES_DIR="intermediates"`,
`JOB_PARTS_DIR="parts"`, `JOB_EXPORTS_DIR="exports"`.

---------------------------------------------------------------------------------------------------

## 13. Pipeline derived from the asset model

### 13.1 Job kinds and plans

| Kind | Created by | Steps (in order; R = required, O = optional) | Result |
|---|---|---|---|
| `image_to_3d`, no `settings.parts` | `POST /jobs` (today's request) | none: `pipeline = null`, legacy path | today's `JobResultData` |
| `image_to_3d` + `parts.enabled` | `POST /jobs` with `settings.parts` | `input_image` R → `segmentation` O → `image_to_3d` R → `part_extraction` O → `export` O | base result + `parts` summary |
| `prompt_to_3d` | `POST /jobs` with `source.type="prompt"` | `prompt` R → `input_image` R → [`segmentation` O] → `image_to_3d` R → [`part_extraction` O → `export` O] | same as image jobs; bracketed steps only when `settings.parts.enabled` |
| `part_decomposition` | `POST /jobs/{id}/parts/rebuild` | `input_image` R → `segmentation` O → `part_extraction` R → `export` R | parent's base result (copied) + new `parts` summary |

`input_image` in a prompt job without parts is still listed, because it produces the conditioning that the
`image_to_3d` step uses; without parts it simply calls the legacy engine path (no prepared conditioning).

### 13.2 Step specification

| Step id (= node `img3d.<id>`) | Weight | Legacy stage reported | Inputs | Outputs (`RunContext.artifacts`) | GPU engine | Provider kind |
|---|---|---|---|---|---|---|
| `prompt` | 6 | `preprocessing` (T2I sampler steps as `step/total`) | `job.source.prompt` | `source/t2i_0.png`, `inputs/<reserved img>.png`, imported reference image | `TextToImageEngine` | text_to_image |
| `input_image` | 2 | `preprocessing` | primary input | `PreparedConditioning` (`intermediates/*`) | reconstruction engine (`prepare_conditioning`) | – |
| `segmentation` | 6 | `preprocessing` | conditioning RGBA, source image + crop transform, vocabulary | `SegmentationOutput` (label map, instances, masks) | `SegmentationEngine` | segmentation_2d |
| `image_to_3d` | 74 | engine stages + `finalizing` (stats) | job, `PreparedConditioning` | `model.glb`, `preview.png`, base `JobResultData` | reconstruction engine (`generate`) | – |
| `part_extraction` | 9 | `finalizing` | `model.glb`, conditioning, segmentation output | `PartitionResult` (labels, owners, parts, relations, seams) | `MeshLabeler` (when lifting) | mesh_labeling |
| `export` | 3 | `finalizing` | `PartitionResult` | staged `parts/` validated and published | none (CPU) | – |

### 13.3 Router and pipeline (signatures)

```python
# backend/pipelines/router.py
class PipelineRouter(GenerationPipeline):
    def __init__(self, legacy: ImageTo3DPipeline, part_aware: PartAwarePipeline | None) -> None: ...
    def run(self, job: Job, reporter: ProgressReporter, cancel: CancellationToken) -> JobResultData:
        if job.kind is JobKind.IMAGE_TO_3D and not parts_requested(job.settings):
            return self._legacy.run(job, reporter, cancel)          # byte-identical legacy path
        if self._part_aware is None:
            raise CapabilityUnavailableError("Part-aware pipeline is not installed", capability="part_extraction")
        return self._part_aware.run(job, reporter, cancel)

def parts_requested(settings: Mapping[str, Any]) -> bool:
    parts = settings.get("parts")
    return isinstance(parts, Mapping) and bool(parts.get("enabled", False))

# backend/pipelines/image_to_3d.py (additive keyword; default None keeps today's behaviour)
@dataclass(frozen=True, slots=True)
class ReconstructionOverrides:
    prepared: PreparedConditioning | None = None
    capture: frozenset[str] = frozenset()        # {"latents"} only when settings.parts_capture_latents
    capture_dir: Path | None = None

class ImageTo3DPipeline(GenerationPipeline):
    def run(self, job, reporter, cancel, *, overrides: ReconstructionOverrides | None = None) -> JobResultData: ...

# backend/pipelines/steps/base.py
class PipelineStep(ABC):
    id: ClassVar[StepId]
    label: ClassVar[str]
    weight: ClassVar[float]
    legacy_stage: ClassVar[JobStage | None]     # None = the engine reports its own stages
    def skip_reason(self, ctx: RunContext) -> str | None: return None
    @abstractmethod
    def run(self, ctx: RunContext) -> StepOutcome: ...

@dataclass(slots=True)
class StepOutcome:
    status: Literal["done", "warning"] = "done"
    message: str | None = None
    warnings: list[StepWarning] = field(default_factory=list)

# backend/pipelines/context.py
@dataclass(slots=True)
class RunContext:
    job: Job
    settings: Settings
    paths: RuntimePaths
    reporter: PlanReporter                 # ProgressReporter + step API
    cancel: CancellationToken
    engine: InferenceEngine
    aux: AuxEngineSet
    residency: GpuResidency
    taxonomy: Taxonomy
    loop: LoopBridge                       # run coroutines on the event loop from the GPU thread
    artifacts: RunArtifacts                # typed slots filled by steps
    staging: Path | None = None

# backend/pipelines/part_aware.py
class PartAwarePipeline(GenerationPipeline):
    def __init__(self, *, legacy: ImageTo3DPipeline, steps: StepRegistry, engine: InferenceEngine,
                 aux: AuxEngineSet, residency: GpuResidency, taxonomy: Taxonomy, settings: Settings,
                 paths: RuntimePaths, loop: LoopBridge) -> None: ...
    def run(self, job, reporter, cancel) -> JobResultData:
        """Runs job.pipeline.steps in order. Required-step failure re-raises (job failed). Optional-step failure marks
        the step failed, skips dependants (export after part_extraction) with reason 'dependency_failed', and the job
        completes with result.parts.status='failed'. Cancel marks the running step 'cancelled' and re-raises
        JobCancelledError. The staging folder is always removed in finally."""
```

`core/loop_bridge.py`: `LoopBridge.bind(loop)` at lifespan startup; `call(coro_factory, timeout_s=30) -> T` uses
`asyncio.run_coroutine_threadsafe(...).result(timeout)`. It is only called from the GPU thread. The loop never waits
on the GPU thread synchronously, so this cannot deadlock.

`GenerationWorker` is **not modified**: it still calls `self._pipeline.run(job, reporter, token)` on the GPU
thread. Only the object passed in `core/container.py` changes (`PipelineRouter`), and
`ServiceContainer.pipeline` is widened to `GenerationPipeline` (no test touches it, contract map §4.1 [V]).

### 13.4 What each step does

- **`prompt`** (§15): build the final prompt from the template, `residency.acquire(t2i)`, `generate_image`
  (cancel per sampler step), write `source/t2i_0.png` and `t2i.json`, normalise to `inputs/<reserved>.png` with
  `processing.image_ops.normalize_to_png`, then `ctx.loop.call(reference_service.import_image(...))`. An import
  failure is a step warning, not a job failure, because the job-local input exists.
- **`input_image`**: if `engine.extensions().prepared_conditioning`, call
  `engine.prepare_conditioning(primary_path, remove_background, out_dir=intermediates)` (BiRefNet runs here), and
  record a readiness check (alpha bbox touching the border → warning `subject_cropped`; text-to-image research
  §8.5). Otherwise it is skipped with reason `engine_has_no_prepare`, and later steps use `preview.png` with an
  alpha threshold on black (warning `alpha_from_preview`). Derived jobs reuse `intermediates/` from the parent when
  present.
- **`segmentation`**: skipped (`no_provider`) when the resolved provider is `none`. Otherwise
  `residency.acquire(segmenter)` and `segment(request)`; the vocabulary is taxonomy aliases of the requested groups
  plus regions, plus prompt nouns, plus `extra_terms`. Then the torch-free 2D heuristics run
  (`parts/heuristics2d.py`: left/right split, eye sub-regions, eyelash band, lid and brow curves; segmentation
  research §5). A provider exception marks the step `failed` (optional) and part extraction falls back to
  `components`.
- **`image_to_3d`**: `residency.acquire(engine)`, then `legacy.run(job, ctx.reporter, cancel,
  overrides=ReconstructionOverrides(prepared=…))`. The returned `JobResultData` is kept in `ctx.artifacts`.
- **`part_extraction`**: load the base arrays (`parts/mesh_io.load_base_mesh`), weld, compute connected components.
  If 2D labels exist and a labeller is available: `residency.acquire(labeler)`, `label_mesh(...)` (registration,
  face-ID raster at conditioning resolution, votes, occlusion pairs). Then `parts/labels.propagate(...)`,
  `parts/policy.plan_parts(...)`, `parts/partition.partition(...)`, `parts/graph.build_relations(...)`.
  Registration below IoU 0.6 → warning `registration_failed` and geometry-only parts.
- **`export`**: write buffers, meshes, `assembled.glb`, masks, thumbnails and manifest into staging; run
  `parts/validate.validate_staging` (I1–I6, buffer hashes, every `uri` resolves, schema check); publish atomically;
  build `PartsSummary`.

### 13.5 GPU sequencing (one job, part-aware prompt job, `low_vram=True` default)

```
gpu-worker thread ─┬─ prompt          : residency.acquire(t2i)    → T2I on GPU → maybe_free hooks → offload to CPU
                   ├─ input_image     : residency.acquire(engine) → BiRefNet (engine rembg) → back to CPU
                   ├─ segmentation    : residency.acquire(seg)    → detector/segmenter on GPU → offload to CPU
                   ├─ image_to_3d     : residency.acquire(engine) → TRELLIS stages (per-stage GPU moves, unchanged)
                   ├─ part_extraction : residency.acquire(labeler)→ nvdiffrast raster (≤2 M faces) → free
                   └─ export          : CPU only (numpy/trimesh), still on the gpu-worker thread
```

- `GpuResidency.acquire(x)` calls `offload()` on every other engine whose `holds_gpu()` is true, then
  `torch.cuda.empty_cache()` through the engine that owns torch (no torch import in pipelines).
- TRELLIS in `low_vram=True` already returns to CPU after each run (generation map §4.1 [V]). With
  `low_vram=False`, `TrellisEngine.offload()` (new, additive) moves the pipeline to CPU before T2I; that is the case
  where a ~15 GB T2I peak would not co-reside with ~16.6 GB of TRELLIS weights [A].
- RNG parity: T2I uses a private `torch.Generator`. Segmentation and labelling run before TRELLIS's
  `torch.manual_seed(seed)` (`generation.py:202` [V]) or after sampling, so same-seed reconstruction is unchanged
  (generation map §4.2 point 5).
- Aux engines stay loaded in CPU RAM between jobs (`aux_keep_loaded=true`; RAM is 127 GB [V]).
  `POST /system/engine/unload` and shutdown also unload aux engines (on the GPU thread).

### 13.6 Progress model

- `Job.progress` keeps the frozen semantics: stage names, indices, `stage_count=9`, `compute_percent`. Pre-steps
  report as `preprocessing` and post-steps as `finalizing` with a clear `message`, so legacy clients still show a
  sensible stage and monotonic percent.
- `Job.pipeline.percent` (0–100): with `A` = steps that are not `skipped`, `W = Σ weight(A)`,
  `percent = 100 · (Σ weight(finished steps in A) + weight(current) · fraction(current)) / W`, capped at 99.9 until
  the job completes, then 100. It is made monotonic with `max(previous, new)` (W can shrink when a step is skipped at
  run time).
- `fraction(image_to_3d) = min(1, compute_percent(stage, step, total) / 98)` (98 = percent at `finalizing`).
  Other steps report `step/total` through `StepProgress.update`, or 0 → 1 on finish.
- `pipeline.eta_seconds = elapsed · (100 − p) / p` once `p ≥ 5`, else null.
- Publishing: step begin and finish publish unthrottled (`reporter.mutate(..., publish=True)`); per-step progress
  rides on the existing throttled `job.updated` (≤ 5/s).

### 13.7 State machines

**Job status**: unchanged (`queued → running → completed | failed | cancelled`).

**Pipeline step status**:

```
pending ──begin──► running ──ok──────────► done
   │                  ├──ok+warnings─────► warning
   │                  ├──error (R)───────► failed      → job failed
   │                  ├──error (O)───────► failed      → dependants skipped(dependency_failed), job continues
   │                  └──cancel──────────► cancelled   → job cancelled
   └──skip_reason─────────────────────────► skipped   (no_provider | not_requested | dependency_failed |
                                                        engine_has_no_prepare | reused_from_parent)
On job failure/cancel, every step still pending becomes skipped (reason "job_stopped").
```

**Prompt image import** (`source.import_state`): `pending → imported | failed`.

**Provider state** (§14.3): `not_configured | disabled | missing_dependency | not_installed | gated |
license_not_accepted | incompatible → available → (loading → ready ⇄ offloaded) | error`.

**Part refinement status**: `untouched` only (§9).

### 13.8 Failure and cancel semantics

| Situation | Job status | `pipeline` | `result.parts` |
|---|---|---|---|
| T2I fails (prompt job) | failed (`engine_error` / provider code) | `prompt` failed, rest skipped | – |
| Segmentation fails | continues | `segmentation` failed; `part_extraction` runs `components` | `status: "partial"` + warning `segmentation_failed` |
| Registration rejected | continues | `part_extraction` warning | `status: "partial"` + warning `registration_failed` |
| Part extraction or export fails (image / prompt kinds) | **completed** (base model is valid) | step failed, `export` skipped | `status: "failed"`, `error: {code, message}` |
| Part extraction fails (`part_decomposition` kind) | failed | step failed | – |
| Cancel during any step | cancelled (existing semantics; `result` stays null) | running step cancelled | – |
| OOM in any GPU step | step failed → per the rows above (`out_of_memory` via `classify_exception`) | | |

---------------------------------------------------------------------------------------------------

## 14. Inference seams, providers and the model registry

### 14.1 Additive changes to `inference/base.py` (seam file; nothing renamed or removed)

```python
@dataclass(frozen=True, slots=True)
class CropTransform:
    source_size: tuple[int, int]          # original upload (w, h)
    downscale: float                      # applied before cropping (long side <= 1024)
    crop_box: tuple[int, int, int, int]   # x0, y0, x1, y1 in downscaled pixels (may exceed bounds)
    size: tuple[int, int]                 # conditioning image (w, h)
    alpha_threshold: float = 0.8

@dataclass(frozen=True, slots=True)
class PreparedConditioning:
    rgb_path: Path                        # exact RGB fed to the image encoder (lossless PNG)
    rgba_path: Path | None                # same crop with alpha (None in "untouched" mode)
    mode: str                             # "alpha_crop" | "background_removed" | "untouched"
    crop: CropTransform | None
    source_image_id: str

@dataclass(frozen=True, slots=True)
class EngineExtensions:
    prepared_conditioning: bool = False
    capture_latents: bool = False
    offload: bool = False

# GenerationRequest gains (defaults keep every existing construction valid):
    prepared: PreparedConditioning | None = None
    capture: frozenset[str] = frozenset()
    capture_dir: Path | None = None

# InferenceEngine gains non-abstract defaults (FakeEngine and UnavailableEngine stay valid):
    def extensions(self) -> EngineExtensions: return EngineExtensions()
    def prepare_conditioning(self, image: ConditioningImage, *, remove_background: bool, out_dir: Path,
                             reporter: ProgressReporter, cancel: CancellationToken) -> PreparedConditioning:
        raise CapabilityUnavailableError("This engine cannot prepare conditioning images",
                                         capability="prepared_conditioning")
    def offload(self) -> bool: return False
    def holds_gpu(self) -> bool: return False
```

`EngineCapabilities` and its pinned `to_dict()` are **not** touched (`test/engine/test_engine_unit.py:81` [V]).

**TRELLIS implementation (`inference/trellis/conditioning.py`, new; `engine.py`, `generation.py`, `imaging.py`
edited):**

- `conditioning.py` re-implements upstream `preprocess_image` (≈ 25 lines: downscale to ≤ 1024, rembg or real
  alpha, `alpha > 0.8·255` bbox, square crop without margin, premultiply on black) torch-free except for the rembg
  call, and returns **both** the RGB image and the RGBA crop plus `CropTransform`.
- `TrellisEngine.prepare_conditioning` runs it under `_lifecycle_lock` on the GPU thread and writes
  `conditioning_rgb.png`, `conditioning_rgba.png`, `conditioning.json`.
- `TrellisGenerationRun._preprocess`: `if request.prepared is not None:` load `rgb_path` (PNG is lossless, so the
  pixels equal the in-memory image) and skip upstream preprocessing; **else** the current code runs unchanged.
- Byte-identity proof: `test/engine/test_prepared_conditioning.py` asserts `np.array_equal(ours_rgb,
  upstream.preprocess_image(img))` for an RGBA input (no rembg) and for the rembg path with a deterministic stub
  rembg. `test_generation_parity.py` stays unchanged and green.
- Optional `capture={"latents"}` (only when `settings.parts_capture_latents`): after `decode_latent`, save
  `latents.npz` (coords, shape and texture feats, effective resolution, pipeline type), the upstream
  `pack_state` pattern (trellis2_internals §2.2). It consumes no RNG and changes nothing else.

### 14.2 Aux engine seam (`inference/aux_base.py`, torch-free)

```python
class AuxKind(StrEnum):
    TEXT_TO_IMAGE = "text_to_image"; SEGMENTATION_2D = "segmentation_2d"; MESH_LABELING = "mesh_labeling"

class ProviderState(StrEnum):
    NOT_CONFIGURED = "not_configured"; DISABLED = "disabled"; MISSING_DEPENDENCY = "missing_dependency"
    NOT_INSTALLED = "not_installed"; GATED = "gated"; LICENSE_NOT_ACCEPTED = "license_not_accepted"
    INCOMPATIBLE = "incompatible"; AVAILABLE = "available"; LOADING = "loading"; READY = "ready"; ERROR = "error"

@dataclass(frozen=True, slots=True)
class ProviderStatus:
    state: ProviderState
    message: str | None = None            # user-facing, e.g. "diffusers 0.39.0 is not installed"
    details: Mapping[str, Any] = field(default_factory=dict)
    @property
    def usable(self) -> bool: return self.state in (ProviderState.AVAILABLE, ProviderState.LOADING, ProviderState.READY)

@dataclass(frozen=True, slots=True)
class ProviderInfo:
    id: str; kind: AuxKind; label: str; description: str
    model_ids: tuple[str, ...]            # registry ids this provider needs
    domains: tuple[str, ...] = ()         # ("anime_character", "realistic_human", "object")
    supports: Mapping[str, Any] = field(default_factory=dict)   # e.g. {"negative_prompt": False, "max_images": 1}
    vram_estimate_gb: float | None = None
    experimental: bool = False

class StepProgress(Protocol):
    def update(self, step: int | None = None, total: int | None = None, message: str | None = None) -> None: ...

class AuxEngine(ABC):
    @property
    @abstractmethod
    def info(self) -> ProviderInfo: ...
    @abstractmethod
    def status(self) -> ProviderStatus: ...          # cheap, thread-safe, never blocks on the GPU
    def load(self, on_status: Callable[[], None] | None = None) -> None: ...   # GPU thread
    def unload(self) -> None: ...                    # GPU thread
    def offload(self) -> None: ...                   # GPU thread: weights → CPU
    def holds_gpu(self) -> bool: return False

@dataclass(frozen=True, slots=True)
class TextToImageRequest:
    job_id: str; prompt: str; negative_prompt: str | None; seed: int; width: int; height: int
    steps: int | None; guidance: float | None; out_dir: Path

@dataclass(slots=True)
class TextToImageOutput:
    paths: list[Path]; seeds: list[int]; metadata: dict[str, Any]   # model_id, revision, license_id, steps, timings

class TextToImageEngine(AuxEngine):
    @abstractmethod
    def generate_image(self, request: TextToImageRequest, progress: StepProgress,
                       cancel: CancellationToken) -> TextToImageOutput: ...

@dataclass(frozen=True, slots=True)
class VocabularyTerm:
    class_id: str; phrases: tuple[str, ...]; expected: bool = False

@dataclass(frozen=True, slots=True)
class SegmentationRequest:
    job_id: str; image_rgba: Path; source_image: Path | None; crop: CropTransform | None
    vocabulary: tuple[VocabularyTerm, ...]; subject: str; head_pass: bool; min_confidence: float; out_dir: Path

@dataclass(frozen=True, slots=True)
class Instance2D:
    value: int; class_id: str; score: float; bbox: tuple[int, int, int, int]; mask_path: Path; model: str

@dataclass(slots=True)
class SegmentationOutput:
    label_map: Path                 # uint16 PNG, conditioning space, values = Instance2D.value
    instances: list[Instance2D]; subject: str; metadata: dict[str, Any]

class SegmentationEngine(AuxEngine):
    @abstractmethod
    def segment(self, request: SegmentationRequest, progress: StepProgress,
                cancel: CancellationToken) -> SegmentationOutput: ...

@dataclass(frozen=True, slots=True)
class MeshLabelRequest:
    job_id: str; mesh_path: Path; conditioning_rgba: Path; crop: CropTransform | None
    label_map: Path; instances: tuple[Instance2D, ...]; extra_views: int
    segmenter: SegmentationEngine | None; out_dir: Path

@dataclass(slots=True)
class ViewFit:
    id: str; yaw_deg: float; pitch_deg: float; fov_deg: float; distance: float; iou: float; accepted: bool
    world_to_camera: tuple[float, ...]; candidates_tested: int

@dataclass(slots=True)
class MeshLabelOutput:
    face_votes: Path               # float32 [F, L] sparse-friendly raw votes (labels = instance values)
    face_labels: Path              # uint16 [F] argmax before propagation (0 = unlabelled)
    face_scores: Path              # uint8 [F]
    views: list[ViewFit]
    occlusion_pairs: list[tuple[int, int, float, int]]   # (front value, back value, mean depth gap, border px)
    metadata: dict[str, Any]

class MeshLabeler(AuxEngine):
    @abstractmethod
    def label_mesh(self, request: MeshLabelRequest, progress: StepProgress,
                   cancel: CancellationToken) -> MeshLabelOutput: ...

@dataclass(slots=True)
class AuxEngineSet:
    text_to_image: dict[str, TextToImageEngine]
    segmentation: dict[str, SegmentationEngine]
    labelers: dict[str, MeshLabeler]
    default_t2i: str | None; default_segmenter: str | None; default_labeler: str | None
    def all(self) -> list[AuxEngine]: ...
```

Rules (same as `InferenceEngine`): every method that touches CUDA runs on the GPU thread only; files and plain
dicts cross the seam, never tensors; `cancel.raise_if_cancelled()` at every sampler step or chunk; GPU memory is
freed in `finally`.

`inference/aux_registry.py`: `create_aux_engines(settings, registry) -> AuxEngineSet` lazily imports each
implementation by dotted path (for example `inference.geometry.nvdiffrast_labeler:NvdiffrastMeshLabeler`) and
substitutes `inference/aux_unavailable.py` objects (`UnavailableTextToImage`, `UnavailableSegmenter`,
`UnavailableLabeler`) that report the precise state and message when the import fails, a dependency is missing or
the weights are absent. It never fabricates output.

### 14.3 Model registry (`inference/model_registry.py`, torch-free)

```python
@dataclass(frozen=True, slots=True)
class LicenceInfo:
    id: str; name: str; url: str | None
    commercial: Literal["allowed", "revenue_capped", "non_commercial", "research_only", "custom", "unknown"]
    gated: bool = False; requires_acceptance: bool = False
    territory_excluded: tuple[str, ...] = (); attribution: str | None = None; notes: str | None = None

@dataclass(frozen=True, slots=True)
class PackageRequirement:
    name: str; version: str                     # exact pin, checked with importlib.metadata (never imported)

@dataclass(frozen=True, slots=True)
class ModelSpec:
    id: str; kind: AuxKind; display_name: str
    repo_id: str | None; revision: str | None   # None = the install step resolves and records the commit sha
    local_dir: str                               # relative to settings.aux_model_root
    required_files: tuple[str, ...]              # presence check for "installed"
    allow_patterns: tuple[str, ...]
    size_bytes: int | None; vram_estimate_gb: float | None
    licence: LicenceInfo
    packages: tuple[PackageRequirement, ...] = ()
    vendored_code: str | None = None             # e.g. "third_party/sam3" (git submodule) when needed
    encoder_config_max_transformers: str | None = "5"   # refuse configs written by transformers >= 5

class ModelRegistry:
    def specs(self, kind: AuxKind | None = None) -> list[ModelSpec]: ...
    def get(self, model_id: str) -> ModelSpec: ...                       # NotFoundError
    def availability(self, model_id: str) -> ProviderStatus: ...         # files, packages, vendored code, approval
    def approved(self, model_id: str) -> bool: ...                       # in settings.approved_models
```

Catalogue entries (registered, **none enabled** until approved; sizes and licences from the research [V]):

| Model id | Kind | Repo / revision | Size | Licence (commercial) | Packages / code | Used by provider |
|---|---|---|---|---|---|---|
| `z-image-turbo` | text_to_image | `Tongyi-MAI/Z-Image-Turbo` @ `f332072aa78be7aecdf3ee76d5c247082da564a6` | 32.8 GB | Apache-2.0 (allowed) | diffusers 0.39.0, accelerate 1.15.0 | `z_image_turbo` |
| `flux2-klein-4b` | text_to_image | `black-forest-labs/FLUX.2-klein-4B` @ `e7b7dc27f91deacad38e78976d1f2b499d76a294` | 16.0 GB | Apache-2.0 (allowed) | diffusers 0.39.0, accelerate 1.15.0 | `flux2_klein_4b` |
| `animagine-xl-4.0` | text_to_image | `cagliostrolab/animagine-xl-4.0` (single file `animagine-xl-4.0.safetensors`) | 6.46 GB | CreativeML OpenRAIL++-M (allowed, use restrictions) | diffusers 0.39.0, accelerate 1.15.0 | `animagine_xl_4` |
| `grounding-dino-base` | segmentation_2d | `IDEA-Research/grounding-dino-base` | 0.93 GB | Apache-2.0 | transformers 4.57.6 (present) | `gdino_sam2` |
| `sam2.1-hiera-large` | segmentation_2d | `facebook/sam2.1-hiera-large` | 0.90 GB | Apache-2.0 | present | `gdino_sam2`, `see_through_anime`, `sam3`, `sapiens2_human` |
| `florence-2-large` | segmentation_2d | `florence-community/Florence-2-large` | 1.55 GB | MIT | present | `gdino_sam2` (prompt-noun grounding, optional) |
| `see-through-l2d-sam-iter2` | segmentation_2d | `24yearsold/l2d_sam_iter2` | 1.29 GB | Apache-2.0 | vendored modules `third_party/see-through` | `see_through_anime` |
| `sam3` | segmentation_2d | `facebook/sam3` (gated) | 3.45 GB | SAM License (custom, gated, acceptance) | vendored `third_party/sam3`; `ftfy`, `iopath` (--no-deps) | `sam3` |
| `sapiens2-seg-0.4b` | segmentation_2d | `facebook/sapiens2` seg 0.4B | 1.63 GB | Sapiens2 License (custom) | vendored `third_party/sapiens2` | `sapiens2_human` |

`revision=None` for segmentation entries is a decision: the approved install records the resolved commit sha in
`<local_dir>/img3d_model.json`, and the registry reports that sha in provenance from then on.

Providers (what users and nodes choose):

| Provider id | Kind | Models | Always available? | Notes |
|---|---|---|---|---|
| `none` | segmentation_2d | – | yes | Segmentation step skipped; geometry-only parts. |
| `gdino_sam2` | segmentation_2d | grounding-dino-base + sam2.1-hiera-large (+ florence-2-large) | after approval + weights | Baseline; no new pip packages. |
| `see_through_anime` | segmentation_2d | see-through-l2d-sam-iter2 + sam2.1 | after approval + weights + vendored code | Dense anime parsing. |
| `sam3` | segmentation_2d | sam3 + sam2.1 | after approval, gate accepted, vendored code | Best open vocabulary. |
| `sapiens2_human` | segmentation_2d | sapiens2-seg-0.4b + gdino_sam2 | after approval | Realistic humans. |
| `nvdiffrast_lift` | mesh_labeling | – | when nvdiffrast imports | No weights; nvdiffrast is already required by export. |
| `z_image_turbo` / `flux2_klein_4b` / `animagine_xl_4` | text_to_image | one model each | after approval + weights + diffusers | |

`segmentation.provider="auto"` resolves to `settings.segmentation_provider` when usable, else `none`.
Part strategy `components` (CPU, `backend/parts/components.py`) is always available.

### 14.4 GPU residency (`inference/gpu_residency.py`)

```python
class GpuResidency:
    def __init__(self, reconstruction: InferenceEngine, aux: AuxEngineSet) -> None: ...
    def acquire(self, owner: InferenceEngine | AuxEngine) -> None:
        """GPU thread only. offload() every other engine whose holds_gpu() is true, then load(owner) if needed."""
    def release_all(self) -> None: ...      # used by /system/engine/unload and shutdown
```

### 14.5 Licence acceptances

`storage/licence_store.py` persists `runtime/licences.json`: `[{model_id, licence_id, accepted_at,
licence_url}]`. A model whose `licence.requires_acceptance` is true reports `license_not_accepted` until an
acceptance exists. Acceptance is recorded only through the (disabled-by-default) install endpoint (§17.3).

### 14.6 Works with any approved subset

| Approved by the user | `prompt_to_3d` | Segmentation | Part output for a character |
|---|---|---|---|
| Nothing (today) | unavailable, `code: not_approved`, honest reason | `none` | host + separate shells (`unknown#n`, real accessories when they are separate pieces); no semantic names |
| T2I only | available | `none` | same as above, for prompt and image jobs |
| `gdino_sam2` | – | baseline | hair, clothes, accessories, objects as physical parts; eyes physical when cuttable; brows and lashes deferred via heuristics; face and body regions |
| `+ see_through_anime` | – | dense anime parsing | better hair, clothing and face coverage for anime inputs |
| `+ sam3` | – | best open vocabulary | better accessories and prompt-noun recall |
| `+ sapiens2_human` | – | realistic humans | limbs, hands and clothing regions for photos |

---------------------------------------------------------------------------------------------------

## 15. Prompt-to-3D

- **Request**: `CreateJobRequest.source = PromptSourceIn` (§17.2). `reference_set_id` and `primary_image_id` must
  be absent or null when `source` is present.
- **Creation** (`JobService.create_job`, event loop):
  1. `capabilities.require("prompt_to_3d", model_id)` → 503 `capability_unavailable` (details: `capability`,
     `provider`, `code`, `reason`) when unusable.
  2. Validate provider-specific fields: `negative_prompt` only if `supports.negative_prompt`; `width`/`height`
     inside `supports.sizes`; otherwise 422 `validation_error` with `type: "unsupported_by_provider"` and
     `loc ["body","source",<field>]`.
  3. Create a reference set named `Prompt: <first 60 chars>`, reserve `img_<12 hex>`.
  4. `Job(kind=prompt_to_3d, reference_set_id=<new set>, primary_image_id=<reserved>,
     references=[JobReference(image_id=<reserved>, slot=front, is_primary=True)],
     source=JobSource(type="prompt", prompt=PromptSpec(...), generated_image_id=<reserved>,
     import_state="pending"), pipeline=build_plan(...))`. No `inputs/` copy yet.
  5. Queue it like any job (429, 503 unchanged).
- **Image seed**: `image_seed` if given, else the job's resolved `seed`. Both are recorded.
- **Templates** (`pipelines/prompt_templates.py`): presets `character` (896×1152), `object` (1024×1024) and `raw`
  (the prompt verbatim), style clauses `realistic`, `stylized`, `anime`, and an optional part-separation clause
  (`part_hint`), copied from text-to-image research §8. Distilled models get positive-only text. The final prompt is
  stored in `PromptSpec.final_prompt`; logs record only its length and SHA-256.
- **Expectations**: nouns that match taxonomy aliases (`Taxonomy.match_phrases`) become `expected_classes`. They
  extend the segmentation vocabulary, and a missing one produces warning `expected_part_missing` and an
  `expected_missing` relation.
- **Import**: after generation the step calls `ReferenceService.import_image(set_id, image_id=<reserved>,
  path=inputs/<reserved>.png, slot=front, make_primary=True, source=ImageSource(kind="t2i", job_id, model_id,
  revision, license_id, seed, prompt_sha256))` through `LoopBridge`. A legacy-UI retry then resends
  `reference_set_id` + `primary_image_id` + `settings` and runs a deterministic image job.
- **Provenance**: `PromptSpec` keeps `model_id`, `model_revision`, `license_id`, `final_prompt`; the manifest
  copies it (§8). `ReferenceImage.source` (new optional field) carries the same data for the reference set.
- **Security**: the prompt is stripped, control characters other than `\n` and `\t` are rejected (422), and the
  length is capped by `max_prompt_chars` (default 2000) in addition to a schema hard cap of 4000. Never
  `trust_remote_code`, never `custom_pipeline`, local pinned snapshots only (CVE-2026-44513 note in T2I research
  §2).

---------------------------------------------------------------------------------------------------

## 16. Settings and feature flags (`config/settings.py`, additive)

| Field | Env | Default | Effect |
|---|---|---|---|
| `parts_enabled` | `IMG3D_PARTS_ENABLED` | `true` | Master switch for part-aware jobs (still opt-in per job via `settings.parts`). |
| `prompt_to_3d_enabled` | `IMG3D_PROMPT_TO_3D_ENABLED` | `true` | Master switch; usable only with an approved, installed T2I provider. |
| `t2i_provider` | `IMG3D_T2I_PROVIDER` | `"none"` | Default T2I provider id. |
| `segmentation_provider` | `IMG3D_SEGMENTATION_PROVIDER` | `"none"` | Default 2D segmentation provider id. |
| `mesh_labeler` | `IMG3D_MESH_LABELER` | `"nvdiffrast_lift"` | Default labeller. |
| `approved_models` | `IMG3D_APPROVED_MODELS` | `[]` | Model ids the user approved (JSON list or `;`-separated). |
| `model_installs_enabled` | `IMG3D_MODEL_INSTALLS_ENABLED` | `false` | Enables `POST /models/{id}/install` for approved models. |
| `aux_keep_loaded` | `IMG3D_AUX_KEEP_LOADED` | `true` | Keep aux weights in CPU RAM between jobs. |
| `parts_write_part_files` | `IMG3D_PARTS_WRITE_PART_FILES` | `true` | Default for `parts.export.part_files`. |
| `parts_min_faces` | `IMG3D_PARTS_MIN_FACES` | `200` | Minimum faces for a physical cut (§4.1). |
| `parts_max_parts` | `IMG3D_PARTS_MAX_PARTS` | `64` | Part budget. |
| `parts_capture_latents` | `IMG3D_PARTS_CAPTURE_LATENTS` | `false` | Persist `intermediates/latents.npz` for future voxel providers. |
| `parts_uv_label_map` | `IMG3D_PARTS_UV_LABEL_MAP` | `false` | Write `textures/part_id_uv.png`. |
| `parts_step_timeout_s` | `IMG3D_PARTS_STEP_TIMEOUT_S` | `900` | Cooperative timeout per part step (checked at progress updates). |
| `max_prompt_chars` | `IMG3D_MAX_PROMPT_CHARS` | `2000` | Prompt length cap. |
| `tuning_enabled` | `IMG3D_TUNING_ENABLED` | **`false`** | Server-side gate for `POST /jobs/{id}/tunings` and startup Blender detection. |
| `part_refinement_enabled` | `IMG3D_PART_REFINEMENT_ENABLED` | **`false`** | `ConsumerRegistry` refuses registrations; `features.part_refinement.enabled=false`. |

All are added to `safe_summary()`. `test_settings.py` pins only existing defaults [V], so the additions are safe.

**Tuning gate details.** `MeshTunerService.create_run` keeps its current order: 404 job, 409 not completed, then
**new** `if not settings.tuning_enabled: raise FeatureDisabledError(feature="mesh_tuning")` (503
`feature_disabled`), then `require_runtime()`. Reading, listing, cancelling and deleting existing runs still work
for history. `core/lifecycle.py` starts Blender detection only when `blender_enabled and tuning_enabled and
blender_detect_on_startup`. `blender_enabled` keeps its pinned default `true`
(`test_blender_runtime_api.py:311` [V]). The tuner API tests opt in with one line:
`make_settings(**{"tuning_enabled": True, **overrides})` in `make_tuner_app` (`test_mesh_tuner_api.py:57` [V]).

---------------------------------------------------------------------------------------------------

## 17. API contract additions (`/api/v1`, JSON, snake_case, additive)

### 17.1 Endpoint table

| # | Method + path | Guard | Request | Success | Errors |
|---|---|---|---|---|---|
| A1 | `GET /features` | Auth | – | 200 `FeaturesOut` | 401 |
| A2 | `GET /taxonomy` | Auth | – | 200 `TaxonomyOut` (ETag = taxonomy version) | 401 |
| A3 | `GET /models?kind=` | Auth | – | 200 `{items: ModelOut[]}` | 401, 422 bad kind |
| A4 | `GET /models/{model_id}` | Auth | – | 200 `ModelOut` | 404 `not_found` |
| A5 | `POST /jobs` (extended) | Auth | `CreateJobRequest` (+ `source`, + `settings.parts`) | 202 `Job` | existing (404, 422, 429, 503) + 422 `validation_error` (source conflict, prompt invalid, unsupported_by_provider, unknown provider id) + 503 `capability_unavailable` |
| A6 | `GET /jobs?limit=&status=&kind=&parent_job_id=` | Auth | – | 200 `{items: Job[]}` | 422 bad kind or id |
| A7 | `POST /jobs/{job_id}/parts/rebuild` | Auth | `PartsRebuildRequest` | 202 `Job` (kind `part_decomposition`) | 404 job, 409 `conflict` (not completed / model missing), 422, 429 `queue_full`, 503 `capability_unavailable` |
| A8 | `GET /jobs/{job_id}/parts` | Auth | – | 200 `PartSetOut` | 404 `not_found` (`details.reason`: `job`, `not_requested`, `failed`), 409 `conflict` (job not terminal) |
| A9 | `GET /jobs/{job_id}/parts/manifest.json` | File | – | `application/json` (raw manifest) | as A8 |
| A10 | `GET /jobs/{job_id}/parts/assembled.glb` | File | – | `model/gltf-binary` inline | as A8, 404 when `assembled_glb` was off |
| A11 | `GET /jobs/{job_id}/parts/export/glb` | File | – | attachment `img3d_<job>_parts.glb` | as A10 |
| A12 | `GET /jobs/{job_id}/parts/export/zip?include_base=true` | File | – | `application/zip` attachment `img3d_<job>_parts.zip` (job-root layout: `model.glb` if `include_base`, `parts/**`, referenced `intermediates/*`) | as A8 |
| A13 | `GET /jobs/{job_id}/parts/{part_id}/model.glb` | File | – | inline GLB | 404 (`part_id` fails `is_valid_id(…,"pt")` or unknown), 409 `conflict` (`details.reason: "no_mesh"` for region and deferred parts or when part files were off) |
| A14 | `GET /jobs/{job_id}/parts/{part_id}/export/glb` | File | – | attachment `img3d_<job>_<key>.glb` (key sanitised to `[a-z0-9_.-]`) | as A13 |
| A15 | `GET /jobs/{job_id}/parts/{part_id}/mask` | File | – | `image/png` | 404 when no mask |
| A16 | `GET /jobs/{job_id}/parts/segmentation/overlay` | File | – | `image/webp` | 404 when segmentation did not run |
| A17 | `GET /jobs/{job_id}/files/{path}` | File | – | the file (type by extension; `.bin` → `application/octet-stream`) | 404 when `path` fails the whitelist or the file is absent |
| A18 | `POST /models/{model_id}/install` | Auth | `{confirm: true, accept_license: bool, license_version: str\|null}` | 202 `ModelOut` | 403 `model_install_disabled`, 403 `model_not_approved`, 404, 409 `conflict` (installing / installed), 422 `confirmation_required` / `license_acceptance_required` |
| A19 | `DELETE /models/{model_id}/install` | Auth | – | 202 `ModelOut` (cancel) | 409 `conflict` (not installing) |

A18–A19 ship in wave 5 and stay 403 until `model_installs_enabled` is true and the model is approved. Every route
validates ids with `is_valid_id` before touching the filesystem. A17 whitelist (anchored regex, applied to the raw
path before any `Path` join; `..`, backslashes and absolute paths are rejected first):

```
^(model\.glb|preview\.png
 |intermediates/(conditioning_rgb\.png|conditioning_rgba\.png|conditioning\.json|latents\.npz)
 |source/(t2i_[0-9]\.png|t2i\.json)
 |parts/(manifest\.json|assembled\.glb|meshes/pt_[0-9a-f]{12}\.glb|buffers/[a-z0-9_]{1,64}\.bin
        |masks/pt_[0-9a-f]{12}\.png|thumbs/pt_[0-9a-f]{12}\.webp
        |segmentation/(labels\.png|overlay\.webp|head_[0-9]\.png)|textures/part_id_uv\.png))$
```

Route collision check [V]: `tunings.jobs_router` owns `/jobs/{id}/tunings`; `results.files_router` owns
`/jobs/{id}/model.glb|export/glb|preview`. `/jobs/{id}/parts…` and `/jobs/{id}/files/…` do not collide.

### 17.2 Request schemas (Pydantic, `RequestModel` = `extra="forbid"`)

```python
# schemas/prompt.py
class PromptSourceIn(RequestModel):
    type: Literal["prompt"]
    prompt: str = Field(min_length=1, max_length=4000)            # service also enforces max_prompt_chars
    negative_prompt: str | None = Field(default=None, max_length=4000)
    preset: Literal["character", "object", "raw"] = "character"
    style: Literal["realistic", "stylized", "anime"] | None = None
    model_id: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")  # None = default provider
    image_seed: int | None = Field(default=None, ge=0, le=MAX_SEED)
    width: int | None = Field(default=None, ge=256, le=2048, multiple_of=16)
    height: int | None = Field(default=None, ge=256, le=2048, multiple_of=16)
    steps: int | None = Field(default=None, ge=1, le=100)
    guidance: float | None = Field(default=None, ge=0, le=20)
    part_hint: bool = False

# schemas/parts_options.py
SeparableGroup = Literal["eyes", "eyebrows", "eyelashes", "hair", "clothes", "accessories", "detachable",
                         "appendages"]
RegionGroup = Literal["face", "body"]
DEFAULT_SEPARATE = ("eyes", "eyebrows", "eyelashes", "hair", "clothes", "accessories", "detachable")

class PartExportOptionsIn(RequestModel):
    assembled_glb: bool = True
    part_files: bool | None = None              # None → settings.parts_write_part_files
    feature_ids: bool = True                    # write _FEATURE_ID_0 on the host
    keep_base_model: Literal[True] = True       # model.glb is never replaced (documented invariant)

class PartOptionsIn(RequestModel):
    enabled: bool = True
    segmentation: Literal["auto", "off"] = "auto"
    segmenter_id: str | None = Field(default=None, pattern=r"^[a-z0-9_]{1,40}$")   # None = configured default
    subject: Literal["auto", "anime_character", "realistic_human", "object"] = "auto"
    extra_terms: list[Annotated[str, StringConstraints(min_length=1, max_length=48)]] = Field(
        default_factory=list, max_length=32)
    decomposition: Literal["auto", "components", "lifted"] = "auto"   # "lifted" requires segmentation + labeller
    separate: list[SeparableGroup] = Field(default_factory=lambda: list(DEFAULT_SEPARATE))   # unique
    regions: list[RegionGroup] = Field(default_factory=lambda: ["face", "body"])           # informational
    granularity: Literal["coarse", "standard", "fine"] = "standard"
    split_by_connectivity: bool = True
    head_pass: bool = True
    extra_views: int = Field(default=0, ge=0, le=24)
    min_confidence: float = Field(default=0.35, ge=0.0, le=1.0)
    export: PartExportOptionsIn = Field(default_factory=PartExportOptionsIn)

# schemas/jobs.py (additive)
class GenerationSettings(RequestModel):
    ...                                          # unchanged fields
    parts: PartOptionsIn | None = None

class CreateJobRequest(RequestModel):
    reference_set_id: str | None = None          # required unless source is present
    primary_image_id: str | None = None
    source: PromptSourceIn | None = None
    settings: GenerationSettings = Field(default_factory=GenerationSettings)
    # model_validator(mode="after"): source present ⇒ reference_set_id and primary_image_id are None
    # (error type "source_conflict"); source absent ⇒ reference_set_id required (error type "missing",
    # msg "Field required", loc ["body", "reference_set_id"] via PydanticCustomError so the 422 looks like today's)

# schemas/parts.py
class PartsRebuildRequest(RequestModel):
    parts: PartOptionsIn = Field(default_factory=PartOptionsIn)   # enabled is forced to true
```

**Stored settings rule** (round trip, contract map §4.3): `JobService` dumps settings with `exclude={"parts"}` when
`parts is None`. Every stored key is accepted again by `GenerationSettings`.

### 17.3 Response shapes

**`Job` (additions; existing keys unchanged):**

```json
{
  "kind": "prompt_to_3d",
  "source": {
    "type": "prompt",
    "prompt": {"prompt": "a knight with a sword and a red cape", "negative_prompt": null, "preset": "character",
               "style": "stylized", "model_id": "z-image-turbo", "model_revision": "f332072aa78be7aecdf3ee76d5c247082da564a6",
               "license_id": "apache-2.0", "image_seed": 123456, "width": 896, "height": 1152, "steps": 9,
               "guidance": 0.0, "part_hint": true, "final_prompt_sha256": "c0ffee…"},
    "generated_image_id": "img_0a1b2c3d4e5f",
    "import_state": "imported",
    "parent_job_id": null,
    "root_job_id": null
  },
  "pipeline": {
    "version": 1,
    "percent": 41.7,
    "eta_seconds": 160.0,
    "current_step": "image_to_3d",
    "steps": [
      {"id": "prompt", "label": "Text to image", "status": "done", "required": true, "weight": 6, "percent": 100.0,
       "step": 9, "total_steps": 9, "message": "Generated 896×1152 image", "provider": "z_image_turbo",
       "started_at": "2026-10-20T10:00:02Z", "finished_at": "2026-10-20T10:00:09Z", "duration_s": 6.9,
       "warnings": [], "error": null, "skip_reason": null},
      {"id": "input_image", "label": "Input image", "status": "done", "required": true, "weight": 2, "percent": 100.0,
       "step": null, "total_steps": null, "message": "Background removed", "provider": "trellis2",
       "started_at": "2026-10-20T10:00:09Z", "finished_at": "2026-10-20T10:00:10Z", "duration_s": 1.1,
       "warnings": [], "error": null, "skip_reason": null},
      {"id": "segmentation", "label": "Segmentation", "status": "skipped", "required": false, "weight": 6,
       "percent": 0.0, "step": null, "total_steps": null, "message": null, "provider": null, "started_at": null,
       "finished_at": null, "duration_s": null, "warnings": [], "error": null, "skip_reason": "no_provider"},
      {"id": "image_to_3d", "label": "Image to 3D", "status": "running", "required": true, "weight": 74,
       "percent": 38.0, "step": 7, "total_steps": 24, "message": "Shape SLat step 7/24", "provider": "trellis2",
       "started_at": "2026-10-20T10:00:10Z", "finished_at": null, "duration_s": null, "warnings": [], "error": null,
       "skip_reason": null},
      {"id": "part_extraction", "label": "Part extraction", "status": "pending", "required": false, "weight": 9,
       "percent": 0.0, "step": null, "total_steps": null, "message": null, "provider": "components",
       "started_at": null, "finished_at": null, "duration_s": null, "warnings": [], "error": null, "skip_reason": null},
      {"id": "export", "label": "Export", "status": "pending", "required": false, "weight": 3, "percent": 0.0,
       "step": null, "total_steps": null, "message": null, "provider": null, "started_at": null, "finished_at": null,
       "duration_s": null, "warnings": [], "error": null, "skip_reason": null}
    ]
  }
}
```

For legacy jobs: `"kind": "image_to_3d", "source": null, "pipeline": null`, and `result.parts` is `null`.
`job.json` on disk omits these keys when they hold their defaults (I1 spirit; downgrade-safe).

**`JobResultOut.parts` (`PartsResultOut`):**

```json
{
  "status": "completed",
  "format": "img3d.parts/1",
  "asset_id": "job_9f3c2a1b4d5e",
  "revision": 1,
  "counts": {"parts": 12, "host": 1, "physical": 5, "physical_deferred": 4, "regions": 2},
  "decomposition": "lifted+components",
  "segmentation_provider": "gdino_sam2",
  "warnings": ["charts_cross_boundary"],
  "error": null,
  "parts_url": "/api/v1/jobs/job_9f3c2a1b4d5e/parts",
  "manifest_url": "/api/v1/jobs/job_9f3c2a1b4d5e/parts/manifest.json",
  "assembled_url": "/api/v1/jobs/job_9f3c2a1b4d5e/parts/assembled.glb",
  "assembled_download_url": "/api/v1/jobs/job_9f3c2a1b4d5e/parts/export/glb",
  "export_zip_url": "/api/v1/jobs/job_9f3c2a1b4d5e/parts/export/zip",
  "overlay_url": "/api/v1/jobs/job_9f3c2a1b4d5e/parts/segmentation/overlay"
}
```

`status`: `completed` (all part steps done), `partial` (done with fallbacks such as `segmentation_failed` or
`registration_failed`), `failed` (base model fine, part layer absent; `error` set), `skipped` (requested but the
job's plan skipped parts). URLs are null when the artifact does not exist.

**`PartSetOut` (A8)** — a projection of the manifest with URLs, no buffer internals:

```json
{
  "job_id": "job_9f3c2a1b4d5e", "format": "img3d.parts/1", "taxonomy": "img3d.taxonomy/1",
  "asset": {"asset_id": "job_9f3c2a1b4d5e", "revision": 1, "parent_job_id": null, "mode": "inline"},
  "domain": "character", "decomposition": "lifted+components", "segmentation_provider": "gdino_sam2",
  "region_attribute": "_FEATURE_ID_0",
  "parts": [
    {"id": "pt_1a2b3c4d5e6f", "key": "hair", "class": "hair", "group": "hair", "label": "Hair", "kind": "physical",
     "side": "c", "parent_id": "pt_a0b1c2d3e4f5", "host_id": null, "attach_to_id": "pt_a0b1c2d3e4f5",
     "symmetric_with_id": null, "node_name": "hair", "label_value": 2, "faces": 410112,
     "bbox_min": [-0.31, 0.12, -0.2], "bbox_max": [0.31, 0.5, 0.17], "pivot": [0.0, 0.29, 0.01],
     "geometry_status": "open_surface", "occluded_geometry_missing": false, "confidence": 0.88,
     "sources": ["lifted"], "expected": false, "tags": [],
     "refinement": {"status": "untouched", "locked": true, "lock_reason": "part_refinement_disabled"},
     "model_url": "/api/v1/jobs/job_9f3c2a1b4d5e/parts/pt_1a2b3c4d5e6f/model.glb",
     "download_url": "/api/v1/jobs/job_9f3c2a1b4d5e/parts/pt_1a2b3c4d5e6f/export/glb",
     "mask_url": "/api/v1/jobs/job_9f3c2a1b4d5e/parts/pt_1a2b3c4d5e6f/mask",
     "thumbnail_url": "/api/v1/jobs/job_9f3c2a1b4d5e/files/parts/thumbs/pt_1a2b3c4d5e6f.webp"}
  ],
  "relations": [{"id": "rel_0", "type": "attached_to", "a": "pt_1a2b3c4d5e6f", "b": "pt_a0b1c2d3e4f5",
                 "via": "seam_0", "confidence": 0.88}],
  "seams": [{"id": "seam_0", "a": "pt_1a2b3c4d5e6f", "b": "pt_a0b1c2d3e4f5", "loop_count": 2,
             "boundary_edges": 3122, "length": 1.84, "host_opening": true}],
  "warnings": [{"code": "charts_cross_boundary", "message": "UV charts of cut parts may cross part borders.",
                "part_id": null}],
  "files": {"manifest_url": "…/parts/manifest.json", "assembled_url": "…/parts/assembled.glb",
            "export_zip_url": "…/parts/export/zip", "overlay_url": "…/parts/segmentation/overlay",
            "files_base_url": "/api/v1/jobs/job_9f3c2a1b4d5e/files/"},
  "created_at": "2026-10-20T10:04:31Z"
}
```

**`FeaturesOut` (A1):**

```json
{
  "schema": "img3d.features/1",
  "flags": {"parts": true, "prompt_to_3d": true, "mesh_tuning": false, "part_refinement": false,
            "model_installs": false},
  "pipeline": {
    "image_to_3d": {"available": true, "code": "ok", "reason": null,
                    "request_keys": ["reference_set_id", "primary_image_id", "settings"]},
    "prompt_to_3d": {"available": false, "code": "not_approved",
                     "reason": "No text-to-image model is approved yet. Choose one in App settings → Models.",
                     "request_keys": ["source.prompt", "source.negative_prompt", "source.preset", "source.style",
                                      "source.model_id", "source.image_seed", "source.width", "source.height",
                                      "source.steps", "source.guidance", "source.part_hint"],
                     "max_prompt_chars": 2000, "presets": [{"id": "character", "label": "Character", "width": 896, "height": 1152},
                                                           {"id": "object", "label": "Object", "width": 1024, "height": 1024},
                                                           {"id": "raw", "label": "Raw prompt", "width": 1024, "height": 1024}],
                     "styles": ["realistic", "stylized", "anime"], "supports_negative_prompt": false,
                     "default_model_id": null},
    "segmentation": {"available": true, "code": "ok", "reason": null,
                     "request_keys": ["settings.parts.segmentation", "settings.parts.segmenter_id",
                                      "settings.parts.subject", "settings.parts.extra_terms",
                                      "settings.parts.head_pass", "settings.parts.min_confidence"],
                     "providers": ["none"], "default_segmenter_id": "none",
                     "subjects": ["auto", "anime_character", "realistic_human", "object"], "max_extra_terms": 32},
    "part_extraction": {"available": true, "code": "ok", "reason": null,
                        "request_keys": ["settings.parts.enabled", "settings.parts.decomposition",
                                         "settings.parts.separate", "settings.parts.regions",
                                         "settings.parts.granularity", "settings.parts.split_by_connectivity",
                                         "settings.parts.extra_views", "settings.parts.export"],
                        "strategies": [{"id": "components", "label": "Geometry (separate pieces)",
                                        "requires_segmentation": false, "available": true},
                                       {"id": "lifted", "label": "Semantic (segmentation lifted to 3D)",
                                        "requires_segmentation": true, "available": false}],
                        "default_strategy": "auto", "rebuild": true,
                        "separable_groups": [{"id": "eyes", "label": "Eyes", "default_kind": "physical",
                                              "default_selected": true,
                                              "note": "Eyes are shallow surface patches; the hidden eyeball is not generated."},
                                             {"id": "eyelashes", "label": "Eyelashes",
                                              "default_kind": "physical_deferred", "default_selected": true,
                                              "note": "Eyelash geometry is deferred; mask + lid curve only."}],
                        "region_groups": [{"id": "face", "label": "Face", "default_kind": "region",
                                           "default_selected": true, "note": null},
                                          {"id": "body", "label": "Body", "default_kind": "region",
                                           "default_selected": true, "note": null}],
                        "granularities": ["coarse", "standard", "fine"], "max_parts": 64},
    "export": {"available": true, "code": "ok", "reason": null, "request_keys": ["settings.parts.export"],
               "formats": ["glb", "parts_glb", "zip"]}
  },
  "providers": {
    "text_to_image": [{"id": "z_image_turbo", "label": "Z-Image-Turbo", "state": "not_installed",
                       "message": "Not approved and not installed.", "approved": false, "selected": false,
                       "models": ["z-image-turbo"], "commercial": "allowed", "domains": [],
                       "supports": {"negative_prompt": false, "sizes": [[896, 1152], [1024, 1024]], "max_images": 1},
                       "vram_estimate_gb": 15.0, "experimental": false}],
    "segmentation_2d": [{"id": "none", "label": "None (geometry only)", "state": "available", "message": null,
                         "approved": true, "selected": true, "models": [], "commercial": "allowed",
                         "domains": [], "supports": {}, "vram_estimate_gb": null, "experimental": false}],
    "mesh_labeling": [{"id": "nvdiffrast_lift", "label": "Face-ID lifting (nvdiffrast)", "state": "available",
                       "message": null, "approved": true, "selected": true, "models": [],
                       "commercial": "non_commercial", "domains": [], "supports": {"extra_views": 24},
                       "vram_estimate_gb": 2.0, "experimental": false}]
  },
  "models_url": "/api/v1/models",
  "taxonomy": {"version": "img3d.taxonomy/1", "url": "/api/v1/taxonomy"},
  "nodes": [
    {"type": "img3d.input_image", "version": 1, "step_id": "input_image", "category": "input", "title": "Input Image",
     "inputs": [], "outputs": [{"id": "images", "type": "image_set"}], "available": true, "code": "ok", "reason": null,
     "request_paths": ["reference_set_id", "primary_image_id"], "params_schema": null}
  ],
  "mesh_tuning": {"enabled": false, "code": "not_approved",
                  "reason": "Mesh tuning and refinement are paused until you approve them."},
  "part_refinement": {"enabled": false, "code": "not_approved",
                      "reason": "Part refinement is not implemented in this milestone."}
}
```

`code` values: `ok`, `disabled`, `not_approved`, `not_installed`, `missing_dependency`, `gated`,
`license_not_accepted`, `incompatible`, `loading`, `error`, `engine_unavailable`. The schema-level key is `schema`
(field `schema_id` with a serialisation alias). The model registry is separate (A3) and linked by `models_url`;
`providers[*].models` lists ids.

**Node catalogue (`FeaturesOut.nodes`)** — the backend's view of the six stages. Availability and parameter bounds
are single-sourced from Pydantic (`model_json_schema()`); the frontend node engine owns layout and visuals.

| Node type | step_id | Category | Inputs (accepts) | Outputs | Request paths | Available when |
|---|---|---|---|---|---|---|
| `img3d.input_image` | `input_image` | input | – | `images: image_set` | `reference_set_id`, `primary_image_id` | always |
| `img3d.prompt` | `prompt` | input | – | `images: image_set` (the generated image) | `source` | `pipeline.prompt_to_3d.available` |
| `img3d.segmentation` | `segmentation` | analyze | `source: [image_set]` required | `masks: segmentation` | `settings.parts.{segmentation, segmenter_id, subject, extra_terms, head_pass, min_confidence}` | `pipeline.segmentation.available` (with only `none`, the node is valid and runs as `skipped`) |
| `img3d.image_to_3d` | `image_to_3d` | generate | `source: [image_set]` required (exactly one source node upstream) | `mesh: mesh` | `settings.{resolution, seed, randomize_seed, polygon_target, texture_size, remove_background, reference_mode, advanced}` | engine state ≠ `unavailable` |
| `img3d.part_extraction` | `part_extraction` | parts | `mesh: [mesh]` required, `masks: [segmentation]` optional | `parts: part_set` | `settings.parts.{enabled, decomposition, separate, regions, granularity, split_by_connectivity, extra_views}` | `pipeline.part_extraction.available` |
| `img3d.export` | `export` | output | `asset: [mesh, part_set]` required | – | `settings.parts.export` (only when fed by `part_set`) | always |

No tuning or refinement node types are listed while `part_refinement_enabled=false`.

**`ModelOut` (A3/A4):**

```json
{
  "id": "sam2.1-hiera-large", "kind": "segmentation_2d", "display_name": "SAM 2.1 Hiera-L",
  "repo_id": "facebook/sam2.1-hiera-large", "revision": null, "state": "not_installed",
  "approved": false, "is_default": false, "reason": "Not approved. Weights are not on disk.",
  "size_bytes": 897897416, "vram_estimate_gb": 2.0, "local_dir": "seg/sam2.1-hiera-large",
  "license": {"id": "apache-2.0", "name": "Apache License 2.0", "url": "https://www.apache.org/licenses/LICENSE-2.0",
              "commercial": "allowed", "gated": false, "requires_acceptance": false, "accepted": false,
              "territory_excluded": [], "attribution": null},
  "packages": [{"name": "transformers", "version": "4.57.6", "installed_version": "4.57.6", "ok": true}],
  "vendored_code": null,
  "providers": ["gdino_sam2", "see_through_anime", "sam3", "sapiens2_human"],
  "install": {"supported": false, "hint": "Model downloads are disabled (IMG3D_MODEL_INSTALLS_ENABLED=false).",
              "state": null, "bytes_done": null, "bytes_total": null, "error": null}
}
```

**`TaxonomyOut` (A2):** `{version, groups: [{id, label, classes[], separable, default_kind_if_separated,
default_selected, note}], classes: [{id, label, group, parent, attach_to, side, symmetric_pair, kind_if_separated,
kind_if_not_separated, geometry_expectation, coarse_class, aliases[]}]}`.

### 17.4 Error codes (additive; envelope unchanged)

| Code | HTTP | Raised by | Details |
|---|---|---|---|
| `capability_unavailable` | 503 | job creation and rebuild when a pipeline, provider or model is not usable | `{capability, provider, code, reason}` |
| `feature_disabled` | 503 | tuning create (`feature: "mesh_tuning"`), consumer registration (`feature: "part_refinement"`) | `{feature}` |
| `model_install_disabled` | 403 | A18 while installs are disabled | – |
| `model_not_approved` | 403 | A18 for a model not in `approved_models` | `{model_id}` |
| `license_acceptance_required` | 422 | A18 without `accept_license` for a licence that needs it | `{licence_id, url}` |
| `confirmation_required` | 422 | A18 without `confirm: true` (existing code, reused) | – |
| existing `not_found`, `conflict`, `validation_error`, `queue_full`, `engine_unavailable`, `out_of_memory`, `engine_error` | | unchanged | new uses add `details.resource` / `details.reason` |

`CapabilityUnavailableError` and `FeatureDisabledError` are added to `core/exceptions.py` as `AppError`
subclasses (additive to a seam file).

### 17.5 Validation order for `POST /jobs`

1. Pydantic (schema bounds, `extra=forbid`, source exclusivity) → 422.
2. Source branch: prompt → capability and provider checks → 503 / 422. Image → existing set and primary checks
   → 404 / 422.
3. `validate_against_capabilities` (engine) → 422 `unsupported_by_engine` (unchanged).
4. `settings.parts.enabled` → `capabilities.require("part_extraction")`; explicit `segmenter_id` unknown → 422,
   known but unusable → 503; `decomposition="lifted"` without a usable segmenter or labeller → 503.
5. Queue capacity → 429 (unchanged). Then the plan is built (`pipelines/plan.build_plan(job, capabilities)`), stored
   in `job.pipeline`, and the job is queued.

---------------------------------------------------------------------------------------------------

## 18. WebSocket

| Type | Data | Job-scoped (`?job_id=`) | Droppable | When |
|---|---|---|---|---|
| `hello` | `{system, features}` (**`features` added**) | – | no | on connect |
| `job.updated` | `Job` (now with `kind`, `source`, `pipeline`, `result.parts`) | yes (unchanged) | progress yes, status no; step begin/finish no | unchanged + step transitions |
| `features.updated` | `FeaturesOut` | no | no (coalesced 250 ms) | provider state, model install, engine state or flag change |
| `model.updated` | `ModelOut` | no | progress frames yes | install progress (wave 5) |

No `parts.updated` event: rebuilds are jobs, so `job.updated` covers them. Unknown types are ignored by current
clients (`frontend/src/api/ws.ts:254-276` [V by contract map]). `schemas/events.py` gains `FEATURES_UPDATED` and
`MODEL_UPDATED`; `_JOB_SCOPED_EVENTS` is unchanged.

---------------------------------------------------------------------------------------------------

## 19. What the frontend needs from the API

**Startup and session:**
1. Fetch `GET /features` at session sync, and replace it on `hello.data.features` and `features.updated`. A 404
   means an older backend: treat everything except image-to-3D as unavailable.
2. Fetch `GET /taxonomy` once per `taxonomy.version` (cache by version).
3. Fetch `GET /models` only for the App settings → Models screen.

**Gating (single source of truth, all four UI modes):**
- Prompt source: enabled iff `features.pipeline.prompt_to_3d.available`; otherwise show `reason`.
- Part toggles and the Segmentation / Part Extraction nodes: `features.pipeline.part_extraction.available` and
  `features.pipeline.segmentation.available`. Group toggles come from `separable_groups` and `region_groups`.
- Tuning UI (all nine mount points, frontend map §7): hidden iff `features.mesh_tuning.enabled == false` (also
  behind the frontend's own `VITE_FEATURE_MESH_TUNER` flag). No `/blender` or `/tunings` calls while hidden.
- Compile only keys listed in `request_keys` (avoids 422 from `extra="forbid"`).

**Requests the UI builds (via one `buildCreateJobRequest`, shared by all renderers and the node compiler):**

| UI state | Request |
|---|---|
| Image source, parts off | today's request, unchanged (`settings` without `parts`) |
| Image source, parts on | `{reference_set_id, primary_image_id, settings: {…, parts: PartOptionsIn}}` |
| Prompt source | `{source: PromptSourceIn, settings: {…[, parts]}}` (no set id) |
| Rebuild parts of a finished job | `POST /jobs/{id}/parts/rebuild {parts}` |
| Retry a prompt job | if `source.import_state == "imported"`: image request with the job's set/primary/settings (deterministic); else resend `source` |

**Progress and status:**
- If `job.pipeline` is non-null: overall bar = `pipeline.percent`, ETA = `pipeline.eta_seconds`, node and step
  chips = `pipeline.steps[*].status` / `.percent` / `.message`; node type = `img3d.` + step id.
- If null (legacy): keep today's stage list; Node UI maps `preprocessing → input_image`,
  `conditioning … exporting → image_to_3d`, `finalizing | done → export`.

**Results and viewer:**
- `result.parts == null` → no parts UI. Otherwise use `result.parts.status`, `counts`, `warnings`.
- Parts list: `GET result.parts.parts_url` → `PartSetOut`. Rows use `label`, `kind`, `side`, `geometry_status`,
  `confidence`, `thumbnail_url`; badges for `physical_deferred` and `occluded_geometry_missing`.
- Viewer "Parts" source: load `assembled_url`. Toggle or highlight parts by glTF node name `parts[].node_name`
  (host = `body`). Regions are not nodes; highlight them via `region_attribute` (`_FEATURE_ID_0` = `label_value`).
- Downloads: `parts[].download_url`, `result.parts.assembled_download_url`, `result.parts.export_zip_url`. All file
  URLs accept `?token=` when auth is on.
- Prompt jobs: show the generated image via the reference-set thumbnail of `source.generated_image_id`, or
  `GET /jobs/{id}/files/source/t2i_0.png` before import.

**Test double:** `test/frontend/mock_backend.mjs` must serve A1–A17 with a `--parts-scenario
none|components|semantic|failed` and `--prompt-scenario unavailable|ok|fail` flag (slice S11), built from small
real files generated at mock start (no fake data in the app itself).

**Reconciliation with `design/frontend_b_command_engine.md` §4** (independent drafts; adapter-only changes):
`Job.steps` there = `Job.pipeline.steps` here, and the step `progress` number = `percent`; `PromptSourceIn` there
has `randomize_image_seed`, which is not needed here (`image_seed: null` derives from the job seed); its
`JobPartsSummaryOut` field names map to `PartsResultOut` (`status`, `counts.*`, `manifest_url` →
`parts_url`, `overlay_url`); `models.updated` there = `model.updated` here; endpoint paths for `/features`,
`/parts/rebuild`, `/parts/assembled.glb`, `/parts/{part_id}/model.glb`, `/parts/{part_id}/mask`,
`/parts/segmentation/overlay` and `/models/{id}/install` match.

---------------------------------------------------------------------------------------------------

## 20. Backward-compatibility matrix

| Surface | Before | After (parts off, no source) | After (new features) |
|---|---|---|---|
| `POST /jobs` body | `{reference_set_id, primary_image_id?, settings}` | identical acceptance; identical 422 for unknown keys | `source`, `settings.parts` optional |
| `job.json` | legacy keys | **identical key set** (defaults excluded) | `kind`, `source`, `pipeline`, `result.parts` |
| `Job` (API) | legacy keys | + `kind: "image_to_3d"`, `source: null`, `pipeline: null` | filled |
| `JobResultOut` | legacy | + `parts: null` | filled |
| `progress` | 9-stage formula | identical | identical semantics (pre/post steps as preprocessing/finalizing) |
| `model.glb`, `preview.png` | engine output | identical bytes (FakeEngine test) and identical engine inputs (TRELLIS CPU test) | identical when prepared conditioning equals upstream (test) |
| `/system`, `limits`, `defaults`, `EngineCapabilities.to_dict()` | pinned | untouched | untouched |
| WS | 6 types | + `features` key inside `hello.data` | + `features.updated`, `model.updated` |
| Tuner | create allowed when Blender exists | **503 `feature_disabled`** until `IMG3D_TUNING_ENABLED=true` (intentional, user rule) | same |
| Retention, delete, recovery | job dirs | unchanged | `parts/`, `source/`, `intermediates/`, `exports/` live inside job dirs; temp staging purged at startup |

---------------------------------------------------------------------------------------------------

## 21. Test plan (all tests under `test/`; run with `& $py -s -m pytest -c E:\14-OCT-2026\Img3D\test\pytest.ini <paths> -q`)

### 21.1 Compatibility (must pass first)

| File | Asserts |
|---|---|
| `test/backend/test_jobs_compat.py` | (1) legacy `POST /jobs` → `job.json` key set equals the pre-change key set, `settings` has no `parts`; (2) the `GenerationRequest` captured by `FakeEngine` has `prepared is None`, `capture == frozenset()` and equals a request built the old way field by field; (3) `model.glb` bytes equal `write_test_glb` output; (4) `JobOut.kind == "image_to_3d"`, `source`/`pipeline`/`result.parts` are null; (5) re-POST of a completed job's `settings` → 202; (6) a committed pre-change fixture `test/backend/fixtures/legacy_job_v1/job.json` (+ tiny `model.glb`) placed in the runtime dir loads at startup and `/result` returns 200; (7) unknown key inside `settings.parts` → 422; (8) with `parts_enabled=False`, `settings.parts.enabled=true` → 503 `capability_unavailable` (`details.code == "disabled"`); with `prompt_to_3d_enabled=False`, a prompt source → 503. |
| existing suites | all 182 backend + aux tests and the engine suite (`test_generation_parity.py`, `test_engine_unit.py`) stay green with unchanged assertions; the only edited existing test line is the `tuning_enabled=True` opt-in in `test_mesh_tuner_api.py`. |
| `test/backend/test_schemas_new.py` | `PromptSourceIn`, `PartOptionsIn`, `CreateJobRequest` bounds and defaults; source-exclusivity errors keep the 422 envelope (`loc ["body", "reference_set_id"]`, `type: "missing"` when no source and no set); `GenerationSettings(parts=None)` dumps without `parts`; `Job` and `JobResultData` load legacy JSON and dump it back with the same keys. |
| `test/engine/test_prepared_conditioning.py` | Img3D conditioning RGB equals upstream `preprocess_image` output (`np.array_equal`) for an RGBA input and for the rembg path with a deterministic stub; the RGBA crop's alpha bbox matches; `CropTransform` maps a pixel round-trip within 1 px. CPU only, no weights. |

### 21.2 Parts library (`test/parts/`, pure, fast)

| File | Asserts |
|---|---|
| `test_taxonomy.py` | ids unique and well-formed; every `symmetric_pair` is mutual; groups cover the user's seven groups; `kind_for` matrix; alias matching for "katana", "twin tails", "cat ears". |
| `test_manifest_schema.py` | round trip of the §10.4 example; unknown fields ignored; wrong `schema` major rejected; `uri` must be relative and whitelisted; duplicate part ids or keys rejected. |
| `test_weld_partition.py` | synthetic meshes (two touching spheres sharing a border, a capped cylinder with UV seams, a double-walled shell): weld groups UV duplicates only; `face_owner` complete (I3); reassembly bit-exact (I4); seams form closed loops on a clean cut and open chains at a non-manifold junction; inner and outer sheet loops land in the same seam record. |
| `test_labels.py` | adjacency diffusion fills a gap; nearest-centroid fill labels a disconnected inner wall; 1-ring majority removes single-face islands; mirror completion pairs `.l/.r` within tolerance. |
| `test_policy.py` | physical below `parts_min_faces` → deferred + warning; eyebrows/eyelashes deferred; separate small shell → physical `unknown#n`; part budget merges extras with `max_parts_exceeded`; granularity coarse/fine mapping. |
| `test_graph.py` | `attached_to` via longest seam; `adjacent_to` border lengths; `symmetric_with` metrics; `covers` from taxonomy; `occludes` from synthetic depth pairs. |
| `test_gltf_export.py` | `assembled.glb` reloaded with trimesh: node names = keys, parent hierarchy, node `extras.img3d`, exactly 1 material and the base number of images, host has `_FEATURE_ID_0` with UNSIGNED_SHORT component type [A to confirm]; per-part GLB vertex order equals `vmap`. |
| `test_reader_consumers.py` | `AssetReader.part()` returns geometry equal to the per-part GLB; `ConsumerRegistry(enabled=False).register()` raises `FeatureDisabledError`; `list()` is empty. |
| `test_validate_publish.py` | validation rejects a tampered buffer hash, a missing uri and a base sha mismatch; publish is atomic and refuses an existing `parts/`. |
| `test_no_torch_import.py` | importing every `parts.*` module in a subprocess leaves `torch`, `diffusers` and `transformers` out of `sys.modules`. |
| `test_heuristics2d.py` (S12) | on synthetic masks: left/right split by x, lid curve monotonic in x, lash band inside the eye bbox margin, eye k-means subregions produce 4 labels. |

### 21.3 Inference seams

| File | Asserts |
|---|---|
| `test/backend/test_model_registry.py` | catalogue complete with licence metadata; `availability()` reports `not_installed` (files), `missing_dependency` (diffusers absent, checked via `importlib.metadata`), `gated`, `license_not_accepted`, `incompatible` (fixture text-encoder config with `transformers_version: "5.2.0"`), `not_approved` per settings; no network access. |
| `test/backend/test_aux_registry.py` | with nothing configured every slot holds an `Unavailable*` with the right state and message; a provider import error becomes `error` with a message (no exception escapes); `GpuResidency.acquire` offloads others (fake engines record calls). |
| `test/engine/test_mesh_labeler.py` (`@pytest.mark.gpu`) | on a synthetic textured GLB with a known front render: registration recovers yaw 0 (IoU ≥ 0.9); lifted labels of a painted region exceed 95 % face agreement; runs with ≤ 2 M faces without the 2²⁴ ID issue. |

### 21.4 Pipeline and services

| File | Asserts |
|---|---|
| `test/backend/test_pipeline_router.py` | legacy job → `ImageTo3DPipeline.run` called with no `overrides` (spy); part job → `PartAwarePipeline`; part job with `part_aware=None` → `capability_unavailable`. |
| `test/backend/test_pipeline_plan.py` | plan per kind (§13.1); `pipeline.percent` monotonic under skips; `fraction(image_to_3d)` from `compute_percent`; pending steps become skipped on failure. |
| `test/backend/test_part_aware_pipeline.py` | with `FakeEngine` (prepared-conditioning capable), `FakeSegmenter`, `FakeMeshLabeler` from `fakes_aux.py`: manifest valid, I1–I6 hold, `result.parts.status == "completed"`; segmentation exception → `partial` with fallback to components; extraction exception on an image job → job completed, `parts.status == "failed"`; cancel during `part_extraction` → job cancelled, no `parts/`, staging removed; step begin/finish published unthrottled. |
| `test/backend/test_part_store_service.py` | `GET`-side resolution: whitelist regex rejects `../`, `parts/../job.json`, backslashes, unknown subfolders, invalid `pt_` ids; `PartSetOut` projection URLs; ZIP contents follow the job-root layout and honour `include_base`. |
| `test/backend/test_feature_service.py` | flags mirror settings; `mesh_tuning.enabled` is false by default and true only with `tuning_enabled` and `blender_enabled`; `prompt_to_3d.code` is `not_approved` / `missing_dependency` / `ok` across fakes; `nodes` list has exactly the six types and no refine types; `params_schema` equals the Pydantic JSON schema. |
| `test/backend/test_prompt_jobs_unit.py` | source exclusivity (both 422 variants); prompt control characters → 422; `max_prompt_chars`; unsupported negative prompt → 422 `unsupported_by_provider`; with `FakeTextToImage`: set created, reserved id used for `inputs/`, `import_state == "imported"`, `ReferenceImage.source` provenance, legacy retry payload → 202 image job. |
| `test/backend/test_prompt_templates.py` | each preset/style yields the documented text and size; `part_hint` adds the clause; final prompt hash stored, raw prompt not logged (caplog). |
| `test/backend/test_reference_import.py` | `import_image` with a reserved id: thumbnail made, primary set, slot front, rejected when the id already exists or the set is gone. |
| `test/backend/test_tuning_gate.py` | default settings: `POST /jobs/{id}/tunings` → 503 `feature_disabled` with `details.feature == "mesh_tuning"` (after the 404/409 checks); listing and reading runs still 200; with `tuning_enabled=True` → 202. |

### 21.5 App-level (after integration, S7)

| File | Asserts |
|---|---|
| `test/backend/test_features_api.py` | A1 shape (top-level keys exact for this new endpoint); auth required when enabled; `hello.data` contains `system` and `features`; `features.updated` on a simulated provider change. |
| `test/backend/test_parts_api.py` | A8–A17 statuses (409 while running, 404 `not_requested` for legacy jobs, 404 for bad part ids, 409 `no_mesh` for regions); file routes accept `?token=` with auth on (pattern of `test_mesh_tuner_api.py:460` [V by contract map]); content types; WS `job.updated` carries `pipeline` transitions in order. |
| `test/backend/test_rebuild_api.py` | A7: derived job kind, hard-linked `model.glb` (same bytes; `os.stat` link count ≥ 2 or copy fallback), parent deletion leaves the child servable, revision = parent + 1, lineage face IoU present, `GET /jobs?kind=part_decomposition&parent_job_id=` filter. |
| `test/backend/test_prompt_jobs_api.py` | end-to-end prompt job with `FakeTextToImage` through the real app; 503 when unconfigured; job and set ids non-null. |
| `test/backend/test_models_api.py` | A3/A4 shapes; A18 → 403 `model_install_disabled` by default; 403 `model_not_approved`; 422 `confirmation_required` and `license_acceptance_required`. |
| `test/engine/run_part_smoke.py` (manual, real GPU) | ALICE and ANIME_CHARACTER_01 with parts on (`segmentation=none`, then each approved provider): job completes, manifest validates, reassembly exact, timings recorded; same seed with parts off and on → compare `model.glb` sha256 (informational: GPU kernels may be non-deterministic; the binding guarantee is §21.1 item 2 plus `test_prepared_conditioning.py`). |

---------------------------------------------------------------------------------------------------

## 22. Implementation plan: slices with disjoint file ownership

Rules: a slice edits **only** its listed files, and ownership is disjoint inside each wave. A file may pass to an
owner in a **later** wave (`services/job_service.py`: S1 → S6; `api/v1/models.py`: S5 → S14). Shared seam files
(`inference/base.py`, `core/exceptions.py`, `models/*`) are edited in S1 only. Every slice ends with the full
backend suite green. `api-contract.md` is updated first (S0) and only by S0; later slices report deviations to
the S0 owner instead of editing it.

### Wave 0 (sequential)

**S0 — Contract and docs.** Owns: `.claude/skills/img3d-architecture/api-contract.md` (new §11 Asset model and
manifest, §12 Pipeline kinds and steps, §13 Features, providers and models, §14 Prompt-to-3D; amendments to §2
settings, §4 errors, §5 inference seam, §7.4 jobs, §8 WS, §9 runtime; fix the drift listed in contract map §6.1
item 10), `.claude/skills/img3d-architecture/SKILL.md` (layering: `parts/` torch-free library; aux engines),
new `.claude/skills/img3d-part-aware/SKILL.md` (index of research and design).
Acceptance: the user approves the contract; every name in §13–§18 of this document appears there.

### Wave 1 (sequential)

**S1 — Foundation (domain, schemas, settings, seams; no behaviour change).** Owns:
`backend/models/pipeline.py` (new: `JobKind`, `StepId`, `StepStatus`, `StepWarning`, `PipelineStepState`,
`PipelineState`, `PromptSpec`, `JobSource`, `PartsSummary`), `backend/models/job.py`, `backend/models/reference.py`
(`ImageSource`, `ReferenceImage.source`), `backend/schemas/jobs.py`, `backend/schemas/prompt.py`,
`backend/schemas/parts_options.py`, `backend/schemas/features.py`, `backend/schemas/parts.py`,
`backend/schemas/models.py`, `backend/schemas/taxonomy.py`, `backend/core/exceptions.py`,
`backend/config/settings.py`, `backend/storage/paths.py`, `backend/utils/ids.py` (`PART_PREFIX`),
`backend/inference/base.py` (§14.1 additive types and defaults), `backend/inference/aux_base.py` (new, §14.2),
`backend/services/capabilities.py` (new: `CapabilityResolver(settings, engine, aux=None, part_pipeline_ready=False)`,
`require(capability, provider=None)`), `backend/storage/job_store.py` (default-excluding dump),
`backend/services/job_service.py` (settings dump excludes `parts`; capability checks for `source` and `parts`;
`plan_factory` and `create_derived_job` stubs raising `CapabilityUnavailableError`),
`test/backend/test_jobs_compat.py`, `test/backend/fixtures/legacy_job_v1/**`, `test/backend/test_schemas_new.py`.
Acceptance: §21.1 green; with defaults, a request with `settings.parts` or `source` gets 503
`capability_unavailable`; existing 182 + engine tests green; no new GET routes yet.

### Wave 2 (parallel)

**S2 — Parts core library.** Owns: `backend/parts/__init__.py`, `taxonomy.py`, `model.py`, `manifest_io.py`,
`buffers.py`, `layout.py` (relative paths, A17 whitelist regex), `publish.py`, `mesh_io.py`, `weld.py`,
`components.py`, `labels.py`, `policy.py`, `partition.py`, `seams.py`, `graph.py`, `frames.py`, `gltf_export.py`,
`reader.py`, `reassemble.py`, `validate.py`, `consumers.py`; `test/parts/**` **except** `test_heuristics2d.py`.
Acceptance: §21.2 green (except heuristics); torch-free import test green; partition + export of a 2 M-face
synthetic mesh completes in < 60 s on CPU [A, measured and reported].

**S3 — Inference seams, registry, TRELLIS prepared conditioning, test doubles.** Owns:
`backend/inference/aux_registry.py`, `aux_unavailable.py`, `model_registry.py`, `gpu_residency.py`,
`backend/inference/trellis/conditioning.py` (new), `trellis/engine.py`, `trellis/generation.py`,
`trellis/imaging.py`, `backend/storage/licence_store.py`, `test/backend/fakes.py` (additive: `FakeEngine` gains
`extensions()` and a deterministic `prepare_conditioning`), `test/backend/fakes_aux.py` (new: `FakeTextToImage`,
`FakeSegmenter`, `FakeMeshLabeler`, `write_parts_test_glb()` multi-component textured GLB),
`test/engine/test_prepared_conditioning.py`, `test/backend/test_model_registry.py`,
`test/backend/test_aux_registry.py`.
Acceptance: §21.3 rows 1–2 and `test_prepared_conditioning.py` green; `test_generation_parity.py` unchanged and
green; latents capture behind `parts_capture_latents` (off) with a unit test of the npz layout on fake tensors.

**S9 — Tuning gate.** Owns: `backend/services/mesh_tuner_service.py`, `test/backend/test_tuning_gate.py`,
`test/backend/test_mesh_tuner_api.py` (one line: `tuning_enabled=True` default in `make_tuner_app`).
Acceptance: §21.4 tuning row green; the 20 tuner API tests green.

**S10 — Mesh labeller (nvdiffrast lifting).** Owns: `backend/inference/geometry/__init__.py`,
`geometry/cameras.py` (look-at, intrinsics; replaces the missing `utils3d`), `geometry/raster.py` (face-ID and
depth raster on the GLB arrays), `geometry/registration.py` (silhouette grid + local refine, §7.3),
`geometry/nvdiffrast_labeler.py` (`NvdiffrastMeshLabeler(MeshLabeler)`), `test/engine/test_mesh_labeler.py`.
Acceptance: GPU test green on the 5090; CPU-only import of the module without CUDA reports `error` state through
the registry rather than raising.

**S11 — Frontend test double for the new contract.** Owns: `test/frontend/mock_backend.mjs`,
`test/frontend/parts_mock_smoke.mjs` (new). Acceptance: the mock serves A1–A17 and the WS additions with the
scenario flags of §19; the existing `mesh_tuner_mock_smoke.mjs` still passes.

**S12 — 2D heuristics.** Owns: `backend/parts/heuristics2d.py`, `test/parts/test_heuristics2d.py`.
Acceptance: §21.2 heuristics row green; numpy/opencv-headless only.

### Wave 3 (parallel; needs S2 and S3)

**S4 — Pipeline orchestration.** Owns: `backend/pipelines/router.py`, `plan.py`, `context.py`,
`part_aware.py`, `pipelines/steps/__init__.py`, `steps/base.py`, `steps/registry.py`, `steps/input_image.py`,
`steps/segmentation_2d.py`, `steps/reconstruction.py`, `steps/part_extraction.py`, `steps/export.py`,
`backend/pipelines/image_to_3d.py` (`overrides` keyword only), `backend/core/loop_bridge.py`,
`test/backend/test_pipeline_router.py`, `test/backend/test_pipeline_plan.py`,
`test/backend/test_part_aware_pipeline.py`. `workers/generation_worker.py` is **not** edited.
Acceptance: §21.4 rows 1–3 green with fakes.

**S5 — Services and API (features, taxonomy, models, parts files).** Owns: `backend/services/feature_service.py`,
`services/node_catalog.py`, `services/part_service.py`, `services/model_service.py`, `backend/storage/part_store.py`,
`backend/api/v1/features.py`, `api/v1/taxonomy.py`, `api/v1/models.py`, `api/v1/parts.py` (A7–A16; A7 delegates to
`JobService.create_derived_job`), `api/v1/job_files.py` (A17), `test/backend/test_part_store_service.py`,
`test/backend/test_feature_service.py`.
Acceptance: §21.4 rows 4–5 green with a manifest produced by S2's writer in a fixture.

**S6 — Prompt-to-3D and derived jobs.** Owns: `backend/pipelines/steps/prompt.py`,
`backend/pipelines/prompt_templates.py`, `backend/services/job_service.py` (prompt branch, `create_derived_job`
with hard links, `list_jobs(kind=, parent_job_id=)`), `backend/services/reference_service.py` (`import_image`),
`backend/utils/files.py` (`link_or_copy`), `backend/api/v1/jobs.py` (A6 filters),
`test/backend/test_prompt_jobs_unit.py`, `test/backend/test_prompt_templates.py`,
`test/backend/test_reference_import.py`.
Acceptance: §21.4 rows 6–8 green; `test_jobs.py` unchanged and green.

### Wave 4 (sequential)

**S7 — Integration.** Owns: `backend/core/container.py` (build `ModelRegistry`, `AuxEngineSet`, `GpuResidency`,
`Taxonomy`, `LoopBridge`, step registry incl. `steps/prompt.py`, `PartAwarePipeline`, `PipelineRouter`,
`CapabilityResolver(part_pipeline_ready=True)`, services; widen `ServiceContainer.pipeline`),
`backend/core/lifecycle.py` (bind `LoopBridge`; aux unload on shutdown via the GPU executor; Blender detection gate),
`backend/api/router.py`, `backend/api/websocket_hub.py` (`hello.data.features`), `backend/schemas/events.py`,
`backend/services/system_service.py` (`/system/engine/unload` also releases aux engines), `test/README.md`,
`.claude/skills/img3d-run-and-test/SKILL.md` (new test paths and the part smoke), §21.5 test files,
`test/engine/run_part_smoke.py`.
Acceptance: full suites green (backend, aux, parts, engine); §21.5 green; manual GPU smoke on both sample images
with `segmentation=none` produces valid manifests; the frontend mock smoke passes against the real backend shapes
(field-by-field diff report).

### Wave 5 (only after the user approves models)

**S8 — Text-to-image provider(s).** Owns: `backend/inference/t2i/__init__.py`, `t2i/diffusers_engine.py`
(Z-Image-Turbo, FLUX.2-klein-4B and/or SDXL single-file adapters; `callback_on_step_end` → progress, `_interrupt`
→ cancel; private generator), `backend/requirements.txt` (`diffusers==0.39.0`, `accelerate==1.15.0`),
`backend/constraints.txt` (new; pins from the environment audit §9), `THIRD_PARTY_NOTICES.md` entries,
`test/engine/test_t2i_smoke.py` (`gpu`). Acceptance: dry-run install shows exactly the two packages and no
upgrades; TRELLIS real-generation smoke unchanged; one prompt job end to end.

**S13 — Segmentation provider(s).** Owns: `backend/inference/segmentation/__init__.py`, `gdino_sam2.py` and the
approved optional ones (`see_through.py`, `sam3.py`, `sapiens2.py`), `backend/third_party/<vendored>` submodules,
`THIRD_PARTY_NOTICES.md` entries, `test/engine/test_segmentation_smoke.py` (`gpu`).
Acceptance: provider states turn `available`; ALICE and ANIME_CHARACTER_01 runs produce named parts; per-class IoU
and timings reported against the hand-labelled set (segmentation research §8).

**S14 — Model install endpoint (optional).** Owns: `backend/services/model_install.py`, the A18–A19 handlers in
`api/v1/models.py` (S5 hands the file over), `test/backend/test_model_install.py`. Acceptance: confirm + licence
acceptance flow, resumable download into `.model/<local_dir>`, `model.updated` events, sha pin recorded.

S8/S13 touch `THIRD_PARTY_NOTICES.md`; run them sequentially or let S13 rebase on S8.

---------------------------------------------------------------------------------------------------

## 23. Open questions for the user

1. **Text-to-image model**: Z-Image-Turbo (32.8 GB, Apache-2.0), FLUX.2-klein-4B (16 GB, Apache-2.0), Animagine
   XL 4.0 (the 6.46 GB file already in `D:\10-SEP-2026\T2I\.models\…`, OpenRAIL++), several, or none for now? Any
   choice also approves installing `diffusers==0.39.0` and `accelerate==1.15.0` with the constraints file.
2. **Copy Animagine into Img3D?** Using the sibling project's file in place would couple Img3D to another folder. The
   proposal copies it into `backend/.model/t2i/animagine-xl-4.0/` if you choose it.
3. **Segmentation stack**: the Grounding DINO + SAM 2.1 (+ Florence-2) baseline (~3.4 GB, Apache/MIT, no new
   packages)? Optional See-through anime parser (vendored code), SAM 3 (gated, SAM Licence, vendored code plus
   `ftfy`/`iopath`), Sapiens2 (custom licence)?
4. **In-app downloads**: should the backend download approved models after an explicit confirmation (S14), or will
   you place weights manually?
5. **Server-side tuning gate**: OK that `POST /jobs/{id}/tunings` returns 503 until `IMG3D_TUNING_ENABLED=true`
   (one-line opt-in in the tuner test fixture)? The alternative is only `IMG3D_BLENDER_ENABLED=false` in your
   local `.env`.
6. **Eyebrows and eyelashes**: accept `physical_deferred` (mask, 3D region and curve now; geometry later) for this
   milestone?
7. **Per-part GLB files by default**: they cost roughly one extra `model.glb` of disk per job. Keep the default on?
8. **Derived jobs in history**: show part rebuilds as separate history entries (grouped by `parent_job_id` in the
   new UIs), or hide them from `GET /jobs` unless `kind` is requested?
9. **Default for parts in the UI**: should new jobs request parts by default in the new UI modes, or stay opt-in
   (the Default UI keeps today's request either way)?
10. **Multi-view labelling**: default `extra_views=0` (input view + propagation) until a segmenter is approved and
    measured, then raise to 8?

---------------------------------------------------------------------------------------------------

## 24. Risks and mitigations

| Risk | Mitigation |
|---|---|
| The prepared-conditioning re-implementation drifts from upstream and changes part-enabled reconstructions | Used only when parts are on; pixel-equality tests for both preprocess modes; legacy path untouched. |
| Remesh topology: labels bleed about 1–2 voxels at contacts; double shell; non-manifold junctions | Labels are computed on final faces with propagation, nearest fill for inner walls, majority smoothing; seams record open chains honestly; `charts_cross_boundary` flagged. |
| Registration fails on non-frontal or object inputs | IoU gate 0.6 → geometry-only parts plus a warning; nothing guessed. |
| Anime eyebrows and lashes missed by open-vocabulary detectors | Deferred kind plus heuristics from eye masks; provider choice is the user's; confidence recorded. |
| GPU contention or OOM when aux models are added | Single GPU thread, `GpuResidency`, low-VRAM pattern, OOM → optional step fails without losing the base model. |
| Long GPU-thread occupancy by CPU-only export | Measured in S7; a later optimisation can move export to a CPU executor (it touches no CUDA). |
| Disk growth (assembled + per-part GLBs ≈ 2× `model.glb`) | `parts_write_part_files` setting; existing retention unchanged. |
| Dependency breakage when installing diffusers | Wave 5 only, constraints file, dry-run first, TRELLIS smoke after. |
| Licences in a public GPL-3.0 repo | Registry licence metadata, acceptance records, notices per model; non-commercial models never vendored; nvdiffrast NC already documented. |
| Frontend and backend drafts disagree on names | §19 reconciliation; frontend adapters isolate wire names; the S0 contract is the arbiter. |

---------------------------------------------------------------------------------------------------

## Appendix A. New and changed backend modules (one line each)

```
backend/
  models/pipeline.py              JobKind, StepId, StepStatus, PipelineState, JobSource, PromptSpec, PartsSummary   (S1)
  models/job.py                   + Job.kind/source/pipeline, JobResultData.parts                                  (S1)
  models/reference.py             + ImageSource, ReferenceImage.source                                             (S1)
  schemas/{jobs,prompt,parts_options,features,parts,models,taxonomy}.py                                           (S1)
  core/exceptions.py              + CapabilityUnavailableError, FeatureDisabledError                               (S1)
  core/loop_bridge.py             LoopBridge                                                                       (S4)
  config/settings.py              + §16 fields                                                                     (S1)
  storage/paths.py                + job_source/intermediates/parts/exports, temp_parts                             (S1)
  storage/job_store.py            default-excluding dump                                                           (S1)
  storage/part_store.py           manifest load (mtime cache), file resolution, ZIP export cache                   (S5)
  storage/licence_store.py        runtime/licences.json                                                            (S3)
  inference/base.py               + CropTransform, PreparedConditioning, EngineExtensions, request fields, defaults (S1)
  inference/aux_base.py           AuxEngine ABCs, requests/outputs, AuxEngineSet                                   (S1)
  inference/aux_registry.py       create_aux_engines                                                               (S3)
  inference/aux_unavailable.py    Unavailable* providers                                                           (S3)
  inference/model_registry.py     ModelSpec, LicenceInfo, ModelRegistry                                            (S3)
  inference/gpu_residency.py      GpuResidency                                                                     (S3)
  inference/trellis/conditioning.py + engine.py/generation.py/imaging.py edits                                    (S3)
  inference/geometry/*            NvdiffrastMeshLabeler, cameras, raster, registration                             (S10)
  inference/t2i/*, inference/segmentation/*                                                                        (S8, S13)
  parts/*                         torch-free asset library (§2–§12)                                                (S2, S12)
  pipelines/{router,plan,context,part_aware,prompt_templates}.py, pipelines/steps/*                                (S4, S6)
  pipelines/image_to_3d.py        + overrides keyword                                                              (S4)
  services/capabilities.py        CapabilityResolver                                                               (S1)
  services/{feature_service,node_catalog,part_service,model_service}.py                                            (S5)
  services/job_service.py         settings dump + checks (S1); prompt jobs, derived jobs, kind filter (S6)
  services/reference_service.py   + import_image                                                                   (S6)
  services/mesh_tuner_service.py  + tuning gate                                                                    (S9)
  services/system_service.py      engine unload also releases aux engines                                          (S7)
  api/v1/{features,taxonomy,models,parts,job_files}.py                                                             (S5)
  api/v1/jobs.py                  + kind / parent_job_id filters                                                   (S6)
  api/router.py, api/websocket_hub.py, schemas/events.py, core/container.py, core/lifecycle.py                     (S7)
  utils/ids.py (+PART_PREFIX, S1), utils/files.py (+link_or_copy, S6)
  workers/generation_worker.py    unchanged
```
