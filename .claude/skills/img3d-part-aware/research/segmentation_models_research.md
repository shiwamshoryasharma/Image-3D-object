# 2D semantic part segmentation for Img3D: model research

Date: 2026-10-16. Phase: read-only understanding (nothing was installed, downloaded, started or modified; the only file written is this report).
Scope: local models that split an input image (anime/stylised characters, realistic people, objects) into parts: eyes (L/R) and
their sub-parts, eyebrows, eyelashes, mouth, hair, skin/face, body, clothes (upper/lower/dress/shoes), accessories (hat, glasses,
earrings, bags, weapons) and detachable objects. The report also maps the outputs onto a fixed Img3D part taxonomy and decides,
per part, whether it becomes a separate physical asset or a semantic region.

Evidence legend: **[V]** verified (I read the local file or the primary page, or queried the HF API); **[V-web]** verified from a
primary web page or release note; **[A]** assumption or estimate that has NOT been measured. Runtime and VRAM figures on the
RTX 5090 are all **[A]**, extrapolated from published numbers. I ran no GPU work.

---

## 0. TL;DR

* **Nothing published covers the whole taxonomy.** No face-parsing dataset has an eyelash class. Realistic parsers (CelebAMask-HQ, ATR, LIP,
  Sapiens) are trained on photos and most of them are non-commercial. The only anime-native part parser is the 2026 **See-through** project.
  Its body parser has 19 classes, Apache-2.0 weights, and no eyebrow or eyelash classes. Its generative layer model has 23 layers, including
  `eyelash`, `eyebrow`, `irides`, `eyewhite` and front/back hair, but it needs transformers 5 and diffusers.
* **SAM 3 (Meta, Nov 2025; SAM 3.1, Mar 2026) is the best open-vocabulary part and accessory segmenter**, but it is **not in transformers 4.57.6**.
  The `Sam3*` classes arrived in **transformers v5.0.0rc0**, and we must stay on 4.57.6 for TRELLIS.2. The official repo code needs only
  `torch`, `timm`, `numpy`, `huggingface_hub`, `regex`, `tqdm`, `psutil`, `triton`, `pkg_resources`, plus the pure-Python
  `ftfy` and `iopath` (the last two are missing here). So SAM 3 can be **vendored like TRELLIS.2 (git submodule + sys.path)** and run in-process
  without touching transformers. Do **not** `pip install sam3`: PyPI 0.1.4 pins `numpy<2.4.0`, `tqdm<4.68.0` and `typing_extensions<4.16.0`,
  which conflict with our pins.
* **A fallback that needs zero new dependencies is already installed** (verified in `transformers 4.57.6`): `GroundingDinoForObjectDetection`,
  `MMGroundingDinoForObjectDetection`, `Owlv2ForObjectDetection`, `Florence2ForConditionalGeneration`, `Sam2Model`, `SamHQModel`,
  `EdgeTamModel`, `CLIPSegForImageSegmentation`, `SegformerForSemanticSegmentation`, `OneFormer…`, `Mask2Former…`, `Eomt…` and
  `Qwen3VLForConditionalGeneration`. All of them support SDPA, so flash-attn and xformers are not needed.
* **Recommended primary pipeline:** SAM 3 (vendored) with concept prompts, running on the conditioning crop and on a high-resolution head crop.
  Add a domain parser for dense coverage: **See-through `l2d_sam_iter2`** (anime; Apache-2.0; vendor about 3 small modules) or **Sapiens2-seg**
  (realistic humans; Sapiens2 License; standalone torch + safetensors). Refine with **SAM 2.1-L** box/point prompts, then split eyes into
  sclera, iris, pupil and lashes with deterministic colour and geometry rules.
  **Fallback** if SAM 3 is unavailable (gated access not granted, or a Python 3.14 import problem): Grounding DINO-base or Florence-2-large boxes
  feeding SAM 2.1-L. Everything in the fallback is native to 4.57.6.
* **Eyelashes:** derive them procedurally from the eye mask, the upper-lid curve and a dark-pixel band. Treat them as a
  *physical-deferred* asset: keep the mask and the lid curve now, and generate geometry only after the tuning layer is approved.
  TRELLIS cannot reconstruct lash strands.
* **Non-commercial models are opt-in only:** `jonathandinu/face-parsing` (CelebAMask-HQ + NVIDIA SegFormer licence), `segformer_b2_clothes`
  (ATR + NVIDIA SegFormer licence), Sapiens v1 (CC-BY-NC-4.0) and FaceSynthetics. Handle them with the same `license_note` pattern
  already used for RMBG-2.0 in `backend/inference/trellis/assets.py:112-120`. The repo is public GPL-3.0.

---

## 1. Img3D context that constrains the choice (verified locally)

| Fact | Evidence |
|---|---|
| Embedded CPython 3.14 (`backend/python`), torch `2.14.1+cu130`, torchvision `0.29.1+cu130`, transformers `4.57.6`, huggingface-hub `0.36.2`, numpy `2.4.1`, timm `1.0.30`, tokenizers `0.22.2`, safetensors `0.8.0`, kornia `0.8.3`, opencv-python-headless `5.0.0.93`, triton-windows `3.8.0.post29`, psutil `7.2.2`, regex, tqdm `4.70.1`, typing_extensions `4.16.0`, setuptools `80.10.2` (so `pkg_resources` exists). **Not installed:** accelerate, diffusers, onnxruntime, ultralytics, mediapipe, pycocotools, ftfy, iopath, open_clip, mmcv | [V] `backend/python/Lib/site-packages/*.dist-info` listing |
| transformers is pinned because "TRELLIS.2's DINOv3 wrapper uses transformers internals". The hub must stay `<1.0` for 4.57.x | [V] `backend/requirements.txt:47-48` |
| numpy is pinned to 2.4.1 | [V] `backend/requirements.txt:36` |
| Third-party model code is imported from `backend/third_party/...` via sys.path, not pip-installed (TRELLIS.2 precedent) | [V] `backend/requirements.txt:73` |
| Model classes present in the installed transformers 4.57.6: `sam`, `sam2`, `sam2_video`, `sam_hq`, `edgetam`, `edgetam_video`, `florence2`, `grounding_dino`, `mm_grounding_dino`, `owlvit`, `owlv2`, `clipseg`, `segformer`, `oneformer`, `mask2former`, `maskformer`, `eomt`, `upernet`, `dpt`, `beit`, `qwen2_5_vl`, `qwen3_vl`, `internvl`, `perception_lm`, `dinov3_vit`. **No `sam3`** | [V] `site-packages/transformers/models/` |
| Class locations: `Sam2Model` `sam2/modeling_sam2.py:1277`; `Sam2Processor` `sam2/processing_sam2.py:38` (`post_process_masks` :479); `SamHQModel` `sam_hq/modeling_sam_hq.py:1235`; `EdgeTamModel` `edgetam/modeling_edgetam.py:920`; `GroundingDinoForObjectDetection` `grounding_dino/modeling_grounding_dino.py:2413` (`post_process_grounded_object_detection` in `processing_grounding_dino.py:192`); `MMGroundingDinoForObjectDetection` `mm_grounding_dino/modeling_mm_grounding_dino.py:2389`; `Owlv2ForObjectDetection` `owlv2/modeling_owlv2.py:1201`; `CLIPSegForImageSegmentation` `clipseg/modeling_clipseg.py:1177`; `SegformerForSemanticSegmentation` `segformer/modeling_segformer.py:661`; `OneFormerForUniversalSegmentation` `oneformer/modeling_oneformer.py:2981`; `Mask2FormerForUniversalSegmentation` `mask2former/modeling_mask2former.py:2240`; `EomtForUniversalSegmentation` `eomt/modeling_eomt.py:1032`; `Qwen3VLForConditionalGeneration` `qwen3_vl/modeling_qwen3_vl.py:1271`; `Sam2VideoModel` `sam2_video/modeling_sam2_video.py:1560` | [V] grep |
| Florence-2 native task tokens: `<REFERRING_EXPRESSION_SEGMENTATION>` and `<REGION_TO_SEGMENTATION>` produce polygons; `<OPEN_VOCABULARY_DETECTION>` and `<CAPTION_TO_PHRASE_GROUNDING>` produce boxes | [V] `florence2/processing_florence2.py:86-111` |
| SDPA is supported, so flash-attn is not required: `Sam2` (`_supports_sdpa` at `modeling_sam2.py:553`), Florence2 (`:499`, `:633`), Qwen3-VL (`:554`). Grounding DINO's deformable attention uses `@use_kernel_forward_from_hub` (`modeling_grounding_dino.py:44`), which falls back to pure PyTorch when the `kernels` package is absent (it is absent) | [V] grep |
| TRELLIS conditioning image: `Trellis2ImageTo3DPipeline.preprocess_image` caps the long side at 1024 px, removes the background (or uses the alpha channel), crops a **square around the alpha>0.8 bbox**, premultiplies onto black and returns RGB. The crop transform is **not returned** | [V] `backend/third_party/TRELLIS.2/trellis2/pipelines/trellis2_image_to_3d.py:127-160` |
| Img3D calls it in `TrellisGenerationRun._preprocess` | [V] `backend/inference/trellis/generation.py:137-178` |
| All GPU work runs on ONE thread (`ThreadPoolExecutor(max_workers=1, thread_name_prefix=GPU_THREAD_NAME)`) | [V] `backend/workers/generation_worker.py:85` |
| `low_vram` defaults to True; TRELLIS returns its weights to the CPU after each run | [V] `backend/config/settings.py:76`; sibling report `backend_generation_map.md` §4 |
| Aux-model download/licence pattern (`_RepoSpec`, `license_note`, gated RMBG-2.0 with an MIT BiRefNet fallback) | [V] `backend/inference/trellis/assets.py:61-126` |
| Repo is public, GPL-3.0 | [V] `.claude/CLAUDE.md` |

**Consequences**

1. Run segmentation on the **same square crop** TRELLIS sees, so 2D labels line up 1:1 with the conditioning image. The adapter must
   recompute and store the crop transform (deterministic from alpha), because upstream does not return it.
2. The conditioning crop is ≤1024 px. In a full-body shot the face is often under 150 px, so eyes, brows and lashes need a **second pass on a head crop
   taken from the ORIGINAL full-resolution upload**, mapped back through the stored transform.
3. Load segmentation models on demand on the same GPU thread and offload them to CPU RAM (130 GB available) before TRELLIS stages.
4. Any model whose code needs transformers 5.x or huggingface-hub 1.x (SAM 3 via HF, See-through LayerDiff, Sa2VA if it moves forward)
   must be vendored at the module level, or run in a separate sidecar Python process. It can never be pip-installed into the main env.

---

## 2. Candidate matrix

Sizes are the HF API file sizes in bytes ([V]). VRAM and runtime are [A] estimates for one image on an RTX 5090 in bf16/fp16 after warm-up.
"Compat" means compatibility with *our* env: Python 3.14, torch 2.14, transformers 4.57.6, Windows, no flash-attn.

| Model | Kind | Classes / prompting | Anime | Realistic | Licence (weights + data) | Weights | VRAM [A] | Time [A] | Compat with our stack |
|---|---|---|---|---|---|---|---|---|---|
| **SAM 3** `facebook/sam3` | open-vocab concept segmentation (text and/or exemplar → all instances) | any short noun phrase; masks + boxes + scores | good for objects, hair and clothes [A]; small facial parts unproven | good | **SAM License** (custom; commercial use not prohibited; acceptable-use and trade-control limits; redistribution must carry the licence) [V-web]; **gated** | `sam3.pt` 3,450,062,241 / `model.safetensors` 3,439,938,512 (848M params, F32) [V] | 3–5 GB | ~30–60 ms backbone + ~10–20 ms per prompt (paper: 30 ms/image on H200 with 100+ objects [V-web]) | HF classes need transformers **≥5.0.0rc0** ✗. The vendored official code is ✓ (needs only `ftfy` and `iopath`; see §4.1) |
| **SAM 3.1** `facebook/sam3.1` | same as SAM 3, with faster multi-object video tracking ("Object Multiplex") | same | same (release notes describe no image-quality change) | same | SAM License; gated (`manual`) [V] | `sam3.1_multiplex.pt` 3,502,755,717 [V] | same | image path ≈ SAM 3 | official code only (`library: checkpoint`) [V]. No benefit for still images, so use SAM 3 |
| **SAM 2.1** hiera-L / B+ / S | promptable (point/box/mask) | none (needs prompts) | excellent boundaries given a box | excellent | **Apache-2.0** [V] | L: 897,897,416; B+: 323,476,296; S: 184,305,280 [V] | 1–2 GB | ~40–60 ms encode + ~5 ms per prompt (official: 39.5 FPS L, A100) [V-web] | ✓ native (`Sam2Model`, added in **4.56.0**) [V] |
| SAM-HQ ViT-H `syscv-community/sam-hq-vit-huge` | promptable, finer edges (hair) | none | good | good | Apache-2.0 [V] | 2,570,816,384 [V] | 3–4 GB | ~150 ms | ✓ native `SamHQModel` [V] |
| EdgeTAM `yonigozlan/edgetam-1` | lightweight SAM 2 | none | fair | fair | not verified | small | <1 GB | fast | ✓ native (added in **4.57.0**) [V]. No reason to prefer it over SAM 2.1-L on a 5090 |
| **Grounding DINO** base / tiny | open-vocab boxes | text phrases separated by "." | weak on small anime parts [A]; OK for hats, bags, weapons | good | **Apache-2.0** [V] | base 933,400,872; tiny 689,359,096 [V] | 1–2 GB | 80–150 ms per call | ✓ native |
| MM Grounding DINO large | open-vocab boxes (stronger LVIS) | text | as above [A] | good | Apache-2.0 [V] | 1,376,363,740 [V] | 2–3 GB | ~150 ms | ✓ native |
| OWLv2 large ensemble | open-vocab boxes | text queries | fair [A] | good | Apache-2.0 [V] | 1,750,520,144 [V] | 2–3 GB | ~100–200 ms | ✓ native |
| **Florence-2-large** `florence-community/Florence-2-large` | seq2seq multitask: OD, phrase grounding, open-vocab detection, referring segmentation (polygons) | free text | fair; polygons are coarse [A] | good | **MIT** [V] | 1,553,541,016 (0.77B) [V] | 2–3 GB | 0.3–1.5 s per expression (autoregressive) | ✓ native (`Florence2ForConditionalGeneration`, **4.56.0**) [V] |
| CLIPSeg `CIDAS/clipseg-rd64-refined` | text → dense heat-map | free text | coarse | coarse | Apache-2.0 [V] | 603,049,496 [V] | <1 GB | ~20 ms | ✓ native. **Input/output 352×352** [V `preprocessor_config.json`], so it is only usable as a prior |
| OneFormer / Mask2Former / EoMT (ADE20K/COCO) | closed-set panoptic | 150 ADE / 133 COCO stuff+things; "person", "handbag", "backpack", "umbrella", "tie", sports gear… **no body or face parts** | poor | OK for objects | mixed (Mask2Former HF weights "other"; OneFormer and EoMT MIT) [A] | 0.2–0.9 GB | 1–3 GB | ~50–100 ms | ✓ native. **Not useful for parts**; at most for detachable-object detection |
| **SegFormer-B5 face-parsing** `jonathandinu/face-parsing` | closed-set face parsing, 512×512 | CelebAMask-HQ 19: background, skin, nose, eye_g, l_eye, r_eye, l_brow, r_brow, l_ear, r_ear, mouth, u_lip, l_lip, hair, hat, ear_r, neck_l, neck, cloth | poor (photo-trained) [A] | **very good** (aligned faces) [A] | **non-commercial**: card says "non-commercial research and educational purposes" [V README:158]; NVIDIA SegFormer licence is NC [V-web]; CelebAMask-HQ is NC-research only [V-web] | 338,580,732 (84.6M) + ONNX 340 MB / int8 89 MB [V] | <1 GB | 20–40 ms | ✓ native. Opt-in because of the licence |
| SegFormer-B2 clothes `mattmdjaga/segformer_b2_clothes` | closed-set human/clothes parsing (ATR) | 18: Background, Hat, Hair, Sunglasses, Upper-clothes, Skirt, Pants, Dress, Belt, L/R-shoe, Face, L/R-leg, L/R-arm, Bag, Scarf; mIoU 0.69 (Dress 0.55, Belt 0.30, Scarf 0.29) [V] | poor-fair [A] | good | licence "other" → NVIDIA SegFormer licence (NC) [V]; ATR data research-only [A] | 109,493,236 (27.4M) [V] | <0.5 GB | 10–20 ms | ✓ native. Opt-in |
| SCHP (LIP/ATR/Pascal-Person-Part) | closed-set human parsing | LIP 20 (adds Glove, Coat, Socks, Jumpsuits); ATR 18; PPP 7 [V-web] | poor | good (mIoU 59.4 LIP / 82.3 ATR) [V-web] | code MIT [V-web]; LIP/ATR data research-only [A] | ~250 MB [A] | 1–2 GB | ~50 ms | ✗ needs `inplace_abn` CUDA extension and Python-3.6-era code. Superseded by Sapiens2 |
| Sapiens v1 seg 0.3B–2B | human body-part seg (Goliath 28) | see Sapiens2 (no Eyeglass) | poor | very good | **CC-BY-NC-4.0** [V-web] | 1B torchscript ≈4.6 GB [A] | 4–8 GB | 0.1–0.3 s | ✓ torchscript, but NC, so skip |
| **Sapiens2-seg** 0.4B / 0.8B / 1B / 5B | human body-part seg, native 1K (4K hierarchical variants) | 29: Background, Apparel, Eyeglass, Face_Neck, Hair, L/R_Foot, L/R_Hand, L/R_Lower_Arm, L/R_Lower_Leg, L/R_Shoe, L/R_Sock, L/R_Upper_Arm, L/R_Upper_Leg, Lower_Clothing, Torso, Upper_Clothing, Lower_Lip, Upper_Lip, Lower_Teeth, Upper_Teeth, Tongue [V-web SEG.md]. **No eyes or brows** | poor-fair [A] | **excellent** (+24.3 mIoU over v1, paper) [V-web] | **Sapiens2 License** (custom Meta; commercial not excluded; bans biometric identification, deepfakes/impersonation, military use; Meta may audit) [V-web] | 0.4B: 1,626,451,404; 1B: 5,883,353,380 (F32) [V] | 0.4B ~2 GB; 1B ~4 GB | 0.1–0.4 s | ✓ likely: README says standalone inference needs only `torch` + `safetensors`; Python ≥3.12, torch ≥2.7 [V-web]. Vendor `facebookresearch/sapiens2` |
| **See-through body parser** `24yearsold/l2d_sam_iter2` | closed-set **anime** body-part seg; SAM-HQ ViT-H with 19 per-class mask decoders, trained on ~9.1k Live2D models (SIGGRAPH 2026) | V2 19 classes: hair, headwear, face, eyes, eyewear, ears, earwear, nose, mouth, neck, neckwear, topwear, handwear, bottomwear, legwear, footwear, tail, wings, objects [V] | **best available** (domain-trained) [A] | poor-fair [A] | **Apache-2.0** weights + code ("commercial use is permitted") [V] | `checkpoint-18000.pt` 1,294,766,917 [V] | 3–4 GB | 0.2–0.4 s at 1024 | ✓ likely: `SemanticSam` (`common/modules/semanticsam.py`, 291 lines) imports only torch, einops and its own `modules/sam` + `extend_sam` [V]. Vendor those 3 modules; do NOT install its requirements (transformers 5.0.0, diffusers 0.37.0, hub 1.7.2, numpy 2.2.6) [V] |
| See-through LayerDiff 3D `layerdifforg/seethroughv0.0.2_layerdiff3d` (+ NF4) | **generative amodal layer decomposition** (inpainted RGBA layers + pseudo-depth) for anime | V3 23 layers: front hair, back hair, headwear, face, **irides, eyebrow, eyewhite, eyelash**, eyewear, ears, earwear, nose, mouth, neck, neckwear, topwear, handwear, bottomwear, legwear, footwear, tail, wings, objects [V `scrap_model.py` `VALID_BODY_PARTS_V3`] | **best for anime, and the only one with eyelash/eyebrow/iris layers plus occluded-region completion** | n/a | openrail++ (Apache-2.0 for the authors' parts + CreativeML Open RAIL++-M inherited from Animagine XL 4.0/SDXL; use-based restrictions apply) [V] | bf16 10,159,649,987 total (UNet 8.14 GB); NF4 3,764,506,934 [V] | 12–16 GB bf16 at 1280; ~8 GB NF4 [V-web] | **2–3 min** at 1280 (HF ZeroGPU) [V-web] | ✗ in the main env (needs transformers 5.0.0 + diffusers 0.37.0 + hub 1.7.2). **Sidecar only**, later milestone |
| Anime-Face-Segmentation (siyeong0) | UNet-MobileNetV2, 512² | 7: background, hair, eye, mouth, face, skin, clothes [V-web] | fair [A] | n/a | code MIT; weights availability unclear [V-web] | small | <0.5 GB | ~10 ms | ✓ trivially, but low value |
| Anzhc YOLOs (face seg v2–v4, eyes-seg-hd, head+hair) | YOLOv8/11-seg, single-class masks | face (v3 targets *including* brows and lashes in the face mask), eye (sclera; excludes lashes), head+hair [V] | good for eye and face boxes | some | **AGPL-3.0** (ultralytics) [V] | nano/small | <1 GB | ~5–10 ms | needs ultralytics (AGPL; pulls opencv-python, which clashes with headless) or an ONNX export. Optional at most |
| deepghs `anime_eye_detection` (imgutils) | YOLO eye **boxes** for anime | `eye` (F1 0.91–0.94) [V] | good | n/a | **openrail** [V] | ONNX 44.6 MB/version [V] | <0.5 GB | ~5 ms | ONNX needs onnxruntime-gpu: 1.31.0 has a **cp314 win_amd64 wheel** [V]. Optional: good box prompts for SAM 2.1 |
| EVF-SAM2-multitask `YxZhang/evf-sam2-multitask` | text-prompted SAM (BEiT-3 + SAM2-L); semantic and **part** prompts (`"[semantic] hair"`), trained jointly on RefCOCO, ADE20k, Objects365, PartImageNet, human parsing, Pascal Part [V-web] | free text | fair [A] | good | Apache-2.0 [V]; training data includes research-only human-parsing sets [A] | 1,797,138,684 [V] | ~3 GB | 0.2–0.5 s | ⚠ requirements.txt pins old stacks (Pillow 9.4, fastapi 0.100, deepspeed, torchscale 0.2.0…) [V], so it would have to be vendored. Inferior to SAM 3 for the same role |
| Sa2VA-Qwen3-VL-4B (ByteDance) | MLLM + SAM2 grounded segmentation | free text / dialogue; RefCOCO 81.7 / + 77.4 / g 80.0 [V] | good reasoning [A] | good | Apache-2.0 [V] | 20,244,782,713 (5B, F32) [V] | 12–14 GB | 2–5 s | ⚠ `trust_remote_code=True` and the quick-start uses `use_flash_attn=True` [V] (must be disabled). Remote code may need a newer transformers [A]. Too heavy for this role |
| LISA (7B/13B) | reasoning segmentation | free text | fair | good | Apache-2.0 [A] | 13–26 GB [A] | 15–30 GB | seconds | ✗ old LLaVA/transformers pins [A]. Superseded by Sa2VA and SAM 3. **Reject** |
| Qwen3-VL-2B/4B/8B-Instruct | VLM with 2D grounding (boxes/points as JSON) | free text ("left eyebrow", "the sword") | **good semantic understanding of anime** [A] | good | Apache-2.0 [V] | 2B 4,255,140,312; 4B 8,875,719,344; 8B ≈17.5 GB [V] | 5–18 GB | 1–4 s per query | ✓ native (`Qwen3VLForConditionalGeneration`, **4.57.0**) [V]. Best as a *planner/verifier* (lists the accessories present and boxes rare parts), not as a mask source |
| EasyPortrait models (SegFormer-B0/FPN, mmseg 0.30) | face parsing | 9: background, person, skin, L/R brow, L/R eye, lips, teeth (eyes exclude lids and lashes) [V-web] | poor | good (fp mIoU 85.4 @1024) [V-web] | dataset: "a variant of CC BY-SA 4.0" [V-web]; model licence unstated | 15–110M | <1 GB | ~20 ms | ⚠ mmseg checkpoints must be key-converted to `SegformerForSemanticSegmentation` [A]. A possible *permissive* realistic face parser; needs a licence check of the PDF variant |
| FaceSynthetics-trained parsers | face parsing | 19 incl. beard, glasses, headwear, facewear; **no eyelash** [V-web] | poor | good | **non-commercial** dataset [V-web] | – | – | – | skip |

Transformers "added in" versions ([V-web] from the release notes): SAM 2, Florence-2 and DINOv3 in **v4.56.0** (29 Aug 2025); EdgeTAM and Qwen3-VL in
**v4.57.0** (03 Oct 2025); `Sam3`, `Sam3Tracker`, `Sam3TrackerVideo` and `Sam3Video` in **v5.0.0rc0** (01 Dec 2025; contributed 2025-11-19).
v5 requires Python ≥3.10 and changes the config, tokenizer and remote-code internals, so it is incompatible with our 4.57.6 pin.

---

## 3. Per-model notes that matter for Img3D

### 3.1 SAM 3 (primary open-vocabulary engine)
* Promptable Concept Segmentation: a short noun phrase and/or image exemplar returns **every** matching instance (masks, boxes, scores),
  with a presence head that suppresses absent concepts. That suppression is exactly what an accessory checklist needs ("earring", "sword", "glasses").
  About 850M parameters: Perception Encoder L++ vision about 450M, text encoder about 300M, detector and tracker about 100M. Fixed **1008×1008** input [V-web, paper].
* Documented limitations: simple noun phrases only (no long referring expressions), and it "struggles to generalize to fine-grained out-of-domain
  concepts" zero-shot [V-web, paper App. B]. There are no anime results. COCO-O (sketch, cartoon, painting) robustness of its PE backbone is 42.5 AP
  vs 31.9 for DINOv2-L [V-web].
* Independent evidence on eyes: Niehorster & Nyström (arXiv 2603.17715, Mar 2026) found SAM 3 visual/concept prompts generally **did not beat SAM 2**
  on (real) eye-image segmentation, and SAM 2 was faster [V-web]. Use SAM 3 to *find* eyes, brows, accessories and objects, and SAM 2.1 to *refine* small masks.
* Official API (vendored path): `build_sam3_image_model(bpe_path=None, device=…, eval_mode=True, checkpoint_path=None, load_from_HF=True,
  enable_segmentation=True, enable_inst_interactivity=False, compile=False)`. Then `Sam3Processor(model, resolution=1008, confidence_threshold=0.5)`
  with `set_image` / `set_text_prompt` / `add_geometric_prompt` / `reset_all_prompts` / `set_confidence_threshold`. The state holds `boxes` (xyxy, original
  size), `scores` (sigmoid × presence), boolean `masks` and `masks_logits` (probabilities) [V-web source]. One `set_image` call is reused across all prompts.

### 3.2 SAM 2.1 / SAM-HQ (refiner)
* `Sam2Model` + `Sam2Processor` (native). Box, point or mask prompts with multimask output and IoU scores. Use it to tighten SAM 3, Grounding DINO or Florence boxes,
  for left/right splitting, and for re-segmenting renders of the 3D result in the "after reconstruction" pass.
* SAM-HQ (`SamHQModel`, native) gives finer hair edges. Hiera-L is the default; SAM-HQ is optional for hair.

### 3.3 Grounding DINO / Florence-2 / OWLv2 (fallback detectors)
* Grounding DINO base (`AutoModelForZeroShotObjectDetection`, thresholds box 0.4 / text 0.3 in the card example [V]) is reliable for whole objects
  (hat, bag, sword, glasses, shoes) and weak for thin or small parts (eyebrow, eyelash) [A].
* Florence-2-large (MIT) adds `<CAPTION_TO_PHRASE_GROUNDING>`, which grounds the nouns of a caption. This is useful for **prompt-to-3D**, where the prompt text
  already lists the parts. `<REFERRING_EXPRESSION_SEGMENTATION>` returns polygons; it is good enough to seed SAM 2.1 but not good enough as a final mask [A].

### 3.4 Domain parsers
* **See-through `l2d_sam_iter2` (anime).** SAM-HQ ViT-H base. The promptable decoder is replaced by 19 independent class decoders, prompts are zeroed, and it runs closed-set.
  Labels were bootstrapped from Live2D ArtMesh visibility plus Danbooru-tag GradCAM++ maps and refined with a self-refining loop. Trained 16k steps on 4×4090 [V-web paper].
  Output is 19 binary masks (`preds > 0`) [V-web `parse_live2d.py`]. This parser is the dense anime backbone for hair, face, clothing and accessories. It does **not**
  separate eyebrows, lashes, iris or eye-white (those exist only in the generative V3 layers).
* **Sapiens2-seg (realistic humans).** Gives the person-relative left/right limbs, hands, feet, shoes, socks, upper/lower clothing, lips, teeth, tongue and eyeglasses.
  `Face_Neck` is a single class, so eyes and brows must come from SAM 3 or a face parser. `Apparel` is a catch-all for worn items that are not
  upper or lower clothing [A], so sub-classify it with SAM 3 prompts.
* **CelebAMask-HQ face parser (opt-in, NC).** Best realistic eye/brow/lip detail. It needs a roughly aligned face crop (512²).

### 3.5 Prompt-to-3D specifics
* The text-to-image image goes through the identical pipeline. In addition, nouns parsed from the user prompt ("witch hat", "katana", "twin tails") are appended to the
  SAM 3 vocabulary and stored as `expected_parts`. A missing expected part becomes a QA warning.
* (Out of scope for segmentation, but it helps a lot: text-to-image prompt suffixes such as "full body, front view, A-pose, plain background" make parts separable.)

---

## 4. Compatibility deep-dive

### 4.1 SAM 3 without transformers 5: vendoring path (recommended)
* Top-level imports of `sam3/model_builder.py` [V-web source]: `os`, `typing`, **`pkg_resources`**, `torch`, `huggingface_hub.hf_hub_download`,
  **`iopath.common.file_io.g_pathmgr`**, plus about 25 internal `sam3.*` modules. Transitively those need: `numpy`, `PIL`, `torchvision` (`masks_to_boxes`,
  `transforms.v2`), `tqdm`, **`timm.models.layers.trunc_normal_`** (the shim exists in timm 1.0.30 [V]), `psutil`, `torch.distributed`, `multiprocessing`,
  `socket`, **`triton` / `triton.language`** (`sam3/model/edt.py`; triton-windows 3.8 is installed [V]), **`ftfy`** and `regex` (`tokenizer_ve.py`).
  `pycocotools`, `decord` and `flash_attn` are **not** imported on this path [V-web source]. `use_fa3` is just a flag.
* Missing in our env: `ftfy` (pure Python; depends on `wcwidth`), `iopath` (pure Python; depends on `portalocker`, whose base install does
  **not** need pywin32 on Windows [V-web]). Install them with `--no-deps` into the embedded interpreter only after approval.
* Don't: `pip install sam3`. PyPI `sam3 0.1.4` (py3-none-any; maintained by Roboflow accounts, not Meta [V-web]) requires `numpy<2.4.0`,
  `tqdm<4.68.0`, `typing_extensions<4.16.0`, `pycocotools<2.1.0`, `einops<0.9`, `iopath<0.2` [V-web]. That conflicts with numpy 2.4.1, tqdm 4.70.1 and typing_extensions 4.16.0.
  pycocotools 2.0.11 has only a **cp314t** (free-threaded) Windows wheel, not cp314 [V-web]. The GitHub `pyproject.toml` pins `numpy>=1.26,<2`, which is worse [V-web].
* Risks [A], all unverified because nothing was run: Python 3.14 syntax or stdlib edge cases in the sam3 sources; `pkg_resources` deprecation warnings
  (pass `bpe_path` explicitly anyway); Windows `torch.distributed` import (it should import, since gloo exists); triton-windows JIT on sm_120 for the EDT kernel
  (used by the video path; the image path should not call it). Gated download: `facebook/sam3` needs the licence to be accepted with the existing `HF_TOKEN`, the same
  flow as DINOv3. Store the weights under `backend/.model/sam3/`.
* Plan B: a **sidecar** process (a second embedded Python with transformers ≥5 and `Sam3Model`) behind a small IPC/file contract. Use it only if vendoring fails.
  The same sidecar could later host See-through LayerDiff 3D.

### 4.2 Others
* See-through: requirements pin transformers 5.0.0, diffusers 0.37.0, huggingface-hub 1.7.2, numpy 2.2.6, torch 2.8.0+cu128 and Python 3.12 [V-web]. Only the
  body parser is vendor-friendly. LayerDiff 3D needs the sidecar.
* Sapiens2: Python ≥3.12, torch ≥2.7, standalone quick-start with `torch` + `safetensors` [V-web]. Vendor it as a git submodule.
* mediapipe 1.1.1 (uploaded 2026-10-14) ships a `py3-none-win_amd64` wheel, but it **requires `opencv-contrib-python`**, which would install a second `cv2` over
  `opencv-python-headless` [V-web]. Avoid it, or install it with `--no-deps`. It is not needed in the recommended plan.
* onnxruntime-gpu 1.31.0 has a `cp314-cp314-win_amd64` wheel (2026-10-09) [V-web]. That makes ONNX-only helpers (deepghs eye/face detectors) possible as an optional extra.
* flash-attn and xformers are never required: every recommended model runs on PyTorch SDPA.

---

## 5. Eyelashes (and eye sub-parts)

**Findings.** No realistic face-parsing dataset labels eyelashes. CelebAMask-HQ (19 classes), Helen and LaPa (11), FaceSynthetics (19) and EasyPortrait (9) all lack one.
EasyPortrait explicitly *excludes* lids and lashes from "eye" [V-web]. Anzhc's anime eye model also excludes lashes, and its face v3 model folds
brows and lashes *into* the face mask [V]. The only open model that outputs an `eyelash` layer is See-through LayerDiff 3D (V3 taxonomy), which is generative,
heavy, and incompatible with our env.

**Recommended approach (deterministic, no new model):**
1. **Eye mask per side.** Anime: SAM 3 `"eye"` on the head crop, intersected with the l2d `eyes` class. Realistic: SAM 3 `"eye"`, refined by SAM 2.1 boxes.
   Opt-in: CelebAMask `l_eye`/`r_eye`. Split sides by connected components sorted by x; the character's left is image-right for a front view.
2. **Upper-lid curve.** Take the per-column top boundary of the eye mask, smooth it (Savitzky–Golay), and store it as a polyline in image space.
3. **Lash band.** Keep the pixels between the lid curve − 0.35·h_eye and the lid curve + 0.12·h_eye, extended about 15% of the eye width past the outer canthus, and
   intersect that band with *dark* pixels (L\* below an Otsu threshold computed in the eye bbox). Remove the brow mask and the iris cluster.
   Anime lash strokes are thick and black, so this works well [A]. Realistic lashes are 1–3 px at our resolutions, so the mask is only a texture/metadata region [A].
4. **Inside the eye (anime).** Run k-means (k=4) in Lab within the eye mask: the darkest cluster touching the top edge is the lash line, the most saturated cluster is the iris,
   the brightest large cluster is the sclera (eyewhite), and small saturated-white blobs inside the iris are highlights. The pupil is the darkest blob inside the iris.
   Realistic: iris is a circle fit (Hough or least-squares on the iris/sclera edge), the pupil is the darkest disc inside it, and the sclera is the rest.
5. Output `eyelash.{l,r}`: a mask, `lid_curve`, `source: "heuristic"` and `confidence`. Geometry stays **deferred**: TRELLIS.2 cannot recover lash
   strands, so the future (approved) tuning layer generates cards along `lid_curve`.
6. Optional later: in the sidecar, use See-through V3 `eyelash`/`eyebrow`/`irides`/`eyewhite` layers for anime. They are amodal (inpainted) and the best quality
   available.

---

## 6. Recommendation

### 6.1 Primary path

Every model in it is Apache, MIT or a permissive custom licence. The only gated one is SAM 3.

```
upload ─► TRELLIS preprocess (unchanged) ─► conditioning crop C (≤1024²) + recomputed crop transform T
        └─► subject router: user flag / Florence-2 <CAPTION> keyword or Qwen3-VL-2B (optional) ─► {anime_character | realistic_human | object}
pass 1 (on C):
   anime  → See-through l2d_sam_iter2 (19 classes)        ┐
   human  → Sapiens2-seg-0.4b (29 classes; 1b if quality) ├─► dense label map L1 (soft masks, per-class prob)
   object → (none)                                        ┘
   all    → SAM 3 concept prompts over the Img3D vocabulary (+ prompt nouns for prompt-to-3D) ─► instance masks M3
pass 2 (head crop H from the ORIGINAL image via T⁻¹, 25 % margin, resized to 1008²):
   SAM 3: "face", "eye", "eyebrow", "iris", "mouth", "nose", "ear", "earring", "glasses", "hair clip"/"hair ornament"
   SAM 2.1-L: refine every small mask from its SAM 3 box (+ positive centroid point)
   eye sub-split + eyelash heuristics (§5)
fusion ─► taxonomy mapping (§7) ─► priority/occlusion resolution ─► per-part masks + relation graph + QA flags
```

* Model-load budget ([A]): l2d 3–4 GB + SAM 3 3–5 GB + SAM 2.1-L 1–2 GB, so **≤10 GB peak**, loaded sequentially on the GPU thread and offloaded to RAM
  before the TRELLIS stages. Wall time is roughly **1–3 s per image** excluding first-load (about 8 GB of weights read from disk).
* Weights to add, about 6.2 GB total: `sam3.pt` 3.45 GB, `checkpoint-18000.pt` 1.29 GB, `sam2.1-hiera-large` 0.90 GB, `sapiens2-seg-0.4b` 1.63 GB.

### 6.2 Fallback path

No gated weights, no new pip packages, everything native to transformers 4.57.6.

* Grounding DINO-base (or MM-GDINO-large) boxes for the vocabulary, plus Florence-2-large `<CAPTION_TO_PHRASE_GROUNDING>` for prompt nouns, feed SAM 2.1-L masks
  (the Grounded-SAM-2 recipe, reimplemented with HF classes; the IDEA repo itself is not needed because it compiles a CUDA op). Add CLIPSeg
  heat-maps as priors for "hair", "skin" and "clothes", and the same eye/lash heuristics.
* Optional opt-in NC add-ons for realistic faces and clothes, behind a `license_note` + explicit setting (pattern from `assets.py:112-120`):
  `jonathandinu/face-parsing`, `mattmdjaga/segformer_b2_clothes`.

### 6.3 Not recommended now
LISA (old stack), Sa2VA (5B, remote code, flash-attn default), EVF-SAM (dependency mess; SAM 3 covers the role), SCHP (inplace_abn), Sapiens v1 (NC),
OneFormer/Mask2Former/EoMT (no parts), Anzhc YOLO (AGPL, single-class), See-through LayerDiff 3D (needs the sidecar; revisit for the
"decomposition *before* reconstruction" milestone because it produces inpainted amodal layers).

---

## 7. Img3D part taxonomy and mapping

### 7.1 Taxonomy

`P` = physical asset (its own mesh/object after the 3D lift). `R` = semantic region on the continuous body mesh.
`P*` = physical, geometry deferred: the mask and metadata are produced now, a texture-baked region is the fallback, and geometry is made by the future approved tuning layer.

| ID | Kind | Parent / attach_to | Notes |
|---|---|---|---|
| `body` | P (base mesh) | root | continuous skin topology: head, neck, torso, limbs |
| `body.head.face` | R | body | face skin |
| `body.head.nose` | R | body.head.face | |
| `body.head.ear.{l,r}` | R | body.head | human ears stay on the body |
| `body.head.eyelid.{l,r}` / `body.head.eye_socket.{l,r}` | R | body.head.face | derived around the eye masks; receive the lash texture fallback |
| `body.head.mouth.lip_upper` / `.lip_lower` | R | body.head.face | |
| `body.head.mouth.interior` (`.teeth_upper`, `.teeth_lower`, `.tongue`) | R | body.head.face | teeth could become P later |
| `body.neck`, `body.torso` | R | body | |
| `body.arm.{upper,lower}.{l,r}`, `body.hand.{l,r}` | R | body | |
| `body.leg.{upper,lower}.{l,r}`, `body.foot.{l,r}` | R | body | |
| `body.skin_other` | R | body | exposed skin not otherwise labelled |
| `eye.{l,r}` (subregions `.sclera`, `.iris`, `.pupil`, `.highlight`) | **P** | attach_to `body.head.eye_socket.{l,r}` | the user asked for separation. The 3D cut keeps the socket region on the body |
| `eyebrow.{l,r}` | **P\*** | attach_to `body.head.face` | painted-on in TRELLIS output, so it is extracted as a shell/decal later |
| `eyelash.{l,r}` | **P\*** | attach_to `body.head.eyelid.{l,r}` | mask + `lid_curve`; cards generated later |
| `hair` (`.front`, `.back`, `.side`, `.ahoge`, `.ponytail`/`.twintail`) | **P** | attach_to `body.head` | front/back split as in See-through V3 |
| `hair.facial` (beard/moustache) | **P** | attach_to `body.head.face` | realistic only |
| `clothing.top`, `clothing.outer`, `clothing.bottom`, `clothing.dress` | **P** | attach_to `body.torso` / legs | `outer` = jacket, coat, cape (layered over the top) |
| `clothing.legwear` | **P** or R | attach_to legs | skin-tight stockings or tights become R with flag `physical_candidate` |
| `clothing.handwear.{l,r}`, `clothing.footwear.{l,r}` | **P** | attach_to hands / feet | |
| `clothing.neckwear` (scarf, tie, bow) | **P** | attach_to `body.neck` | |
| `accessory.headwear` (hat, ribbon, hair clip, crown, horns-as-prop) | **P** | attach_to `hair` or `body.head` | |
| `accessory.eyewear` | **P** | attach_to `body.head.face` | |
| `accessory.earwear` (earrings) | **P** | attach_to `body.head.ear.{l,r}` | |
| `accessory.jewelry` (necklace, choker, bracelet, ring) | **P** | attach_to neck / wrist | |
| `accessory.belt`, `accessory.bag` | **P** | attach_to torso / shoulder | |
| `appendage.tail`, `appendage.wings`, `appendage.animal_ears` | **P** | attach_to `body.torso` / `hair` | anime "ears" above the hairline become `animal_ears` |
| `object.held.{l,r}` (weapon, staff, book…) / `object.detached` | **P** | `held_by: body.hand.{l,r}` or none | detachable |
| `unknown` | – | – | unassigned foreground (QA flag) |

Relations stored per part: `parent`, `attach_to`, `held_by`, `side` (character-relative) **and** `image_side`, `symmetric_pair`, `occludes[]`
(2D layering from mask adjacency + priority), `source_models[]`, `confidence`, `kind` (P/P\*/R), and `expected` (from prompt nouns).
This is what lets later refinement consume and reassemble parts.

### 7.2 Label mapping

| Source label(s) | → Img3D ID |
|---|---|
| l2d: hair / headwear / face / eyes / eyewear / ears / earwear / nose / mouth / neck / neckwear / topwear / handwear / bottomwear / legwear / footwear / tail / wings / objects | hair / accessory.headwear / body.head.face / eye.{l,r} (CC split) / accessory.eyewear / body.head.ear.{l,r} or appendage.animal_ears (above hairline) / accessory.earwear / body.head.nose / body.head.mouth.* / body.neck / clothing.neckwear or accessory.jewelry (SAM 3 sub-prompt "necklace" vs "scarf") / clothing.top (+ clothing.outer via SAM 3 "jacket/coat/cape") / clothing.handwear / clothing.bottom (dress = top∪bottom continuity + SAM 3 "dress") / clothing.legwear / clothing.footwear / appendage.tail / appendage.wings / object.held |
| See-through V3 extra: front hair, back hair, irides, eyebrow, eyewhite, eyelash | hair.front, hair.back, eye.*.iris, eyebrow.*, eye.*.sclera, eyelash.* |
| Sapiens2: Apparel / Eyeglass / Face_Neck / Hair / {L,R}_Foot / {L,R}_Hand / {L,R}_Lower_Arm / {L,R}_Upper_Arm / {L,R}_Lower_Leg / {L,R}_Upper_Leg / {L,R}_Shoe / {L,R}_Sock / Lower_Clothing / Upper_Clothing / Torso / Upper_Lip / Lower_Lip / Upper_Teeth / Lower_Teeth / Tongue | accessory.* or clothing.* via SAM 3 sub-classification / accessory.eyewear / body.head.face + body.neck (split at the jaw using SAM 3 "face") / hair / body.foot.* / body.hand.* / body.arm.lower.* / body.arm.upper.* / body.leg.lower.* / body.leg.upper.* / clothing.footwear.* / clothing.legwear / clothing.bottom / clothing.top / body.torso / lips / teeth / tongue (Sapiens L/R are **person-relative**, so they map directly to `side`) |
| CelebAMask-HQ (opt-in): skin, nose, eye_g, l_eye, r_eye, l_brow, r_brow, l_ear, r_ear, mouth, u_lip, l_lip, hair, hat, ear_r, neck_l, neck, cloth | body.head.face, nose, accessory.eyewear, eye.*, eyebrow.*, ear.*, mouth.interior, lip_upper, lip_lower, hair, accessory.headwear, accessory.earwear, accessory.jewelry, body.neck, clothing.top. **Recompute sides from geometry**; do not trust the l_/r_ convention |
| ATR (opt-in): Hat, Hair, Sunglasses, Upper-clothes, Skirt, Pants, Dress, Belt, L/R-shoe, Face, L/R-leg, L/R-arm, Bag, Scarf | headwear, hair, eyewear, top, bottom, bottom, dress, belt, footwear.*, face, leg.* (upper+lower), arm.* (upper+lower), bag, neckwear |
| SAM 3 / Grounding DINO / Florence vocabulary | "hair", "bangs" → hair(.front); "ponytail", "twin tails", "ahoge" → hair.*; "beard", "moustache" → hair.facial; "eye" → eye; "iris", "pupil" → eye.*; "eyebrow" → eyebrow; "eyelashes" → eyelash (rarely works; heuristics dominate); "face" → face; "mouth", "lips", "teeth" → mouth.*; "ear" → ear; "hat", "cap", "beret", "helmet", "hood", "crown", "tiara", "hair ribbon", "hair clip", "hairband" → accessory.headwear; "glasses", "sunglasses", "goggles", "eyepatch", "mask" → accessory.eyewear (mask → facewear subtype); "earring" → earwear; "necklace", "choker", "pendant", "bracelet", "watch", "ring" → jewelry; "scarf", "tie", "bow tie", "collar" → clothing.neckwear; "jacket", "coat", "cape", "cloak", "hoodie", "vest" → clothing.outer; "shirt", "blouse", "t-shirt", "sweater", "top" → clothing.top; "skirt", "pants", "shorts", "jeans" → clothing.bottom; "dress", "kimono", "robe", "jumpsuit" → clothing.dress; "glove" → handwear; "sock", "stocking", "tights", "leggings" → legwear; "shoe", "boot", "sandal" → footwear; "belt" → belt; "bag", "backpack", "purse", "pouch" → bag; "tail" → tail; "wings" → wings; "animal ears", "cat ears", "horns" → appendage.*; "sword", "gun", "staff", "wand", "spear", "shield", "bow", "umbrella", "book", "phone", "cup" (+ prompt nouns) → object.held |

**Fusion rules.**
* Per-pixel argmax with a z-priority. Front-most first: object.held > accessory.* > hair.front > eyewear > eyelash > eyebrow > eye > clothing.outer >
  clothing.* > hair.back > body regions.
* Keep soft per-part masks so overlaps survive (amodal-ish). When both a domain parser and SAM 3 claim a pixel, prefer the domain parser for dense classes
  (hair, face, clothes) and SAM 3 + SAM 2.1 for small or rare ones (eyes, brows, jewelry, objects).
* Minimum area and confidence gates create `unknown` instead of mislabels.

---

## 8. Open questions and validation plan (after approval; nothing was run)

1. Run on `test/images/ALICE` and `test/images/ANIME_CHARACTER_01` with both paths. Hand-label about 20 images (10 anime, 10 realistic) in the §7 taxonomy, then report per-part IoU,
   time per stage and peak VRAM (`torch.cuda.max_memory_allocated`) on the 5090.
2. Verify the vendored SAM 3 imports and runs under Python 3.14 (`-s`) with only `ftfy` and `iopath` added, and confirm the `facebook/sam3` gate is accepted for the existing token.
3. Confirm that the l2d parser's `modules/sam` + `extend_sam` have no extra dependencies. Check its 1024² input handling on non-square crops (it pads).
4. Decide whether the opt-in NC models are worth exposing at all in a public GPL-3.0 project.
5. "After reconstruction": the same SAM 3 / SAM 2.1 stack can segment **renders** of the TRELLIS mesh from several views for multi-view label voting. That is the
   2D side of the 3D lift; the 3D side is covered by the 3D-part research.

---

## Sources

* SAM 3 repo, install and licence: https://github.com/facebookresearch/sam3 · pyproject: https://raw.githubusercontent.com/facebookresearch/sam3/main/pyproject.toml · LICENSE: https://raw.githubusercontent.com/facebookresearch/sam3/main/LICENSE · SAM 3.1 notes: https://raw.githubusercontent.com/facebookresearch/sam3/main/RELEASE_SAM3p1.md · model_builder: https://raw.githubusercontent.com/facebookresearch/sam3/main/sam3/model_builder.py · processor: https://raw.githubusercontent.com/facebookresearch/sam3/main/sam3/model/sam3_image_processor.py
* SAM 3 paper (arXiv 2511.16719): https://arxiv.org/html/2511.16719v1 · HF card: https://huggingface.co/facebook/sam3 · transformers docs: https://huggingface.co/docs/transformers/v5.0.0rc0/model_doc/sam3 · PyPI sam3: https://pypi.org/project/sam3/
* transformers releases: v5.0.0rc0 https://github.com/huggingface/transformers/releases/tag/v5.0.0rc0 · v4.57.0 https://github.com/huggingface/transformers/releases/tag/v4.57.0 · v4.56.0 https://github.com/huggingface/transformers/releases/tag/v4.56.0
* SAM 2: https://github.com/facebookresearch/sam2 · EdgeTAM docs: https://huggingface.co/docs/transformers/v4.57.1/en/model_doc/edgetam · SAM-HQ: https://huggingface.co/lkeab/hq-sam
* SAM 3 vs SAM 2 for eyes (arXiv 2603.17715): https://arxiv.org/abs/2603.17715
* Grounding DINO: https://huggingface.co/IDEA-Research/grounding-dino-base · Grounded-SAM-2: https://github.com/IDEA-Research/Grounded-SAM-2 · Florence-2: https://huggingface.co/florence-community/Florence-2-large · CLIPSeg: https://huggingface.co/CIDAS/clipseg-rd64-refined
* Face/human parsing: https://huggingface.co/jonathandinu/face-parsing · https://huggingface.co/mattmdjaga/segformer_b2_clothes · NVIDIA SegFormer licence: https://github.com/NVlabs/SegFormer/blob/master/LICENSE · CelebAMask-HQ: https://mmlab.ie.cuhk.edu.hk/projects/CelebA/CelebAMask_HQ.html · SCHP: https://github.com/GoGoDuck912/Self-Correction-Human-Parsing · EasyPortrait: https://github.com/hukenovs/easyportrait · FaceSynthetics: https://github.com/microsoft/FaceSynthetics
* Sapiens v1: https://huggingface.co/facebook/sapiens-seg-1b-torchscript · Sapiens2: https://github.com/facebookresearch/sapiens2 · classes: https://raw.githubusercontent.com/facebookresearch/sapiens2/main/docs/SEG.md · licence: https://raw.githubusercontent.com/facebookresearch/sapiens2/main/LICENSE.md · paper: https://arxiv.org/abs/2604.21681
* See-through (SIGGRAPH 2026): https://github.com/shitagaki-lab/see-through · paper: https://arxiv.org/html/2602.03749v1 · taxonomy: https://raw.githubusercontent.com/shitagaki-lab/see-through/main/common/live2d/scrap_model.py · parser weights: https://huggingface.co/24yearsold/l2d_sam_iter2 · LayerDiff 3D: https://huggingface.co/layerdifforg/seethroughv0.0.2_layerdiff3d · requirements: https://raw.githubusercontent.com/shitagaki-lab/see-through/main/requirements.txt
* Anime helpers: https://huggingface.co/Anzhc/Anzhcs_YOLOs · https://huggingface.co/deepghs/anime_eye_detection · https://github.com/siyeong0/Anime-Face-Segmentation
* EVF-SAM: https://github.com/hustvl/EVF-SAM · Sa2VA: https://github.com/bytedance/Sa2VA · https://huggingface.co/ByteDance/Sa2VA-Qwen3-VL-4B · Qwen3-VL: https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct
* Wheels: https://pypi.org/project/onnxruntime-gpu/1.31.0/ · https://pypi.org/project/mediapipe/1.1.1/ · https://pypi.org/project/pycocotools/ · https://pypi.org/project/portalocker/
