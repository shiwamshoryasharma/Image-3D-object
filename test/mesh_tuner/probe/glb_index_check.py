"""Probe (no Blender): count index-degenerate / duplicate triangles in a GLB and list image bufferView sizes."""
import json, struct, sys
import numpy as np
for path in sys.argv[1:]:
    with open(path, "rb") as f:
        data = f.read()
    clen = struct.unpack_from("<I", data, 12)[0]
    js = json.loads(data[20:20 + clen])
    bin_off = 20 + clen + 8
    def view(acc_i):
        a = js["accessors"][acc_i]; bv = js["bufferViews"][a["bufferView"]]
        dt = {5125: np.uint32, 5123: np.uint16, 5126: np.float32}[a["componentType"]]
        n = {"SCALAR": 1, "VEC2": 2, "VEC3": 3}[a["type"]]
        off = bin_off + bv.get("byteOffset", 0) + a.get("byteOffset", 0)
        return np.frombuffer(data, dt, a["count"] * n, off).reshape(-1, n) if n > 1 else np.frombuffer(data, dt, a["count"], off)
    prim = js["meshes"][0]["primitives"][0]
    idx = view(prim["indices"]).astype(np.int64).reshape(-1, 3)
    deg = (idx[:, 0] == idx[:, 1]) | (idx[:, 1] == idx[:, 2]) | (idx[:, 0] == idx[:, 2])
    s = np.sort(idx, 1)
    u, cnt = np.unique(s, axis=0, return_counts=True)
    print(path, "tris", len(idx), "index_degenerate", int(deg.sum()), "dup_index_triples_extra", int((cnt - 1).sum()))
    print("  images", [(im.get("mimeType"), js["bufferViews"][im["bufferView"]]["byteLength"]) for im in js.get("images", [])])
