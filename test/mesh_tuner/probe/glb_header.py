"""Dump the JSON chunk summary of a GLB (no Blender). Usage: python -s glb_header.py file.glb"""
import json, struct, sys
for path in sys.argv[1:]:
    with open(path, "rb") as f:
        magic, ver, length = struct.unpack("<4sII", f.read(12))
        clen, ctype = struct.unpack("<II", f.read(8))
        js = json.loads(f.read(clen))
        blen, btype = struct.unpack("<II", f.read(8))
    print("==", path, magic, ver, length, "json", clen, "bin", blen)
    print(" asset", js.get("asset"), "extUsed", js.get("extensionsUsed"), "extReq", js.get("extensionsRequired"))
    print(" scenes", js.get("scenes"), "nodes", js.get("nodes"))
    for m in js.get("meshes", []):
        for p in m["primitives"]:
            print(" prim attrs", p["attributes"], "mode", p.get("mode"), "indices", p.get("indices"), "material", p.get("material"))
    for i, a in enumerate(js.get("accessors", [])):
        print(" acc", i, a.get("componentType"), a.get("type"), a.get("count"), a.get("min"), a.get("max"))
    print(" materials", json.dumps(js.get("materials")))
    print(" textures", js.get("textures"), "images", [{k: v for k, v in im.items()} for im in js.get("images", [])], "samplers", js.get("samplers"))
    print(" bufferViews", [(b.get("byteLength")) for b in js.get("bufferViews", [])])
