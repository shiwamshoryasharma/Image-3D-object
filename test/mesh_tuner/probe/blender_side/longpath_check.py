# Blender-side: test reading/importing/exporting via a long path (>260) with and without the extended-length prefix.
import sys, os, json, bpy
PFX = "\\" * 2 + "?" + "\\"  # the 4 chars  \\?\
args = sys.argv[sys.argv.index("--") + 1:]
src = args[0]
res = {"len": len(src), "prefix_repr": repr(PFX)}
for label, p in (("plain", src), ("extended", PFX + src)):
    r = {}
    try:
        r["os.path.exists"] = os.path.exists(p)
        with open(p, "rb") as f:
            r["open_bytes"] = len(f.read())
    except Exception as e:
        r["open_err"] = repr(e)[:160]
    bpy.ops.wm.read_factory_settings(use_empty=True)
    try:
        ret = bpy.ops.wm.obj_import(filepath=p)
        r["obj_import"] = list(ret)
        r["objects"] = len(bpy.data.objects)
    except Exception as e:
        r["obj_import_err"] = repr(e)[:120]
    out = p[:-4] + "_out_" + label + ".glb"
    try:
        ret = bpy.ops.export_scene.gltf(filepath=out, export_format="GLB")
        r["gltf_export"] = list(ret)
    except Exception as e:
        r["gltf_export_err"] = repr(e).splitlines()[-1][-160:]
    plain_out = out[len(PFX):] if out.startswith(PFX) else out
    r["export_file_exists"] = os.path.exists(PFX + plain_out)
    res[label] = r
print("LP_JSON " + json.dumps(res), flush=True)
