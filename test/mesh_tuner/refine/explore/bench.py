"""Exploration (test only): micro-benchmarks of numpy ops at TRELLIS scale inside Blender's Python."""
import time

import numpy as np

V, T = 907_534, 1_891_444
rng = np.random.default_rng(0)
co = rng.random((V, 3))
tris = rng.integers(0, V, (T, 3))


def tm(label, fn, n=3):
    t = time.perf_counter()
    for _ in range(n):
        fn()
    print(f"{label:40s} {(time.perf_counter() - t) / n * 1000:8.1f} ms")


tm("gather co[tris[:,0]] f64", lambda: co[tris[:, 0]])
co32 = co.astype(np.float32)
tris32 = tris.astype(np.int32)
tm("gather co32[tris32[:,0]]", lambda: co32[tris32[:, 0]])
p0, p1, p2 = co[tris[:, 0]], co[tris[:, 1]], co[tris[:, 2]]
tm("cross f64", lambda: np.cross(p1 - p0, p2 - p0))
q0, q1, q2 = p0.astype(np.float32), p1.astype(np.float32), p2.astype(np.float32)
tm("cross f32", lambda: np.cross(q1 - q0, q2 - q0))


def cross_manual(a, b):
    return np.stack([a[:, 1] * b[:, 2] - a[:, 2] * b[:, 1], a[:, 2] * b[:, 0] - a[:, 0] * b[:, 2],
                     a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]], 1)


tm("cross manual f64", lambda: cross_manual(p1 - p0, p2 - p0))
w = rng.random(T)
v = tris[:, 0]
tm("bincount 1.9M -> 0.9M", lambda: np.bincount(v, weights=w, minlength=V))
v32 = v.astype(np.int32)
tm("bincount int32 idx", lambda: np.bincount(v32, weights=w, minlength=V))
flat = tris.ravel()
w3 = rng.random(3 * T)
tm("bincount 5.7M -> 0.9M", lambda: np.bincount(flat, weights=w3, minlength=V))
tm("einsum ij,ij->i 1.9M", lambda: np.einsum("ij,ij->i", p0, p1))
tm("sum(a*b,1) 1.9M", lambda: (p0 * p1).sum(1))
tm("norm 1.9M", lambda: np.linalg.norm(p0, axis=1))
tm("sqrt(einsum) 1.9M", lambda: np.sqrt(np.einsum("ij,ij->i", p0, p0)))
# column-major layout
cx, cy, cz = co[:, 0].copy(), co[:, 1].copy(), co[:, 2].copy()
tm("gather 3 cols", lambda: (cx[v], cy[v], cz[v]))
tm("argsort 5.7M stable", lambda: np.argsort(flat, kind="stable"), n=1)
order = np.argsort(flat, kind="stable")
tm("np.add.reduceat CSR", lambda: np.add.reduceat(w3[order], np.arange(0, 3 * T, 6)), n=3)
