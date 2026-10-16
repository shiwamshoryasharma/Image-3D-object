"""2D segmentation providers (part-aware DESIGN.md sections 4.5 and 5.2).

:mod:`inference.segmentation.base` is torch-free; implementation modules (Grounding DINO, SAM 2.1, Florence-2 and the
vendored See-through / Sapiens2 parsers) import torch/transformers inside their functions and are loaded lazily by the
provider registry.
"""
