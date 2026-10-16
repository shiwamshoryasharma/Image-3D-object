"""Blender helper (test only): unit checks of img3d_amt.meshdata / analysis / config inside Blender.

Run:
  blender --background --factory-startup --python-exit-code 86 --python selftest_meshdata.py -- \
      --scripts <backend/blender_scripts> [--glb <synthetic.glb>]
Prints one line "@@SELFTEST@@{json}" = {"results": {name: {"ok": bool, "info": ...}}, "errors": {...}}.
"""

import argparse
import json
import sys
import traceback

import bmesh
import bpy
import numpy as np


def args_():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--scripts", required=True)
    p.add_argument("--glb", default=None)
    return p.parse_args(argv)


A = args_()
sys.path.insert(0, A.scripts)

from img3d_amt import meshdata  # noqa: E402

RESULTS = {}
ERRORS = {}


def check(name):
    def deco(fn):
        try:
            ok, info = fn()
            RESULTS[name] = {"ok": bool(ok), "info": info}
        except Exception:
            RESULTS[name] = {"ok": False, "info": None}
            ERRORS[name] = traceback.format_exc()
        return fn
    return deco


def mesh_from_bmesh(name, build):
    bm = bmesh.new()
    build(bm)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    obj = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(obj)
    return obj


bpy.ops.wm.read_factory_settings(use_empty=True)
CUBE = mesh_from_bmesh("cube", lambda bm: (bmesh.ops.create_cube(bm, size=1.0),
                                           bmesh.ops.triangulate(bm, faces=bm.faces[:])))
SPHERE = mesh_from_bmesh("sphere", lambda bm: bmesh.ops.create_icosphere(bm, subdivisions=4, radius=1.0))


def grid_with_hole(bm):
    bmesh.ops.create_grid(bm, x_segments=10, y_segments=10, size=1.0)
    centre = [f for f in bm.faces if f.calc_center_median().length < 0.15]
    bmesh.ops.delete(bm, geom=centre, context="FACES")


GRID = mesh_from_bmesh("grid", grid_with_hole)


@check("positions_roundtrip")
def _():
    me = SPHERE.data
    co = meshdata.get_positions(me)
    meshdata.set_positions(me, co * 2.0)
    co2 = meshdata.get_positions(me)
    meshdata.set_positions(me, co)
    return np.allclose(co2, co * 2.0, atol=1e-6) and co.dtype == np.float64 and co.shape == (len(me.vertices), 3), \
        {"V": len(co)}


@check("triangles_and_loops")
def _():
    me = CUBE.data
    tris = meshdata.get_triangles(me)
    lt, lp, ll = meshdata.get_loop_triangles(me)
    start, total = meshdata.get_polygon_loops(me)
    ok = tris.shape == (12, 3) and lt.shape == (12, 3) and (total == 3).all() and meshdata.is_all_triangles(me)
    ok &= meshdata.is_all_triangles(GRID.data) is False
    try:
        meshdata.get_triangles(GRID.data)
        ok = False
    except ValueError:
        pass
    return ok, {"tris": tris.shape}


@check("edge_faces_mesh_vs_numpy")
def _():
    me = SPHERE.data
    ef = meshdata.edge_faces(me)
    tris = meshdata.get_triangles(me)
    ef2 = meshdata.edge_faces_from_triangles(tris, len(me.vertices))
    ok = len(ef.edges) == len(ef2.edges) and (ef.face_count == 2).all() and (ef2.face_count == 2).all()
    ok &= ef.indptr[-1] == len(me.loops)
    # every face listed for an edge really contains both edge vertices
    e = 17
    for f in ef.faces_of(e):
        ok &= set(ef.edges[e].tolist()) <= set(tris[f].tolist())
    return ok, {"E": len(ef.edges)}


@check("boundary_loops_and_order")
def _():
    me = GRID.data
    ef = meshdata.edge_faces(me)
    co = meshdata.get_positions(me)
    loops = meshdata.boundary_loops(ef.edges, ef.boundary, co)
    ok = loops.count == 2 and loops.edge_count[0] == 40 and loops.edge_count[1] >= 4
    ok &= len(loops.holes(5)) == 2 and len(loops.holes(10)) == 1  # outer rim (40) + inner hole (8)
    cyc = meshdata.order_loop_vertices(ef.edges[loops.loop_edge_ids(0)])
    ok &= cyc is not None and len(cyc) == 40 and len(set(cyc.tolist())) == 40
    return ok, {"loops": loops.edge_count.tolist(), "perimeter": loops.perimeter.round(4).tolist()}


@check("components_and_islands")
def _():
    # sphere + two separate triangles
    co = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [5, 0, 0], [6, 0, 0], [5, 1, 0], [6, 1, 0]], float)
    tris = np.array([[0, 1, 2], [3, 4, 5], [4, 6, 5]])
    ef = meshdata.edge_faces_from_triangles(tris, len(co))
    flab, sizes = meshdata.face_components(len(co), ef.edges, tris[:, 0])
    ok = sizes.tolist() == [2, 1] and flab.tolist() == [1, 0, 0]
    lab = meshdata.connected_components(6, np.array([0, 2, 4]), np.array([1, 3, 5]))
    ok &= len(set(lab.tolist())) == 3 and lab[0] == lab[1] and lab[2] == lab[3]
    return ok, {"sizes": sizes.tolist()}


@check("inconsistent_winding")
def _():
    me = CUBE.data.copy()
    obj = bpy.data.objects.new("cube_flip", me)
    bpy.context.scene.collection.objects.link(obj)
    lv0 = meshdata.get_loop_vertices(me)
    ef = meshdata.edge_faces(me)
    n0 = len(meshdata.inconsistent_winding_edges(ef, lv0))
    me.polygons[0].flip()
    me.update()
    ef = meshdata.edge_faces(me)
    n1 = len(meshdata.inconsistent_winding_edges(ef, meshdata.get_loop_vertices(me)))
    tris = meshdata.get_triangles(me)
    ef2 = meshdata.edge_faces_from_triangles(tris, len(me.vertices))
    n2 = len(meshdata.inconsistent_winding_edges(ef2, tris.reshape(-1)))
    return n0 == 0 and n1 == 3 and n2 == 3, {"before": n0, "after": n1, "numpy": n2}


@check("adjacency_csr")
def _():
    me = SPHERE.data
    edges = meshdata.get_edges(me)
    indptr, idx = meshdata.vertex_adjacency(len(me.vertices), edges)
    deg = np.diff(indptr)
    ok = deg.sum() == 2 * len(edges) and set(np.unique(deg).tolist()) == {5, 6}
    v = 0
    nbrs = set(idx[indptr[v]:indptr[v + 1]].tolist())
    expect = {int(b) for a, b in edges.tolist() if a == v} | {int(a) for a, b in edges.tolist() if b == v}
    ok &= nbrs == expect
    ef = meshdata.edge_faces(me)
    fptr, fidx = meshdata.face_adjacency(ef, len(me.polygons))
    ok &= (np.diff(fptr) == 3).all()
    lv = meshdata.get_loop_vertices(me)
    _, total = meshdata.get_polygon_loops(me)
    vptr, vf = meshdata.vertex_faces(len(me.vertices), lv, meshdata.loop_faces(total))
    ok &= set(np.unique(np.diff(vptr)).tolist()) == {5, 6}
    return ok, {"deg": np.unique(deg).tolist()}


@check("face_adjacency_non_manifold")
def _():
    # three triangles sharing edge (0,1)
    tris = np.array([[0, 1, 2], [1, 0, 3], [0, 1, 4]])
    ef = meshdata.edge_faces_from_triangles(tris, 5)
    ptr, idx = meshdata.face_adjacency(ef, 3, include_non_manifold=True)
    ptr0, _ = meshdata.face_adjacency(ef, 3)
    return int(ef.non_manifold.sum()) == 1 and ptr[-1] == 4 and ptr0[-1] == 0, {"pairs": int(ptr[-1])}


@check("weld_by_distance")
def _():
    obj = mesh_from_bmesh("split", lambda bm: (bmesh.ops.create_grid(bm, x_segments=4, y_segments=4, size=1.0),
                                               bmesh.ops.split(bm, geom=bm.faces[:3], use_only_faces=False)))
    me = obj.data
    v0 = len(me.vertices)
    dup = meshdata.count_duplicate_positions(meshdata.get_positions(me))
    merged = meshdata.weld_by_distance(me, 1e-6)
    return merged == dup and merged > 0 and len(me.vertices) == v0 - merged, {"merged": merged, "dup": dup}


@check("snapshot_restore")
def _():
    obj = mesh_from_bmesh("snap", lambda bm: bmesh.ops.create_icosphere(bm, subdivisions=2, radius=1.0))
    co = meshdata.get_positions(obj.data)
    snap = meshdata.snapshot_mesh(obj)
    meshdata.set_positions(obj.data, co + 1.0)
    name = obj.data.name
    meshdata.restore_mesh(obj, snap)
    ok = np.allclose(meshdata.get_positions(obj.data), co) and obj.data.name == name
    n_before = len(bpy.data.meshes)
    snap2 = meshdata.snapshot_mesh(obj)
    ok &= len(bpy.data.meshes) == n_before + 1 and snap2.use_fake_user
    meshdata.discard_mesh(snap2)
    ok &= len(bpy.data.meshes) == n_before
    return ok, {"meshes": len(bpy.data.meshes)}


@check("attributes")
def _():
    me = SPHERE.data
    n = len(me.vertices)
    f = np.linspace(0, 1, n).astype(np.float32)
    b = (np.arange(n) % 3 == 0)
    v3 = np.random.default_rng(0).random((n, 3)).astype(np.float32)
    i = np.arange(len(me.polygons), dtype=np.int32)
    meshdata.set_attribute(me, "amt_f", f)
    meshdata.set_attribute(me, "amt_b", b)
    meshdata.set_attribute(me, "amt_v", v3)
    meshdata.set_attribute(me, "amt_i", i, domain="FACE")
    ok = np.allclose(meshdata.get_attribute(me, "amt_f"), f)
    ok &= (meshdata.get_attribute(me, "amt_b") == b).all()
    ok &= np.allclose(meshdata.get_attribute(me, "amt_v"), v3)
    ok &= (meshdata.get_attribute(me, "amt_i") == i).all()
    meshdata.set_attribute(me, "amt_f", b)  # type change -> recreated as BOOLEAN
    ok &= me.attributes["amt_f"].data_type == "BOOLEAN"
    removed = meshdata.remove_attributes(me, "amt_")
    ok &= sorted(removed) == ["amt_b", "amt_f", "amt_i", "amt_v"] and meshdata.get_attribute(me, "amt_f") is None
    return ok, {"removed": removed}


@check("smoothing_primitives")
def _():
    me = SPHERE.data
    co = meshdata.get_positions(me)
    adj = meshdata.vertex_adjacency(len(co), meshdata.get_edges(me))
    rng = np.random.default_rng(1)
    noisy = co * (1.0 + rng.normal(scale=0.02, size=(len(co), 1)))
    fixed = np.zeros(len(co), bool)
    fixed[:10] = True
    lap = meshdata.laplacian_fair(noisy, adj, fixed, iterations=5, lam=0.5)
    tau = meshdata.taubin(noisy, adj, None, iterations=10)
    r_noisy = np.linalg.norm(noisy, axis=1).std()
    r_lap = np.linalg.norm(lap, axis=1)
    r_tau = np.linalg.norm(tau, axis=1)
    ok = np.allclose(lap[:10], noisy[:10]) and r_lap.std() < r_noisy and r_tau.std() < r_noisy
    ok &= abs(r_tau.mean() - 1.0) < abs(r_lap.mean() - 1.0) + 1e-9  # Taubin shrinks less
    return ok, {"std_noisy": round(float(r_noisy), 5), "std_lap": round(float(r_lap.std()), 5),
                "std_taubin": round(float(r_tau.std()), 5), "mean_lap": round(float(r_lap.mean()), 5),
                "mean_taubin": round(float(r_tau.mean()), 5)}


@check("bvh_nearest_and_sampling")
def _():
    me = SPHERE.data
    co = meshdata.get_positions(me)
    tris = meshdata.get_triangles(me)
    bvh = meshdata.bvh_from_arrays(co, tris)
    bvh2 = meshdata.bvh_from_mesh(me)
    pts, idx = meshdata.sample_surface(co, tris, 500, seed=3)
    d_on, _, face = meshdata.nearest_on_surface(bvh, pts, max_dist=0.01)
    vn = meshdata.vertex_normals_from_triangles(co, tris)
    off = co[:200] + vn[:200] * 0.05
    d_off, _, _ = meshdata.nearest_on_surface(bvh2, off, max_dist=0.001)  # forces the fallback re-query
    ok = d_on.max() < 1e-5 and np.allclose(d_off, 0.05, atol=2e-3) and (face >= 0).all()
    areas = meshdata.triangle_areas(co, tris)
    ok &= abs(areas.sum() - meshdata.get_face_areas(me).sum()) < 1e-4
    nrm = meshdata.triangle_normals(co, tris)
    ok &= np.allclose(nrm, meshdata.get_face_normals(me), atol=1e-4)
    return ok, {"d_on_max": float(d_on.max()), "d_off_mean": float(d_off.mean())}


@check("image_sampling")
def _():
    img = bpy.data.images.new("grad", 16, 8, alpha=True)
    yy, xx = np.mgrid[0:8, 0:16]
    px = np.zeros((8, 16, 4), np.float32)
    px[..., 0] = xx / 15.0
    px[..., 1] = yy / 7.0
    px[..., 3] = 1.0
    img.pixels.foreach_set(px.ravel())
    pix = meshdata.image_pixels(img)
    uv = np.array([[(3 + 0.5) / 16, (5 + 0.5) / 8], [0.0, 0.0]], np.float32)
    s = meshdata.sample_image(pix, uv)
    sn = meshdata.sample_image(pix, uv, bilinear=False, wrap=False)
    tol = 1.5 / 255  # 8-bit image buffer
    ok = pix.shape == (8, 16, 4) and abs(s[0, 0] - 3 / 15) < tol and abs(s[0, 1] - 5 / 7) < tol
    ok &= abs(sn[1, 0]) < 1e-6
    mat = bpy.data.materials.new("m")
    if mat.node_tree is None:
        mat.use_nodes = True
    nt = mat.node_tree
    bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = img
    nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    ok &= meshdata.find_base_color_image(mat) == img and meshdata.material_images(mat) == [img]
    return ok, {"sample": s[0].tolist()}


@check("custom_normals_clear")
def _():
    if not A.glb:
        return True, "skipped (no --glb)"
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=A.glb, merge_vertices=False, import_shading="NORMALS")
    obj = next(o for o in bpy.data.objects if o not in before and o.type == "MESH")
    had = obj.data.has_custom_normals
    removed = meshdata.clear_custom_normals(obj.data)
    uv = meshdata.get_uvs(obj.data)
    ok = had and removed and not obj.data.has_custom_normals and uv is not None and uv.shape[1] == 2
    meshdata.set_uvs(obj.data, uv)
    return ok, {"had": had, "removed": removed}


print("@@SELFTEST@@" + json.dumps({"results": RESULTS, "errors": ERRORS}), flush=True)
