"""Import a GLB and print mesh attributes / images (version comparison probe)."""
import bpy, sys, time
glb = sys.argv[sys.argv.index("--") + 1]
bpy.ops.wm.read_factory_settings(use_empty=True)
t = time.perf_counter(); bpy.ops.import_scene.gltf(filepath=glb); dt = time.perf_counter() - t
for m in bpy.data.meshes:
    print("@@", bpy.app.version_string, "import_s", round(dt, 2), m.name, len(m.vertices), len(m.polygons), [(a.name, a.domain, a.data_type) for a in m.attributes], "custom", m.has_custom_normals)
for im in bpy.data.images:
    print("@@img", im.name, tuple(im.size), im.file_format, im.packed_file is not None)
for mt in bpy.data.materials:
    print("@@mat", mt.name, mt.blend_method, getattr(mt, "surface_render_method", None), mt.use_backface_culling)
