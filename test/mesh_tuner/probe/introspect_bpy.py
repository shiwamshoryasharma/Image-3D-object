"""Print Blender version + glTF import/export operator params + render engines (probe only)."""
import bpy, sys, json
out = {"version": bpy.app.version_string, "version_tuple": list(bpy.app.version), "build_hash": bpy.app.build_hash.decode() if isinstance(bpy.app.build_hash, bytes) else bpy.app.build_hash,
       "python": sys.version, "background": bpy.app.background}
def props(op):
    rna = op.get_rna_type()
    res = {}
    for p in rna.properties:
        if p.identifier == "rna_type":
            continue
        d = {"type": p.type}
        try:
            if p.type == "ENUM":
                d["items"] = [i.identifier for i in p.enum_items]
                d["default"] = list(p.default_flag) if p.is_enum_flag else p.default
            elif getattr(p, "array_length", 0) > 0:
                d["default"] = list(p.default_array)
            else:
                d["default"] = p.default
        except Exception as e:
            d["err"] = str(e)
        res[p.identifier] = d
    return res
out["import_gltf"] = props(bpy.ops.import_scene.gltf)
out["export_gltf"] = props(bpy.ops.export_scene.gltf)
out["render_engines"] = [e for e in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items.keys()]
sh = bpy.types.View3DShading.bl_rna.properties
out["shading_light"] = [i.identifier for i in sh["light"].enum_items]
out["shading_color_type"] = [i.identifier for i in sh["color_type"].enum_items]
out["matcaps"] = [m for m in bpy.context.preferences.studio_lights.keys() if True][:80]
out["scene_display_shading_has_cavity"] = hasattr(bpy.context.scene.display.shading, "show_cavity")
import addon_utils
out["addons_enabled"] = sorted(bpy.context.preferences.addons.keys())
for mod in addon_utils.modules():
    if "gltf" in mod.__name__:
        out["gltf_addon"] = {"name": mod.__name__, "version": list(mod.bl_info.get("version", ())), "file": mod.__file__}
# mesh API presence checks
m = bpy.data.meshes.new("t")
out["mesh_attrs"] = {k: hasattr(m, k) for k in ["vertex_normals", "corner_normals", "polygon_normals", "use_auto_smooth", "calc_normals_split", "set_sharp_from_angle", "attributes", "color_attributes", "uv_layers", "normals_domain", "shade_smooth", "shade_flat", "validate", "calc_loop_triangles", "has_custom_normals"]}
import bmesh
out["bmesh_ops_sample"] = {k: hasattr(bmesh.ops, k) for k in ["remove_doubles", "find_doubles", "holes_fill", "recalc_face_normals", "dissolve_degenerate", "delete", "triangulate", "join_triangles", "symmetrize", "smooth_vert", "smooth_laplacian_vert", "edgenet_fill", "bridge_loops", "grid_fill"]}
out["mesh_ops_sample"] = {k: hasattr(bpy.ops.mesh, k) for k in ["fill_holes", "remove_doubles", "merge_by_distance", "normals_make_consistent", "delete_loose", "dissolve_degenerate", "tris_convert_to_quads", "symmetrize", "symmetry_snap", "select_non_manifold", "print3d_clean_non_manifold", "separate", "select_linked", "vertices_smooth_laplacian"]}
out["object_ops_sample"] = {k: hasattr(bpy.ops.object, k) for k in ["voxel_remesh", "quadriflow_remesh", "shade_smooth", "shade_auto_smooth", "shade_smooth_by_angle", "data_transfer", "parent_set", "vertex_group_clean", "vertex_group_limit_total", "vertex_group_normalize_all", "vertex_group_smooth"]}
print("@@JSON@@" + json.dumps(out))
