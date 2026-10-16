"""Part-aware asset layer (part-aware DESIGN.md sections 2 and 3).

A torch-free library (numpy, scipy, Pillow, json, struct) that turns one finished ``model.glb`` plus lifted 2D labels
into an immutable part layer: physical parts, deferred part records, semantic regions, seams, relations and the
``img3d.parts/1`` manifest. It never imports ``services``, ``api`` or ``workers``.

Frozen interfaces (slice S1): :mod:`parts.types` (vocabulary and shared records), :mod:`parts.taxonomy` (part classes
v1) and :mod:`parts.manifest` (the pydantic manifest). The geometry core and the asset layer build on them.
"""
