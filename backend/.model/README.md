# `backend/.model/` — model weights (not in Git)

> Only this README is tracked. The weights (~18 GB) belong to their authors and are downloaded by each user
> from the official Hugging Face repositories, under each model's own license.

## What lives here

| Folder | Model | Source | License | Role |
|---|---|---|---|---|
| `TRELLIS.2-4B/` | TRELLIS.2-4B (4B-param flow transformers + O-Voxel VAEs, `pipeline.json`) | [microsoft/TRELLIS.2-4B](https://huggingface.co/microsoft/TRELLIS.2-4B) | MIT | Image → 3D generation (required) |
| `dinov3-vitl16-pretrain-lvd1689m/` | DINOv3 ViT-L/16 | [facebook/dinov3-vitl16-pretrain-lvd1689m](https://huggingface.co/facebook/dinov3-vitl16-pretrain-lvd1689m) (gated) | [DINOv3 License](https://ai.meta.com/resources/models-and-libraries/dinov3-license/) | Image conditioning encoder (required) |
| `TRELLIS-image-large/ckpts/ss_dec_conv3d_16l8_fp16.*` | TRELLIS v1 sparse-structure decoder | [microsoft/TRELLIS-image-large](https://huggingface.co/microsoft/TRELLIS-image-large) | MIT | Decodes the sparse structure (required) |
| `BiRefNet/` | BiRefNet | [ZhengPeng7/BiRefNet](https://huggingface.co/ZhengPeng7/BiRefNet) | MIT | Background removal (default) |
| `RMBG-2.0/` | BRIA RMBG-2.0 | [briaai/RMBG-2.0](https://huggingface.co/briaai/RMBG-2.0) (gated) | CC BY-NC 4.0 (non-commercial) | Optional background removal |
| `.hf_cache/` | Hugging Face hub cache + remote-code modules | — | — | Created by the downloader |

## How to get the weights

1. **TRELLIS.2-4B** — download the repository snapshot into `TRELLIS.2-4B/`:

   ```powershell
   .\backend\python\python.exe -s -c "from huggingface_hub import snapshot_download; snapshot_download('microsoft/TRELLIS.2-4B', local_dir=r'backend\.model\TRELLIS.2-4B')"
   ```

2. **Gated models** — accept the terms on the DINOv3 (and optionally RMBG-2.0) model pages with your Hugging Face
   account, put a read token into `backend/.env` (`HF_TOKEN=...`), then run:

   ```powershell
   .\backend\python\python.exe -s backend\scripts\download_aux_models.py          # download what is missing
   .\backend\python\python.exe -s backend\scripts\download_aux_models.py --check  # verify, offline
   ```

   The script fetches DINOv3, the TRELLIS v1 SS decoder and BiRefNet (used automatically when RMBG-2.0 is not
   available). After that the app runs fully offline.

## Licenses I follow

- Weights are used **as published, unmodified, for local inference**. None of them is redistributed by this
  repository, and nothing here re-licenses them.
- **DINOv3** — used under the DINOv3 License; I accepted it on Hugging Face. Its acceptable-use and trade-control
  terms apply to anyone running Img3D, and research that uses it must acknowledge DINOv3.
- **RMBG-2.0** — CC BY-NC 4.0: non-commercial use only (commercial use needs an agreement with BRIA AI).
  Img3D uses MIT-licensed BiRefNet by default, so RMBG-2.0 is optional.
- **TRELLIS.2-4B, TRELLIS-image-large, BiRefNet** — MIT.
- Every user must review and accept these licenses themselves before downloading. Details:
  [`THIRD_PARTY_NOTICES.md`](../../THIRD_PARTY_NOTICES.md).
