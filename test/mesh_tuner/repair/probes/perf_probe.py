"""Probe (repair agent): BVH ray-cast throughput + GPU ID-render/readback speed on a real TRELLIS GLB.

blender --background --factory-startup --python-exit-code 86 --python perf_probe.py -- <glb>
"""
import sys
import time

sys.stdout.reconfigure(line_buffering=True)
import bmesh
import bpy
import numpy as np
from mathutils.bvhtree import BVHTree

path = sys.argv[sys.argv.index("--") + 1]
bpy.ops.wm.read_factory_settings(use_empty=True)
t = time.perf_counter()
bpy.ops.import_scene.gltf(filepath=path, merge_vertices=False)
print("import", time.perf_counter() - t)
obj = max((o for o in bpy.data.objects if o.type == "MESH"), key=lambda o: len(o.data.polygons))
me = obj.data
co = np.empty(len(me.vertices) * 3, np.float32)
me.vertices.foreach_get("co", co)
co = co.reshape(-1, 3)
diag = float(np.linalg.norm(co.max(0) - co.min(0)))
bm = bmesh.new()
bm.from_mesh(me)
bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=1e-6 * diag)
bm.to_mesh(me)
bm.free()
me.update()
co = np.empty(len(me.vertices) * 3, np.float32)
me.vertices.foreach_get("co", co)
co = co.reshape(-1, 3).astype(np.float64)
lv = np.empty(len(me.loops), np.int32)
me.loops.foreach_get("vertex_index", lv)
tris = lv.reshape(-1, 3).astype(np.int64)
T = len(tris)
print("faces", T, "verts", len(co))
# duplicate faces (same vertex set)
s = np.sort(tris, axis=1)
key = (s[:, 0] * len(co) + s[:, 1]) * len(co) + s[:, 2]
u, inv, cnt = np.unique(key, return_inverse=True, return_counts=True)
print("dup face groups", int((cnt > 1).sum()), "faces in groups", int((cnt[inv] > 1).sum()), "max", cnt.max())

p0 = co[tris[:, 0]]
n = np.cross(co[tris[:, 1]] - p0, co[tris[:, 2]] - p0)
ln = np.linalg.norm(n, axis=1)
nrm = n / np.maximum(ln, 1e-30)[:, None]
cen = (co[tris[:, 0]] + co[tris[:, 1]] + co[tris[:, 2]]) / 3
t = time.perf_counter()
bvh = BVHTree.FromPolygons(co.tolist(), tris.tolist(), all_triangles=True)
print("bvh build (with tolist)", time.perf_counter() - t)
N = 200000
idx = np.random.default_rng(0).choice(T, N, replace=False)
eps = 1e-5 * diag
orig = (cen[idx] - nrm[idx] * eps).tolist()
dirs = (-nrm[idx]).tolist()
tmax = 0.006
rc = bvh.ray_cast
t = time.perf_counter()
hits = 0
dist = []
for o, d in zip(orig, dirs):
    h = rc(o, d, tmax)
    if h[0] is not None:
        hits += 1
        dist.append(h[3])
dt = time.perf_counter() - t
print("ray_cast %d rays %.3fs -> %.2f us/ray, hits %d, median dist %.6f" % (N, dt, dt / N * 1e6, hits, np.median(dist)))
t = time.perf_counter()
for o, d in zip(orig, dirs):
    h = rc(o, d)
dt = time.perf_counter() - t
print("ray_cast unlimited %.2f us/ray" % (dt / N * 1e6))

# GPU
import gpu
from gpu_extras.batch import batch_for_shader
from mathutils import Matrix

t = time.perf_counter()
gpu.init()
print("gpu.init", time.perf_counter() - t, gpu.platform.renderer_get(), gpu.platform.backend_type_get())
t = time.perf_counter()
pos = co[tris].reshape(-1, 3).astype(np.float32)
ids = np.arange(1, T + 1, dtype=np.int64)
rgb = np.stack([ids & 255, (ids >> 8) & 255, (ids >> 16) & 255, np.full(T, 255)], axis=1).astype(np.float32) / 255.0
col = np.repeat(rgb, 3, axis=0)
print("arrays", time.perf_counter() - t)
shader = gpu.shader.from_builtin("FLAT_COLOR")
t = time.perf_counter()
batch = batch_for_shader(shader, "TRIS", {"pos": pos, "color": col})
print("batch_for_shader numpy", time.perf_counter() - t)
for res in (2048, 3072, 4096):
    off = gpu.types.GPUOffScreen(res, res, format="RGBA8")
    c = 0.5 * (co.max(0) + co.min(0))
    R = 0.5 * diag * 1.01
    d = np.array([0.0, -1.0, 0.0])
    up = np.array([0.0, 0.0, 1.0])
    r = np.cross(up, d)
    r /= np.linalg.norm(r)
    u = np.cross(d, r)
    M = np.eye(4)
    M[0, :3] = r / R
    M[1, :3] = u / R
    M[2, :3] = -d / R
    M[:3, 3] = -M[:3, :3] @ c
    times = {}
    for cull in ("NONE", "FRONT"):
        t = time.perf_counter()
        with off.bind():
            fb = gpu.state.active_framebuffer_get()
            fb.clear(color=(0.0, 0.0, 0.0, 0.0), depth=1.0)
            gpu.state.depth_test_set("LESS")
            gpu.state.depth_mask_set(True)
            gpu.state.face_culling_set(cull)
            gpu.matrix.load_matrix(Matrix(M.tolist()))
            gpu.matrix.load_projection_matrix(Matrix.Identity(4))
            batch.draw(shader)
            t1 = time.perf_counter()
            buf = fb.read_color(0, 0, res, res, 4, 0, "UBYTE")
            t2 = time.perf_counter()
            dbuf = fb.read_depth(0, 0, res, res)
            t3 = time.perf_counter()
            gpu.state.face_culling_set("NONE")
            gpu.state.depth_test_set("NONE")
        try:
            a = np.frombuffer(buf, dtype=np.uint8)
            how = "frombuffer"
        except Exception as e:
            a = None
            how = repr(e)
        t4 = time.perf_counter()
        if a is None:
            try:
                a = np.asarray(buf, dtype=np.uint8).reshape(-1)
                how += " asarray"
            except Exception as e:
                how += " " + repr(e)
        t5 = time.perf_counter()
        try:
            dd = np.frombuffer(dbuf, dtype=np.float32)
            dhow = "frombuffer"
        except Exception as e:
            dd = np.asarray(dbuf, dtype=np.float32).reshape(-1)
            dhow = "asarray " + repr(e)
        t6 = time.perf_counter()
        v = a.view(np.uint32) & 0xFFFFFF
        vis = v[v != 0] - 1
        mark = np.zeros(T, bool)
        mark[vis] = True
        nz = np.flatnonzero(mark)
        t7 = time.perf_counter()
        print(f"res {res} cull {cull}: draw {t1-t:.3f} read_color {t2-t1:.3f} read_depth {t3-t2:.3f} to_np {t5-t4:.3f} ({how}) "
              f"depth_np {t6-t5:.3f} ({dhow}) decode {t7-t6:.3f} visible_faces {len(nz)} maxid {int(v.max())} "
              f"depth range {float(dd[dd<1].min()) if (dd<1).any() else None}..{float(dd[dd<1].max()) if (dd<1).any() else None}")
        if cull == "NONE":
            ndot = nrm[nz] @ d
            print("   front-facing fraction among visible", float((ndot > 0).mean()))
        else:
            ndot = nrm[nz] @ d
            print("   cull FRONT -> visible faces facing camera fraction", float((ndot > 0).mean()))
    off.free()
print("PROBE_DONE")
