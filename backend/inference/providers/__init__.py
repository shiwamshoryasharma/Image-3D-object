"""Auxiliary model providers and the model registry (part-aware DESIGN.md section 5).

:mod:`inference.providers.base` holds the torch-free vocabulary every provider shares (model specs, licences, honest
states, the :class:`~inference.providers.base.ModelProvider` ABC). The registry, the catalog, the unavailable stand-in
and the VRAM helpers live next to it; implementation modules under ``inference/{imagegen,segmentation,parts}/`` are the
only places that import torch, diffusers, transformers or nvdiffrast, and the registry imports them lazily.
"""
