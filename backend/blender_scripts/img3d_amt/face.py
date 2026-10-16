"""Face refinement (``face_refinement``): X-mirror symmetry repair + humanoid face corrections.

Order of work
1. **Gates** - global mirror score (:mod:`symmetry`), head detection (:func:`detect_head`: front-half cross-section
   width profile along +Z, the neck is the narrowest band below the head with wider shoulders underneath; head-only
   inputs without a neck are accepted when the profile looks like a head) and an upright / symmetric shape test.
   Animals and props (fox, castle: mirror error ~3 %) are not humanoid -> no face work, symmetry ``auto`` skipped.
2. **Symmetry** (``options.symmetry``, see :mod:`symmetry`) - local defect repair from the better side, or full
   symmetrize on request.
3. **Face region** - front-facing (normal . -Y > ~0.3) side of the head, stored as POINT attribute
   ``amt_face_region`` (0..1, read by topology_tuning to avoid double smoothing).
4. **Eyes** (``options.eye_correction``) - base-colour texture sampled through the UVs (skin = robust mode of the
   lower-central face; eye = clearly non-skin, non-hair colour in the upper-middle face band) combined with geometry
   (recessed relative to a heavily smoothed head surface), mirror-paired across the symmetry plane. Correction:
   pits / blobs are re-faired (thin-plate), comb / slit grooves removed with a low-pass inside the eye while the
   shallow recess is kept, raised rims softened slightly, smooth falloff at the boundary.
5. **Midline crease** - the sharp vertical ridge on the face / chin below the eyes is rounded in a narrow band
   around the plane; the nose (most forward midline point between the eyes and the chin) is protected.
6. **Light facial denoise** on the skin of the face region.

Every detected region / score goes to ``details`` (eyes, skin colour, crease dihedral before/after ...).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import bpy
import numpy as np

from . import meshdata, smoothing, symmetry
from .context import StepOutcome, TunerContext
from .topology import MeshArrays, apply_positions

log = logging.getLogger(__name__)

FRONT = np.array([0.0, -1.0, 0.0])
FACE_FRONT_DOT = 0.3
PROFILE_BINS = 100
NECK_SEARCH = (0.06, 0.75)          # fraction of the height below the top searched for the neck
NECK_MAX_RATIO = 0.7                # neck width / head width
SHOULDER_MIN_RATIO = 1.35           # widest band within 20 % of the height below the neck / neck width
EYE_BAND = (0.10, 0.75)             # fraction of the head height (neck -> top) containing the eyes
CREASE_HALF_WIDTH = 0.12            # midline band half width / head half width
NOSE_PROTECT = (0.05, 0.09)         # full / no protection radius around the nose tip (fraction of head height)
FACE_HOLE_MAX_EDGES = 200           # face holes (eye slits, pits) closed before fairing ...
FACE_HOLE_MAX_PERIMETER = 0.6       # ... when their perimeter is below this fraction of the head height
FACE_ISLAND_MAX_VERTS = 16          # floating specks inside the face removed
FACE_ATTRIBUTE = "amt_face_region"


# ================================================================================================================
# data
# ================================================================================================================


@dataclass
class HeadInfo:
    found: bool = False
    reason: str = ""
    z_top: float = 0.0
    z_neck: float = 0.0
    center_x: float = 0.0
    center_y: float = 0.0
    half_width: float = 0.0
    head_only: bool = False
    neck_ratio: float | None = None
    z_chin: float | None = None

    @property
    def height(self) -> float:
        return self.z_top - self.z_neck

    def to_dict(self) -> dict[str, Any]:
        return {"found": self.found, "reason": self.reason, "z_top": round(self.z_top, 5),
                "z_neck": round(self.z_neck, 5), "center_x": round(self.center_x, 5),
                "center_y": round(self.center_y, 5), "half_width": round(self.half_width, 5),
                "head_only": self.head_only,
                "neck_ratio": None if self.neck_ratio is None else round(self.neck_ratio, 3),
                "z_chin": None if self.z_chin is None else round(self.z_chin, 5)}


@dataclass
class FaceRegion:
    soft: np.ndarray                 # (V,) 0..1
    mask: np.ndarray                 # (V,) bool (soft > 0.5)
    head: np.ndarray                 # (V,) bool vertices of the head block
    normals: np.ndarray              # (V, 3) smoothed vertex normals
    offset: np.ndarray               # (V,) signed offset from a heavily smoothed head surface (+ = protruding)
    ref_normals: np.ndarray          # (V, 3) normals of that smoothed surface
    local: np.ndarray                # (V,) offset minus its neighbourhood average (recess < 0 < rim / bump)


@dataclass
class ColorInfo:
    available: bool = False
    reason: str = ""
    colors: np.ndarray | None = None     # (V, 3) sRGB 0..1 (NaN where not sampled)
    skin_rgb: np.ndarray | None = None
    hair_rgb: np.ndarray | None = None
    skin: np.ndarray | None = None       # (V,) bool
    eye_like: np.ndarray | None = None   # (V,) bool
    hair_like: np.ndarray | None = None  # (V,) bool
    image: str | None = None


@dataclass
class EyeResult:
    found: bool = False
    reason: str = ""
    mask: np.ndarray | None = None       # (V,) bool, both eyes
    eyes: list[dict[str, Any]] = field(default_factory=list)
    method: str = ""
    candidates: list[dict[str, Any]] = field(default_factory=list)


# ================================================================================================================
# step
# ================================================================================================================


def run(ctx: TunerContext) -> StepOutcome:
    """Symmetry repair + face corrections on the primary mesh (see module doc)."""
    opts = ctx.options
    want_sym = opts.symmetry != "off"
    want_face = bool(opts.refine_face)
    if not want_sym and not want_face:
        return StepOutcome("skipped", "Face refinement and symmetry repair are disabled", {})
    obj = ctx.require_primary()
    me = obj.data
    if len(me.polygons) == 0:
        return StepOutcome("skipped", "The mesh has no faces", {})
    t_step = time.perf_counter()
    timings: dict[str, float] = {}
    details: dict[str, Any] = {}
    messages: list[str] = []
    warn: list[str] = []

    t = time.perf_counter()
    ctx.progress(0.02, "Reading mesh")
    arrays = MeshArrays(me)
    timings["read_topology"] = time.perf_counter() - t

    t = time.perf_counter()
    ctx.progress(0.08, "Measuring mirror symmetry")
    score, bvh = symmetry.measure(arrays.co, arrays.tris, arrays.diag)
    ctx.store["symmetry"] = score.to_dict()
    timings["symmetry_measure"] = time.perf_counter() - t

    t = time.perf_counter()
    bmin, bmax = arrays.co.min(0), arrays.co.max(0)
    head = detect_head(arrays.co, bmin, bmax, center_x=score.plane.offset,
                       edge=float(np.median(arrays.edge_len())))
    humanoid, why = humanoid_gate(bmin, bmax, score, head)
    timings["head"] = time.perf_counter() - t
    details["humanoid"] = humanoid
    details["humanoid_reason"] = why
    details["head"] = head.to_dict()
    details["symmetry"] = {"score": score.to_dict()}

    changed = False
    symmetrized = False
    if want_face and humanoid:
        t = time.perf_counter()
        ctx.progress(0.12, "Closing small face holes")
        soft0, _, _ = face_soft_mask(arrays.co, arrays, head)
        cinfo = cleanup_face(me, arrays, soft0 > 0.3, FACE_HOLE_MAX_PERIMETER * head.height)
        details["face_cleanup"] = {k: v for k, v in cinfo.items() if k != "changed"}
        if cinfo["changed"]:
            changed = True
            arrays = MeshArrays(me)
            bvh = symmetry.surface_bvh(arrays.co, arrays.tris)
            if cinfo["holes_filled"] or cinfo["islands_removed"]:
                messages.append(f"closed {cinfo['holes_filled']} small face hole(s), removed "
                                f"{cinfo['islands_removed']} face speck(s)")
        timings["face_cleanup"] = time.perf_counter() - t
    co = arrays.co.copy()
    # ---------------------------------------------------------------- symmetry
    action, reason = symmetry.decide(opts.symmetry, score, humanoid)
    details["symmetry"]["action"] = action
    details["symmetry"]["reason"] = reason
    filled = _filled_mask(me)
    if action == "repair":
        t = time.perf_counter()
        ctx.progress(0.15, "Repairing asymmetric defects")
        tree = symmetry.build_kdtree(co)
        co, sinfo = symmetry.local_repair(co, arrays.tris, arrays.topo, score.plane, tree, bvh, arrays.diag,
                                          filled=filled, edge_len=arrays.edge_len(),
                                          progress=lambda f, m: ctx.progress(0.15 + 0.2 * f, m))
        del tree
        timings["symmetry_repair"] = time.perf_counter() - t
        details["symmetry"]["repair"] = sinfo
        if sinfo.get("moved_vertices"):
            changed = True
            messages.append(f"symmetry: repaired {sinfo['repaired_clusters']} local defect(s) from the mirror side")
        else:
            messages.append("symmetry: no local defects to repair")
    elif action == "symmetrize":
        t = time.perf_counter()
        ctx.progress(0.15, "Symmetrizing")
        sinfo = _run_symmetrize(me, arrays, score, filled, obj)
        timings["symmetrize"] = time.perf_counter() - t
        details["symmetry"]["symmetrize"] = sinfo
        if sinfo.get("applied"):
            symmetrized = True
            messages.append(f"symmetrized from the {sinfo['source_side']} side ({sinfo['note']})")
            # connectivity changed: re-read everything
            arrays = MeshArrays(me)
            co = arrays.co.copy()
            bmin, bmax = co.min(0), co.max(0)
            head = detect_head(co, bmin, bmax, center_x=score.plane.offset,
                               edge=float(np.median(arrays.edge_len())))
            details["head"] = head.to_dict()
        else:
            warn.append(f"symmetrize skipped: {sinfo.get('reason')}")
    else:
        messages.append(f"symmetry: {reason}")
    del bvh

    # ---------------------------------------------------------------- face
    face_soft = None
    if want_face and humanoid:
        t = time.perf_counter()
        ctx.progress(0.4, "Finding the face")
        region = build_face_region(co, arrays, head)
        face_soft = region.soft
        timings["face_region"] = time.perf_counter() - t
        details["face_region_vertices"] = int(region.mask.sum())
        t = time.perf_counter()
        ctx.progress(0.48, "Sampling face colours")
        colors = analyze_colors(obj, me, co, region, head, arrays.topo)
        timings["colors"] = time.perf_counter() - t
        details["colors"] = _color_details(colors)
        eyes = EyeResult(reason="eye correction disabled")
        if opts.eye_correction:
            t = time.perf_counter()
            ctx.progress(0.58, "Detecting eyes")
            eyes = detect_eyes(co, arrays, region, head, colors, score.plane)
            timings["eye_detection"] = time.perf_counter() - t
            details["eyes"] = {"found": eyes.found, "reason": eyes.reason, "method": eyes.method,
                               "eyes": eyes.eyes}
            if eyes.found:
                t = time.perf_counter()
                ctx.progress(0.66, "Correcting eyes")
                co, einfo = correct_eyes(co, arrays, region, eyes)
                timings["eye_correction"] = time.perf_counter() - t
                details["eyes"]["correction"] = einfo
                changed = True
                messages.append(f"eyes corrected ({einfo['vertices']:,} vertices, groove roughness "
                                f"{einfo['roughness_before']:.3f} -> {einfo['roughness_after']:.3f})")
            else:
                messages.append(f"eyes not corrected ({eyes.reason})")
        else:
            details["eyes"] = {"found": False, "reason": "eye correction disabled"}
        t = time.perf_counter()
        ctx.progress(0.78, "Softening the midline crease")
        co, cinfo = soften_midline_crease(co, arrays, region, head, colors, eyes, score.plane)
        timings["crease"] = time.perf_counter() - t
        details["crease"] = cinfo
        if cinfo.get("vertices"):
            changed = True
            messages.append(f"midline crease softened (ridge {cinfo['ridge_before']:.3f} -> "
                            f"{cinfo['ridge_after']:.3f})")
        t = time.perf_counter()
        ctx.progress(0.88, "Light facial denoise")
        co, dinfo = facial_denoise(co, arrays, region, colors, eyes)
        timings["face_denoise"] = time.perf_counter() - t
        details["face_denoise"] = dinfo
        if dinfo.get("vertices"):
            changed = True
    elif want_face:
        messages.append(f"face refinement skipped: {why}")
        details["face_skipped"] = why

    # ---------------------------------------------------------------- write back
    if changed:
        disp = np.linalg.norm(co - arrays.co, axis=1)
        details["displacement"] = {"moved_vertices": int((disp > 0).sum()),
                                   "mean_pct": _pct(float(disp[disp > 0].mean()) if (disp > 0).any() else 0.0,
                                                    arrays.diag),
                                   "max_pct": _pct(float(disp.max()), arrays.diag)}
        apply_positions(me, co)
    if face_soft is not None:
        meshdata.set_attribute(me, FACE_ATTRIBUTE, face_soft.astype(np.float32), domain="POINT",
                               data_type="FLOAT")
    timings["total"] = time.perf_counter() - t_step
    details["timings_s"] = {k: round(v, 3) for k, v in timings.items()}
    ctx.progress(1.0, "Face refinement finished")
    for w in warn:
        ctx.warn(f"Face refinement: {w}")
    message = "; ".join(messages) if messages else "nothing to do"
    message = message[:1].upper() + message[1:]
    if not changed and not symmetrized and face_soft is None:
        return StepOutcome("skipped", message, details)
    return StepOutcome("warning" if warn else "done", message, details)


def _pct(value: float, diag: float) -> float:
    return round(100.0 * float(value) / diag, 5) if diag > 0 else 0.0


def _filled_mask(me: bpy.types.Mesh) -> np.ndarray | None:
    values = meshdata.get_attribute(me, "amt_filled")
    if values is None or values.ndim != 1 or len(values) != len(me.vertices):
        return None
    return values > 0.5


def _run_symmetrize(me: bpy.types.Mesh, arrays: MeshArrays, score: symmetry.SymmetryScore,
                    filled: np.ndarray | None, obj: bpy.types.Object) -> dict[str, Any]:
    if me.shape_keys is not None:
        return {"applied": False, "reason": "the mesh has shape keys"}
    normals = smoothing.vertex_normals(arrays.co, arrays.tris)
    sides = symmetry.side_defect_scores(arrays.co, arrays.topo, normals, arrays.edge_len(), score.plane, filled)
    source = "positive" if sides["positive"] <= sides["negative"] else "negative"
    weld = 0.5 * float(np.median(arrays.edge_len()))
    info = symmetry.symmetrize(obj, score.plane, source, weld)
    info["applied"] = True
    info["side_scores"] = {k: round(v, 4) for k, v in sides.items()}
    info["note"] = ("character's left (+X) kept" if source == "positive" else "character's right (-X) kept")
    if score.mean_pct >= symmetry.AUTO_MAX_ERROR_PCT:
        info["note"] += f"; model was asymmetric ({score.mean_pct:.2f}%)"
    return info


# ================================================================================================================
# gates: head + humanoid
# ================================================================================================================


def _central_run(xs: np.ndarray, center: float, binw: float) -> float:
    """Width of the contiguous occupied run of ``xs`` (bins of ``binw``) containing (or nearest to) ``center``."""
    if len(xs) == 0:
        return 0.0
    lo = float(min(xs.min(), center))
    idx = np.floor((xs - lo) / binw).astype(np.int64)
    occ = np.zeros(int(idx.max()) + 2, bool)
    occ[idx] = True
    c = int(np.floor((center - lo) / binw))
    if c >= len(occ) or not occ[c]:
        filled = np.nonzero(occ)[0]
        c = int(filled[np.argmin(np.abs(filled - c))])
    empty = np.nonzero(~occ)[0]
    left = empty[empty < c]
    right = empty[empty > c]
    a = int(left.max()) + 1 if len(left) else 0
    b = int(right.min()) - 1 if len(right) else len(occ) - 1
    return (b - a + 1) * binw


def width_profile(co: np.ndarray, bmin: np.ndarray, bmax: np.ndarray, center_x: float,
                  bins: int = PROFILE_BINS, edge: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    """Front-half central-run width per z band from the top: ``(widths (bins,), band centre z (bins,))``."""
    height = float(bmax[2] - bmin[2])
    edges = bmax[2] - np.arange(bins + 1) * height / bins
    order = np.argsort(-co[:, 2], kind="stable")
    zs = -co[order, 2]
    cut = np.searchsorted(zs, -edges, side="left")
    widths = np.zeros(bins)
    binw = max(0.0025 * height, 1.5 * float(edge))  # occupancy bins must not resolve the vertex spacing
    for k in range(bins):
        sl = order[cut[k]:cut[k + 1]] if k < bins - 1 else order[cut[k]:]
        if len(sl) < 5:
            continue
        pts = co[sl]
        front = pts[pts[:, 1] < np.median(pts[:, 1])]
        widths[k] = _central_run(front[:, 0], center_x, binw)
    smooth = widths.copy()
    smooth[1:-1] = np.median(np.stack([widths[:-2], widths[1:-1], widths[2:]]), axis=0)
    return smooth, 0.5 * (edges[:-1] + edges[1:])


def detect_head(co: np.ndarray, bmin: np.ndarray, bmax: np.ndarray, center_x: float = 0.0,
                edge: float = 0.0) -> HeadInfo:
    """Head = top block above the neck (see module doc). ``center_x`` = symmetry plane offset."""
    height = float(bmax[2] - bmin[2])
    if height <= 0 or len(co) < 100:
        return HeadInfo(reason="empty mesh")
    widths, zc = width_profile(co, bmin, bmax, center_x, edge=edge)
    k0 = max(2, int(NECK_SEARCH[0] * PROFILE_BINS))
    k1 = min(PROFILE_BINS - 3, int(NECK_SEARCH[1] * PROFILE_BINS))
    best = None
    for k in range(k0, k1 + 1):  # topmost valid neck (lower minima are waists / arm gaps of a T-pose)
        head_w = float(widths[:k].max())
        w = float(widths[k])
        if head_w <= 0 or w <= 0 or head_w < 0.04 * height:
            continue
        ratio = w / head_w
        below = float(widths[k + 1:min(PROFILE_BINS, k + 21)].max()) if k + 1 < PROFILE_BINS else 0.0
        if ratio < NECK_MAX_RATIO and below > SHOULDER_MIN_RATIO * w:
            window = widths[k:min(k + 6, PROFILE_BINS)]
            k = k + int(np.argmin(window))
            best = (k, float(widths[k]) / head_w, head_w)
            break
    top = float(bmax[2])
    if best is not None:
        k, ratio, head_w = best
        z_neck = float(zc[k])
        info = HeadInfo(found=True, reason="neck found", z_top=top, z_neck=z_neck, center_x=center_x,
                        half_width=0.5 * head_w, neck_ratio=ratio)
    else:
        # head-only input: no neck; accept a compact, roughly round profile (width ~ height)
        width = float(widths.max())
        if 0.55 * height <= width <= 1.4 * height and widths[-5:].max() > 0.25 * width:
            info = HeadInfo(found=True, reason="head-only model (no neck)", z_top=top, z_neck=float(bmin[2]),
                            center_x=center_x, half_width=0.5 * width, head_only=True)
        else:
            return HeadInfo(reason="no neck / head shape found")
    in_head = (co[:, 2] >= info.z_neck) & (np.abs(co[:, 0] - center_x) <= 1.2 * info.half_width)
    if in_head.sum() < 200:
        return HeadInfo(reason="too few vertices in the head block")
    info.center_y = float(np.median(co[in_head, 1]))
    info.z_chin = estimate_chin(co, info)
    return info


def estimate_chin(co: np.ndarray, head: HeadInfo) -> float:
    """Lowest z of the chin: walking down the front-most midline profile, the chin ends where the surface jumps
    back to the neck (the neck band found by the width profile often cuts through the pointed anime chin)."""
    h = head.height
    if head.head_only or h <= 0:
        return head.z_neck
    sel = (np.abs(co[:, 0] - head.center_x) < 0.15 * head.half_width) & (co[:, 1] < head.center_y) \
        & (co[:, 2] > head.z_neck - 0.4 * h) & (co[:, 2] < head.z_neck + 0.5 * h)
    if sel.sum() < 20:
        return head.z_neck
    pts = co[sel]
    step = 0.02 * h
    top = head.z_neck + 0.5 * h
    k = np.floor((top - pts[:, 2]) / step).astype(np.int64)
    nb = int(k.max()) + 1
    ymin = np.full(nb, np.inf)
    np.minimum.at(ymin, k, pts[:, 1])
    start = int(round(0.3 * h / step))  # ~ nose / mouth level
    prev = None
    for b in range(start, nb):
        if not np.isfinite(ymin[b]):
            continue
        if prev is not None and ymin[b] - ymin[prev] > 0.12 * h:
            return float(top - (prev + 1) * step)
        prev = b
    return head.z_neck


def humanoid_gate(bmin: np.ndarray, bmax: np.ndarray, score: symmetry.SymmetryScore,
                  head: HeadInfo) -> tuple[bool, str]:
    """``(is_humanoid, reason)`` from the bbox, the mirror error and the head detection."""
    dims = bmax - bmin
    if dims[2] < dims[1] or dims[2] < 0.55 * dims[0]:
        return False, f"not upright (bbox {dims[0]:.2f} x {dims[1]:.2f} x {dims[2]:.2f})"
    if score.mean_pct >= symmetry.HUMANOID_MAX_ERROR_PCT:
        return False, f"not mirror-symmetric (mean error {score.mean_pct:.2f}% of the size)"
    if not head.found:
        return False, f"no head found ({head.reason})"
    return True, f"upright symmetric character ({head.reason})"


# ================================================================================================================
# face region
# ================================================================================================================


def face_soft_mask(co: np.ndarray, arrays: MeshArrays, head: HeadInfo
                   ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """``(soft (V,) 0..1, head_block (V,) bool, smoothed vertex normals (V, 3))`` of the front side of the head."""
    topo = arrays.topo
    h = head.height
    vn = smoothing.vertex_normals(co, arrays.tris)
    vn = smoothing.neighbor_mean(vn, topo.adj, topo.adj_rows)
    vn /= np.maximum(np.linalg.norm(vn, axis=1), 1e-12)[:, None]
    dx = np.abs(co[:, 0] - head.center_x)
    z_low = min(head.z_neck, head.z_chin if head.z_chin is not None else head.z_neck)
    head_block = (co[:, 2] >= z_low - 0.08 * h) & (co[:, 2] <= head.z_top + 1e-9) \
        & (dx <= 1.25 * head.half_width)
    facing = smoothing.smoothstep(FACE_FRONT_DOT - 0.15, FACE_FRONT_DOT + 0.05, vn @ FRONT)
    ahead = smoothing.smoothstep(head.center_y + 0.02 * h, head.center_y - 0.05 * h, co[:, 1])
    above = smoothing.smoothstep(z_low - 0.06 * h, z_low, co[:, 2])
    side = smoothing.smoothstep(1.25 * head.half_width, 1.0 * head.half_width, dx)
    soft = facing * ahead * above * side * head_block
    soft = np.clip(smoothing.smooth_scalar(soft, topo, iterations=1, lam=0.5), 0.0, 1.0)
    soft[~head_block] = 0.0
    return soft, head_block, vn


def build_face_region(co: np.ndarray, arrays: MeshArrays, head: HeadInfo) -> FaceRegion:
    """Front-facing side of the head (soft mask), smoothed normals and the offset from a smooth head surface."""
    topo = arrays.topo
    soft, head_block, vn = face_soft_mask(co, arrays, head)
    # heavily smoothed head surface: Laplacian low-pass on the head block (shrinks slowly; used for local offsets)
    active = smoothing.dilate(head_block, topo, 2)
    ref = smoothing.laplacian_smooth(co, topo, iterations=60, lam=0.6, active=active, locked=topo.locked)
    rn = smoothing.vertex_normals(ref, arrays.tris)
    offset = np.einsum("ij,ij->i", co - ref, rn)
    offset[~active] = 0.0
    # the Laplacian reference shrinks a little on the convex head: compare with the neighbourhood average instead
    base = smoothing.smooth_scalar(offset, topo, iterations=25, lam=0.6)
    local = np.where(head_block, offset - base, 0.0)
    return FaceRegion(soft=soft, mask=soft > 0.5, head=head_block, normals=vn, offset=offset, ref_normals=rn,
                      local=local)


def cleanup_face(me: bpy.types.Mesh, arrays: MeshArrays, region: np.ndarray, max_perimeter: float
                 ) -> dict[str, Any]:
    """Close small holes and drop tiny floating islands that lie completely inside the face ``region``.

    TRELLIS eye slits / pits become open holes once the inner shell is removed; their rims would otherwise stay
    fixed while the eyes are faired. Simple boundary loops of 3..``FACE_HOLE_MAX_EDGES`` edges are closed with
    ``bmesh.ops.edgeloop_fill`` (winding follows the neighbours; the corner attributes - UVs - are copied from the
    adjacent face corners) and triangulated; islands up to ``FACE_ISLAND_MAX_VERTS`` vertices are deleted.
    Returns counts; ``changed`` tells the caller to re-read the mesh.
    """
    import bmesh

    t0 = time.perf_counter()
    topo = arrays.topo
    info: dict[str, Any] = {"holes_filled": 0, "hole_edges": 0, "islands_removed": 0, "island_vertices": 0,
                            "changed": False}
    zone = smoothing.dilate(region, topo, 2)
    # tiny islands entirely inside the zone: components of the zone subgraph without an edge leaving the zone
    labels, count = smoothing.mask_components(zone, topo)
    leaves = np.bincount(topo.adj_rows, weights=(~zone)[topo.adj_indices].astype(np.float64),
                         minlength=topo.n_verts) > 0
    small = np.zeros(max(count, 1), bool)
    if count:
        sizes = np.bincount(labels[zone], minlength=count)
        touching = np.bincount(labels[zone], weights=leaves[zone].astype(np.float64), minlength=count) > 0
        small = (sizes <= FACE_ISLAND_MAX_VERTS) & ~touching
    in_small = zone & small[np.maximum(labels, 0)]
    drop_verts = np.nonzero(in_small)[0]
    # simple boundary loops entirely inside the zone, up to FACE_HOLE_MAX_EDGES edges / a head-sized perimeter
    border = topo.edge_face_count == 1
    loops = meshdata.boundary_loops(topo.edges, border, arrays.co)
    loop_edges: list[np.ndarray] = []
    if loops.count:
        e = topo.edges[loops.edge_ids]
        inside = zone[e[:, 0]] & zone[e[:, 1]] & ~in_small[e[:, 0]]
        n_in = np.bincount(loops.loop_of_edge, weights=inside.astype(np.float64), minlength=loops.count)
        ok = (n_in >= loops.edge_count) & (loops.edge_count >= 3) & (loops.edge_count <= FACE_HOLE_MAX_EDGES) \
            & (loops.perimeter <= max_perimeter)
        order = np.argsort(loops.loop_of_edge, kind="stable")
        starts = np.zeros(loops.count + 1, np.int64)
        np.cumsum(loops.edge_count, out=starts[1:])
        grouped = loops.edge_ids[order]
        for k in np.nonzero(ok)[0].tolist():
            ids = grouped[starts[k]:starts[k + 1]]
            if meshdata.order_loop_vertices(topo.edges[ids]) is not None:  # simple cycles only
                loop_edges.append(ids)
    info["select_s"] = round(time.perf_counter() - t0, 3)
    if len(drop_verts) == 0 and not loop_edges:
        return info
    from .topology import _blender_edge_ids

    t0 = time.perf_counter()

    bm = bmesh.new()
    filled_faces = 0
    try:
        bm.from_mesh(me)
        bm.edges.ensure_lookup_table()
        bm.verts.ensure_lookup_table()
        uv_layers = list(bm.loops.layers.uv.values())
        new_faces = []
        for ids in loop_edges:
            bids = ids if arrays.all_tris else _blender_edge_ids(me, topo.edges[ids])
            if np.any(bids < 0):
                continue
            try:
                res = bmesh.ops.edgeloop_fill(bm, edges=[bm.edges[i] for i in bids.tolist()])
            except (ValueError, RuntimeError):
                continue
            for f in res["faces"]:
                _copy_corner_data(f, uv_layers)
                new_faces.append(f)
            info["holes_filled"] += 1 if res["faces"] else 0
            info["hole_edges"] += len(ids) if res["faces"] else 0
        ngons = [f for f in new_faces if f.is_valid and len(f.verts) > 3]
        if ngons:
            bmesh.ops.triangulate(bm, faces=ngons, quad_method="BEAUTY", ngon_method="BEAUTY")
        filled_faces = len(new_faces)
        if len(drop_verts):
            bmesh.ops.delete(bm, geom=[bm.verts[i] for i in drop_verts.tolist()], context="VERTS")
            info["islands_removed"] = int(small.sum())
            info["island_vertices"] = int(len(drop_verts))
        bm.to_mesh(me)
    finally:
        bm.free()
    me.update()
    info["fill_faces"] = filled_faces
    info["bmesh_s"] = round(time.perf_counter() - t0, 3)
    info["changed"] = True
    return info


def _copy_corner_data(face, uv_layers: list) -> None:
    """Give a freshly created fill face the attributes of its neighbourhood.

    Corner layers (colours ...) are copied from an adjacent corner at the same vertex; UVs are then re-derived by
    extrapolating the affine 3D -> UV map of ONE adjacent face (the largest), so a patch closing a crack between two
    UV islands stays inside one island instead of stretching across the texture atlas.
    """
    for loop in face.loops:
        for other in loop.vert.link_loops:
            if other.face is not face:
                loop.copy_from(other)
                break
    neighbours = [f for e in face.edges for f in e.link_faces if f is not face]
    if not neighbours:
        return
    ref = max(neighbours, key=lambda f: f.calc_area())
    face.material_index = ref.material_index
    corners = list(ref.loops)[:3]
    p0, p1, p2 = (np.array(c.vert.co[:], dtype=np.float64) for c in corners)
    m = np.stack([p1 - p0, p2 - p0], axis=1)  # (3, 2)
    gram = m.T @ m
    if abs(np.linalg.det(gram)) < 1e-20:
        return
    inv = np.linalg.inv(gram) @ m.T  # least-squares barycentric solve (2, 3)
    for layer in uv_layers:
        uv0, uv1, uv2 = (np.array(c[layer].uv[:], dtype=np.float64) for c in corners)
        for loop in face.loops:
            st = inv @ (np.array(loop.vert.co[:], dtype=np.float64) - p0)
            loop[layer].uv = (uv0 + st[0] * (uv1 - uv0) + st[1] * (uv2 - uv0)).tolist()


# ================================================================================================================
# colours (base colour texture through the UVs)
# ================================================================================================================


def sample_vertex_colors(obj: bpy.types.Object, me: bpy.types.Mesh, mask: np.ndarray) -> tuple[np.ndarray | None,
                                                                                                  str]:
    """Mean base-colour (sRGB 0..1) of the corners of every ``mask`` vertex: ``(colors (V, 3) NaN elsewhere,
    image name or reason)``."""
    uv = meshdata.get_uvs(me)
    if uv is None:
        return None, "no UV map"
    images = meshdata.object_base_color_images(obj)
    if not any(img is not None for img in images):
        return None, "no base colour texture"
    lv = meshdata.get_loop_vertices(me)
    sel = mask[lv]
    if not sel.any():
        return None, "no face corners"
    _, total = meshdata.get_polygon_loops(me)
    mat = meshdata.get_material_indices(me)[meshdata.loop_faces(total)]
    n = len(me.vertices)
    acc = np.zeros((n, 3), np.float64)
    cnt = np.zeros(n, np.float64)
    used = None
    for slot in np.unique(mat[sel]).tolist():
        image = images[slot] if 0 <= slot < len(images) else None
        if image is None:
            continue
        loops = np.nonzero(sel & (mat == slot))[0]
        try:
            pixels = meshdata.image_pixels(image)
        except (ValueError, RuntimeError) as exc:
            log.debug("cannot read %s: %s", image.name, exc)
            continue
        rgb = meshdata.sample_image(pixels, uv[loops])[:, :3].astype(np.float64)
        del pixels
        verts = lv[loops]
        for k in range(3):
            acc[:, k] += np.bincount(verts, weights=rgb[:, k], minlength=n)
        cnt += np.bincount(verts, minlength=n)
        used = image.name
    if used is None:
        return None, "texture unreadable"
    out = np.full((n, 3), np.nan)
    ok = cnt > 0
    out[ok] = acc[ok] / cnt[ok, None]
    return out, used


def _luma(rgb: np.ndarray) -> np.ndarray:
    return rgb @ np.array([0.2126, 0.7152, 0.0722])


def srgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """sRGB (N, 3) in 0..1 -> CIE L*a*b* (D65) (N, 3)."""
    rgb = np.clip(np.asarray(rgb, dtype=np.float64), 0.0, 1.0)
    lin = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    m = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]])
    xyz = (lin @ m.T) / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16.0 / 116.0)
    return np.stack([116.0 * f[:, 1] - 16.0, 500.0 * (f[:, 0] - f[:, 1]), 200.0 * (f[:, 1] - f[:, 2])], axis=1)


def _mode_color(rgb: np.ndarray, levels: int = 10) -> np.ndarray:
    """Robust dominant colour: most populated RGB histogram cell, averaged over its members."""
    q = np.clip((rgb * levels).astype(np.int64), 0, levels - 1)
    key = (q[:, 0] * levels + q[:, 1]) * levels + q[:, 2]
    best = np.bincount(key).argmax()
    return rgb[key == best].mean(axis=0)


SKIN_AB_TOL = 10.0          # CIE a*b* distance from the skin mode
SKIN_L_RANGE = (-35.0, 15.0)
HAIR_CELL = 8.0             # Lab histogram cell size of the hair palette
HAIR_CELL_MIN_FRAC = 0.02   # a palette cell holds >= 2 % of the upper-head non-skin samples


def analyze_colors(obj: bpy.types.Object, me: bpy.types.Mesh, co: np.ndarray, region: FaceRegion,
                   head: HeadInfo, topo: smoothing.MeshTopology | None = None) -> ColorInfo:
    """Skin colour (mode of the lower-central face), skin mask and eye-like (non-skin, non-hair) mask.

    Skin = CIE a*b* close to the skin mode with a wide L* tolerance (baked shading), connected to the lower-central
    seed; hair = dominant non-skin colour of the upper head; eye-like = neither skin nor hair-like.
    """
    colors, name = sample_vertex_colors(obj, me, region.mask)
    if colors is None:
        return ColorInfo(available=False, reason=name)
    h = head.height
    dx = np.abs(co[:, 0] - head.center_x)
    rel_z = (co[:, 2] - head.z_neck) / max(h, 1e-9)
    valid = region.mask & ~np.isnan(colors[:, 0])
    ref = valid & (dx < 0.35 * head.half_width) & (rel_z > 0.10) & (rel_z < 0.42)
    if ref.sum() < 30:
        ref = valid & (dx < 0.5 * head.half_width) & (rel_z < 0.55)
    if ref.sum() < 30:
        return ColorInfo(available=False, reason="too few face samples for a skin colour", image=name)
    skin_rgb = _mode_color(colors[ref])
    rgb = np.where(np.isnan(colors), 0.0, colors)
    lab = srgb_to_lab(rgb)
    skin_lab = srgb_to_lab(skin_rgb[None])[0]
    dab = np.linalg.norm(lab[:, 1:] - skin_lab[1:], axis=1)
    dl = lab[:, 0] - skin_lab[0]
    skin = valid & (dab < SKIN_AB_TOL) & (dl > SKIN_L_RANGE[0]) & (dl < SKIN_L_RANGE[1])
    if topo is not None and skin.any():  # keep the skin connected to the seed (drops skin-coloured hair strands)
        labels, count = smoothing.mask_components(skin, topo)
        seeds = np.unique(labels[skin & ref])
        keep = np.zeros(count, bool)
        keep[seeds[seeds >= 0]] = True
        skin &= keep[np.maximum(labels, 0)]
    # hair palette: populated Lab cells of the non-skin colours on the upper head (hair has several shades)
    top = valid & ~skin & (rel_z > 0.7)
    hair_rgb = _mode_color(colors[top]) if top.sum() >= 30 else None
    non_skin = valid & ~skin & ((dab > 1.4 * SKIN_AB_TOL) | (dl < SKIN_L_RANGE[0]) | (dl > SKIN_L_RANGE[1]))
    hair_like = np.zeros(len(co), bool)
    if top.sum() >= 30:
        cells = np.floor(lab / HAIR_CELL).astype(np.int64)
        top_cells = cells[top]
        uniq, counts = np.unique(top_cells, axis=0, return_counts=True)
        palette = uniq[counts >= max(10, int(HAIR_CELL_MIN_FRAC * top.sum()))]
        if len(palette):
            cand_ids = np.nonzero(non_skin)[0]
            c = cells[cand_ids]
            hit = np.zeros(len(cand_ids), bool)
            for cell in palette:  # small palette (tens of cells)
                hit |= np.all(np.abs(c - cell) <= 1, axis=1)
            hair_like[cand_ids[hit]] = True
    eye_like = non_skin & ~hair_like
    return ColorInfo(available=True, colors=colors, skin_rgb=skin_rgb, hair_rgb=hair_rgb, skin=skin,
                     eye_like=eye_like, hair_like=hair_like, image=name)


def _hex(rgb: np.ndarray | None) -> str | None:
    if rgb is None:
        return None
    c = np.clip(np.round(np.asarray(rgb) * 255), 0, 255).astype(int)
    return "#{:02x}{:02x}{:02x}".format(*c.tolist())


def _color_details(colors: ColorInfo) -> dict[str, Any]:
    if not colors.available:
        return {"available": False, "reason": colors.reason}
    return {"available": True, "image": colors.image, "skin": _hex(colors.skin_rgb), "hair": _hex(colors.hair_rgb),
            "skin_vertices": int(colors.skin.sum()), "eye_like_vertices": int(colors.eye_like.sum()),
            "hair_like_vertices": int(colors.hair_like.sum()) if colors.hair_like is not None else 0}


# ================================================================================================================
# eyes
# ================================================================================================================


def detect_eyes(co: np.ndarray, arrays: MeshArrays, region: FaceRegion, head: HeadInfo, colors: ColorInfo,
                plane: symmetry.MirrorPlane) -> EyeResult:
    """Mirror-paired eye regions in the upper-middle face band (see module doc)."""
    topo = arrays.topo
    h = head.height
    hw = head.half_width
    edge = float(np.median(arrays.edge_len()))
    side = plane.signed_distance(co)
    rel_z = (co[:, 2] - head.z_neck) / max(h, 1e-9)
    band = region.mask & (rel_z > EYE_BAND[0]) & (rel_z < EYE_BAND[1]) \
        & (np.abs(side) > 0.08 * hw) & (np.abs(side) < 0.95 * hw)
    if band.sum() < 50:
        return EyeResult(reason="face band too small")
    recess = region.local
    not_protruding = recess < 0.6 * edge
    if colors.available:
        cand = band & colors.eye_like & not_protruding
        method = "texture+geometry"
    else:
        cand = band & (recess < -0.5 * edge)
        method = "geometry"
    cand = smoothing.erode(smoothing.dilate(cand, topo, 2), topo, 2) & band  # close small gaps
    labels, count = smoothing.mask_components(cand, topo)
    if count == 0:
        return EyeResult(reason="no eye-like region in the face band", method=method)
    comps = []
    sizes = np.bincount(labels[cand], minlength=count)
    skin = colors.skin if colors.available else region.mask
    facing = region.normals @ FRONT
    for c in np.nonzero(sizes >= 12)[0].tolist():
        ids = np.nonzero(labels == c)[0]
        comp_mask = np.zeros(len(co), bool)
        comp_mask[ids] = True
        ring = smoothing.dilate(comp_mask, topo, 3) & ~comp_mask
        pts = co[ids]
        sd = side[ids]
        zc = float(np.median(pts[:, 2]))
        below = ring & (co[:, 2] < zc)
        comps.append({"label": c, "ids": ids, "n": len(ids), "side": float(np.median(sd)), "z": zc,
                      "width": float(np.percentile(sd, 95) - np.percentile(sd, 5)),
                      "height": float(np.percentile(pts[:, 2], 95) - np.percentile(pts[:, 2], 5)),
                      "recess": float(np.median(recess[ids])),
                      "surround_skin": float(skin[ring].mean()) if ring.any() else 0.0,
                      "below_skin": float(skin[below].mean()) if below.any() else 0.0,
                      "facing": float(np.median(facing[ids]))})
    pos = [c for c in comps if c["side"] > 0]
    neg = [c for c in comps if c["side"] < 0]
    best = None
    for a in pos:
        for b in neg:
            dside = abs(a["side"] + b["side"])
            dz = abs(a["z"] - b["z"])
            if dside > 0.25 * hw or dz > 0.12 * h:
                continue
            size_sim = min(a["n"], b["n"]) / max(a["n"], b["n"])
            if size_sim < 0.3:
                continue
            width = 0.5 * (a["width"] + b["width"])
            if width < 0.05 * hw or width > 0.9 * hw:
                continue
            surround = 0.25 * (a["surround_skin"] + b["surround_skin"] + a["below_skin"] + b["below_skin"])
            facing_ab = 0.5 * (a["facing"] + b["facing"])
            centrality = 0.5 * (a["side"] - b["side"]) / hw          # eyes sit at ~0.3-0.6 of the head half width
            recess_ab = 0.5 * (a["recess"] + b["recess"]) / edge
            quality = (np.sqrt(a["n"] + b["n"]) * size_sim
                       * (0.15 + surround) * max(facing_ab, 0.05)
                       * float(np.exp(-((centrality - 0.45) / 0.3) ** 2))
                       * float(np.exp(-max(recess_ab, 0.0)))
                       / (1.0 + dside / (0.05 * hw) + dz / (0.03 * h)))
            if best is None or quality > best[0]:
                best = (quality, a, b)
    diag_cands = [{k: (round(v, 4) if isinstance(v, float) else v) for k, v in c.items() if k not in ("ids", "label")}
                  for c in sorted(comps, key=lambda c: -c["n"])[:8]]
    if best is None:
        return EyeResult(reason=f"no mirror-paired eye regions ({len(pos)} right / {len(neg)} left candidates)",
                         method=method, candidates=diag_cands)
    _, a, b = best
    mask = np.zeros(len(co), bool)
    mask[a["ids"]] = True
    mask[b["ids"]] = True
    # mirror completion: union with the mirror image of each eye (fills gaps where the texture cue was weak)
    mirrored = _mirror_mask(co, mask, plane, edge, topo)
    mask |= mirrored & band
    mask = _fill_holes(smoothing.erode(smoothing.dilate(mask, topo, 2), topo, 2), topo, region.mask)
    # never flatten hair strands lying over the eyes: drop hair-coloured / protruding vertices (+1 ring)
    over = region.local > 1.0 * edge
    if colors.available and colors.hair_like is not None:
        over |= colors.hair_like
    mask &= ~smoothing.dilate(over & mask, topo, 1)
    eyes = []
    for comp in (a, b):
        sel = mask & (np.sign(side) == np.sign(comp["side"]))
        ids = np.nonzero(sel)[0]
        pts = co[ids]
        eyes.append({"side": "left" if comp["side"] > 0 else "right", "vertices": int(len(ids)),
                     "center": [round(float(v), 5) for v in pts.mean(0)],
                     "width_pct_head": round(100.0 * comp["width"] / (2 * hw), 2),
                     "height_pct_head": round(100.0 * comp["height"] / h, 2),
                     "recess_edges": round(float(np.median(recess[ids])) / edge, 3),
                     "texture_frac": round(float(colors.eye_like[ids].mean()), 3) if colors.available else None,
                     "surround_skin": round(comp["surround_skin"], 3), "below_skin": round(comp["below_skin"], 3),
                     "facing": round(comp["facing"], 3)})
    return EyeResult(found=True, reason=f"mirror-paired eyes (pair score {best[0]:.3g}, {len(comps)} candidates)",
                     mask=mask, eyes=eyes, method=method, candidates=diag_cands)


def _mirror_mask(co: np.ndarray, mask: np.ndarray, plane: symmetry.MirrorPlane, edge: float,
                 topo: smoothing.MeshTopology) -> np.ndarray:
    """Vertices within ~1 edge of the mirror image of ``mask`` (KD-tree on the mirrored mask points)."""
    from mathutils import kdtree

    ids = np.nonzero(mask)[0]
    out = np.zeros(len(co), bool)
    if len(ids) == 0:
        return out
    mpts = plane.reflect(co[ids])
    tree = kdtree.KDTree(len(ids))
    for i, p in enumerate(mpts.tolist()):
        tree.insert(p, i)
    tree.balance()
    lo, hi = mpts.min(0) - 2 * edge, mpts.max(0) + 2 * edge
    near = np.nonzero(np.all((co >= lo) & (co <= hi), axis=1))[0]
    find = tree.find
    for v, p in zip(near.tolist(), co[near].tolist()):
        if find(p)[2] <= 1.2 * edge:
            out[v] = True
    return out


def _fill_holes(mask: np.ndarray, topo: smoothing.MeshTopology, within: np.ndarray) -> np.ndarray:
    """Add enclosed non-mask islands (inside ``within``) that do not touch the region border."""
    inside = within & ~mask
    labels, count = smoothing.mask_components(inside, topo)
    if count == 0:
        return mask
    border = smoothing.dilate(~within, topo, 1) & inside
    touching = np.zeros(count, bool)
    touching[np.unique(labels[border])] = True
    sizes = np.bincount(labels[inside], minlength=count)
    big = sizes > 0.5 * max(int(mask.sum()), 1)
    fill = inside & ~touching[np.maximum(labels, 0)] & ~big[np.maximum(labels, 0)]
    return mask | fill


def correct_eyes(co: np.ndarray, arrays: MeshArrays, region: FaceRegion,
                 eyes: EyeResult) -> tuple[np.ndarray, dict[str, Any]]:
    """Fair the eye interiors (pits, blobs, comb grooves) keeping the shallow recess; soften raised rims."""
    topo = arrays.topo
    mask = eyes.mask & ~topo.locked
    edge_len = arrays.edge_len()
    edge = float(np.median(edge_len))
    normals = region.normals
    work = smoothing.dilate(mask, topo, 3)

    def roughness(x: np.ndarray) -> float:
        hp = smoothing.high_pass(x, topo, iterations=6, active=work, locked=topo.locked)
        r = np.abs(np.einsum("ij,ij->i", hp, normals)) / np.maximum(edge_len, 1e-12)
        return float(r[mask].mean()) if mask.any() else 0.0

    rough_before = roughness(co)
    x = co.copy()
    # 1. interior: thin-plate re-fairing with the eye's boundary ring held fixed. Removes pits, blobs and the
    #    comb / slit grooves; the plate continues the slopes of the fixed ring, so the shallow recess survives.
    movable = ~smoothing.boundary_vertices(topo)
    core = smoothing.erode(eyes.mask, topo, 1) & movable
    if core.any():
        x = smoothing.fair(x, topo, core, order=2, iterations=600)
    # 2. blend the boundary ring (mask minus core + one ring outside) with a short low-pass
    ring_in = smoothing.dilate(eyes.mask, topo, 1) & ~core & ~topo.locked
    x = smoothing.taubin(x, topo, iterations=3, lam=0.5, mu=-0.53, active=ring_in, locked=topo.locked)
    # 3. raised rims: ring around the eye that sticks out of the smooth head surface -> pull back 35 %
    ring = smoothing.dilate(eyes.mask, topo, 3) & ~eyes.mask & ~topo.locked
    raise_ = np.where(ring, np.clip(region.local, 0.0, 1.5 * edge), 0.0)
    fall = smoothing.smoothstep(3.5, 0.5, smoothing.ring_distance(eyes.mask, topo, 4).astype(np.float64))
    x -= (0.35 * raise_ * fall)[:, None] * region.ref_normals
    # 4. smooth falloff of the total displacement outside the eye
    delta = x - co
    band = smoothing.dilate(eyes.mask, topo, 4) & ~eyes.mask & ~topo.locked
    delta = smoothing.taubin(delta, topo, iterations=2, lam=0.5, mu=0.0, active=band)
    delta[topo.locked] = 0.0
    x = co + delta
    disp = np.linalg.norm(delta, axis=1)
    moved = disp > 0
    shift_n = np.einsum("ij,ij->i", delta, region.ref_normals)
    info = {"vertices": int(eyes.mask.sum()), "faired_vertices": int(core.sum()),
            "locked_in_eye": int((eyes.mask & ~movable).sum()),
            "rim_vertices": int((raise_ > 0).sum()),
            "roughness_before": round(rough_before, 4), "roughness_after": round(roughness(x), 4),
            "recess_before_edges": round(float(np.median(region.local[eyes.mask])) / edge, 3),
            "mean_normal_shift_edges": round(float(shift_n[core].mean()) / edge, 3) if core.any() else 0.0,
            "moved_vertices": int(moved.sum()),
            "max_shift_edges": round(float(disp.max()) / edge, 3) if moved.any() else 0.0}
    return x, info


# ================================================================================================================
# midline crease
# ================================================================================================================


def soften_midline_crease(co: np.ndarray, arrays: MeshArrays, region: FaceRegion, head: HeadInfo,
                          colors: ColorInfo, eyes: EyeResult,
                          plane: symmetry.MirrorPlane) -> tuple[np.ndarray, dict[str, Any]]:
    """Round the sharp vertical ridge on the lower face / chin around the symmetry plane (nose protected)."""
    topo = arrays.topo
    h = head.height
    hw = head.half_width
    edge = float(np.median(arrays.edge_len()))
    side = plane.signed_distance(co)
    width = max(3.0 * edge, CREASE_HALF_WIDTH * hw)
    if eyes.found and eyes.mask is not None and eyes.mask.any():
        z_eye_low = float(np.percentile(co[eyes.mask, 2], 5))
    else:
        z_eye_low = head.z_neck + 0.5 * h
    skin = colors.skin if colors.available else region.mask
    z_chin = head.z_chin if head.z_chin is not None else head.z_neck
    band = region.mask & (np.abs(side) < width) & (co[:, 2] < z_eye_low) & (co[:, 2] > z_chin - 0.01 * h)
    if colors.available:
        band &= smoothing.dilate(skin, topo, 1)
    if band.sum() < 10:
        return co, {"vertices": 0, "reason": "no midline band on the lower face"}
    # nose: most forward (smallest y) point of the band in its upper part (between the eyes and the mouth)
    zb = co[band, 2]
    z_lo, z_hi = float(zb.min()), float(zb.max())
    nose_zone = band & (co[:, 2] > z_lo + 0.35 * (z_hi - z_lo))
    protect = np.zeros(len(co))
    nose = None
    if nose_zone.any():
        ids = np.nonzero(nose_zone)[0]
        tip = ids[np.argmin(co[ids, 1])]
        nose = co[tip]
        r = np.linalg.norm(co - nose, axis=1)
        protect = smoothing.smoothstep(NOSE_PROTECT[1] * h, NOSE_PROTECT[0] * h, r)
    movable = ~smoothing.boundary_vertices(topo)
    free = band & (protect < 0.5) & movable
    if free.sum() < 5:
        return co, {"vertices": 0, "reason": "midline band fully protected (nose)"}
    fn0, _, _ = smoothing.face_geometry(co, arrays.tris)
    dih0 = _band_dihedral(fn0, topo, band, side, edge)
    ridge0 = _ridge(co, arrays, band, side, edge)
    # thin-plate re-fairing of the band (flanks fixed): the V cross-section becomes a rounded one
    faired = smoothing.fair(co, topo, free, order=2, iterations=400)
    gain = (1.0 - protect)[:, None]
    x = co + (faired - co) * gain
    fn1, _, _ = smoothing.face_geometry(x, arrays.tris)
    dih1 = _band_dihedral(fn1, topo, band, side, edge)
    ridge1 = _ridge(x, arrays, band, side, edge)
    disp = np.linalg.norm(x - co, axis=1)
    return x, {"vertices": int(free.sum()), "band_width_pct_head": round(100.0 * 2 * width / (2 * hw), 2),
               "ridge_before": round(ridge0, 4), "ridge_after": round(ridge1, 4),
               "z_range": [round(z_lo, 5), round(z_hi, 5)],
               "nose_tip": None if nose is None else [round(float(v), 5) for v in nose],
               "dihedral_before": round(dih0, 3), "dihedral_after": round(dih1, 3),
               "max_shift_edges": round(float(disp.max()) / edge, 3)}


def _ridge(co: np.ndarray, arrays: MeshArrays, band: np.ndarray, side: np.ndarray, edge: float) -> float:
    """Crease sharpness: mean convexity (umbrella along the normal / edge length) of the band vertices on the
    plane (a sharp V ridge ~0.1-0.2, a rounded one ~0.02)."""
    on = band & (np.abs(side) < 1.0 * edge)
    if not on.any():
        return 0.0
    ids = np.nonzero(on)[0]
    rows, nbrs, deg = smoothing._region_rows(arrays.topo.adj, ids)
    mean = np.stack([np.bincount(rows, weights=co[nbrs, k], minlength=len(ids)) for k in range(3)], axis=1)
    mean /= np.maximum(deg, 1)[:, None]
    nrm = smoothing.vertex_normals(co, arrays.tris)[ids]
    return float((-np.einsum("ij,ij->i", mean - co[ids], nrm) / edge).mean())


def _band_dihedral(fn: np.ndarray, topo: smoothing.MeshTopology, band: np.ndarray, side: np.ndarray,
                   edge: float) -> float:
    """Mean dihedral (degrees) of manifold edges inside ``band`` that cross / touch the plane (the crease)."""
    e = topo.edges[topo.pair_edges]
    near = band[e[:, 0]] & band[e[:, 1]] & ((np.abs(side[e[:, 0]]) < 1.5 * edge) | (np.abs(side[e[:, 1]]) < 1.5 * edge))
    if not near.any():
        return 0.0
    return float(smoothing.dihedral_angles(fn, topo)[near].mean())


# ================================================================================================================
# light facial denoise
# ================================================================================================================


def facial_denoise(co: np.ndarray, arrays: MeshArrays, region: FaceRegion, colors: ColorInfo,
                   eyes: EyeResult) -> tuple[np.ndarray, dict[str, Any]]:
    """A few Taubin passes on the face skin (eyes excluded: they were already faired)."""
    topo = arrays.topo
    w = region.soft.copy()
    if colors.available:
        w *= smoothing.smooth_scalar(colors.skin.astype(np.float64), topo, iterations=2, lam=0.5)
    if eyes.found and eyes.mask is not None:
        w[smoothing.dilate(eyes.mask, topo, 1)] = 0.0
    w = np.clip(w, 0.0, 1.0)
    if not np.any(w > 0.05):
        return co, {"vertices": 0}
    active = w > 0.0
    x = smoothing.taubin(co, topo, iterations=4, lam=0.5, mu=-0.53, weights=w, active=active, locked=topo.locked)
    disp = np.linalg.norm(x - co, axis=1)
    edge = float(np.median(arrays.edge_len()))
    return x, {"vertices": int(active.sum()), "iterations": 4,
               "mean_shift_edges": round(float(disp[active].mean()) / edge, 4),
               "max_shift_edges": round(float(disp.max()) / edge, 4)}
