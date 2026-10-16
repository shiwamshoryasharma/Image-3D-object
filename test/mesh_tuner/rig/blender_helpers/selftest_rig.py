"""Unit tests of ``img3d_amt.rig`` building blocks, run INSIDE Blender 5.2 (pure numpy parts + small bpy scenes).

blender --background --factory-startup --python-exit-code 86 --python selftest_rig.py

Prints ``@@SELFTEST@@{"passed": [...], "failed": {name: traceback}}``; exit code 0 even on failures (the pytest
wrapper asserts on the JSON).
"""

import json
import math
import os
import sys
import traceback
from types import SimpleNamespace

import bpy
import numpy as np
from mathutils import Matrix

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "..", "..", "..", "backend", "blender_scripts")))

from img3d_amt import rig  # noqa: E402

TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


def close(a, b, tol=1e-9):
    return abs(float(a) - float(b)) <= tol


def line_graph(n):
    co = np.stack([np.arange(n, dtype=float), np.zeros(n), np.zeros(n)], 1)
    edges = np.stack([np.arange(n - 1), np.arange(1, n)], 1)
    return co, rig.MeshGraph.build(co, edges)


def grid(nx, ny, step=1.0):
    co = np.array([(x * step, y * step, 0.0) for y in range(ny) for x in range(nx)])
    edges = []
    for y in range(ny):
        for x in range(nx):
            i = y * nx + x
            if x + 1 < nx:
                edges.append((i, i + 1))
            if y + 1 < ny:
                edges.append((i, i + nx))
    return co, rig.MeshGraph.build(co, np.array(edges))


# ---------------------------------------------------------------------------------------------------- numpy parts


@test
def padded_prune_limit_normalize():
    v = np.array([0, 0, 0, 0, 0, 0, 1, 1, 2])
    g = np.array([0, 1, 2, 3, 4, 5, 0, 1, 2])
    w = np.array([0.5, 0.3, 0.2, 0.1, 0.05, 1e-5, 0.6, 0.6, np.nan])
    pw = rig.PaddedWeights.from_entries(3, v, g, w)
    assert pw.width == 6 and pw.counts().tolist() == [6, 2, 0], pw.counts()   # NaN dropped
    assert pw.group[0, 0] == 0 and pw.weight[0, 0] == 0.5                       # sorted descending
    assert pw.prune(1e-3) == 1 and pw.counts()[0] == 5
    over = pw.limit(4)
    assert over.tolist() == [True, False, False] and pw.width == 4
    assert set(pw.group[0].tolist()) == {0, 1, 2, 3}
    pw.normalize()
    assert close(pw.sums()[0], 1.0) and close(pw.sums()[1], 1.0) and pw.sums()[2] == 0.0
    vv, gg, ww = pw.entries()
    assert len(vv) == 6 and (ww > 0).all()


@test
def accumulate_rows_sums_and_normalizes():
    rows = np.array([0, 0, 1])
    groups = np.array([[0, 1], [1, -1], [2, -1]])
    weights = np.array([[1.0, 1.0], [2.0, 0.0], [3.0, 0.0]])
    r, g, w = rig.accumulate_rows(rows, groups, weights, n_groups=3, width=4)
    assert r.tolist() == [0, 1]
    assert g[0, :2].tolist() == [1, 0] and np.allclose(w[0, :2], [0.75, 0.25]) and g[0, 2] == -1
    assert g[1, 0] == 2 and close(w[1, 0], 1.0)


@test
def fill_propagates_along_graph():
    co, graph = line_graph(7)
    pw = rig.PaddedWeights.from_entries(7, np.array([0, 6]), np.array([0, 1]), np.array([1.0, 1.0]), width=4)
    info = rig.fill_unweighted(pw, graph, co, n_groups=2, min_weight=1e-3)
    assert info["filled_verts"] == 5 and info["rigid_verts"] == 0 and info["unfillable_verts"] == 0
    assert (pw.counts() > 0).all()
    dense = np.zeros((7, 2))
    for v, g, w in zip(*pw.entries()):
        dense[v, g] += w
    assert np.allclose(dense.sum(1), 1.0)
    assert dense[1, 0] == 1.0 and dense[5, 1] == 1.0 and np.allclose(dense[3], [0.5, 0.5]), dense


@test
def fill_attaches_disconnected_parts_rigidly():
    co = np.array([[0, 0, 0], [1, 0, 0], [1.2, 0, 0], [1.4, 0, 0]], float)
    graph = rig.MeshGraph.build(co, np.array([[0, 1], [2, 3]]))
    pw = rig.PaddedWeights.from_entries(4, np.array([0, 1, 1]), np.array([0, 0, 1]), np.array([1.0, 0.7, 0.3]),
                                        width=4)
    info = rig.fill_unweighted(pw, graph, co, n_groups=2, min_weight=1e-3)
    assert info["rigid_verts"] == 2 and info["rigid_parts"] == 1 and info["rigid_mask"][[2, 3]].all()
    for v in (2, 3):
        assert pw.group[v, :2].tolist() == [0, 1] and np.allclose(pw.weight[v, :2], [0.7, 0.3])


@test
def smoothing_only_touches_masked_vertices():
    co, graph = line_graph(5)
    pw = rig.PaddedWeights.from_entries(5, np.arange(5), np.array([0, 0, 1, 1, 1]), np.ones(5), width=4)
    before = pw.copy()
    mask = np.array([False, False, True, False, False])
    n = rig.smooth_weights(pw, graph, mask, 2, iterations=3, factor=0.5, min_weight=1e-3)
    assert n == 1
    assert np.array_equal(pw.group[[0, 1, 3, 4]], before.group[[0, 1, 3, 4]])
    assert np.array_equal(pw.weight[[0, 1, 3, 4]], before.weight[[0, 1, 3, 4]])
    d = {int(g): float(w) for g, w in zip(pw.group[2], pw.weight[2]) if g >= 0}
    assert set(d) == {0, 1} and close(sum(d.values()), 1.0) and 0.2 < d[0] < 0.8, d


@test
def isolated_far_influence_is_flagged_but_tip_is_not():
    co, graph = grid(5, 5, step=0.1)                       # 0.4 x 0.4 plate, centre vertex 12 at (0.2, 0.2)
    heads = np.array([[0.2, 0.2, -0.05], [3.0, 0.2, 0.0]])  # bone 0 just under the plate, bone 1 far away
    tails = np.array([[0.2, 0.2, -0.04], [3.5, 0.2, 0.0]])
    ok = np.array([True, True])
    v = np.arange(25)
    g = np.zeros(25, int)
    g[12] = 1
    pw = rig.PaddedWeights.from_entries(25, v, g, np.ones(25))
    fv, fs = rig.isolated_far_influences(pw, graph, co, heads, tails, ok)
    assert fv.tolist() == [12] and pw.group[12, fs[0]] == 1
    # the same isolated influence on a bone that ends AT the vertex (e.g. a tail tip) is legitimate
    heads2 = heads.copy()
    tails2 = tails.copy()
    heads2[1], tails2[1] = (0.2, 0.2, 0.3), (0.2, 0.2, 0.0)
    fv2, _ = rig.isolated_far_influences(pw, graph, co, heads2, tails2, ok)
    assert len(fv2) == 0, fv2


@test
def stray_small_far_islands_are_flagged():
    co, graph = line_graph(6)                                    # vertices at x = 0..5
    co[:, 2] = 0.06
    heads = np.array([[-0.5, 0, 0], [2.5, 0, 0], [20.0, 0, 0]])  # bone 0: x -0.5..2.5, bone 1: 2.5..5.5, bone 2 far
    tails = np.array([[2.5, 0, 0], [5.5, 0, 0], [21.0, 0, 0]])
    ok = np.array([True, True, True])
    v = np.array([0, 0, 1, 1, 2, 3, 4, 5, 5])
    g = np.array([0, 2, 0, 1, 0, 1, 1, 1, 2])
    w = np.array([0.95, 0.05, 0.92, 0.08, 1.0, 1.0, 1.0, 0.7, 0.3])
    pw = rig.PaddedWeights.from_entries(6, v, g, w)
    sv, ss = rig.stray_far_influences(pw, graph, co, heads, tails, ok)
    # v0: 5 % on the far bone 2, detached from bone 2's main region (v5) -> stray
    # v1: 8 % on bone 1 - an island too, but bone 1 starts 1.5 away (not "far") -> kept; v5: 30 % main region -> kept
    assert sv.tolist() == [0] and pw.group[0, ss[0]] == 2, (sv, ss)
    # a smooth gradient of a far bone over the whole strip is one region (no detached island) -> nothing flagged
    v2 = np.repeat(np.arange(6), 2)
    g2 = np.tile([0, 2], 6)
    w2 = np.ravel([[1.0 - s, s] for s in np.linspace(0.01, 0.06, 6)])
    pw2 = rig.PaddedWeights.from_entries(6, v2, g2, w2)
    sv2, _ = rig.stray_far_influences(pw2, graph, co, heads, tails, ok)
    assert len(sv2) == 0, sv2


@test
def weight_l1_distance():
    co, graph = line_graph(3)
    pw = rig.PaddedWeights.from_entries(3, np.array([0, 1, 2]), np.array([0, 1, 0]), np.ones(3), width=4)
    l1 = rig.weight_l1_to_neighbours(pw, graph, np.array([1, 0]))
    assert close(l1[0], 2.0) and close(l1[1], 2.0)


@test
def measure_pose_ratios_collapse_and_spike():
    co, _ = grid(4, 4, step=1.0)
    mesh = bpy.data.meshes.new("t")
    faces = [(y * 4 + x, y * 4 + x + 1, (y + 1) * 4 + x + 1, (y + 1) * 4 + x) for y in range(3) for x in range(3)]
    mesh.from_pydata(co.tolist(), [], faces)
    topo = rig.PoseTopology.build(mesh, co)
    posed = co.copy()
    posed[5, 2] += 10.0                                       # one interior vertex pops out
    spike = np.zeros(16)
    m = rig.measure_pose(topo, co, posed, spike)
    assert m["moved_verts"] == 1 and m["affected_edges"] == 4 and m["max_stretch"] > 9.0
    assert m["stretched_edges"] == 4 and m["compressed_edges"] == 0
    assert spike[5] > 9.0 and spike[np.arange(16) != 5].max() == 0.0, spike
    squashed = co.copy()
    squashed[:, 1] *= 0.01                                    # every quad collapses to 1 % of its area
    m2 = rig.measure_pose(topo, co, squashed)
    assert m2["collapsed_tris"] == 18 and m2["compressed_edges"] > 0
    bpy.data.meshes.remove(mesh)


@test
def pose_check_statuses():
    s = rig.RigSettings()
    good = {"max_influences": 4, "over_limit_verts": 0, "unnormalized_verts": 0, "unweighted_verts": 0}

    def summary(ratio, bad=0.0, collapsed=0, pct=0.0):
        return {"max_stretch": ratio, "bad_edge_pct": bad, "collapsed_tris": collapsed, "collapsed_pct": pct,
                "bones_tested": 3, "rotations_per_bone": 4, "stretched_edges": 0, "compressed_edges": 0,
                "worst_bones": [{"bone": "b", "max_stretch": ratio}]}

    st = lambda checks: {c["id"]: c["status"] for c in checks}  # noqa: E731
    assert st(rig.build_pose_checks(summary(30.0), summary(1.4), good, s)) == {
        "rig_weights": "pass", "pose_stretch": "pass", "pose_collapse": "pass"}
    assert st(rig.build_pose_checks(None, summary(3.0), good, s))["pose_stretch"] == "warn"
    assert st(rig.build_pose_checks(None, summary(1.5, bad=2.0), good, s))["pose_stretch"] == "warn"
    assert st(rig.build_pose_checks(None, summary(6.0), good, s))["pose_stretch"] == "fail"
    assert st(rig.build_pose_checks(None, summary(1.2, collapsed=3, pct=0.1), good, s))["pose_collapse"] == "warn"
    assert st(rig.build_pose_checks(None, summary(1.2, collapsed=99, pct=5.0), good, s))["pose_collapse"] == "fail"
    bad_w = dict(good, unweighted_verts=3)
    checks = rig.build_pose_checks(None, None, bad_w, s)
    assert [c["id"] for c in checks] == ["rig_weights"] and checks[0]["status"] == "fail"
    assert st(rig.build_pose_checks(None, None, dict(good, unnormalized_verts=1), s))["rig_weights"] == "warn"
    for c in rig.build_pose_checks(summary(9.0), summary(1.1), good, s):
        assert set(c) == {"id", "label", "status", "value", "threshold", "message"}
    assert rig.stretch_pct(2.0) == 100.0 and rig.stretch_pct(0.9) == 0.0 and rig.stretch_pct(None) is None


@test
def settings_from_options():
    s = rig.RigSettings.from_options(SimpleNamespace(rig_min_weight=0.01, rig_max_influences=99,
                                                     rig_pose_checks=False, rig_smooth_iterations="2"))
    assert s.min_weight == 0.01 and s.max_influences == 8 and s.pose_checks is False and s.smooth_iterations == 2
    d = rig.RigSettings.from_options(SimpleNamespace())
    assert d == rig.RigSettings()


# ---------------------------------------------------------------------------------------------------- bpy parts


def make_skinned_strip():
    """A 6x2 quad strip along +X skinned to a 2-bone chain (+ subsurf modifier) -> (obj, arm)."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    co = [(x * 0.2, y * 0.2, 0.0) for y in range(2) for x in range(6)]
    faces = [(x, x + 1, x + 7, x + 6) for x in range(5)]
    me = bpy.data.meshes.new("strip")
    me.from_pydata(co, [], faces)
    obj = bpy.data.objects.new("strip", me)
    bpy.context.scene.collection.objects.link(obj)
    arm = bpy.data.objects.new("rig", bpy.data.armatures.new("rig"))
    bpy.context.scene.collection.objects.link(arm)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    a = arm.data.edit_bones.new("A")
    a.head, a.tail = (0, 0.1, 0), (0.5, 0.1, 0)
    b = arm.data.edit_bones.new("B")
    b.head, b.tail = (0.5, 0.1, 0), (1.0, 0.1, 0)
    b.parent = a
    c = arm.data.edit_bones.new("ctrl")
    c.head, c.tail = (0, 1, 0), (0, 1.2, 0)
    c.use_deform = False
    bpy.ops.object.mode_set(mode="OBJECT")
    ga = obj.vertex_groups.new(name="A")
    gb = obj.vertex_groups.new(name="B")
    for v in me.vertices:
        t = v.co.x / 1.0
        ga.add([v.index], 1.0 - t, "REPLACE")
        gb.add([v.index], t, "REPLACE")
    obj.vertex_groups.new(name="empty_extra")
    obj.vertex_groups.new(name="ctrl")
    obj.vertex_groups.new(name="masked_ref")
    paint = obj.vertex_groups.new(name="paint")
    paint.add([0, 1], 0.5, "REPLACE")
    mod = obj.modifiers.new("Armature", "ARMATURE")
    mod.object = arm
    obj.parent = arm
    sub = obj.modifiers.new("Subdivision", "SUBSURF")
    sub.levels = 1
    disp = obj.modifiers.new("Displace", "DISPLACE")
    disp.vertex_group = "masked_ref"
    disp.strength = 0.0
    return obj, arm


@test
def bindings_write_weights_and_drop_empty_groups():
    obj, arm = make_skinned_strip()
    ctx = SimpleNamespace(mesh_objects=[obj], armatures=[arm])
    bindings, n_arm = rig.find_bindings(ctx)
    assert n_arm == 1 and len(bindings) == 1
    b = bindings[0]
    assert b.group_names[:2] == ["A", "B"] and b.deform.tolist() == [True, True, False, False, False, False]
    assert b.bone_ok[:2].all() and np.allclose(b.heads[1], [0.5, 0.1, 0.0])
    raw = rig.read_weights(obj)
    new = rig.PaddedWeights.from_entries(12, np.arange(12), np.zeros(12, int), np.ones(12), width=4)
    info = rig.write_weights(obj, raw, new, np.array([0, 1]))
    v, g, w = rig.read_weights(obj)
    deform = g < 2
    assert set(g[deform].tolist()) == {0} and np.allclose(w[deform], 1.0) and info["entries_removed"] > 0
    assert sorted(zip(v[g == 5].tolist(), w[g == 5].tolist())) == [(0, 0.5), (1, 0.5)]   # paint untouched
    removed = rig.remove_empty_groups(obj, {"A", "B"})
    names = [gr.name for gr in obj.vertex_groups]
    assert sorted(removed) == ["ctrl", "empty_extra"], removed
    assert names == ["A", "B", "masked_ref", "paint"], names                           # B kept (deform bone)


@test
def pose_evaluator_restores_everything():
    obj, arm = make_skinned_strip()
    pa, pb = arm.pose.bones["A"], arm.pose.bones["B"]
    pa.rotation_mode = "XYZ"
    pa.rotation_euler = (0.1, 0.2, 0.3)
    pa.location = (0.01, 0.0, 0.02)
    pb.rotation_quaternion = (0.9, 0.1, 0.2, 0.3)
    con = pb.constraints.new("LIMIT_ROTATION")
    con.mute = False
    arm.data.pose_position = "REST"
    snapshot = {p.name: (p.rotation_mode, tuple(p.location), tuple(p.rotation_quaternion),
                         tuple(p.rotation_euler), tuple(p.scale)) for p in arm.pose.bones}
    n = len(obj.data.vertices)
    with rig.PoseEvaluator(obj, [arm]) as ev:
        assert ev.rest.shape == (n, 3), ev.rest.shape                     # subsurf hidden during the tests
        assert np.allclose(ev.rest, rig.meshdata.get_positions(obj.data), atol=1e-6)   # identity pose == bind
        assert con.mute is True and arm.data.pose_position == "POSE"
        ev.set_rotation(arm, "B", "Z", math.radians(45))
        posed = ev.evaluate()
        assert np.abs(posed - ev.rest).max() > 0.05
    after = {p.name: (p.rotation_mode, tuple(p.location), tuple(p.rotation_quaternion),
                      tuple(p.rotation_euler), tuple(p.scale)) for p in arm.pose.bones}
    assert after == snapshot, (after, snapshot)
    assert con.mute is False and arm.data.pose_position == "REST"
    assert [m.show_viewport for m in obj.modifiers] == [True, True, True]
    assert ev.restored is True and ev.restore_error == 0.0


@test
def pose_evaluator_with_animated_armature():
    """An action keyed on the armature must not override the test pose (channels set via RNA stay until a frame
    change) and the keyed pose must come back afterwards."""
    obj, arm = make_skinned_strip()
    obj.modifiers.remove(obj.modifiers["Subdivision"])
    pb = arm.pose.bones["B"]
    pb.rotation_quaternion = (0.92388, 0.0, 0.0, 0.38268)       # 45 deg about Z
    pb.keyframe_insert("rotation_quaternion", frame=1)
    bpy.context.scene.frame_set(1)
    keyed = rig.meshdata.get_positions(obj.evaluated_get(bpy.context.evaluated_depsgraph_get()).data)
    bind = rig.meshdata.get_positions(obj.data)
    assert np.abs(keyed - bind).max() > 0.05                    # the action really deforms the mesh
    with rig.PoseEvaluator(obj, [arm]) as ev:
        assert np.allclose(ev.rest, bind, atol=1e-6), np.abs(ev.rest - bind).max()
        ev.set_rotation(arm, "A", "X", math.radians(45))
        assert np.abs(ev.evaluate() - ev.rest).max() > 0.05
    assert ev.restored is True, ev.restore_error
    assert np.allclose(tuple(pb.rotation_quaternion), (0.92388, 0.0, 0.0, 0.38268), atol=1e-6)


@test
def pose_tests_on_strip():
    obj, arm = make_skinned_strip()
    ctx = SimpleNamespace(mesh_objects=[obj], armatures=[arm])
    (b,), _ = rig.find_bindings(ctx)
    v, g, w = rig.read_weights(obj)
    keep = b.deform[g]
    pw = rig.PaddedWeights.from_entries(len(obj.data.vertices), v[keep], g[keep], w[keep])
    bones = rig.select_pose_bones(b, pw, 40, len(obj.data.vertices))
    assert [x[1] for x in bones] == ["A", "B"] or [x[1] for x in bones] == ["B", "A"], bones
    summary = rig.run_pose_tests(b, bones, 45.0, collect_spikes=True)
    assert summary["bones_tested"] == 2 and summary["rotations_per_bone"] == 4 and summary["restored"] is True
    assert summary["spike_score"].shape == (len(obj.data.vertices),)
    assert 1.0 <= summary["max_stretch"] < 2.0, summary["max_stretch"]
    assert summary["worst_bones"][0]["bone"] in ("A", "B")


def main():
    passed, failed = [], {}
    for fn in TESTS:
        try:
            fn()
            passed.append(fn.__name__)
        except Exception:
            failed[fn.__name__] = traceback.format_exc()
    print("@@SELFTEST@@" + json.dumps({"passed": passed, "failed": failed}), flush=True)


main()
