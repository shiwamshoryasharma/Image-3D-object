# TRELLIS.2 internals for a part-aware Img3D backend

Research for the "Tripo-style part-aware pipeline" milestone. This phase was read-only: no project file was
changed except this report, nothing was installed or downloaded, and no GPU work or backend run happened.

Every claim is tagged:
- **[V]** verified: I read the code or data cited.
- **[V-run]** verified from existing job metadata (`backend/runtime/jobs/*/job.json`). The two excluded jobs were
  not touched.
- **[A]** assumption or inference, not executed. It needs a runtime check before anyone relies on it.

Paths are relative to `E:/14-OCT-2026/Img3D`. "Upstream" means `backend/third_party/TRELLIS.2` (microsoft/TRELLIS.2
at commit `75fbf01`). CuMesh sources are at `backend/third_party/extensions/CuMesh` (commit `12289e1`), and the
installed packages are in `backend/python/Lib/site-packages`.

---

## 0. Short answers

| Question | Answer |
|---|---|
| (1) Pipeline | DINOv3 image tokens → sparse structure flow (16³×8 latent → 64³ occupancy, max-pooled to 32³) → shape SLat flow (32³ tokens; cascade adds 64³ or 96³ tokens) → texture SLat flow (same tokens) → sparse decoders upsample ×16 to 512/1024/1536³ voxels → flexible-dual-grid mesh (**one vertex per voxel**) plus 6-channel PBR voxels → `to_glb`. `to_glb` does fill holes → BVH → narrow-band dual-contouring remesh → cumesh simplify → cumesh/xatlas UV unwrap → nvdiffrast UV rasterization → BVH projection to the raw mesh → trilinear voxel sampling → cv2 inpaint → trimesh PBR GLB. |
| (2) Native parts? | **No.** There is no part or instance channel anywhere. The training data concatenates all Blender objects into one mesh before voxelizing. **However, an exact chain of correspondences exists** from token to voxel to raw-mesh vertex to raw-mesh face (same order, verified). It breaks at `to_glb`'s remesh and simplify, and **can be recovered** with the BVH nearest-surface query that `to_glb` itself uses to bake textures. 2D labels can therefore be lifted onto voxels and carried to the final faces. |
| (3) Lifting 2D masks | The output is in a **canonical object frame**, not the camera frame. Conditioning views at training time were random over the sphere, with FOV 10–70° and a tight square crop. Front is −Y in the TRELLIS/Blender frame and +Z in glTF; image-right is +X. The input camera must be **estimated** (silhouette and colour fit, seeded at the front view). After that, a face-ID render of the raw mesh with nvdiffrast gives exact per-pixel face IDs. Hidden surfaces need multi-view voting and/or voxel-graph label diffusion. **Pitfall:** raw meshes at 1536 have up to 16.5M faces, right at nvdiffrast's 2²⁴ float-ID limit, so IDs above 16,777,216 need the bit-decode rule. |
| (4) Per-part GLBs with UVs | `to_glb` has no face-subset or multi-mesh API. **Cheapest correct option:** keep the single `to_glb` call unchanged, label the final faces through a BVH projection to the labelled raw mesh, then `trimesh.submesh` per part and export one multi-node GLB. trimesh dedupes the material by hash, so all parts share **one** atlas and nothing is re-baked. **Better option (later):** an Img3D-owned copy of `to_glb` that computes charts per part and packs all parts into one xatlas atlas, so UV islands never cross part borders. Calling `to_glb` once per part works but re-runs remesh and bake, and gives K textures. |
| (5) Texturing pipeline | `Trellis2TexturingPipeline.run(mesh: trimesh, image, seed, tex params, resolution 512/1024, texture_size)` re-encodes any mesh into a shape SLat, samples the texture SLat, decodes PBR voxels, and bakes onto the **input topology and UVs**. It does not simplify or remesh. It can retexture parts if they are passed **in the shared global frame**: texture the assembly and then split it, rather than normalizing each part on its own. Img3D does not load it today; it would need about 0.71 GB extra (`shape_enc`) and reuses all other modules. |
| (6) Key pitfalls | The double-walled shell comes from `remesh=True, band=1, project=0`. UV-seam duplicates are 39–50% of vertices. Non-manifold junctions split cumesh connected components. Labels bleed about 1 voxel at part contacts. The 2²⁴ triangle-ID limit. Parts generated separately are re-normalized into the unit cube. `utils3d` is not installed, so `render_utils` and `PbrMeshRenderer` cannot be imported, while `MeshRenderer` can. |

---

## 1. The full pipeline, stage by stage

### 1.1 Model files (`backend/.model/TRELLIS.2-4B`, MIT, <https://huggingface.co/microsoft/TRELLIS.2-4B>) [V]

`pipeline.json` (`Trellis2ImageTo3DPipeline`, `default_pipeline_type: "1024_cascade"`) and the `ckpts/*.json` configs:

| key | checkpoint | class / key args | size |
|---|---|---|---|
| sparse_structure_flow_model | `ss_flow_img_dit_1_3B_64_bf16` | `SparseStructureFlowModel` res 16, in/out 8, 1536 ch, 30 blocks, 12 heads, RoPE, cond 1024 | 2,584,426,920 B |
| sparse_structure_decoder | `microsoft/TRELLIS-image-large/ckpts/ss_dec_conv3d_16l8_fp16` (TRELLIS v1) | `SparseStructureDecoder` latent 8, channels [512,128,32], 2 upsamplings, so 16³ → 64³ | 147,591,972 B |
| shape_slat_flow_model_512 | `slat_flow_img2shape_dit_1_3B_512_bf16` | `SLatFlowModel` res 32, in/out 32 | 2,584,574,424 B |
| shape_slat_flow_model_1024 | `slat_flow_img2shape_dit_1_3B_1024_bf16` | `SLatFlowModel` res 64, in/out 32 | 2,584,574,424 B |
| shape_slat_decoder | `shape_dec_next_dc_f16c32_fp16` | `FlexiDualGridVaeDecoder` latent 32, channels [1024,512,256,128,64], blocks [4,16,8,4,0], 4× `SparseResBlockC2S3d` (×2 each, so ×16 total), out 7 | 948,490,494 B |
| tex_slat_flow_model_512 / _1024 | `slat_flow_imgshape2tex_dit_1_3B_{512,1024}_bf16` | `SLatFlowModel` res 32/64, **in 64** (32 noise + 32 shape concat), out 32 | 2,584,672,728 B each |
| tex_slat_decoder | `tex_dec_next_dc_f16c32_fp16` | `SparseUnetVaeDecoder` out 6, **`pred_subdiv: false`** (follows the shape decoder's subdivisions) | 948,458,812 B |
| (texturing only) shape_slat_encoder | `shape_enc_next_dc_f16c32_fp16` | `FlexiDualGridVaeEncoder` in 6 (3 dual-vertex offsets + 3 intersect flags) | 708,797,208 B |
| (unused) tex encoder | `tex_enc_next_dc_f16c32_fp16` | `SparseUnetVaeEncoder` in 6 | 708,797,208 B |
| image_cond_model | DINOv3 ViT-L/16 (`backend/.model/dinov3-vitl16-pretrain-lvd1689m`, [DINOv3 License](https://ai.meta.com/resources/models-and-libraries/dinov3-license/)) | hidden 1024, patch 16, 4 register tokens | 1,212,559,808 B |
| rembg_model | BiRefNet class (Img3D points it at BiRefNet MIT or RMBG-2.0 CC BY-NC) | | |

Sampler defaults (`FlowEulerGuidanceIntervalSampler`, `sigma_min 1e-5`) [V]:
- **SS:** 12 steps, CFG 7.5, rescale 0.7, interval [0.6, 1.0], rescale_t 5.0.
- **Shape:** 12 steps, 7.5, 0.5, [0.6, 1.0], 3.0.
- **Texture:** 12 steps, **CFG 1.0** (effectively no CFG), 0.0, [0.6, 0.9], 3.0.

Shape and texture SLats are normalized with the 32-dimensional mean/std in `pipeline.json`.

### 1.2 Stage table (`trellis2/pipelines/trellis2_image_to_3d.py`; Img3D replica in `backend/inference/trellis/generation.py`)

| # | Stage (Img3D `JobStage`) | Code | Produces [V unless marked] |
|---|---|---|---|
| 0 | preprocessing | `preprocess_image` L127–162; Img3D `_preprocess` generation.py L137–178 | RGBA input or rembg output. The image is downscaled so its longest side is ≤1024 (L137–140). The bbox is taken from `alpha > 0.8·255` (L152). A **tight square crop** is centred on the bbox with side = max(w, h) and **no margin** (L153–158); PIL fills out-of-image areas with transparent black. RGB is premultiplied by alpha on **black** (L159–161). **Alpha and the crop box are discarded.** Img3D saves this RGB image as `preview.png` (`make_preview`, imaging.py L68–73; no resize because it is already ≤1024). |
| 1 | conditioning | `get_cond` L164–186; `DinoV3FeatureExtractor` image_feature_extractor.py L59–118 | The image is resized to 512² (and 1024² unless the pipeline is "512") → DINOv3 tokens `(1, 1+4+32², 1024)` = (1, 1029, 1024) and (1, 4101, 1024) [A: CLS + 4 registers + patches, from the HF embedding layout]. `neg_cond` is zeros. |
| 2 | sparse_structure | `sample_sparse_structure` L188–235 | Noise `(1, 8, 16, 16, 16)` → flow → `z_s` → SS decoder → logits `(1, 1, 64, 64, 64)`, thresholded at `> 0` → `max_pool3d` to 32³ for 512, 1024_cascade and 1536_cascade (L230–232; `ss_res` map at L541) → `coords (N, 4) int32 = [batch, x, y, z]` (L233). Real jobs: **1,621–2,579 occupied 32³ cells** [V-run]. |
| 3 | shape | `sample_shape_slat` L237–275 or `sample_shape_slat_cascade` L277–364 | LR: `SparseTensor(feats (N, 32), coords)` sampled with the 512 model on 32³ (cond 512) and de-normalized. **Upsample:** `shape_slat_decoder.upsample(slat, 4)` (sparse_unet_vae.py L509–522) runs the decoder's 4 C2S stages and returns coords at **512³**. These are re-quantized to an `hr_resolution/16` grid (64³ for 1024, **96³ for 1536**) with `unique` (L328–333). If there are ≥ `max_num_tokens = 49,152` tokens, `hr_resolution` drops in 128 steps but never below 1024 (L334–339). HR: the **1024 model** is sampled on those coords with cond 1024. 1536 therefore uses the 1024 model on a 96³ grid. Real jobs: **16,050–26,713 HR tokens** at 1536 [V-run]. |
| 4 | texture | `sample_tex_slat` L391–432 | Noise `(N, 32)` on the **same coords** (`shape_slat.replace`, L412). `concat_cond` = normalized shape SLat (channels concatenated; `SLatFlowModel.forward` L177–178). Image cond is via cross-attention. Output: tex SLat `(N, 32)` on identical coords and order. |
| 5 | decoding | `decode_latent` L455–486 | (a) `decode_shape_slat` L366–389: decoder `set_resolution(res)` then `forward(slat, return_subs=True)` gives `h` at res³ with 7 channels plus `subs` (4 subdivision masks). `FlexiDualGridVaeDecoder.forward` eval branch (fdg_vae.py L97–110) splits these into dual-vertex offset `(1+2·0.5)·σ − 0.5 ∈ (−0.5, 1.5)` voxel units, 3 "intersected" flags `> 0`, and a quad split weight `softplus`. It then calls `flexible_dual_grid_to_mesh` (o_voxel/convert/flexible_dual_grid.py L142–283). (b) `decode_tex_slat` L434–453: tex decoder with **`guide_subs=subs`**, `·0.5 + 0.5`, giving 6 channels (base_color 0:3, metallic 3, roughness 4, alpha 5; layout L73–78). (c) `m.fill_holes()` (mesh/base.py L35–57, max perimeter 3e-2). (d) `MeshWithVoxel(vertices, faces, origin [−0.5]³, voxel_size 1/res, coords (M, 3), attrs (M, 6), voxel_shape (1, 6, res, res, res))` (mesh/base.py L190–234). Real jobs at 1536: **4.76–8.29M vertices, 9.68–16.47M faces**, decode 1.3–27 s [V-run]. |
| 6 | exporting | Img3D `export_glb` (export.py L20–93) → `o_voxel.postprocess.to_glb` (postprocess.py L14–331) | See §1.3. Real jobs: **bake 70–323 s** at 2M faces / 4096² (the dominant cost) [V-run]. |

The decoded mesh is a single unit-cube object: AABB `[−0.5, 0.5]³`, longest side ≈ 1.0. For example, job_04535's glTF bbox is 0.78 × 1.00 × 0.39 [V-run].

Resolution summary [V]:

| pipeline_type (Img3D res) | SS grid used | Stage-3 token grid(s) | Decoded voxel grid | Tex flow / cond |
|---|---|---|---|---|
| "512" (512) | 32³ | 32³ (512 model) | 512³ | 512 model / DINOv3@512 |
| "1024" (not exposed by Img3D) | 64³ | 64³ (1024 model) | 1024³ | 1024 / @1024 |
| "1024_cascade" (1024) | 32³ | 32³ (512 model) → 64³ (1024 model) | 1024³ | 1024 / @1024 |
| "1536_cascade" (1536) | 32³ | 32³ → 96³ (1024 model; fallback 88³/80³/72³/64³) | 1536³ (or 1408/1280/1152/1024) | 1024 / @1024 |

### 1.3 How the final mesh and PBR textures are made (`o_voxel/postprocess.py::to_glb`)

Img3D calls it exactly like upstream `app.py` L493–507: `grid_size=res`, `aabb=[[−.5]*3, [.5]*3]`, `decimation_target=polygon_target`, `texture_size`, **`remesh=True, remesh_band=1, remesh_project=0`** (export.py L41–56) [V].

1. **Fill holes and build a BVH (L104–122).** `CuMesh.init`, then `fill_holes(3e-2)`, then `read()`, then `cumesh.cuBVH(vertices, faces)`. The BVH is built on this raw, hole-filled mesh and kept for baking. `cuBVH` converts to numpy (bvh.py L16–23).
2. **Remesh branch (L165–187).** `cumesh.remeshing.remesh_narrow_band_dc` (remeshing.py):
   - It sets `eps = band·scale/res` (L100), with `scale = (res + 3·band)/res`.
   - It builds a sparse narrow band by octree refinement from ≤32³ (L113–136) and evaluates the **unsigned** distance, extracting the isosurface of `|UDF − eps|` (L124, L164).
   - It runs simple dual contouring and splits quads into triangles.
   - `project_back = 0` skips the snap-back step (L238).
   - **Result:** a closed shell on *both* sides of the raw surface, about 2 voxels thick. This is the root cause of the "hollow double-walled shell" defect (trellis_mesh_defects.md §6.1).
   - It then calls `simplify(decimation_target)`, a GPU quadric-style collapse (cumesh.py L320–359). The target is a **face** count. No vertex or face map is returned.
   - The non-remesh branch (L134–162) instead does simplify ×3, removes duplicate faces, repairs non-manifold edges, removes small components, fills holes, simplifies to target and unifies orientation, with `doubleSided=True`.
3. **UV unwrap (L201–216).** `CuMesh.uv_unwrap` (cumesh.py L408–480):
   - It removes degenerate faces, then GPU `compute_charts` clusters faces using cone angle 90°, 0 refine iterations, 1 global iteration and smooth 1.
   - Every chart is added as a separate mesh to **one** `cumesh.xatlas.Atlas`; xatlas parameterizes and packs them.
   - It returns new `vertices, faces, uvs, vmaps`. `vmaps` maps each new vertex to a simplified-mesh vertex. Faces are reordered chart by chart, and seam vertices are duplicated.
   - Vertex normals come from `mesh.read_vertex_normals()[vmaps]`.
4. **Bake (L229–266).**
   - nvdiffrast `RasterizeCudaContext` rasterizes the **UV layout** at `texture_size²`. The UVs are mapped to clip space as `[uv·2−1, 0, 1]`, in chunks of 100k faces, and the face ID is stored in the `rast` alpha channel (`+= i`).
   - `dr.interpolate` gives the 3D position of every texel. `bvh.unsigned_distance(pos, return_uvw=True)` projects each texel to the closest **raw-mesh face** (face_id + barycentrics).
   - Each projected point is re-expressed on that raw face (L254–256).
   - `flex_gemm.ops.grid_sample.grid_sample_3d(attrs, coords, shape (1, 6, R, R, R), mode='trilinear')` samples the PBR voxel volume (L259–266).
5. **Finish (L278–331).**
   - Channels are converted to uint8 and `cv2.inpaint` (TELEA) dilates them into empty texels: radius 3 for base color, 1 for the others.
   - `trimesh.visual.material.PBRMaterial` gets a baseColor RGBA texture (alpha in A) and a metallicRoughness texture (G = roughness, B = metallic). The material is `OPAQUE`, and **`doubleSided = not remesh`**, so it is False for Img3D.
   - **Axis swap to glTF:** `y_gltf = z_trellis, z_gltf = −y_trellis`, applied to vertices and normals, and `v = 1 − v` (L313–315).
   - Img3D then writes it with `glb.export(..., extension_webp=...)` (export.py L68).

---

## 2. Native parts and the correspondence chain

### 2.1 No native notion of parts or instances [V]
- No model input or output has a part, instance or object channel. The SS decoder has 1 output channel (occupancy), the shape decoder 7 and the tex decoder 6.
- **The training data merges objects.** `data_toolkit/blender_script/dump_mesh.py` L193–222 dumps each Blender object separately, but `data_toolkit/dual_grid.py` L38–48 concatenates all objects into one vertex and face list before `mesh_to_flexible_dual_grid`. PBR voxelization (`o_voxel/convert/volumetic_attr.py::blender_dump_to_volumetric_attr`, L305+) also loops over `dump['objects']` into one volume.
- **One dual vertex per voxel** (`flexible_dual_grid_to_mesh` L222–243). Two surfaces that pass through the same voxel cannot both be represented. Interpenetrating or touching parts (eyeball under eyelid, scalp under hair, skin under clothes) collapse into one surface. At 1536 one voxel is 1/1536 of the longest side, about 1.1 mm for a 1.75 m figure.
- Hidden geometry (the body under clothes, the full eyeball) is therefore normally **absent**. The defect study confirms this: eyes are shallow recesses and the mouth exists only in the texture (trellis_mesh_defects.md §6.11).
- **Implication:** "physically separating" eyes, eyebrows, eyelashes or clothes from a TRELLIS mesh gives open surface patches, and leaves holes in the remaining body. A true detachable part with its hidden side requires new geometry: a template, a separate generation, or completion. This is outside the current milestone, but the data model must allow for it.
- **Instance proxies that do exist:**
  - Connected components: `CuMesh.read_connected_components()` gives a per-face component id (cumesh.py L125–134; connectivity.cu L818–859). They are joined **only across manifold edges**, so non-manifold T-junctions split components.
  - Separate pieces seen in real jobs: dd0e's legs were a separate 448k-face piece, plus floaters (defect report §6.5).
- Text conditioning: `trellis2/trainers/flow_matching/mixins/text_conditioned.py` (CLIP ViT-L/14) is **training code only**. No text model or config is shipped (`configs/gen/*` are all image-conditioned). **Prompt-to-3D must be text-to-image followed by image-to-3D.**

### 2.2 Exact correspondence chain (what survives where) [V]

```
SS 32³ coords ──(LR shape SLat: same coords)──► 32³ tokens
   └─ decoder.upsample(·,4) → 512³ coords → re-quantized to 64³/96³ HR tokens (spatial containment, not 1:1)
HR shape SLat coords  ==  tex SLat coords  (same tensor order: shape_slat.replace(...), L412)
   └─ shape decoder: 4× SparseChannel2Spatial: child = parent·2 + subidx bits, children ordered by parent then subidx
        (spatial2channel.py L67–79)  ⇒  full-res voxel v ↦ token = coords_v // 16 (exact)
   └─ tex decoder uses guide_subs = the shape decoder's subdivision masks
        ⇒ identical full-res coords AND order (deterministic C2S given same parent order + same masks)  [V by code; assert at runtime]
Full-res voxel i  ↔  raw-mesh vertex i   (flexible_dual_grid_to_mesh L243: vertices = (coords + dual_offset)·voxel_size + aabb0, one per voxel, same order)
Raw face 2k, 2k+1  ↔  quad k  = the 4 voxels around one "intersected" primal edge (L229–238, split L256)
   ⇒ face label = f(labels of its 3 voxel-vertices)  (owner voxel = quad_indices[k,0])
Mesh.fill_holes (decode_latent L474) and to_glb fill_holes (L110): CuMesh appends new verts/faces at the END
   (clean_up.cu L685–686 `vertices.extend`, `faces.extend`); prefix order preserved; CuMesh.init/read are plain copies (io.cu L110–140)
── to_glb remesh (new DC grid) + simplify (no map)  ✗ correspondence lost
── uv_unwrap: vmaps (new vertex → simplified vertex) only
── RECOVERY: final face centroid (or texel) → bvh.unsigned_distance → raw face_id (+uvw) → voxel labels
   (identical to how to_glb assigns texture colors, L254–256 ⇒ labels and colors are consistent)
```

Consequences:
- **Per-voxel labels are the canonical store.** Use uint8 `part_id` per full-res voxel aligned with `mesh.coords`; at 1536 that is 4.8–8.3M bytes. From it, derive token labels (majority over `coords // 16`), raw-face labels (mode of 3 vertex labels; fill-hole vertices take their loop neighbours' label) and final-face labels (via BVH).
- Labels can also be added as **K one-hot channels next to `attrs`**. `grid_sample_3d(..., 'trilinear')` and an argmax then give smooth labels at any 3D point, including **per-texel part-ID maps** in an Img3D-owned bake.
- Persisting latents is cheap and an upstream pattern. `app.py` `pack_state`/`unpack_state` (L325–341) store `shape_slat_feats`, `tex_slat_feats`, `coords` and `res`, and re-decode later. At 1536 that is about 18k × (32 + 32) × 4 B + coords ≈ 5 MB per job. Re-decoding takes 1.3–27 s [V-run]. Store voxel labels with the latents, not with the GLB.

---

## 3. Lifting 2D part masks from the conditioning image to 3D

### 3.1 Frames and conventions [V unless marked]
- **TRELLIS internal frame = Blender frame:** Z-up, unit cube `[−0.5, 0.5]³`, **front = −Y**, image-right = +X.
  - The training meshes are Blender world coordinates after glTF import (dump_mesh.py L211–222).
  - `to_glb` maps to glTF Y-up with **front = +Z** (postprocess.py L313).
  - The texturing pipeline's `preprocess_mesh` does the inverse (`y' = −z, z' = y`, trellis2_texturing.py L116–118).
  - The defect study confirmed this empirically: the −Y view matches the input (fox silhouette IoU 0.697 vs 0.186 mirrored), and image-right = +X (trellis_mesh_defects.md §5).
- **The model is not camera-aligned.** Training conditioning views are random:
  - `data_toolkit/render_cond.py` L29–47 samples Hammersley points over the whole sphere. The remap in `utils.py` L36–44 puts about 75% of views at pitch ≥ 0.
  - FOV is uniform-in-1/r² over 10–70°, with radius `r = (√3/2)/sin(fov/2)` so the cube's bounding sphere fills the view.
  - Images are then **tight-square-cropped to the alpha bbox** (`datasets/components.py` L113–123) and premultiplied on black (L124–130). Inference preprocessing matches this.
  - So the network infers the object in its dataset-canonical pose, and the input view is unknown. For frontal character art it is near the −Y front view [A, consistent with the measured jobs], but **not guaranteed**: 3/4 views, top-down views and non-character objects can differ.
- **Framing after preprocessing:** the object's alpha bbox (threshold 0.8) exactly spans the longer image side and is centred along the shorter one. Pixel scale is therefore defined by the object, not by a camera. To compare a render with the conditioning image, **apply the same tight-bbox square crop to the rendered silhouette**. Absolute radius and FOV then only matter for perspective strength.
- **Mode caveat:** with `remove_background=false` and no real alpha, Img3D uses the image **untouched** (no crop; generation.py L167–170). Framing is then unknown and the fit must also solve a 2D scale and translation.

### 3.2 Rendering the generated mesh from the input view (face IDs) [V for APIs, A for timings]
- `trellis2/renderers/mesh_renderer.py::MeshRenderer` is importable: it needs only `easydict` and nvdiffrast 0.4.0, both installed.
  - Extrinsics are **OpenCV world→camera** (x right, y down, z forward). Intrinsics are normalized (cx = cy = 0.5).
  - `intrinsics_to_projection` (L8–33) gives an OpenGL clip matrix whose rows map to image rows top-down [A: verify with one test render].
  - `rast[..., 3]` is the triangle ID + 1 (0 = background, L148).
  - `return_types=["attr"]` on a `MeshWithVoxel` renders PBR attributes directly from the voxel volume (L161–174). This allows textured renders of the **raw** mesh without exporting a GLB, for colour-based pose fitting and multi-view segmentation.
- **`utils3d` is NOT installed** (checked site-packages), so `trellis2/utils/render_utils.py` (imports utils3d at L4) and `renderers/pbr_mesh_renderer.py` (L5) fail to import. Re-implement `extrinsics_look_at` and `intrinsics_from_fov_xy` in Img3D, about 20 lines.
  - Front camera: eye = `r·(sin yaw·cos p, cos yaw·cos p, sin p)` with **yaw = π** puts the camera at −Y (render_utils.py L27–32 convention). The look-at has up = +Z, giving camera x = +X and camera y = −Z.
  - `utils3d` is MIT (<https://github.com/EasternJournalist/utils3d>) [A: licence not re-checked]; installing it is not needed.
- **2²⁴ triangle-ID limit:**
  - nvdiffrast stores triangle IDs as float32. IDs ≤ 16,777,216 are plain; larger IDs are bit-encoded as `__int_as_float(0x4a800000 + id)` (`extensions/nvdiffrast/csrc/common/common.h` L185–192; maximum 889,192,447).
  - Real raw meshes reach **16,468,360 faces** (fox job) [V-run]. Decode in Python with `id = x if x <= 2**24 else x.view(int32) − 0x4a800000`.
  - Upstream avoids the problem with `mesh.simplify(16777216)` before rendering (`app.py` L401, `example.py` L26). **Do not do that before labelling**, because it destroys the voxel↔vertex mapping.
  - Never add chunk offsets to float IDs, as `to_glb` L242 and `MeshRenderer` chunk mode do, on meshes above 2²⁴ faces.
- **Camera estimation recipe [A].**
  1. Simplify a *copy* of the raw mesh to about 200k faces as a proxy.
  2. Grid-search yaw 0–360° in 10° steps, pitch −30…60° in 10° steps and FOV {10, 30, 50, 70}, seeded at yaw = π.
  3. Score each candidate: render the silhouette at 256², tight-square-crop it as in §3.1, and compute IoU against the conditioning alpha. Add a colour L1 term from the `attr` base_color render to break front/back ties.
  4. Refine the top-k with nvdiffrast gradients (`dr.antialias`) or Nelder–Mead.
  5. Do a final **full-resolution face-ID render of the raw mesh** at the conditioning resolution (≤1024²).
  6. Store the camera with the job.

  Accept the fit only if IoU ≥ about 0.6. The defect study suggests an IoU < 0.5 gate for garbage meshes.
- **Lifting:** for each 2D part mask k and each pixel, vote (face_id → k), then aggregate votes to voxels through the face's 3 vertices. This labels only the **visible front surface**: about 50% of the area, and less on the double shell. Use the raw mesh, which is single-layered, never the remeshed shell.

### 3.3 Labelling the hidden side [A]
1. **Multi-view voting (preferred for semantic parts).**
   - Render N = 8–24 views of the raw `MeshWithVoxel` (base-colour `attr` plus lighting), run the *same* 2D segmenter on each, and accumulate per-face votes weighted by `cos(view, normal)` and pixel coverage.
   - Fuse the votes with the input-view votes, giving the input view the highest weight.
   - Cost: rasterizing about 13M faces at 1024² is fast on the 5090; the 2D segmenter dominates.
2. **Voxel-graph diffusion.**
   - Graph: 26-neighbourhood on the full-res sparse voxels, built with a hashmap (`o_voxel._C.hashmap_*` exists) or sort/searchsorted.
   - Edge weights: base-colour difference from `attrs`, plus normal difference.
   - Method: random-walk or graph-cut diffusion from the input-view seeds. This works for hair, clothes and accessories whose back side is contiguous with the labelled front.
3. **Mirror prior.** Characters come out almost exactly symmetric about X = 0 (defect report §6.13), so labels of paired parts (eyes, brows) can be mirrored when the mirror error is small.
4. **Experimental: cross-attention lifting without a camera.**
   - Every SLat token cross-attends to the DINOv3 patch tokens: 32×32 at 512, 64×64 at 1024 (`SLatFlowModel.forward` L169–200, `block.cross_attn`). Attention maps from a late block, computed separately with an explicit softmax, give a soft token→patch map, so mask labels could be transferred per token.
   - Img3D's SDPA fallback (`inference/trellis/compat.py`) does not expose attention weights.
   - Quality is unknown. Treat it as a research option only.

---

## 4. Per-part meshes that keep UVs and materials

### 4.1 What `to_glb` can and cannot do [V]
- It takes one `(vertices, faces)` and one volume (`attr_volume`, `coords`). It has **no face mask, label input or multi-mesh output**, and returns one `trimesh.Trimesh` with one `PBRMaterial`.
- Passing `faces[part_mask]` *does* work mechanically: the full volume can stay; unreferenced vertices are fine for `CuMesh.init` and the BVH. Per call it then:
  - re-runs `fill_holes(3e-2)`. This **caps cut boundaries whose perimeter is below 0.03**, so eye holes in a "face" part get capped unpredictably.
  - runs a UDF remesh of the open part. That gives a closed pancake or double shell per part.
  - simplifies with the full `decimation_target`, so per-part budgets must be computed.
  - unwraps and **bakes its own `texture_size²` atlas**, giving K materials and K×(2 textures).
- Total time ≈ the whole-asset export again or more; whole-asset bakes are already 70–323 s. Tiny parts can trip `cuBVH`'s `> 8 triangles` assert (bvh.py L20) or produce empty remeshes.
- Simplifying parts separately creates **cracks and T-junctions** at shared borders. cumesh `simplify` exposes no boundary lock (`options`: `thresh`, `lambda_edge_length`, `lambda_skinny` only). So **simplify once, then split**.

### 4.2 Option A, cheapest correct (recommended first) [V for trimesh code paths, A for timing]
1. Keep `export_glb`/`to_glb` exactly as today, so `model.glb` stays byte-compatible.
2. Before export, take the per-voxel labels and raw mesh. After `to_glb` returns:
   - (a) Convert the final vertices back to the TRELLIS frame: `(x, y, z)_trellis = (x_g, −z_g, y_g)`.
   - (b) Build `cumesh.cuBVH` on the raw `(mesh.vertices, mesh.faces)`. This is a second BVH build because `to_glb` does not return its own.
   - (c) Query the final face centroids: `unsigned_distance(..., return_uvw=True)` gives raw face IDs and from them labels.
   - (d) Smooth with a 1-ring majority on the final mesh's face adjacency.
3. Split with `glb.submesh([idx_k ...], append=False)` (trimesh `base.py` L2930; `TextureVisuals.face_subset` texture.py L157–165 copies the UV subset and the material).
4. Build a `trimesh.Scene` with one node per part, named by part.
5. Export: the trimesh 5.1.1 GLB exporter **dedupes materials by `hash(mat)`** (`exchange/gltf/__init__.py` L703, L2163–2166). `PBRMaterial.__hash__` hashes the image bytes (`visual/material.py` L402–416), so all parts reference **one material and one set of WebP images**.
   - To avoid K deep copies of the 4096² images (about 117 MB each), re-assign the same `PBRMaterial` object to every part.
6. Metadata:
   - Part metadata goes into `mesh.metadata`, which is written to glTF **mesh `extras`** (L879).
   - A per-vertex `_PART_ID` can go in `mesh.vertex_attributes` and is exported as a custom attribute with an `_` prefix (L988–999).
   - Scene metadata goes to `scenes[0].extras` (L695).
   - [A: whether Blender imports `_PART_ID` as a mesh attribute needs a check.]
7. Trade-offs: no re-bake, and the reassembly is lossless (identity transforms, identical coordinates). But UV charts can **straddle part borders**, borders follow about 3-voxel triangles, and parts are open surfaces. Per-part GLB files would each embed the full atlas, so prefer one multi-node `parts.glb`.

### 4.3 Option B, part-clean atlas (recommended next) [A]
An Img3D-owned `export_parts.py`: about 150 lines reusing the same cumesh, xatlas, nvdiffrast and flex_gemm calls, with upstream left untouched.
1. Do whole-mesh `fill_holes`, BVH, remesh and simplify once, exactly as now, and keep that BVH.
2. Label the final faces with the same BVH.
3. For each part, run `CuMesh.init(part)` and `compute_charts`, then add **every part's charts to one `cumesh.xatlas.Atlas`** and `pack_charts` once. `uv_unwrap` already does this for charts (cumesh.py L451–474).
4. Do **one** nvdiffrast UV rasterization and bake over the concatenated mesh.
5. Split by the per-part vertex and face ranges.

The result is one atlas, islands that never cross part borders, optional per-part texel-density weighting, and an optional baked **part-ID texture** (argmax of one-hot label channels sampled with the same `grid_sample_3d`). Cost should be about one current export [A].

### 4.4 Semantic regions vs physical parts in the file
- **Continuous face and body topology:** keep it as one node, with per-face region IDs stored as `_PART_ID` and/or as separate glTF primitives sharing the vertex set. With trimesh the practical form is separate nodes plus a "region_of" relation.
- **Physical parts:** separate nodes, with `kind: physical`. Add `occluded_geometry_missing: true` whenever the cut leaves a hole in the parent. That is the normal case for clothes and eyes (§2.1).

---

## 5. The texturing pipeline (`trellis2/pipelines/trellis2_texturing.py`, config `texturing_pipeline.json`) [V]

- **Models:**
  - Loaded: `shape_slat_encoder` (new, 0.71 GB), `tex_slat_decoder`, and `tex_slat_flow_model_512` / `_1024` (L32–37).
  - Same sampler params and normalizations as image-to-3D.
  - DINOv3 and rembg are the same classes.
  - Img3D's loader builds only `Trellis2ImageTo3DPipeline` (`backend/inference/trellis/loader.py` L301–385). A texturing pipeline could be constructed reusing the already-loaded tex flows, tex decoder, DINOv3 and rembg, plus the encoder.
- **Inputs to `run(mesh: trimesh.Trimesh, image: PIL, seed=42, tex_slat_sampler_params={}, preprocess_image=True, resolution=1024, texture_size=2048)`** (L374–408):
  1. The image goes through the same tight-crop preprocessing (L122–157).
  2. **`preprocess_mesh`** (L106–120) recentres the mesh bbox, scales it to 0.99999 / max extent, and converts glTF Y-up to TRELLIS Z-up. The output keeps this normalized frame, so callers must re-apply the inverse similarity transform.
  3. DINOv3 conditioning at 512 or 1024.
  4. **`encode_shape_slat`** (L183–222): `o_voxel.convert.mesh_to_flexible_dual_grid` **on CPU** (`_C.mesh_to_flexible_dual_grid_cpu`, flexible_dual_grid.py L128) at `grid_size = resolution`. Weights: face 1.0, boundary 0.2, regularization 1e-2. This gives voxel indices, dual vertices and intersect flags, which the encoder turns into a shape SLat at res/16.
  5. `sample_tex_slat` (identical to image-to-3D).
  6. `decode_tex_slat` without `guide_subs` (L267–285). It works because `SparseTensor.replace` keeps `_spatial_cache` (modules/sparse/basic.py L671–676), so the encoder's `channel2spatial` caches drive the upsampling **back to exactly the input mesh's voxels**.
- **`postprocess_mesh`** (L287–371):
  - If the mesh **has UVs, they are kept** (V flipped). Otherwise it runs `cumesh.uv_unwrap`.
  - It rasterizes the UVs at `texture_size²`, interpolates positions and samples PBR voxels trilinearly. There is **no BVH projection, simplification or remesh; topology is preserved**.
  - It inpaints and returns a glTF-frame `trimesh.Trimesh` with a `PBRMaterial` (`doubleSided=True`).
  - Multi-material or overlapping UVs are not handled.
- **Re-texturing individual parts:**
  - Mechanically yes. But `preprocess_mesh` would rescale a lone part, such as an eye, to fill the unit cube while the image shows the whole character. That is out of distribution [A].
  - Correct use: texture **the assembled asset in its shared frame**, then split it (Option A/B); or bypass `preprocess_mesh` and apply the *assembly's* normalization to the part before encoding.
  - Voxels shared by touching parts bleed colour within about 1 voxel.
  - Upstream has no masked or partial re-texture. RePaint-style resampling of only a part's tex-SLat tokens (keep the others fixed) is possible with the Euler sampler but is a refinement algorithm, so it is out of scope for this milestone.

---

## 6. Recommendations

### 6.1 Architecture (backend, non-breaking)
1. **Leave the generation path and `model.glb` unchanged.** Add part awareness as **post-decode, opt-in** steps in a new Img3D module, e.g. `backend/inference/trellis/parts/`:
   - `frames.py`: camera helpers replacing `utils3d`, TRELLIS↔glTF frame conversion, and the nvdiffrast ID decode.
   - `lift.py`: camera fit, face-ID render, vote to voxels.
   - `propagate.py`: multi-view votes and voxel-graph diffusion.
   - `export_parts.py`: Option A, then Option B.

   Keep upstream untouched, as compat.py already does. Keep torch out of `api/` and `services/`.
2. **Persist part-ready intermediates per job.** This needs only small code in `generation.py`.
   - `latents.npz`: `pack_state`-style shape and tex feats, coords, effective res, pipeline_type, seed.
   - `conditioning.png` (RGBA) plus `conditioning.json`: the exact crop box, scale and alpha threshold. Upstream discards the alpha and the bbox, so re-implement the roughly 25-line crop in Img3D's `imaging.py` and unit-test pixel equality with `pipeline.preprocess_image`.
   - Later: `parts/voxel_labels.npz`, `parts/manifest.json` and `parts.glb`.
   - This makes part extraction re-runnable without re-sampling (decode takes about 1.3–27 s) and gives future tuning a per-part entry point.
3. **Canonical data model.** Use per-voxel `part_id` (uint8; `o_voxel.io.write_vxz` requires uint8 attributes, io/vxz.py L249–251; npz takes any dtype) aligned with `MeshWithVoxel.coords`, plus a part graph in JSON. Each part record holds:
   - id, label, `kind` (physical | region), parent / attached_to
   - adjacency with shared-border length, bbox, voxel/face counts
   - `source` (lifted | component | generated)
   - `occluded_geometry_missing`
   - transform (identity in the shared frame)
   - an optional link to its own latents for future per-part generations.
4. **Semantic decomposition "before reconstruction"** (Tripo-style per-part generation) has to resolve **re-normalization**: every TRELLIS run fits its output to the unit cube in a canonical pose. Register each part generation into the whole-asset frame, using the input-view camera from §3.2 and silhouette/voxel overlap.
   - A cheaper experimental route that stays in the global frame: run stage 3 and 4 (`sample_shape_slat` / `sample_tex_slat`) on a **subset of the SS/token coords** belonging to one part. These functions accept arbitrary coords.
   - The model was never trained on partial structures, so boundary hallucination is likely [A]. Keep it behind a flag.
5. **Prompt-to-3D** = text-to-image, then the same image path. TRELLIS.2-4B has no text conditioning (§2.1). The conditioning image of a prompt-generated input is then a normal job input, so part lifting is identical.
6. **API and stages:** `JobStage` values are frozen in api-contract.md §3 and the stage table has 9 stages. Either run part extraction inside `exporting` with sub-steps, or as a separate follow-up "parts" operation on a completed job. Adding a stage needs a contract change.

### 6.2 Pitfalls checklist
- **Double-walled shell (export only).** It comes from `remesh=True, band=1, project=0` (§1.3; defect study §6.1).
  - Do all labelling on the **raw single-layer mesh or the voxels**.
  - Labels transferred by BVH give both walls the same label, which is correct.
  - Never treat the inner wall as a part. Exclude it from visibility votes; its back faces are never visible from outside.
  - `remesh_project > 0` would collapse the walls into coincident opposite-wound faces (remeshing.py L238–251).
- **Coincident opposite-winding faces** (305–3,960 groups) and **non-manifold junctions** (2.2k–33k edges):
  - Label borders that run through them produce slivers.
  - cumesh connected components split at non-manifold edges. That is useful as an instance hint, but not a semantic split.
  - Never use "keep the largest component": dd0e's legs were a separate component.
- **UV-seam duplicates** (39–50% of GLB vertices): assign labels **per face**, never per raw vertex index. Weld before any vertex-graph operation on the GLB.
- **Label bleeding:** trilinear sampling plus 1-voxel single-layer storage mixes labels and colours across contacts within about 1–2 voxels. Use a 1-ring majority filter and per-label small-component cleanup.
- **fill_holes in `to_glb` (3e-2 perimeter)** silently caps small cut boundaries if `to_glb` is called per part.
- **nvdiffrast float triangle IDs above 2²⁴** (§3.2). Do not `simplify(16777216)` before labelling.
- **Frames:** TRELLIS (Z-up, −Y front) vs glTF (Y-up, +Z front). `to_glb` swaps axes at the very end and also flips V. Keep all part data in the TRELLIS frame and convert only at export.
- **`untouched` preprocessing mode:** no crop, so framing must be estimated.
- **`utils3d` missing:** do not import `render_utils` or `PbrMeshRenderer`.
- **Memory and time:**
  - The raw mesh at 1536 is 4.8–8.3M vertices / 9.7–16.5M faces. A second BVH build and 2M centroid queries are probably seconds [A, unmeasured].
  - Each copy of the 4096² baseColor+MR textures is about 117 MB; share one material object.
  - Exports already take 70–330 s; avoid per-part re-bakes.
- **Licences (all read in-repo):**
  - TRELLIS.2 code and weights: MIT (<https://github.com/microsoft/TRELLIS.2/blob/main/LICENSE>).
  - CuMesh: MIT (cumesh-0.0.1 METADATA; <https://github.com/JeffreyXiang/CuMesh>), with bundled xatlas (MIT) and cubvh (MIT).
  - FlexGEMM: MIT.
  - trimesh 5.1.1: MIT.
  - **nvdiffrast 0.4.0: NVIDIA Source Code License, non-commercial** (<https://github.com/NVlabs/nvdiffrast/blob/main/LICENSE.txt>). Any face-ID rendering or baking inherits this, as `to_glb` already does (`backend/third_party/README.md`).
  - DINOv3: DINOv3 License.

### 6.3 Runtime checks to add before relying on this (tests under `test/` only)
1. `torch.equal(shape_decoder_h.coords, tex_voxels.coords)` after `decode_latent`: the coordinate-order identity.
2. `mesh.vertices[:N]` is within one voxel of `(mesh.coords + 0.5) / res − 0.5`: voxel↔vertex identity after `fill_holes`.
3. A face-ID render of a raw mesh above 2²⁴ faces decodes to valid IDs.
4. A front-camera render (yaw = π) overlays `preview.png` with image-right = +X and image rows top-down.
5. A trimesh multi-node export of submeshes contains exactly 1 material and 2 images.
6. Option A labels on the final GLB are stable: 1-ring majority filter changes < 1% of faces.
