# Img3D Auto Mesh Tuner: what real TRELLIS.2 meshes look like in Blender 5.2

The probe works and ran on 7 real jobs plus 1 engine_mechanics mesh. All runs exited 0 and every `errors` array is empty. Workbench and EEVEE both render in `--background` on this PC (OpenGL backend on the RTX 5090, driver 617.42).

The biggest finding is that every TRELLIS.2 GLB is a hollow, double-walled shell. Almost all other defects follow from that, so the repair pipeline has to remove the inner wall before anything else.

I skipped `job_269a864ac597` (excluded input; see `CLAUDE.local.md`), so I did not render it.

## 1. What I wrote

All scripts are in `E:\14-OCT-2026\Img3D\test\mesh_tuner\probe\`:

* **`analyze_glb.py`** (1079 lines) is the main probe. It takes `--input <glb> --out <dir>`, plus optional `--front auto|-Y|+Y|-X|+X`, `--head-center x,y,z`, `--head-scale`, `--export-welded`, `--timing-merge-import`, `--skip-render` and `--skip-export`.
  * It writes `<out>\analysis.json` and prints `@@RESULT@@<path>`.
  * Each stage is timed and records Windows working-set and private memory (`mem()` at line 59, `Stage` class).
  * The stages start at: import (L517), raw stats (L543), raw export round-trip (L607), bmesh weld (L621–636), welded topology (L647), components (L691), boundary loops (L743), orientation match (L781), symmetry (L825), ray-cast (L874), renders (L950–998), slice plots (L1008), welded export (L1045).
* **`run_batch.sh`** runs the probe over a list of jobs.
* **`shell_experiment.py`** tests visibility from outside and whether Blender's voxel remesh closes the shell.
* **`render_region.py`** renders close-ups of any region, smooth and flat-shaded.
* **`glb_header.py`** and **`glb_index_check.py`** read the GLB file directly, without Blender.
* **`introspect_bpy.py`**, **`quick_import_test.py`** and **`quick_attrs.py`** compare Blender 4.2 and 5.2.

Two files are outside the probe folder:
* `test\outputs\mesh_tuner\analysis\bpy_introspect_{4.2,5.2}.json` hold the operator parameter dumps.
* Other files in `probe\` (`detect_*`, `jobobject_probe.py`, etc.) are not mine.

## 2. Inputs analyzed

| job | input image | settings (res, poly target, texture, ss/shape/tex steps) | preprocess |
|---|---|---|---|
| job_04535ae38930 | ALICE T-pose | 1536, 2M, 4096, 25/25/42 | alpha_crop |
| job_2496cdfeecc8 | same ALICE | 1536, 2M, 4096, 12/12/12 | alpha_crop |
| job_a68b9057a882 | same ALICE | 1536, 2M, 4096, 50/50/50 | alpha_crop |
| job_046e19f0ca62 | same ALICE | 1536, **1M, 2048**, 12/12/12 | alpha_crop |
| job_dd0e629ac377 | anime armor A-pose (5 MB input) | 1536, 2M, 4096, 12 steps | background_removed |
| job_57425383ed74 | fox (not humanoid, asymmetric) | 1536, 2M, 4096, 12 steps | alpha_crop |
| job_5b72e3768e6e | floating castle | 1536, 2M, 4096, 12 steps | alpha_crop |
| engine_mechanics/job_smoke145706 | ALICE, pipeline "512" | 100k, 1024 | alpha_crop |

Every GLB is written by trimesh with one node (`geometry_0`) and one primitive (POSITION, TEXCOORD_0, NORMAL, uint32 indices). There is one material with two WebP images: base color + alpha, and metallic-roughness (green = roughness, blue = metallic). `EXT_texture_webp` is listed as **required**, with no PNG fallback.

## 3. Per-mesh summary (condensed from `analysis.json`)

"Welded" means after merging vertices at 1e-6 × bbox diagonal with `bmesh.ops.remove_doubles`.

| job | MB | raw V / F | importer dropped F | welded V / F | boundary E | non-manifold E (>2 faces) | non-manifold V | coincident opposite-winding face groups | components | main part % | 2nd part (faces) | tiny parts <0.1% | holes ≥5 edges | largest hole (edges / % diag) | inconsistent-winding E | thickness p50 (voxels) | faces with wall <3 voxels behind | symmetry mean / p95 (% diag) | normals within 5° of an axis (% area) | dihedral p50 / p95 (°) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 04535 | 81.9 | 1,697,772 / 1,895,413 | 278 | 907,500 / 1,891,419 | 5,946 | 33,076 | 53,685 | 3,960 | 259 | 97.0 | 9,610 | 252 | 191 | 112 / 8.48 | 160 | 2.14 | 73.7% | 0.056 / 0.114 | 2.09 | 5.7 / 70.6 |
| 2496 | 80.9 | 1,624,550 / 1,923,596 | 244 | 942,464 / 1,921,966 | 2,545 | 14,721 | 27,492 | 1,594 | 157 | 98.1 | 17,281 | 155 | 34 | 14 / 1.03 | 33 | 2.08 | 74.6% | 0.102 / 0.220 | 1.65 | 4.3 / 61.5 |
| a68b | 81.4 | 1,628,439 / 1,925,965 | 306 | 940,868 / 1,923,726 | 3,342 | 17,761 | 32,116 | 2,187 | 228 | 95.3 | 21,706 | 219 | 49 | 17 / 1.66 | 55 | 2.09 | 73.8% | 0.099 / 0.233 | 1.73 | 5.3 / 64.5 |
| 046e | 41.0 | 865,166 / 995,320 | 43 | 479,519 / 993,761 | 2,105 | 13,474 | 23,932 | 1,534 | 173 | 95.4 | 5,140 | 162 | 40 | 27 / 2.82 | 59 | 2.09 | 74.8% | 0.117 / 0.404 | 2.01 | 6.5 / 71.0 |
| dd0e | 82.2 | 1,630,008 / 1,987,788 | 211 | 972,685 / 1,984,886 | 4,365 | 18,372 | 30,716 | 2,876 | 255 | 74.9 | **448,000** (the legs) | 250 | 156 | 26 / 3.38 | 145 | 2.01 | 84.3% | 0.083 / 0.175 | 1.82 | 2.8 / 55.7 |
| fox 5742 | 85.9 | 1,782,831 / 1,861,189 | 1 | 899,380 / 1,859,317 | 2,924 | 5,187 | 13,294 | 1,834 | 430 | 99.7 | 113 | 429 | 5 | 11 / 1.49 | 23 | 2.26 | 76.2% | 3.03 / 15.5 | 6.83 | **19.7 / 80.5** |
| castle 5b72 | 80.8 | 1,620,151 / 1,982,549 | 47 | 981,573 / 1,982,222 | 587 | 2,224 | 5,276 | 306 | 240 | 99.4 | 410 | 239 | 2 | 6 / 0.32 | 2 | 2.13 | 80.2% | 2.87 / 8.2 | 10.48 | 10.9 / 54.6 |
| smoke 512 | 4.0 | 78,437 / 97,930 | 0 | 48,193 / 97,889 | 62 | 241 | 490 | 38 | 24 | 86.2 | 9,479 | 14 | 0 | 4 / 1.28 | 2 | 2.75 | 55.4% | 4.08 / 22.3 | **40.0** | 11.6 / 60.5 |

Other numbers that held across all real jobs:
* **Mesh type:** triangles only; no quads or n-gons.
* **Duplicate vertices (UV seams):** 39–50% of raw vertices sit at exactly the same position as another vertex (385k–883k). At 1e-6 × diag only 0–12 more appear; after welding, 0–4 remain at 1e-5 × diag.
* **Loose geometry:** 0–2 wire edges, 0–1 loose vertices, 0–4 vertices without faces.
* **Degenerate faces:** 13–34 with zero area; 13–375 with area < 1e-8 × diag².
* **Faces whose winding disagrees with the GLB's stored normals:** 3,103–26,093 (0.2–1.4%).
* **Inward-facing faces (ray test):** odd ray parity on 0.18–0.65% of sampled faces.
* **Texture coordinates:** one UV layer `UVMap`, all within 0–1. No color attributes.
* **Textures:** 4096² WebP, packed (2048² for job 046e). Base color is sRGB, roughness/metal is Non-Color.
* **Transform:** single object, no parent, identity transform.
* **Mean edge length:** about 0.15% of the diagonal (≈3 voxels at 1536).
* **Single-vertex spikes:** essentially none. Max Laplacian offset / mean edge length is ≤ 2.25, and at most 1 vertex exceeds 2.

## 4. Timings and memory for an 80 MB GLB

| Step | Time | Memory (private) |
|---|---|---|
| Blender startup | ~1.5 s | |
| `import_scene.gltf` (raw) | 3.3–4.1 s (41 MB file: 2.0 s; 4 MB: 0.22 s) | 1.25 GB after, 1.5 GB peak |
| import with `merge_vertices=True` | 2.8–3.0 s; gives 907,534 V / 1,891,444 F, close to my bmesh weld | |
| numpy stats via `foreach_get` | 4.4 s | |
| `bm.from_mesh` | 0.85 s | +1 GB |
| `find_doubles` ×2 | 1.7 s | |
| `remove_doubles` | 2.4 s | |
| welded topology (numpy) | 3.1–3.4 s | |
| components | 0.65 s | |
| ray-cast (20k faces) | 0.6–0.8 s | |
| symmetry (KD-tree + plane search) | 4.4–15 s | |
| Workbench render, 1024², 1.9M triangles | 0.12–0.2 s each | |
| EEVEE render, 32 samples | 0.2–0.4 s each | +1.9 GB for two 4K textures |
| `export_scene.gltf` GLB | 2.2–2.7 s | output is 1.004–1.007× input size |
| **Full analysis** | **32–44 s wall** | **4.4 GB peak before renders; 6.2–6.7 GB peak with EEVEE; working set peak 4.7–5.1 GB** |

The first EEVEE render on a fresh profile took about 11.5 s, because shaders compile once. For scale, TRELLIS's own export stage for these jobs took 77–330 s, so Blender processing is cheap by comparison.

## 5. Orientation

* **In the glTF file:** +Y is up and the character faces +Z (the glTF standard front).
* **After Blender import:** +Z is up and the character faces **−Y**. Blender's Front view (numpad 1, camera at −Y) shows the input image as drawn, not mirrored. The character's left is +X, and image-right equals +X.
  * The fox is the deciding test because it is asymmetric: the −Y view matches the input silhouette with IoU 0.697, versus 0.186 when mirrored.
  * The ALICE and dd0e jobs agree: the colour-match score at −Y beats +Y every time (0.232–0.263 vs 0.268–0.311).
* **Why:** TRELLIS works Z-up internally. `o_voxel/postprocess.py:313` swaps Y/Z for glTF, and Blender's importer undoes that swap, so Blender coordinates equal TRELLIS coordinates.
* **Scale and placement:** the model is fitted into the unit cube [−0.5, 0.5]³ (`backend/inference/trellis/export.py:16` `GLB_AABB`). Height is about 1.0 and the feet sit at z ≈ −0.5, not at the origin.
* **Automatic front detection** works for characters but not for the castle, which picked +Y by a small margin. Hard-code −Y as front.
* **Head close-up heuristic** (z = top − 0.10 × height) works for upright humanoids. It framed the fox's ear, so the fox needed `--head-center 0.26,0,0.32`.

## 6. Defect taxonomy (counts are for the 7 real jobs)

1. **Hollow double-walled shell.** This is the root defect and the most serious.
   * An inner wall sits about 2.0–2.3 voxels (1.3–1.5e-3 units) behind the outer surface on 74–84% of sampled faces. This is true even for the fox's solid body.
   * On job_04535, only 51.3% of faces are visible from outside (24 hemisphere rays each), and no face's back side is visible.
   * The main part's volume divided by half its surface area is 1.47e-3 units, which is exactly the wall thickness. Slice plots show a double outline around every feature (`slice_transverse_head_zoom_front.png`: two parallel lines about 0.0014 units apart).
   * Cause: TRELLIS's export uses `remesh=True, remesh_band=1, remesh_project=0` (`backend/inference/trellis/export.py:51-53`), a dual-contouring remesh around an unsigned distance field.
   * Blender's voxel remesh does **not** fix it. At voxel size 0.002, volume goes from 0.00227 to 0.00298; at 0.004 it reaches 0.0097, and visible faces rise only to 0.65/0.69. The cavity is open to the outside through holes. (`job_04535ae38930\shell_experiment.json`)
2. **Faces stacked on top of each other with opposite winding.** 305–3,960 groups, over 99.7% of them opposite-wound, where the two walls collapse onto each other.
   * The importer also silently drops exact duplicate triangles: 1–306 per mesh, which I confirmed by checking the GLB index buffer.
3. **Non-manifold geometry after welding:** 2.2k–33k edges shared by 3–6 faces, and 5k–54k non-manifold vertices.
4. **Holes.** Only 2–191 real holes (5 or more edges) per mesh; the largest is 112 edges / 8.5% of the diagonal, on the ALICE skirt and hip at the back.
   * 77–92% of the "boundary loops" are 1–2-edge fragments next to non-manifold junctions, not real holes.
   * TRELLIS only runs `fill_holes` before the remesh (`postprocess.py:110`), not after it.
5. **Floating pieces.** 155–429 parts under 0.1% of faces, totalling 5k–35k faces (0.09–0.97% of area).
   * Visible examples: two bow-shaped floaters between the legs and detached hair arcs (04535), specks near the head (a68b).
   * Legitimate separate parts also exist: in dd0e the legs are a separate 448k-face piece. A "keep the largest piece" rule would wrongly delete them.
6. **Pieces enclosed inside the body** (heuristic, ray parity from each piece's centre): 23–231 per mesh.
7. **Inconsistent winding:** 2–160 edges (4–297 faces) where neighbouring faces disagree, plus 0.2–1.4% of faces disagreeing with the GLB's stored normals.
8. **Degenerate and sliver triangles:** 13–34 zero-area; 1.2k–6.3k with quality < 0.1.
9. **Surface noise.**
   * On characters, flat shading shows fine triangle-scale bumpiness (`regions\face_front_flat.png`).
   * On fur, the fox has flaky shard geometry with pits (dihedral p50 19.7°, 86k edges over 90°).
   * Fold-overs (dihedral > 150°): 219–6,490 edges.
10. **Flat facets and stair-stepping.** Large flat triangles on smooth areas: legs, back of the hair, long skirts (`regions\legs_front.png`, `wb_back.png`). The dd0e dress shows vertical stripe banding.
    * Area with normals within 5° of an axis is 1.65–2.1% on characters, versus 0.38% expected for a random surface.
    * Hands are faceted with fused, webbed fingers (`regions\hand_right.png`).
11. **Face-specific defects** (from the close-ups I viewed):
    * Eyes are flat, shallow recesses with raised lid rims.
    * Vertical comb/slit grooves inside the eyes (dd0e, a68b).
    * Small pits in an eye (2496, 046e), and a blob under one eye (dd0e).
    * A sharp vertical crease runs down the middle of the face to a V-shaped chin, on every anime job.
    * Pointed nose bump; the mouth exists only in the texture, not the geometry.
    * Hair locks fused into the cheeks; serrated edges on a hair ornament (a68b).
    * The fox's bare face is clean: real eye sockets and nostril pockets.
12. **Texture defects:** white speckles on the hair and face (dd0e); dark smudges around the eyes and collar (04535).
13. **Symmetry.** Characters come out almost perfectly mirror-symmetric about X=0, with the best-fit plane at offset 0 and yaw 0.
    * Even an asymmetric input detail was mirrored: ALICE's one-sided ribbon appears on both sides.
    * The fox (3.0% mean) and castle (2.9%) are asymmetric, so symmetrize must be optional.
14. **Garbage generations.** All engine_mechanics GLBs (≈14:57–15:27, before the real jobs) have non-character bounding boxes. smoke145706 is a flat relief slab: 40% of its area has axis-aligned normals and it matches the input silhouette with IoU of only 0.31. Meshes like this should be rejected, not repaired.

## 7. Blender 5.2 API notes and gotchas (compared with 4.2)

* **EEVEE engine name:** it is `BLENDER_EEVEE` in 5.2 but `BLENDER_EEVEE_NEXT` in 4.2. Using the wrong one raises a TypeError ("enum not found"); I verified both directions.
* **Engine discovery doesn't work in background:** `RenderSettings.bl_rna.properties['engine'].enum_items` returns only the EEVEE item. Set the engine inside try/except instead.
* **glTF importer (add-on v5.2.40; 4.2 has v4.2.83):**
  * New options: `import_scene_as_collection` (default True), `import_merge_material_slots`, `import_select_created_objects`, `import_scene_extras`, `import_unused_materials`, `import_point_as_pointcloud`.
  * `merge_vertices` defaults to False. `import_shading='NORMALS'` imports the GLB normals as custom normals.
  * WebP-only textures load whether or not `import_webp_texture` is set.
  * After `read_factory_settings(use_empty=True)` no import collection was created; the object went into Scene Collection.
* **Material created by the importer:** blend method `HASHED` (render method `DITHERED`) even though the GLB says OPAQUE, and `use_backface_culling=True` because the GLB has `doubleSided: false`.
* **glTF exporter:**
  * New options: `export_meshopt_compression_enable`, `export_meshopt_extension` (EXT_/KHR_meshopt_compression), `export_gltfpack_kn`, `export_merge_animation`, `export_sampling_interpolation_fallback`, `export_vertex_color_name`.
  * `export_materials` gained `VIEWPORT`; `export_vertex_color` gained `NAME`.
  * `export_image_format` offers AUTO/JPEG/WEBP/NONE only. AUTO re-embeds the packed WebP bytes unchanged (same byte length, 4,436,456) and keeps `EXT_texture_webp` required.
  * `export_format` is a dynamic list, so pass `'GLB'` explicitly.
  * It logs a harmless warning: "More than one shader node tex image used for a texture".
  * The exporter re-splits vertices on UV seams, so welding before export saves only about 0.7% (82.24 MB raw vs 81.69 MB welded).
* **Mesh API:**
  * Custom normals are now a visible attribute `custom_normal` (corner domain, INT16_2D); 4.2 does not list it. `mesh.normals_domain` is `'CORNER'` after import.
  * `use_auto_smooth` and `calc_normals_split` are gone. Use `mesh.corner_normals` (read `'vector'`), `Mesh.shade_flat()`/`shade_smooth()`, `set_sharp_from_angle`, `object.shade_auto_smooth`.
  * To flat-shade an imported GLB you must first run `bpy.ops.mesh.customdata_custom_splitnormals_clear()`.
* **`World.use_nodes`** raises a DeprecationWarning ("expected to be removed in Blender 6.0").
* **Matcap names changed:** 5.2 has no `basic_1.exr`, `basic_2.exr`, `skin.exr`, `toon.exr`, `jade.exr` or `metal_*`. Pick from `preferences.studio_lights` filtered by `type=='MATCAP'`; I used `clay_studio.exr`.
* **bmesh:** `find_doubles`, `remove_doubles` and `BMVert.is_manifold` are fast at this size, and welding keeps UVs and `custom_normal`.
* **Python:** Blender 5.2 bundles Python 3.13.13 (4.2 bundles 3.11.7), with numpy included.

## 8. Renders and outputs

Everything is under `E:\14-OCT-2026\Img3D\test\outputs\mesh_tuner\analysis\<job>\`. The job folders are `job_04535ae38930`, `job_2496cdfeecc8`, `job_a68b9057a882`, `job_046e19f0ca62`, `job_dd0e629ac377`, `job_57425383ed74`, `job_5b72e3768e6e` and `engine_mechanics_job_smoke145706`.

Each folder contains:
* **Matcap + cavity views:** `wb_front.png`, `wb_left.png` (camera on the character's left, +X), `wb_three_quarter_front_left.png`, `wb_back.png`, `wb_front_backface_culled.png`
* **Face close-ups:** `face_closeup_matcap.png`, `face_closeup_matcap_34.png`, `face_closeup_studio.png`, `face_closeup_textured.png` (EEVEE with the forest.exr HDRI)
* **Textured full view:** `front_textured.png`
* **Cross-sections:** `slice_transverse_head.png`, `slice_transverse_head_zoom_front.png`, `slice_transverse_mid.png`, `slice_sagittal_center.png`
* **Orientation-test renders:** `_orient_{nY,pY,nX,pX}.png`
* **Data and logs:** `analysis.json`, `blender_stdout.log`, `roundtrip_raw.glb`, `roundtrip_welded.glb`

`job_04535ae38930` also has `regions\{legs_front,skirt_side,face_front,hand_right}{,_flat}.png` and `shell_experiment.json`.

The analysis folder takes about 1.1 GB, almost all of it the round-trip GLBs.

## 9. Implications for the repair pipeline

1. **Weld first:** merge exact-duplicate vertices (or import with `merge_vertices=True`). Nearly all duplicates are UV-seam copies at exactly the same position.
2. **Remove the inner wall before any other operation**, using visibility from outside. Rays cast from Python are too slow for 2M faces; render face IDs on the GPU or use the Geometry Nodes Raycast node instead. I have not tested either. Alternatively, evaluate changing the engine export settings in `export.py:51-53`; I did not change them, per the rules.
3. **Then delete duplicate and degenerate faces**, repair non-manifold geometry, fill holes (real holes are few and small), and drop small parts by size combined with whether they are enclosed. Never use a "keep the largest part" rule.
4. **Smoothing:** use an edge- or curvature-aware smooth, since the noise is sheet-like rather than single-vertex spikes. Have a separate fur mode.
5. **Face pass:** build it around the midline crease and the eye recesses.
6. **Symmetrize** only when the mirror error is below about 0.3% of the diagonal.
7. **Quality gate:** reject meshes whose axis-aligned normal fraction is over about 10% (castles excepted) or whose silhouette IoU against the input is under 0.5.

Budget about 40 s and 7 GB of RAM per 2M-face job for the analysis alone.