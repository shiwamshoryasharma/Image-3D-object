"""Text-to-image providers (prompt-to-3D, part-aware DESIGN.md section 4.4).

:mod:`inference.imagegen.base` is torch-free; implementation modules (e.g. ``zimage.py``) import torch/diffusers inside
their functions and are loaded lazily by the provider registry.
"""
