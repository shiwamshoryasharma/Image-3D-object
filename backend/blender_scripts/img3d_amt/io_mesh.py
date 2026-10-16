"""Mesh I/O: import (glb/gltf/obj/fbx/ply/stl/blend), scene normalization + weld, GLB export with fidelity check.

Import rules (DESIGN sections 3-5)
* glTF: ``merge_vertices=False`` + ``import_shading='NORMALS'`` (GLB normals become custom normals), then we weld
  exact duplicates ourselves (``1e-6 x diag``, bmesh ``remove_doubles``) - TRELLIS GLBs are 39-50% UV-seam copies.
* The coordinate frame is kept (no re-centring/scaling). Unrigged inputs with several mesh objects are joined into
  the largest one and object transforms are applied to the mesh data (identity for TRELLIS); rigged inputs keep
  their objects and transforms untouched.
* A hidden copy of the loaded mesh (``ctx.original``) is kept for deviation checks and "before" previews.

Export rules
* GLB, ``export_image_format='AUTO'`` (re-embeds the original WebP/PNG/JPEG bytes), skins when an armature exists,
  ``amt_*`` attributes removed, Subdivision/Multires modifiers never applied (temporarily disabled).
* The importer turns glTF ``doubleSided``/``alphaMode`` into Blender settings; the exported JSON chunk is compared
  with the input's and material ``alphaMode``/``alphaCutoff``/``doubleSided`` are patched back when they differ.
"""

from __future__ import annotations

import json
import logging
import os
import re
import struct
import time
from typing import Any

import bpy
import numpy as np
from mathutils import Matrix

from . import meshdata
from .context import InputError, StepError, StepOutcome, TunerContext

log = logging.getLogger(__name__)

SUPPORTED_FORMATS: dict[str, str] = {
    "glb": "glTF binary", "gltf": "glTF", "obj": "Wavefront OBJ", "fbx": "FBX", "ply": "PLY", "stl": "STL",
    "blend": "Blender",
}
WELD_REL_DIST = 1e-6
"""Weld tolerance relative to the bbox diagonal (exact duplicates only)."""
ORIGINAL_NAME = "AMT_original"
_GLB_MAGIC = b"glTF"
_CHUNK_JSON = 0x4E4F534A
_GLTF_MATERIAL_DEFAULTS = {"alphaMode": "OPAQUE", "alphaCutoff": 0.5, "doubleSided": False}


# ================================================================================================================
# glTF JSON helpers (no bpy)
# ================================================================================================================


def read_gltf_json(path: str) -> dict[str, Any] | None:
    """Parse the JSON of a ``.glb`` (chunk 0) or ``.gltf`` file; ``None`` for other files or on parse errors."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(20)
            if head[:4] == _GLB_MAGIC and len(head) == 20:
                clen, ctype = struct.unpack_from("<II", head, 12)
                if ctype != _CHUNK_JSON:
                    return None
                return json.loads(fh.read(clen).decode("utf-8"))
        if path.lower().endswith(".gltf"):
            with open(path, encoding="utf-8-sig") as fh:
                return json.load(fh)
    except (OSError, ValueError, UnicodeDecodeError, struct.error) as exc:
        log.warning("Could not read glTF JSON of %s: %s", path, exc)
    return None


def write_glb_json(path: str, document: dict[str, Any]) -> None:
    """Replace the JSON chunk of a GLB file in place (atomic: temp file + ``os.replace``)."""
    with open(path, "rb") as fh:
        data = fh.read()
    magic, version, _length = struct.unpack_from("<4sII", data, 0)
    clen, ctype = struct.unpack_from("<II", data, 12)
    if magic != _GLB_MAGIC or ctype != _CHUNK_JSON:
        raise ValueError(f"{path} is not a GLB file")
    rest = data[20 + clen:]
    payload = json.dumps(document, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    payload += b" " * (-len(payload) % 4)
    header = struct.pack("<4sII", magic, version, 12 + 8 + len(payload) + len(rest))
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(header)
        fh.write(struct.pack("<II", len(payload), _CHUNK_JSON))
        fh.write(payload)
        fh.write(rest)
    os.replace(tmp, path)


def _material_value(material: dict[str, Any], key: str) -> Any:
    return material.get(key, _GLTF_MATERIAL_DEFAULTS[key])


def _strip_dup_suffix(name: str) -> str:
    return re.sub(r"\.\d{3}$", "", name or "")


def gltf_material_names(document: dict[str, Any]) -> list[str]:
    """Blender material names the glTF importer gives each glTF material (``name`` or ``Material_<i>``)."""
    return [m.get("name") or f"Material_{i}" for i, m in enumerate(document.get("materials") or [])]


def _match_materials(src: dict[str, Any], out: dict[str, Any]) -> list[tuple[int, int]]:
    """Pairs (out_index, src_index): by (de-suffixed) name, then by index when counts match."""
    src_names = gltf_material_names(src)
    out_mats = out.get("materials") or []
    pairs: list[tuple[int, int]] = []
    used: set[int] = set()
    for oi, mat in enumerate(out_mats):
        name = _strip_dup_suffix(mat.get("name") or "")
        if name in src_names and src_names.index(name) not in used:
            si = src_names.index(name)
            pairs.append((oi, si))
            used.add(si)
    if not pairs and len(out_mats) == len(src_names):
        pairs = [(i, i) for i in range(len(out_mats))]
    return pairs


def compare_and_patch_gltf(src: dict[str, Any] | None, out: dict[str, Any]) -> dict[str, Any]:
    """Compare input vs exported glTF JSON; fix material alphaMode/alphaCutoff/doubleSided IN ``out``.

    Returns ``{"checked", "patched": [...], "issues": [...], "materials", "images", "textures", "mime_types",
    "extensions_required"}`` (``*_in``/``*_out`` pairs). ``out`` is modified when ``patched`` is non-empty.
    """
    images_out = out.get("images") or []
    info: dict[str, Any] = {
        "checked": src is not None, "patched": [], "issues": [],
        "materials_out": len(out.get("materials") or []), "images_out": len(images_out),
        "textures_out": len(out.get("textures") or []),
        "mime_types_out": sorted({im.get("mimeType") or _uri_mime(im.get("uri")) for im in images_out}),
        "extensions_required_out": sorted(out.get("extensionsRequired") or []),
    }
    if src is None:
        return info
    images_in = src.get("images") or []
    info.update({
        "materials_in": len(src.get("materials") or []), "images_in": len(images_in),
        "textures_in": len(src.get("textures") or []),
        "mime_types_in": sorted({im.get("mimeType") or _uri_mime(im.get("uri")) for im in images_in}),
        "extensions_required_in": sorted(src.get("extensionsRequired") or []),
    })
    src_mats = src.get("materials") or []
    out_mats = out.get("materials") or []
    for oi, si in _match_materials(src, out):
        want, have = src_mats[si], out_mats[oi]
        for key in ("alphaMode", "doubleSided", "alphaCutoff"):
            if key == "alphaCutoff" and _material_value(want, "alphaMode") != "MASK":
                continue
            w, h = _material_value(want, key), _material_value(have, key)
            if w != h:
                if w == _GLTF_MATERIAL_DEFAULTS[key]:
                    have.pop(key, None)
                else:
                    have[key] = w
                info["patched"].append({"material": have.get("name") or oi, "field": key, "from": h, "to": w})
    if info["materials_out"] < info["materials_in"]:
        info["issues"].append(f"materials: {info['materials_in']} -> {info['materials_out']}")
    if info["images_out"] < info["images_in"]:
        info["issues"].append(f"images: {info['images_in']} -> {info['images_out']}")
    if info["textures_out"] < info["textures_in"]:
        info["issues"].append(f"textures: {info['textures_in']} -> {info['textures_out']}")
    if set(info["mime_types_in"]) - set(info["mime_types_out"]):
        info["issues"].append(f"image formats changed: {info['mime_types_in']} -> {info['mime_types_out']}")
    return info


def _uri_mime(uri: str | None) -> str | None:
    if not uri:
        return None
    if uri.startswith("data:"):
        return uri[5:].split(";", 1)[0]
    ext = os.path.splitext(uri)[1].lower()
    return {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(ext)


# ================================================================================================================
# import
# ================================================================================================================


def reset_scene() -> None:
    """Start from an empty factory scene (no default cube/camera/light)."""
    bpy.ops.wm.read_factory_settings(use_empty=True)


def _op_kwargs(op, **kwargs: Any) -> dict[str, Any]:
    """Drop keyword arguments the operator does not know (exporter/importer options differ between versions)."""
    try:
        known = set(op.get_rna_type().properties.keys())
    except Exception:  # pragma: no cover - very old API
        return kwargs
    return {k: v for k, v in kwargs.items() if k in known}


def import_file(path: str, fmt: str) -> list[bpy.types.Object]:
    """Import ``path`` (format id from :data:`SUPPORTED_FORMATS`) into the current scene; returns new objects.

    Raises:
        InputError: unsupported format, importer failure or nothing imported.
    """
    before = set(bpy.data.objects)
    try:
        if fmt in ("glb", "gltf"):
            op = bpy.ops.import_scene.gltf
            result = op(**_op_kwargs(op, filepath=path, merge_vertices=False, import_shading="NORMALS",
                                        disable_bone_shape=True))  # no bone-shape helper meshes
        elif fmt == "obj":
            result = bpy.ops.wm.obj_import(filepath=path)
        elif fmt == "fbx":
            result = bpy.ops.import_scene.fbx(filepath=path)
        elif fmt == "ply":
            result = bpy.ops.wm.ply_import(filepath=path)
        elif fmt == "stl":
            result = bpy.ops.wm.stl_import(filepath=path)
        elif fmt == "blend":
            result = _append_blend(path)
        else:
            raise InputError("unsupported_format", f"Unsupported input format '.{fmt}'")
    except InputError:
        raise
    except (RuntimeError, TypeError, AttributeError, OSError, ValueError) as exc:
        raise InputError("input_unreadable", f"Could not import {os.path.basename(path)}: {exc}") from exc
    if "FINISHED" not in result:
        raise InputError("input_unreadable", f"The {SUPPORTED_FORMATS.get(fmt, fmt)} importer failed "
                                             f"({', '.join(sorted(result))})")
    new = [o for o in bpy.data.objects if o not in before]
    if not new:
        raise InputError("no_mesh", f"Nothing was imported from {os.path.basename(path)}")
    return new


def _append_blend(path: str) -> set[str]:
    """Append all MESH/ARMATURE/EMPTY objects of a .blend file into the scene collection."""
    with bpy.data.libraries.load(path, link=False) as (data_from, data_to):
        data_to.objects = list(data_from.objects)
    scene = bpy.context.scene
    linked = 0
    for obj in data_to.objects:
        if obj is not None and obj.type in {"MESH", "ARMATURE", "EMPTY"}:
            scene.collection.objects.link(obj)
            linked += 1
    return {"FINISHED"} if linked else {"CANCELLED"}


def _join_meshes(primary: bpy.types.Object, others: list[bpy.types.Object]) -> None:
    view_layer = bpy.context.view_layer
    for obj in bpy.data.objects:
        obj.select_set(False)
    for obj in [primary, *others]:
        obj.select_set(True)
    view_layer.objects.active = primary
    sel = [primary, *others]
    with bpy.context.temp_override(active_object=primary, object=primary, selected_objects=sel,
                                   selected_editable_objects=sel):
        result = bpy.ops.object.join()
    if "FINISHED" not in result:
        raise StepError("join_failed", "Could not join the imported mesh objects")


def _apply_transform(obj: bpy.types.Object) -> bool:
    """Bake ``matrix_world`` into the mesh (keeps world positions), clear parent; True when something changed."""
    mw = obj.matrix_world.copy()
    if obj.parent is None and mw == Matrix.Identity(4):
        return False
    obj.data.transform(mw)
    if mw.determinant() < 0:
        obj.data.flip_normals()
    obj.parent = None
    obj.matrix_world = Matrix.Identity(4)
    obj.data.update()
    return True


def load_input(ctx: TunerContext) -> StepOutcome:
    """The ``load`` step: validate the input, import, normalize, weld, snapshot. Raises ``InputError`` (exit 3)."""
    path = ctx.config.input_path
    fmt = ctx.config.input_format
    if not os.path.isfile(path):
        raise InputError("input_not_found", f"Input file not found: {path}")
    if fmt not in SUPPORTED_FORMATS:
        raise InputError("unsupported_format",
                         f"Unsupported input format '.{fmt}' (supported: {', '.join(SUPPORTED_FORMATS)})")
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            head = fh.read(4)
    except OSError as exc:
        raise InputError("input_unreadable", f"Input file is not readable: {exc}") from exc
    if size == 0:
        raise InputError("input_unreadable", "Input file is empty")
    if fmt == "glb" and head != _GLB_MAGIC:
        raise InputError("input_unreadable", "Input .glb file has no glTF header (corrupt or not a GLB)")
    ctx.input_info = {"path": path, "format": fmt, "size_bytes": size}
    details: dict[str, Any] = {"format": fmt, "size_bytes": size}
    if fmt in ("glb", "gltf"):
        ctx.source_gltf = read_gltf_json(path)
        if ctx.source_gltf is not None:
            details["generator"] = (ctx.source_gltf.get("asset") or {}).get("generator")

    ctx.progress(0.05, "Importing")
    reset_scene()
    t0 = time.perf_counter()
    with ctx.timer("import"):
        objects = import_file(path, fmt)
    details["import_s"] = round(time.perf_counter() - t0, 3)
    ctx.progress(0.45, "Preparing mesh")

    meshes = [o for o in objects if o.type == "MESH" and len(o.data.polygons) > 0]
    armatures = [o for o in objects if o.type == "ARMATURE"]
    ignored = [o.name for o in objects if o.type == "MESH" and len(o.data.polygons) == 0]
    if not meshes:
        raise InputError("no_mesh", "The input contains no mesh with faces")
    primary = max(meshes, key=lambda o: len(o.data.polygons))
    details.update({"objects": len(objects), "mesh_objects": len(meshes), "armatures": len(armatures)})
    if ignored:
        details["ignored_objects_without_faces"] = ignored
    rigged = bool(armatures) or any(m.type == "ARMATURE" for o in meshes for m in o.modifiers)
    if not rigged:
        others = [o for o in meshes if o is not primary]
        if others:
            _join_meshes(primary, others)
            details["joined_objects"] = len(others) + 1
            meshes = [primary]
        details["transform_applied"] = _apply_transform(primary)
    ctx.mesh_objects = meshes
    ctx.armatures = armatures
    ctx.set_primary(primary)
    me = primary.data
    details["raw_vertices"] = len(me.vertices)
    diag = ctx.update_bounds()
    if diag <= 0:
        raise InputError("no_mesh", "The input mesh has zero size")

    ctx.progress(0.55, "Welding duplicate vertices")
    t0 = time.perf_counter()
    with ctx.timer("weld"):
        merged = 0
        for obj in meshes:
            merged += meshdata.weld_by_distance(obj.data, WELD_REL_DIST * diag)
    details["weld_s"] = round(time.perf_counter() - t0, 3)
    details["merged_vertices"] = merged
    details.update({"vertices": len(me.vertices), "faces": len(me.polygons), "uv_layers": len(me.uv_layers),
                    "materials": len([m for m in me.materials if m is not None]),
                    "images": len(meshdata.unique_images(me.materials)),
                    "custom_normals": bool(getattr(me, "has_custom_normals", False))})
    ctx.update_bounds()

    ctx.progress(0.9, "Keeping a copy of the original")
    ctx.original = create_original_snapshot(ctx)
    ctx.store["source_frame"] = detect_source_frame(ctx)
    details["source_frame"] = ctx.store["source_frame"]
    details["bbox_min"] = [round(float(x), 6) for x in ctx.bbox_min]
    details["bbox_max"] = [round(float(x), 6) for x in ctx.bbox_max]
    details["diag"] = round(ctx.diag, 6)
    ctx.store["load"] = details
    msg = (f"{details['vertices']:,} vertices, {details['faces']:,} faces"
           + (f" ({merged:,} duplicate vertices welded)" if merged else ""))
    return StepOutcome("done", msg, details)


def create_original_snapshot(ctx: TunerContext) -> bpy.types.Object:
    """Hidden, never-exported copy of the primary object (own mesh datablock, same materials/modifiers)."""
    src = ctx.require_primary()
    obj = src.copy()
    obj.data = src.data.copy()
    obj.name = ORIGINAL_NAME
    obj.data.name = ORIGINAL_NAME
    ctx.scene.collection.objects.link(obj)
    obj.hide_render = True
    obj.hide_viewport = True
    obj.hide_select = True
    return obj


def detect_source_frame(ctx: TunerContext) -> dict[str, Any]:
    """Describe the coordinate frame after import (Blender: +Z up; TRELLIS/glTF characters face -Y)."""
    fmt = ctx.input_info.get("format")
    generator = None
    if ctx.source_gltf is not None:
        generator = (ctx.source_gltf.get("asset") or {}).get("generator")
    source = ctx.config.context.get("source")
    if not source:
        source = "trellis2" if generator and "trimesh" in str(generator).lower() else fmt
    unit_cube = bool(np.all(ctx.bbox_min >= -0.5 - 1e-3) and np.all(ctx.bbox_max <= 0.5 + 1e-3))
    converted = {"glb": "glTF +Y up -> Blender +Z up", "gltf": "glTF +Y up -> Blender +Z up",
                 "obj": "OBJ importer axis conversion (Y up -> Z up)", "fbx": "FBX importer axis conversion"}
    return {"up": "+Z", "front": "-Y", "left": "+X", "source": source, "generator": generator,
            "unit_cube": unit_cube, "conversion": converted.get(fmt, "none (assumed Blender frame)")}


# ================================================================================================================
# export
# ================================================================================================================


def _export_objects(ctx: TunerContext) -> list[bpy.types.Object]:
    objs = [o for o in ctx.mesh_objects if o is not None and o.name in bpy.data.objects]
    for arm in ctx.armatures:
        if arm is not None and arm.name in bpy.data.objects and arm not in objs:
            objs.append(arm)
    return objs


def sync_material_settings(ctx: TunerContext) -> list[str]:
    """Set ``use_backface_culling`` of imported materials from the source glTF ``doubleSided`` (exporter input)."""
    changed: list[str] = []
    if not ctx.source_gltf:
        return changed
    names = gltf_material_names(ctx.source_gltf)
    for index, gmat in enumerate(ctx.source_gltf.get("materials") or []):
        target = names[index]
        for mat in bpy.data.materials:
            if mat.name == target or _strip_dup_suffix(mat.name) == target:
                want_cull = not bool(gmat.get("doubleSided", False))
                if mat.use_backface_culling != want_cull:
                    mat.use_backface_culling = want_cull
                    changed.append(mat.name)
                break
    return changed


def export_glb(ctx: TunerContext, path: str) -> dict[str, Any]:
    """Export the result objects to ``path`` (GLB) and verify/patch material fidelity against the input.

    Raises:
        StepError: ``export_failed`` when the exporter fails or writes nothing.
    """
    objs = _export_objects(ctx)
    if not objs:
        raise StepError("export_failed", "Nothing to export")
    removed: list[str] = []
    for obj in objs:
        if obj.type == "MESH":
            removed += meshdata.remove_attributes(obj.data, "amt_")
    synced = sync_material_settings(ctx)
    view_layer = bpy.context.view_layer
    for obj in view_layer.objects:
        obj.select_set(False)
    for obj in objs:
        obj.hide_set(False)
        obj.select_set(True)
    view_layer.objects.active = objs[0]

    disabled: list[tuple[Any, bool, bool]] = []
    other_modifiers = False
    for obj in objs:
        for mod in getattr(obj, "modifiers", []):
            if mod.type in ("SUBSURF", "MULTIRES"):
                disabled.append((mod, mod.show_viewport, mod.show_render))
                mod.show_viewport = False
                mod.show_render = False
            elif mod.type != "ARMATURE" and mod.show_viewport:
                other_modifiers = True
    has_rig = ctx.has_armature
    op = bpy.ops.export_scene.gltf
    kwargs = _op_kwargs(op, filepath=path, check_existing=False, export_format="GLB", use_selection=True,
                        export_image_format="AUTO", export_materials="EXPORT", export_yup=True,
                        export_apply=other_modifiers, export_texcoords=True, export_normals=True,
                        export_tangents=False, export_skins=has_rig, export_animations=has_rig,
                        export_cameras=False, export_lights=False, export_extras=False)
    if os.path.exists(path):
        os.remove(path)
    t0 = time.perf_counter()
    try:
        result = op(**kwargs)
    except (RuntimeError, TypeError) as exc:
        raise StepError("export_failed", f"GLB export failed: {exc}") from exc
    finally:
        for mod, show_viewport, show_render in disabled:
            mod.show_viewport = show_viewport
            mod.show_render = show_render
    duration = time.perf_counter() - t0
    if "FINISHED" not in result or not os.path.isfile(path) or os.path.getsize(path) == 0:
        raise StepError("export_failed", f"GLB export did not produce a file ({', '.join(sorted(result))})")

    fidelity: dict[str, Any] = {"checked": False}
    out_doc = read_gltf_json(path)
    if out_doc is None:
        raise StepError("export_failed", "The exported GLB cannot be parsed")
    fidelity = compare_and_patch_gltf(ctx.source_gltf, out_doc)
    if fidelity["patched"]:
        write_glb_json(path, out_doc)
        log.info("Restored %d material setting(s) in the exported GLB", len(fidelity["patched"]))
    for issue in fidelity["issues"]:
        ctx.warn(f"Export changed {issue}")
    return {"path": path, "size_bytes": os.path.getsize(path), "duration_s": round(duration, 3),
            "export_apply": other_modifiers, "skins": has_rig, "removed_attributes": sorted(set(removed)),
            "materials_synced": synced, "subdivision_kept_as_modifier": len(disabled), "fidelity": fidelity}


def save_blend(path: str) -> dict[str, Any]:
    """Save the current file as a self-contained ``.blend`` copy (images packed, compressed)."""
    try:
        bpy.ops.file.pack_all()
    except RuntimeError as exc:  # images that cannot be packed stay external
        log.warning("Could not pack all images: %s", exc)
    t0 = time.perf_counter()
    result = bpy.ops.wm.save_as_mainfile(filepath=path, copy=True, compress=True, check_existing=False)
    if "FINISHED" not in result or not os.path.isfile(path):
        raise StepError("blend_save_failed", f"Could not save {os.path.basename(path)}")
    return {"path": path, "size_bytes": os.path.getsize(path), "duration_s": round(time.perf_counter() - t0, 3)}


def remove_original_snapshot(ctx: TunerContext) -> None:
    """Delete ``ctx.original`` (object + mesh) - e.g. before saving the .blend."""
    obj = ctx.original
    ctx.original = None
    if obj is None:
        return
    try:
        mesh = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if mesh is not None and mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    except ReferenceError:
        pass
