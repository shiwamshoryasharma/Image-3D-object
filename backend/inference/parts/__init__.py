"""3D part providers (part-aware DESIGN.md sections 4.6 and 5.2).

:mod:`inference.parts.base` is torch-free; the raster label lifter and its camera helpers import torch/nvdiffrast/cumesh
inside their functions and are loaded lazily by the provider registry. The 3D segmenter, amodal completer and part
generator seams are interfaces only: no implementation ships in this milestone.
"""
