# Part-aware milestone — user decisions and environment changes (authoritative over open questions)

Decided by the user (Shiwam Shorya Sharma) on 2026-10-16. These override any "open question" in DESIGN.md /
UI_DESIGN.md.

## Decisions
1. **Prompt-to-3D text-to-image model: Z-Image-Turbo** (`Tongyi-MAI/Z-Image-Turbo`, revision
   `f332072aa78be7aecdf3ee76d5c247082da564a6`, Apache-2.0, not gated). Pipeline: text → image (Z-Image-Turbo,
   `num_inference_steps=9`, `guidance_scale=0.0`, bf16) → the existing TRELLIS.2 image-to-3D path.
   Not chosen: FLUX.2-klein-4B, Animagine XL 4.0, SAM-3-style transformers-5 models.
2. **2D segmentation models: open baseline + anime parser + Sapiens2-seg** (NOT SAM 3):
   - Grounding DINO base (`IDEA-Research/grounding-dino-base`, Apache-2.0) — open-vocabulary boxes
   - SAM 2.1 hiera-large (`facebook/sam2.1-hiera-large`, Apache-2.0, transformers `Sam2Model` format) — mask refinement
   - Florence-2-large (`florence-community/Florence-2-large`, MIT, transformers-native Florence2) — referring segmentation / boxes
   - See-through anime part parser (`24yearsold/l2d_sam_iter2`, Apache-2.0; `checkpoint-18000.pt`, load with
     `torch.load(weights_only=True)`; model code vendored from `shitagaki-lab/see-through`, small modules only, never
     its requirements) — 19 anime classes
   - Sapiens2-seg 0.4B (`facebook/sapiens2-seg-0.4b`, Sapiens2 License: custom Meta, bans biometric identification,
     deepfakes/impersonation, military use; code vendored from `facebookresearch/sapiens2`) — realistic humans, 29 classes,
     no eye/brow classes
   Exact revisions are recorded in `backend/.model/seg/download_manifest.json` after download.
3. **Physical separation policy:** hair, clothes, accessories, detachable objects and the **eye areas** are cut into
   separate open meshes with seam records (reversible; shared texture atlas, no re-bake). **Eyebrows and eyelashes**
   are physical part RECORDS (masks + surface regions + metadata) whose geometry is deferred to the future, approved
   tuning layer. Continuous skin/face/body stays one mesh with semantic regions.
4. **Git:** paused tuner work committed locally as `8654b90` (not pushed) before the restructure.

## Environment changes already made (2026-10-16)
- `backend/constraints.txt` created (freezes the TRELLIS.2 stack; forbids extra OpenCV builds). ALWAYS install with
  `-c backend\constraints.txt --only-binary ":all:"`.
- Installed into the embedded Python: `diffusers==0.39.0`, `accelerate==1.15.0` (dry-run: exactly 2 new packages,
  0 changes). `uv pip check`: 82 packages compatible. `from diffusers import ZImagePipeline` works.
  Still to do: add both pins to `backend/requirements.txt`; re-run the real TRELLIS.2 smoke test.
- Model downloads (public, `token=False`):
  - `backend/.model/t2i/Z-Image-Turbo/` (diffusers sub-folders only, 32.85 GB, 19 files)
  - `backend/.model/seg/{grounding-dino-base, sam2.1-hiera-large, Florence-2-large, l2d_sam_iter2, sapiens2-seg-0.4b}/`
- Hugging Face gotcha: this machine has an EXPIRED cached OAuth token, so anonymous Hub calls must pass
  `token=False` (otherwise 401 "OAuth token has expired"). The project's `HF_TOKEN` in `backend/.env` is separate.
- All downloads finished: Z-Image-Turbo 32.85 GB; seg models pinned in `backend/.model/seg/download_manifest.json`
  (grounding-dino-base 12bdfa31…, sam2.1-hiera-large 665f8e2a…, Florence-2-large 4271c66b…, l2d_sam_iter2 0b060831…,
  sapiens2-seg-0.4b 449b3c53… — sapiens2 has two identical 1.63 GB files: model.safetensors and
  sapiens2_0.4b_seg.safetensors).

## Measured feasibility probes (2026-10-16, RTX 5090) — scripts in `test/part_aware/probes/`
- `seg_stack_probe.py` on ALICE_full_front_view.png (outputs in `test/outputs/part_aware/probe/`): Grounding DINO base
  + SAM 2.1 (transformers `AutoModelForZeroShotObjectDetection`, `Sam2Model`/`Sam2Processor`, local files) and
  Florence-2-large (`Florence2ForConditionalGeneration`, fp16, `<REFERRING_EXPRESSION_SEGMENTATION>`) all load and run
  on transformers 4.57.6. Peak VRAM 2.1 GB. Quality: GDINO+SAM2 good for both eyes, mouth, hands, both boots, long
  hair; skirt only partial; one "eyebrow" box landed on the tiara (brows need the head-crop pass + rules); Florence-2
  got the eyes but only the top of the hair (weak as primary). First-call times 55 s (GDINO) / 15 s (SAM2) are CUDA
  warm-up — measure warm; keep models cached in RAM.
- `t2i_probe.py`: `ZImagePipeline.from_pretrained(local, torch_dtype=bfloat16, local_files_only=True)` works on
  diffusers 0.39 + transformers 4.57.6. load 30 s, first `.to("cuda")` 307 s (disk-bound: transformer stored fp32 on
  E:), generation 896×1152 9 steps: 16.7 s cold / 3.6 s warm, peak VRAM 23.3 GB, back to 0.03 GB after `.to("cpu")`.
  ⇒ keep the pipeline resident in CPU RAM between jobs, never co-resident on the GPU with TRELLIS.2, consider a
  one-time bf16 re-save to halve cold load. Image quality: clean full body, centred, plain light-gray background,
  front view — TRELLIS-friendly; "anime girl" rendered semi-realistic ⇒ the prompt template needs explicit style
  presets (anime / realistic / stylised).
- Mesh tooling packages proposed by the audit (rtree, scikit-image, manifold3d) are NOT installed — only add them if
  the design needs them (they resolve cleanly under the constraints file).

## Defaults applied 2026-10-16 (assistant, pending user override)
Applied by the assistant to close DESIGN.md §12; the user's decisions above are unchanged (Q1 and Q4 are user
decisions: the six models above are approved by default, and See-through/Sapiens2 are vendored as pinned git
submodules under `backend/third_party/` with notices and the Sapiens2 conditions shown in the UI). Each default
below holds until the user says otherwise.
- **Q2 Manual candidate pick inside a workflow:** not in v1. Review-first is the two-call flow
  (`POST /image-generations` → `…/candidates/{n}/use` → `POST /jobs` or `POST /workflows`); one-shot uses
  `auto_first`. A paused pick step stays deferred (DESIGN D9).
- **Q3 Tuning code gate:** code gate in `MeshTunerService.create_run` plus one `os.environ.setdefault` line in
  `test/conftest.py`. The Blender detection/download endpoints stay as they are; every tuner/Blender UI mount point
  (and the four locked refine nodes) is hidden in all four UI modes. Tuning stays disabled.
- **Q5 SD1 generation intermediates:** not approved, deferred (DESIGN D1). `IMG3D_PERSIST_GENERATION_INTERMEDIATES`
  stays inert and is always reported `false`; the protected TRELLIS files are untouched.
- **Q6 SD2 Z-Image bf16 re-save:** not approved, deferred (DESIGN D12). The cold load is addressed by reading the
  weights into RAM in a CPU step instead; Q6 is re-asked only if that measures ≥ 30 s for the first GPU transfer.
- **Q7 Eye cut scope:** cut only the lifted eye faces; the eyelid ring (taxonomy values 26/27) stays a base region.
- **Q3 UI amendment (user spec alignment, 2026-10-16; follows the user's milestone spec "Preserve the existing UI" / "Do not delete existing working functionality"):** replaces the "hidden in all four UI modes" clause of Q3 above (and DESIGN.md §8.2 G4 / §12 Q3 wording) for the UI: the Default UI keeps the existing tuner UI where it is today, rendered but LOCKED while `/features.mesh_tuning.enabled` is false (start/configure tuning and Blender download/setup disabled with the notice "Paused until you approve the part-aware milestone"; Blender pill read-only; existing tuned results stay viewable); the same flag unlocks it with no code change; a legacy backend behaves as today; Mobile/Tablet/Node add no tuner UI; refine nodes stay locked and hidden; no tuner file is edited (UI_DESIGN.md §3.9).
