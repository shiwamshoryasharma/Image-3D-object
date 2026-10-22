"""Model catalog v1 (part-aware DESIGN.md section 5.4). Data only, torch-free, no I/O.

It follows ``DECISIONS.md``: the six chosen models (Z-Image-Turbo, Grounding DINO base, SAM 2.1 hiera-large,
Florence-2-large, the See-through anime parser and Sapiens2-seg 0.4B) are ``default_approved=True`` (Q1). The raster
lifter needs no approval (nvdiffrast is already required by the TRELLIS.2 export). The future 3D seams (P3-SAM,
PartField, HoloPart, X-Part) are listed with ``provider=None`` so ``/models`` can explain why they are unsupported;
they are hidden by default.

Segmentation revisions are pinned here and cross-checked at registry start against
``backend/.model/seg/download_manifest.json`` (keyed by folder name); a mismatch is a startup warning. Adding a model
later takes one :class:`~inference.providers.base.ModelSpec` here plus one provider module and one
``PROVIDER_PATHS`` entry in :mod:`inference.providers.registry`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from inference.providers.base import DependencySpec, LicenseInfo, ModelSpec

_GB: Final = 1_000_000_000

TRANSFORMERS_DEPENDENCY: Final = DependencySpec("transformers", "==4.57.6")
"""Every segmentation provider uses the transformers 4.57.6 native classes (never transformers 5)."""


def _hf_url(repo_id: str) -> str:
    return f"https://huggingface.co/{repo_id}"


def _apache(repo_id: str) -> LicenseInfo:
    return LicenseInfo(id="apache-2.0", name="Apache License 2.0", url=_hf_url(repo_id), commercial="allowed")


@dataclass(frozen=True, slots=True)
class VendoredSource:
    """A model whose code is vendored as a pinned git submodule (section 5.6)."""

    package: str
    """``inference.providers.vendor_import.VendoredPackage`` name (``see_through`` | ``sapiens2``)."""
    label: str
    """Short name used in reasons, e.g. "See-through code is not vendored under backend/third_party/see-through"."""


Z_IMAGE_TURBO: Final = ModelSpec(
    id="z_image_turbo",
    kind="text_to_image",
    roles=("generator",),
    display_name="Z-Image-Turbo",
    provider="zimage",
    repo_id="Tongyi-MAI/Z-Image-Turbo",
    revision="f332072aa78be7aecdf3ee76d5c247082da564a6",
    local_subdir="t2i/Z-Image-Turbo",
    required_files=(
        "model_index.json",
        "scheduler/scheduler_config.json",
        "text_encoder/config.json",
        "text_encoder/model.safetensors.index.json",
        "text_encoder/model-00001-of-00003.safetensors",
        "text_encoder/model-00002-of-00003.safetensors",
        "text_encoder/model-00003-of-00003.safetensors",
        "tokenizer/tokenizer.json",
        "tokenizer/tokenizer_config.json",
        "transformer/config.json",
        "transformer/diffusion_pytorch_model.safetensors.index.json",
        "transformer/diffusion_pytorch_model-00001-of-00003.safetensors",
        "transformer/diffusion_pytorch_model-00002-of-00003.safetensors",
        "transformer/diffusion_pytorch_model-00003-of-00003.safetensors",
        "vae/config.json",
        "vae/diffusion_pytorch_model.safetensors",
    ),
    size_bytes=32_850_000_000,
    vram_peak_gb=24.0,
    ram_gb=21.0,
    cold_load_s=337.0,
    license=_apache("Tongyi-MAI/Z-Image-Turbo"),
    dependencies=(DependencySpec("diffusers", "==0.39.0"), DependencySpec("accelerate", "==1.15.0")),
    default_approved=True,
    capabilities=MappingProxyType(
        {
            "sizes": [[1024, 1024], [896, 1152], [1152, 896]],
            "steps": {"default": 9, "min": 1, "max": 50},
            "guidance": {"default": 0.0, "min": 0.0, "max": 0.0},
            "supports_negative_prompt": False,
            "max_images": 4,
        }
    ),
    notes="bf16; weights are read fully into CPU RAM by load() and moved to the GPU only inside a GPU phase",
)

GROUNDING_DINO_BASE: Final = ModelSpec(
    id="grounding_dino_base",
    kind="segmentation",
    roles=("detector",),
    display_name="Grounding DINO base",
    provider="hf_grounding_dino",
    repo_id="IDEA-Research/grounding-dino-base",
    revision="12bdfa3120f3e7ec7b434d90674b3396eccf88eb",
    local_subdir="seg/grounding-dino-base",
    required_files=(
        "config.json",
        "model.safetensors",
        "preprocessor_config.json",
        "special_tokens_map.json",
        "tokenizer.json",
        "tokenizer_config.json",
        "vocab.txt",
    ),
    size_bytes=934_349_912,
    vram_peak_gb=2.0,
    ram_gb=1.0,
    cold_load_s=None,
    license=_apache("IDEA-Research/grounding-dino-base"),
    dependencies=(TRANSFORMERS_DEPENDENCY,),
    default_approved=True,
    capabilities=MappingProxyType({"max_text_tokens": 256, "batch_text_tokens": 200, "open_vocabulary": True}),
)

SAM2_1_HIERA_LARGE: Final = ModelSpec(
    id="sam2_1_hiera_large",
    kind="segmentation",
    roles=("refiner",),
    display_name="SAM 2.1 hiera-large",
    provider="hf_sam2",
    repo_id="facebook/sam2.1-hiera-large",
    revision="665f8e2ad61cf5f53d65644ff27c8ee525124610",
    local_subdir="seg/sam2.1-hiera-large",
    required_files=("config.json", "model.safetensors", "preprocessor_config.json"),
    size_bytes=897_924_488,
    vram_peak_gb=2.0,
    ram_gb=1.0,
    cold_load_s=None,
    license=_apache("facebook/sam2.1-hiera-large"),
    dependencies=(TRANSFORMERS_DEPENDENCY,),
    default_approved=True,
    capabilities=MappingProxyType({"prompts": ["box"]}),
)

FLORENCE2_LARGE: Final = ModelSpec(
    id="florence2_large",
    kind="segmentation",
    roles=("grounder",),
    display_name="Florence-2-large",
    provider="hf_florence2",
    repo_id="florence-community/Florence-2-large",
    revision="4271c66b88cdbc05735372ec13b2360108de5317",
    local_subdir="seg/Florence-2-large",
    required_files=(
        "config.json",
        "generation_config.json",
        "model.safetensors",
        "preprocessor_config.json",
        "processor_config.json",
        "tokenizer.json",
        "tokenizer_config.json",
        "vocab.json",
        "merges.txt",
    ),
    size_bytes=1_558_931_269,
    vram_peak_gb=3.0,
    ram_gb=1.6,
    cold_load_s=None,
    license=LicenseInfo(
        id="mit", name="MIT License", url=_hf_url("florence-community/Florence-2-large"), commercial="allowed"
    ),
    dependencies=(TRANSFORMERS_DEPENDENCY,),
    default_approved=True,
    capabilities=MappingProxyType({"tasks": ["<REFERRING_EXPRESSION_SEGMENTATION>"], "dtype": "float16"}),
)

SEE_THROUGH_L2D: Final = ModelSpec(
    id="see_through_l2d",
    kind="segmentation",
    roles=("parser",),
    display_name="See-through anime part parser",
    provider="vendored_see_through",
    repo_id="24yearsold/l2d_sam_iter2",
    revision="0b0608310fb7a89e32ecd7397147a249540cf1e7",
    local_subdir="seg/l2d_sam_iter2",
    required_files=("checkpoint-18000.pt",),
    size_bytes=1_294_783_156,
    vram_peak_gb=4.0,
    ram_gb=1.3,
    cold_load_s=None,
    license=_apache("24yearsold/l2d_sam_iter2"),
    dependencies=(
        DependencySpec("einops", ">=0.8"),
        DependencySpec("accelerate", "==1.15.0"),
        DependencySpec("safetensors", ">=0.4"),
        DependencySpec("PyYAML", ">=6"),
        DependencySpec("opencv-python-headless", ">=4.8"),
    ),
    vendored_source="third_party/see-through",
    default_approved=True,
    capabilities=MappingProxyType({"subject": "anime_character", "profile": "anime", "classes": 19}),
    notes="Loaded with torch.load(weights_only=True); model code vendored from shitagaki-lab/see-through",
)

SAPIENS2_SEG_0_4B: Final = ModelSpec(
    id="sapiens2_seg_0_4b",
    kind="segmentation",
    roles=("parser",),
    display_name="Sapiens2-seg 0.4B",
    provider="vendored_sapiens2",
    repo_id="facebook/sapiens2-seg-0.4b",
    revision="449b3c5335e6722bb94990abdd1aa6e612432f22",
    local_subdir="seg/sapiens2-seg-0.4b",
    required_files=("config.json", "model.safetensors"),
    size_bytes=1_626_456_032,
    vram_peak_gb=3.0,
    ram_gb=1.7,
    cold_load_s=None,
    license=LicenseInfo(
        id="sapiens2-license",
        name="Sapiens2 License",
        url=_hf_url("facebook/sapiens2-seg-0.4b"),
        commercial="allowed_with_conditions",
        conditions=("No biometric identification", "No deepfakes or impersonation", "No military use"),
    ),
    dependencies=(DependencySpec("safetensors", ">=0.4"),),
    vendored_source="third_party/sapiens2",
    default_approved=True,
    capabilities=MappingProxyType({"subject": "realistic_human", "profile": "realistic", "classes": 29}),
    notes="model.safetensors is used; the byte-identical sapiens2_0.4b_seg.safetensors is ignored",
)

RASTER_LIFTER: Final = ModelSpec(
    id="raster_lifter",
    kind="part_extraction",
    roles=("lifter",),
    display_name="Raster label lifter",
    provider="nvdiffrast_raster",
    repo_id=None,
    revision=None,
    local_subdir=None,
    required_files=(),
    size_bytes=None,
    vram_peak_gb=4.0,
    ram_gb=0.5,
    cold_load_s=None,
    license=LicenseInfo(
        id="nvidia-source-code-license",
        name="NVIDIA Source Code License (nvdiffrast)",
        url="https://github.com/NVlabs/nvdiffrast/blob/main/LICENSE.txt",
        commercial="non_commercial",
        attribution="nvdiffrast (NVIDIA), already required by the TRELLIS.2 export",
    ),
    dependencies=(DependencySpec("nvdiffrast", "==0.4.0"), DependencySpec("cumesh", "==0.0.1")),
    approval_required=False,
    capabilities=MappingProxyType({"max_views": 24}),
    notes="No weights: rasterises the generated mesh with nvdiffrast to lift 2D labels onto faces",
)

_NO_WHEELS: Final = "no Python 3.14 / CUDA 13 wheels"

P3SAM: Final = ModelSpec(
    id="p3sam",
    kind="segmentation_3d",
    roles=("segmenter",),
    display_name="P3-SAM (Hunyuan3D-Part)",
    provider=None,
    repo_id="tencent/Hunyuan3D-Part",
    revision=None,
    local_subdir=None,
    required_files=(),
    size_bytes=451_000_000,
    vram_peak_gb=0.0,
    ram_gb=0.0,
    cold_load_s=None,
    license=LicenseInfo(
        id="tencent-hunyuan-3d-part-community",
        name="Tencent Hunyuan 3D-Part Community License",
        url=_hf_url("tencent/Hunyuan3D-Part"),
        commercial="unknown",
    ),
    unsupported_reason=f"Needs spconv and torch_scatter ({_NO_WHEELS})",
    hidden_by_default=True,
)

PARTFIELD: Final = ModelSpec(
    id="partfield",
    kind="segmentation_3d",
    roles=("segmenter",),
    display_name="PartField",
    provider=None,
    repo_id="nvidia/PartField",
    revision=None,
    local_subdir=None,
    required_files=(),
    size_bytes=int(1.24 * _GB),
    vram_peak_gb=0.0,
    ram_gb=0.0,
    cold_load_s=None,
    license=LicenseInfo(
        id="nvidia-license", name="NVIDIA License", url=_hf_url("nvidia/PartField"), commercial="non_commercial"
    ),
    unsupported_reason=f"Needs torch_scatter, open3d and pymeshlab ({_NO_WHEELS})",
    hidden_by_default=True,
)

HOLOPART: Final = ModelSpec(
    id="holopart",
    kind="completion",
    roles=("completer",),
    display_name="HoloPart",
    provider=None,
    repo_id="VAST-AI/HoloPart",
    revision=None,
    local_subdir=None,
    required_files=(),
    size_bytes=int(3.83 * _GB),
    vram_peak_gb=0.0,
    ram_gb=0.0,
    cold_load_s=None,
    license=LicenseInfo(id="mit", name="MIT License", url=_hf_url("VAST-AI/HoloPart"), commercial="allowed"),
    unsupported_reason="Needs diso, torch_cluster and a numpy 1.x pin (incompatible with this runtime)",
    hidden_by_default=True,
)

X_PART: Final = ModelSpec(
    id="x_part",
    kind="part_generation",
    roles=("generator",),
    display_name="X-Part (Hunyuan3D-Part)",
    provider=None,
    repo_id="tencent/Hunyuan3D-Part",
    revision=None,
    local_subdir=None,
    required_files=(),
    size_bytes=9 * _GB,
    vram_peak_gb=0.0,
    ram_gb=0.0,
    cold_load_s=None,
    license=LicenseInfo(
        id="tencent-hunyuan-3d-part-community",
        name="Tencent Hunyuan 3D-Part Community License",
        url=_hf_url("tencent/Hunyuan3D-Part"),
        commercial="unknown",
    ),
    unsupported_reason=f"Needs the Sonata/spconv stack ({_NO_WHEELS})",
    hidden_by_default=True,
)

CATALOG: Final[tuple[ModelSpec, ...]] = (
    Z_IMAGE_TURBO,
    GROUNDING_DINO_BASE,
    SAM2_1_HIERA_LARGE,
    FLORENCE2_LARGE,
    SEE_THROUGH_L2D,
    SAPIENS2_SEG_0_4B,
    RASTER_LIFTER,
    P3SAM,
    PARTFIELD,
    HOLOPART,
    X_PART,
)
"""Catalog v1 in display order (the registry keeps this order)."""

VENDORED_SOURCES: Final[Mapping[str, VendoredSource]] = MappingProxyType(
    {
        SEE_THROUGH_L2D.id: VendoredSource(package="see_through", label="See-through"),
        SAPIENS2_SEG_0_4B.id: VendoredSource(package="sapiens2", label="Sapiens2"),
    }
)
"""Model id -> vendored package (models whose ``vendored_source`` is set)."""

SEG_DOWNLOAD_MANIFEST: Final = "seg/download_manifest.json"
"""Relative to ``settings.aux_model_root``: ``{folder: {"repo", "revision", "size_bytes"}}`` written at download."""
