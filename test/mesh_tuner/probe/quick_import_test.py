"""Quick: import a GLB with/without import_webp_texture, report images + engines + a tiny workbench/eevee render."""
import bpy, sys, time, json, os
argv = sys.argv[sys.argv.index("--") + 1:]
glb, out = argv[0], argv[1]
os.makedirs(out, exist_ok=True)
res = {}
for webp in (False, True):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    t = time.perf_counter()
    r = bpy.ops.import_scene.gltf(filepath=glb, import_webp_texture=webp)
    res[f"webp={webp}"] = {"ret": list(r), "t": round(time.perf_counter() - t, 2),
        "images": [(im.name, list(im.size), im.file_format, im.packed_file is not None, im.has_data, im.source, im.filepath) for im in bpy.data.images],
        "objects": [(o.name, o.type, o.parent.name if o.parent else None, [list(o.location), list(o.rotation_euler), list(o.scale)]) for o in bpy.data.objects],
        "collections": [c.name for c in bpy.data.collections],
        "mesh_attrs": {m.name: [(a.name, a.domain, a.data_type) for a in m.attributes] for m in bpy.data.meshes},
        "has_custom_normals": {m.name: m.has_custom_normals for m in bpy.data.meshes},
        "normals_domain": {m.name: m.normals_domain for m in bpy.data.meshes},
        "materials": [(m.name, [n.bl_idname for n in m.node_tree.nodes] if m.node_tree else None) for m in bpy.data.materials]}
sc = bpy.context.scene
eng = {}
for e in ("BLENDER_WORKBENCH", "BLENDER_EEVEE", "BLENDER_EEVEE_NEXT", "CYCLES"):
    try:
        sc.render.engine = e; eng[e] = "ok"
    except Exception as ex:
        eng[e] = f"ERR {ex}"
res["engines"] = eng
cam = bpy.data.cameras.new("c"); co = bpy.data.objects.new("c", cam); sc.collection.objects.link(co); sc.camera = co
co.location = (0, -3, 0); co.rotation_euler = (1.5708, 0, 0); cam.type = "ORTHO"; cam.ortho_scale = 1.2
sc.render.resolution_x = sc.render.resolution_y = 256
for e in ("BLENDER_WORKBENCH", "BLENDER_EEVEE"):
    sc.render.engine = e
    sc.render.filepath = os.path.join(out, f"quick_{e}.png")
    t = time.perf_counter()
    try:
        bpy.ops.render.render(write_still=True); res[f"render_{e}"] = round(time.perf_counter() - t, 2)
    except Exception as ex:
        res[f"render_{e}"] = f"ERR {ex}"
try:
    import gpu
    res["gpu"] = {"backend": gpu.platform.backend_type_get(), "renderer": gpu.platform.renderer_get(), "vendor": gpu.platform.vendor_get(), "version": gpu.platform.version_get()}
except Exception as ex:
    res["gpu"] = f"ERR {ex}"
print("@@JSON@@" + json.dumps(res, default=str))
