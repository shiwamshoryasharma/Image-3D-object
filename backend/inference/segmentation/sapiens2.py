"""Sapiens2-seg 0.4B realistic-human parser (``sapiens2_seg_0_4b``; part-aware DESIGN.md sections 4.5.3, 5.2, 5.6).

Licence: Sapiens2 License (Meta, custom; commercial use allowed with conditions: no biometric identification, no
deepfakes or impersonation, no military use). The conditions travel in ``ModelOut.license.conditions`` (catalog).

Model code: the pinned submodule ``backend/third_party/sapiens2`` (facebookresearch/sapiens2), imported only through
:class:`~inference.providers.vendor_import.VendoredPackage` as ``img3d_vendor.sapiens2.sapiens2`` (never
``sys.path``). Only upstream's **standalone** backbone ``sapiens/backbones/standalone/sapiens2.py`` is executed (pure
torch; model definitions only). The full ``sapiens`` package is not imported: its ``__init__`` loads the training
engine, which needs termcolor, rich, matplotlib, iopath, ... (not installed, never installed for Img3D). The
segmentation head (``sapiens/dense/src/models/heads/seg_head.py``, ``SegHead``) is only a ``ConvTranspose2d`` /
``InstanceNorm2d`` / ``SiLU`` stack plus a 1x1 classifier but sits behind that package ``__init__``; :func:`_seg_head`
builds the same layers here (same parameter names, so the checkpoint loads with ``strict=True``).

Weights: ``backend/.model/seg/sapiens2-seg-0.4b/model.safetensors`` (float32; the byte-identical
``sapiens2_0.4b_seg.safetensors`` is ignored), loaded with ``safetensors.torch.load_file`` into the backbone
``Sapiens2(arch="sapiens2_0.4b", img_size=(1024, 768), out_type="featmap")`` + head, all 424 keys matching.

Inference follows upstream ``dense/tools/vis/vis_seg.py`` and the 0.4B seg config: RGB resized to 1024x768 (H x W,
no aspect keep), normalised with mean (123.675, 116.28, 103.53) / std (58.395, 57.12, 57.375), logits resized
bilinearly to the input size, softmax over the 29 ``DOME_CLASSES_29`` classes, label = argmax.

Output contract (S5, ``Parser2D.parse``): per taxonomy class one 8-bit soft PNG (summed class probability * 255) and
the argmax mask; ``score`` = mean probability inside the mask. Sapiens left/right are person-relative, i.e. already
character-relative, so ``Left_Hand`` -> ``body.hand.left``. Upper and lower arm/leg merge into ``body.arm.*`` /
``body.leg.*``; shoes, socks and lips/teeth/tongue merge into ``clothing.footwear``, ``clothing.legwear`` and
``body.head.mouth``. ``Face_Neck`` -> ``body.head.face`` (one class upstream). ``Background`` and the catch-all
``Apparel`` are not emitted (no taxonomy class fits ``Apparel`` honestly; accessories come from the detector).
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, Final

import numpy as np

from inference.segmentation.see_through import Plane, VendoredParserProvider

WEIGHTS_FILE: Final = "model.safetensors"
ARCH: Final = "sapiens2_0.4b"
INPUT_HW: Final = (1024, 768)
MEAN: Final = (123.675, 116.28, 103.53)
STD: Final = (58.395, 57.12, 57.375)
EMBED_DIM: Final = 1024
DECONV_OUT: Final = (512, 256, 128, 64)
DECONV_KERNELS: Final = (4, 4, 4, 4)
CONV_OUT: Final = (64, 64)
CONV_KERNELS: Final = (1, 1)

CLASS_NAMES: Final[tuple[str, ...]] = (
    "Background", "Apparel", "Eyeglass", "Face_Neck", "Hair", "Left_Foot", "Left_Hand", "Left_Lower_Arm",
    "Left_Lower_Leg", "Left_Shoe", "Left_Sock", "Left_Upper_Arm", "Left_Upper_Leg", "Lower_Clothing", "Right_Foot",
    "Right_Hand", "Right_Lower_Arm", "Right_Lower_Leg", "Right_Shoe", "Right_Sock", "Right_Upper_Arm",
    "Right_Upper_Leg", "Torso", "Upper_Clothing", "Lower_Lip", "Upper_Lip", "Lower_Teeth", "Upper_Teeth", "Tongue",
)  # fmt: skip
"""Upstream ``DOME_CLASSES_29`` (``sapiens/dense/src/datasets/seg/seg_utils.py``), index = logit channel."""

LABEL_MAP: Final[Mapping[str, str]] = MappingProxyType(
    {
        "Eyeglass": "accessory.eyewear",
        "Face_Neck": "body.head.face",
        "Hair": "hair",
        "Left_Foot": "body.foot.left",
        "Right_Foot": "body.foot.right",
        "Left_Hand": "body.hand.left",
        "Right_Hand": "body.hand.right",
        "Left_Lower_Arm": "body.arm.left",
        "Left_Upper_Arm": "body.arm.left",
        "Right_Lower_Arm": "body.arm.right",
        "Right_Upper_Arm": "body.arm.right",
        "Left_Lower_Leg": "body.leg.left",
        "Left_Upper_Leg": "body.leg.left",
        "Right_Lower_Leg": "body.leg.right",
        "Right_Upper_Leg": "body.leg.right",
        "Left_Shoe": "clothing.footwear",
        "Right_Shoe": "clothing.footwear",
        "Left_Sock": "clothing.legwear",
        "Right_Sock": "clothing.legwear",
        "Lower_Clothing": "clothing.bottom",
        "Upper_Clothing": "clothing.top",
        "Torso": "body.torso",
        "Lower_Lip": "body.head.mouth",
        "Upper_Lip": "body.head.mouth",
        "Lower_Teeth": "body.head.mouth",
        "Upper_Teeth": "body.head.mouth",
        "Tongue": "body.head.mouth",
    }
)
"""Source class -> taxonomy id (segmentation research section 7.2); ``Background`` and ``Apparel`` are unmapped."""


class Sapiens2Provider(VendoredParserProvider):
    """Closed-set human part masks from the vendored Sapiens2 standalone backbone + segmentation head."""

    vendored_name = "sapiens2"
    label_map = LABEL_MAP

    def _load_components(self) -> tuple[Any, Any]:
        import accelerate
        from safetensors.torch import load_file

        standalone = self.vendored.import_module("sapiens2")
        with accelerate.init_empty_weights(include_buffers=False):
            model = _segmentor(standalone.Sapiens2)
        state = load_file(str(self.model_dir / WEIGHTS_FILE), device="cpu")
        model.load_state_dict(state, strict=True, assign=True)
        return None, model

    def _predict(self, model: Any, rgb: np.ndarray) -> list[Plane]:
        import cv2
        import torch
        import torch.nn.functional as F

        height, width = rgb.shape[:2]
        resized = cv2.resize(rgb, (INPUT_HW[1], INPUT_HW[0]), interpolation=cv2.INTER_LINEAR).astype(np.float32)
        normalized = (resized - np.asarray(MEAN, np.float32)) / np.asarray(STD, np.float32)
        parameter = next(model.parameters())
        inputs = torch.from_numpy(normalized).permute(2, 0, 1)[None].to(parameter.device, parameter.dtype)
        with torch.inference_mode():
            logits = model(inputs).float()
            logits = F.interpolate(logits, size=(height, width), mode="bilinear", align_corners=False)
            probs_t = logits.softmax(dim=1)[0]
            labels = probs_t.argmax(dim=0).cpu().numpy()
            probs = probs_t.cpu().numpy()
        return [(name, probs[i], labels == i) for i, name in enumerate(CLASS_NAMES) if name in LABEL_MAP]


def _segmentor(backbone_cls: Any) -> Any:
    """``backbone`` (vendored ``Sapiens2``) + ``decode_head`` (:func:`_seg_head`); forward = upstream SegEstimator."""
    import torch.nn as nn

    class Sapiens2Segmentor(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.backbone = backbone_cls(
                arch=ARCH, img_size=INPUT_HW, patch_size=16, final_norm=True, use_tokenizer=False,
                with_cls_token=True, out_type="featmap",
            )  # fmt: skip
            self.decode_head = _seg_head(len(CLASS_NAMES))

        def forward(self, x: Any) -> Any:
            return self.decode_head(self.backbone(x)[0])

    return Sapiens2Segmentor()


def _seg_head(num_classes: int) -> Any:
    """Upstream ``SegHead`` layers for the 0.4B seg config (inference only; same parameter names)."""
    import torch.nn as nn

    class SegHead(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            deconv: list[nn.Module] = []
            channels = EMBED_DIM
            for out_channels, kernel in zip(DECONV_OUT, DECONV_KERNELS, strict=True):
                assert kernel == 4  # padding 1, output_padding 0 (upstream table)
                deconv += [
                    nn.ConvTranspose2d(channels, out_channels, kernel, stride=2, padding=1, output_padding=0,
                                       bias=False),
                    nn.InstanceNorm2d(out_channels),
                    nn.SiLU(inplace=True),
                ]  # fmt: skip
                channels = out_channels
            self.deconv_layers = nn.Sequential(*deconv)
            conv: list[nn.Module] = []
            for out_channels, kernel in zip(CONV_OUT, CONV_KERNELS, strict=True):
                conv += [
                    nn.Conv2d(channels, out_channels, kernel, stride=1, padding=(kernel - 1) // 2),
                    nn.InstanceNorm2d(out_channels),
                    nn.SiLU(inplace=True),
                ]
                channels = out_channels
            self.conv_layers = nn.Sequential(*conv)
            self.conv_seg = nn.Conv2d(channels, num_classes, kernel_size=1)

        def forward(self, x: Any) -> Any:
            return self.conv_seg(self.conv_layers(self.deconv_layers(x)))

    return SegHead()
