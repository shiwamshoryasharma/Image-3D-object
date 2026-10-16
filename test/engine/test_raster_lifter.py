"""Raster label lifter (part-aware DESIGN.md sections 3.7, 4.6.4, 4.6.5 and 10.2; slice S7).

Camera math and the triangle-id decode run everywhere. The GPU tests need CUDA + nvdiffrast and must run under the
GPU lock (from the repository root)::

    backend\\python\\python.exe -s test\\part_aware\\gpu_lock.py --label s7 -- ^
        backend\\python\\python.exe -s -m pytest -c test\\pytest.ini test\\engine\\test_raster_lifter.py -q -p no:cacheprovider

They cover: face-ID decode (also above 2^24 on a 16.8 M-face mesh), the camera distance rule at FOV 10°/50°, the front
camera orientation (image-right = +X, rows top-down), a 2 M-face raster, the silhouette fit on a synthetic textured
mesh (IoU >= 0.9), the textured render against the atlas colour, the CPU fake against the real raster, and the camera
fit on the real ``alice_front`` / ``anime_alice`` fixtures of ``test/part_aware/fixtures.json``. The fixtures are
copied into ``test/.runtime/s7/<case>/`` first (never read from ``backend/runtime/jobs``); the fit results and
overlays go to ``test/outputs/part_aware/s7/``.
"""

from __future__ import annotations

import io
import json
import logging
import re
import shutil
import struct
import time
from pathlib import Path

import numpy as np
import pytest
from fakes_lifting import FakeLabelLifter, lifter_spec
from PIL import Image

from inference.base import CancellationToken
from inference.parts import cameras
from inference.parts.base import CameraGrid, CameraSpec, MeshArrays

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_JSON = PROJECT_ROOT / "test" / "part_aware" / "fixtures.json"
RUNTIME_DIR = PROJECT_ROOT / "test" / ".runtime" / "s7"
OUTPUT_DIR = PROJECT_ROOT / "test" / "outputs" / "part_aware" / "s7"
EXCLUDED_JOB_IDS = frozenset({"job_269a864ac597", "job_fa0c4771ec03"})
FIXTURE_CASES = ("alice_front", "anime_alice")

log = logging.getLogger("test.raster_lifter")


# --------------------------------------------------------------------------------------------------
# synthetic meshes
# --------------------------------------------------------------------------------------------------


def quadrant_mesh() -> MeshArrays:
    """Four quads in the z=0 plane facing +Z: faces 2k, 2k+1 form quadrant k = TL(-x,+y), TR, BL, BR."""
    positions, faces, uvs = [], [], []
    for qy, qx in ((1, -1), (1, 1), (-1, -1), (-1, 1)):
        base = len(positions)
        x0, x1 = (0.0, 0.5) if qx > 0 else (-0.5, 0.0)
        y0, y1 = (0.0, 0.5) if qy > 0 else (-0.5, 0.0)
        for x, y in ((x0, y1), (x1, y1), (x0, y0), (x1, y0)):  # TL, TR, BL, BR (counter-clockwise faces below)
            positions.append((x, y, 0.0))
            uvs.append((x + 0.5, 0.5 - y))  # glTF UV: (0, 0) = top-left of the atlas
        faces += [(base, base + 2, base + 1), (base + 1, base + 2, base + 3)]
    return MeshArrays(
        positions=np.asarray(positions, dtype=np.float32),
        faces=np.asarray(faces, dtype=np.uint32),
        uv=np.asarray(uvs, dtype=np.float32),
        atlas_rgb=None,
    )


def box(lo: tuple[float, float, float], hi: tuple[float, float, float]) -> tuple[np.ndarray, np.ndarray]:
    x0, y0, z0 = lo
    x1, y1, z1 = hi
    v = np.array(
        [[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0], [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]],
        dtype=np.float32,
    )
    f = np.array(
        [[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4],
         [3, 6, 2], [3, 7, 6], [0, 4, 7], [0, 7, 3], [1, 2, 6], [1, 6, 5]],
        dtype=np.int64,
    )
    return v, f


def subdivide(positions: np.ndarray, faces: np.ndarray, times: int) -> tuple[np.ndarray, np.ndarray]:
    """Midpoint subdivision without vertex sharing (keeps the silhouette, multiplies faces by 4^times)."""
    pos, tri = positions.astype(np.float32), faces.astype(np.int64)
    for _ in range(times):
        a, b, c = pos[tri[:, 0]], pos[tri[:, 1]], pos[tri[:, 2]]
        ab, bc, ca = (a + b) / 2, (b + c) / 2, (c + a) / 2
        corners = np.stack([a, ab, ca, ab, b, bc, ca, bc, c, ab, bc, ca], axis=1).reshape(-1, 3)
        pos = corners.astype(np.float32)
        tri = np.arange(len(pos), dtype=np.int64).reshape(-1, 3)
    return pos, tri


def figure_mesh(subdivisions: int = 0) -> MeshArrays:
    """An asymmetric box figure (one arm on +X, a nose on +Z) with a 4-colour atlas and per-box UVs."""
    parts = [
        ((-0.12, -0.10, -0.08), (0.12, 0.22, 0.08)),   # torso
        ((-0.08, 0.24, -0.08), (0.08, 0.42, 0.08)),    # head
        ((-0.02, 0.30, 0.08), (0.02, 0.34, 0.16)),     # nose (front, +Z)
        ((0.12, 0.10, -0.04), (0.40, 0.18, 0.04)),     # arm (+X only)
        ((-0.11, -0.48, -0.06), (-0.02, -0.10, 0.06)),  # left leg
        ((0.02, -0.48, -0.06), (0.11, -0.10, 0.06)),   # right leg
        ((-0.20, 0.00, -0.05), (-0.12, 0.08, 0.20)),   # short block pointing forward on -X
    ]
    all_v, all_f, all_uv = [], [], []
    offset = 0
    for i, (lo, hi) in enumerate(parts):
        v, f = box(lo, hi)
        if subdivisions:
            v, f = subdivide(v, f, subdivisions)
        u = (i % 4 + 0.5) / 4.0
        uv = np.tile(np.array([[u, 0.5]], dtype=np.float32), (len(v), 1))
        all_v.append(v)
        all_f.append(f + offset)
        all_uv.append(uv)
        offset += len(v)
    atlas = np.zeros((16, 64, 3), dtype=np.uint8)
    for k, color in enumerate(((220, 40, 40), (40, 200, 60), (40, 60, 220), (230, 210, 40))):
        atlas[:, k * 16 : (k + 1) * 16] = color
    return MeshArrays(
        positions=np.concatenate(all_v).astype(np.float32),
        faces=np.concatenate(all_f).astype(np.uint32),
        uv=np.concatenate(all_uv),
        atlas_rgb=atlas,
    )


def plane_grid(cells: int) -> MeshArrays:
    """A ``cells x cells`` grid in z=0 facing +Z over [-0.5, 0.5]^2 (2·cells² faces, row-major from the top)."""
    n = cells + 1
    lin = np.linspace(-0.5, 0.5, n, dtype=np.float32)
    xx, yy = np.meshgrid(lin, lin[::-1])
    positions = np.stack([xx.ravel(), yy.ravel(), np.zeros(n * n, dtype=np.float32)], axis=1)
    idx = np.arange(n * n, dtype=np.int64).reshape(n, n)
    tl, tr, bl, br = idx[:-1, :-1].ravel(), idx[:-1, 1:].ravel(), idx[1:, :-1].ravel(), idx[1:, 1:].ravel()
    faces = np.empty((2 * cells * cells, 3), dtype=np.uint32)
    faces[0::2] = np.stack([tl, bl, tr], axis=1)
    faces[1::2] = np.stack([tr, bl, br], axis=1)
    return MeshArrays(positions=positions, faces=faces, uv=None, atlas_rgb=None)


def project(points: np.ndarray, clip: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """World points → pixel coordinates (x, y) with rows top-down."""
    homo = np.concatenate([points, np.ones((len(points), 1))], axis=1) @ clip.T
    ndc = homo[:, :2] / homo[:, 3:4]
    return np.stack([(ndc[:, 0] + 1) * 0.5 * size[0], (ndc[:, 1] + 1) * 0.5 * size[1]], axis=1)


def upstream_square_crop(mask: np.ndarray, out_size: int) -> np.ndarray:
    """The conditioning framing of DESIGN.md section 4.5.2 applied to a binary render (PIL float-box crop)."""
    rows, cols = np.nonzero(mask)
    x0, x1, y0, y1 = cols.min(), cols.max(), rows.min(), rows.max()
    center = ((x0 + x1) / 2, (y0 + y1) / 2)
    size = int(max(x1 - x0, y1 - y0))
    crop_box = (center[0] - size // 2, center[1] - size // 2, center[0] + size // 2, center[1] + size // 2)
    img = Image.fromarray(mask.astype(np.uint8) * 255).crop(crop_box).resize((out_size, out_size), Image.NEAREST)
    return np.asarray(img) > 127


# --------------------------------------------------------------------------------------------------
# camera math (CPU)
# --------------------------------------------------------------------------------------------------


def test_front_camera_matches_the_manifest_example():
    w2c = cameras.world_to_camera(CameraSpec(0.0, 0.0, 30.0, 1.93))
    expected = np.array([[1, 0, 0, 0], [0, -1, 0, 0], [0, 0, -1, 1.93], [0, 0, 0, 1]], dtype=np.float64)
    np.testing.assert_allclose(w2c, expected, atol=1e-12)
    fx, fy, cx, cy = cameras.camera_intrinsics(30.0, None, (1000, 1000))
    assert round(fx, 3) == round(fy, 3) == 1.866 and cx == cy == 0.5


def test_camera_position_convention_yaw_zero_is_front():
    np.testing.assert_allclose(cameras.camera_position(0, 0, 2), [0, 0, 2], atol=1e-12)
    np.testing.assert_allclose(cameras.camera_position(90, 0, 2), [2, 0, 0], atol=1e-12)
    np.testing.assert_allclose(cameras.camera_position(0, 90, 2)[1], 2 * np.sin(np.radians(89)), atol=1e-12)
    assert cameras.angle_from_front(180, 0) == pytest.approx(180)
    assert cameras.yaw_difference(350, 10) == pytest.approx(20)


def test_projection_maps_principal_point_and_rows_top_down():
    camera = CameraSpec(0.0, 0.0, 30.0, 2.0)
    clip, _ = cameras.view_clip_matrix(camera, 0.5, (100, 100))
    px = project(np.array([[0.0, 0.0, 0.0], [0.1, 0.2, 0.0]]), clip, (100, 100))
    np.testing.assert_allclose(px[0], [50, 50], atol=1e-9)
    assert px[1, 0] > 50 and px[1, 1] < 50  # +X → image-right, +Y → upper rows


def test_crop_intrinsics_map_the_crop_onto_the_output():
    camera = CameraSpec(20.0, 10.0, 30.0, 0.0)
    crop = (0.2, 0.1, 0.7, 0.85)
    size = (200, 300)  # aspect of the crop (0.5 / 0.75)
    full, _ = cameras.view_clip_matrix(camera, 0.6, (1000, 1000))
    cropped, _ = cameras.view_clip_matrix(camera, 0.6, size, crop)
    pts = np.random.default_rng(0).uniform(-0.3, 0.3, (20, 3))
    a = project(pts, full, (1000, 1000)) / 1000.0
    b = project(pts, cropped, size)
    np.testing.assert_allclose((a[:, 0] - crop[0]) / (crop[2] - crop[0]) * size[0], b[:, 0], atol=1e-6)
    np.testing.assert_allclose((a[:, 1] - crop[1]) / (crop[3] - crop[1]) * size[1], b[:, 1], atol=1e-6)


def test_framing_crop_reproduces_the_upstream_square_crop():
    # silhouette 0.3 wide x 0.8 tall; conditioning foreground spans the full height and is centred
    crop = cameras.framing_crop((0.3, 0.1, 0.6, 0.9), (312.5, 0.0, 687.5, 1000.0), (1000, 1000))
    np.testing.assert_allclose(crop, (0.05, 0.1, 0.85, 0.9), atol=1e-12)
    # untouched job: foreground occupies a quarter of a landscape image → scale + translation fit
    crop = cameras.framing_crop((0.4, 0.4, 0.6, 0.6), (100.0, 50.0, 200.0, 150.0), (400, 200))
    np.testing.assert_allclose(crop, (0.2, 0.3, 1.0, 0.7), atol=1e-12)  # 500 px per unit, centres aligned


def test_extra_view_cameras_follow_the_design_schedule():
    views = cameras.extra_view_cameras(CameraSpec(10.0, 5.0, 30.0, 2.5), 8)
    assert [round(v.yaw_deg, 6) for v in views] == [50.0, 90.0, 130.0, 170.0, 210.0, 250.0, 290.0, 330.0]
    assert [v.pitch_deg for v in views] == [0.0, 30.0] * 4
    assert all(v.fov_deg == 30.0 and v.distance == 2.5 for v in views)


def test_default_grid_has_756_candidates_and_605_refinements():
    grid = cameras.default_camera_grid()
    assert len(cameras.coarse_candidates(grid)) == 756
    assert grid.refine_top_k * len(cameras.refine_candidates(0, 0, 30, grid)) == 605
    assert grid.accept_iou == 0.6


def test_distance_rule_contains_the_bounding_sphere():
    for fov in (10.0, 30.0, 50.0):
        d = cameras.fit_distance(fov, 0.5)
        half_angle = np.degrees(np.arcsin(0.5 / d))
        assert half_angle < fov / 2


@pytest.mark.parametrize(
    "face_index", [-1, 0, 1, 2**24 - 2, 2**24 - 1, 2**24, 2**24 + 1, 16_468_359, 889_192_446]
)
def test_triangle_id_decode_roundtrip(face_index):
    encoded = cameras.encode_triangle_ids(np.array([face_index]))
    if face_index + 1 <= 2**24:
        assert encoded[0] == np.float32(face_index + 1)
    else:
        assert encoded[0] > np.float32(2**24)
    assert cameras.decode_triangle_ids(encoded)[0] == face_index


# --------------------------------------------------------------------------------------------------
# GPU
# --------------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def lifter():
    torch = pytest.importorskip("torch")
    if not torch.cuda.is_available():
        pytest.skip("CUDA is required for the raster lifter")
    pytest.importorskip("nvdiffrast.torch")
    from inference.parts.raster_lifter import RasterLifterProvider

    instance = RasterLifterProvider(lifter_spec(), settings=None, model_dir=None, device="cuda")
    instance.load(CancellationToken())
    instance.warm(CancellationToken())
    yield instance
    instance.unload()


def test_lifecycle_reports_honest_states(lifter):
    status = lifter.status()
    assert status.model_id == "raster_lifter" and status.state == "loaded" and status.loaded_device == "cuda"
    handle = lifter.upload(quadrant_mesh())
    lifter.release(handle)
    lifter.release(handle)  # idempotent
    with pytest.raises(ValueError, match="unknown or released"):
        lifter.raster(handle, CameraSpec(0, 0, 30, 0.0), (8, 8), None)


def test_front_camera_orientation_and_buffers(lifter):
    handle = lifter.upload(quadrant_mesh())
    try:
        size = (64, 64)
        front = lifter.raster(handle, CameraSpec(0.0, 0.0, 30.0, 0.0), size, None)
        quadrant = lambda out, r, c: int(out.face_id[r, c]) // 2  # noqa: E731
        assert [quadrant(front, 24, 24), quadrant(front, 24, 40), quadrant(front, 40, 24), quadrant(front, 40, 40)] == [
            0, 1, 2, 3,
        ]  # image-right = +X, rows top-down = +Y first
        covered = front.face_id >= 0
        distance = cameras.fit_distance(30.0, cameras.bounding_radius(quadrant_mesh().positions))
        assert np.allclose(front.depth[covered], distance, rtol=1e-4)  # the plane is perpendicular to the axis
        fx, fy, cx, cy = cameras.camera_intrinsics(30.0, None, size)
        rows, cols = np.nonzero(covered)
        x = ((cols + 0.5) / size[0] - cx) / fx * distance
        y = ((rows + 0.5) / size[1] - cy) / fy * distance
        expected_cos = distance / np.sqrt(x**2 + y**2 + distance**2)
        np.testing.assert_allclose(front.cos[rows, cols], expected_cos, atol=1e-4)
        assert (front.depth[~covered] == 0).all() and (front.cos[~covered] == 0).all()
        back = lifter.raster(handle, CameraSpec(180.0, 0.0, 30.0, 0.0), size, None)
        assert quadrant(back, 24, 24) == 1 and quadrant(back, 24, 40) == 0  # mirrored from behind
        tilted = lifter.raster(handle, CameraSpec(60.0, 0.0, 10.0, 0.0), size, None)
        tilted_cos = tilted.cos[tilted.face_id >= 0]
        assert abs(float(np.median(tilted_cos)) - 0.5) < 0.02 and tilted_cos.min() > 0.4 and tilted_cos.max() < 0.6
    finally:
        lifter.release(handle)


def test_distance_rule_frames_the_silhouette(lifter):
    handle = lifter.upload(figure_mesh())
    try:
        for yaw, pitch in ((0, 0), (90, 40), (180, -20), (270, 10), (45, 30)):
            narrow = lifter.raster(handle, CameraSpec(yaw, pitch, 10.0, 0.0), (256, 256), None).face_id >= 0
            assert narrow.any()
            assert not (narrow[0].any() or narrow[-1].any() or narrow[:, 0].any() or narrow[:, -1].any())
            wide = lifter.raster(handle, CameraSpec(yaw, pitch, 50.0, 0.0), (256, 256), None).face_id >= 0
            x0, y0, x1, y1 = cameras.mask_bbox(wide)
            assert max(x1 - x0, y1 - y0) >= 0.5 * 256, (yaw, pitch, (x0, y0, x1, y1))
    finally:
        lifter.release(handle)


def test_raster_of_a_2m_face_mesh_hits_the_right_triangles(lifter):
    mesh = plane_grid(1000)  # 2 000 000 faces
    handle = lifter.upload(mesh)
    try:
        camera = CameraSpec(0.0, 0.0, 30.0, 0.0)
        size = (1024, 1024)
        torch = pytest.importorskip("torch")
        torch.cuda.synchronize()
        started = time.perf_counter()
        out = lifter.raster(handle, camera, size, None)
        elapsed = time.perf_counter() - started
        log.info("2M-face raster at 1024x1024: %.3f s, coverage %.3f", elapsed, (out.face_id >= 0).mean())
        _assert_pixels_inside_their_triangles(mesh, out.face_id, camera, size, sample=4000)
        assert out.face_id.max() < 2_000_000 and (out.face_id >= 0).mean() > 0.3
    finally:
        lifter.release(handle)


def test_triangle_ids_above_2_pow_24_decode_on_the_gpu(lifter):
    mesh = plane_grid(2897)  # 16 785 218 faces > 2^24: drawn in range-mode chunks
    assert len(mesh.faces) > 2**24
    handle = lifter.upload(mesh)
    try:
        camera = CameraSpec(0.0, 0.0, 30.0, 0.0)
        size = (1024, 1024)
        # zoom onto the bottom edge of the plane, where the last rows (face ids above 2^24) are
        radius = cameras.bounding_radius(mesh.positions)
        clip, _ = cameras.view_clip_matrix(camera, radius, (1, 1))
        u, v = project(np.array([[0.0, -0.5, 0.0]]), clip, (1, 1))[0]
        crop = (u - 0.01, v - 0.015, u + 0.01, v + 0.005)
        out = lifter.raster(handle, camera, size, crop)
        high = out.face_id >= 2**24
        log.info("16.8M-face raster: %d pixels carry ids above 2^24 (max %d)", int(high.sum()), int(out.face_id.max()))
        assert high.any() and (out.face_id < 2**24).any() and out.face_id.max() < len(mesh.faces)
        _assert_pixels_inside_their_triangles(mesh, out.face_id, camera, size, sample=2000, only=high, crop=crop)
        _assert_pixels_inside_their_triangles(mesh, out.face_id, camera, size, sample=2000, crop=crop)
    finally:
        lifter.release(handle)


def test_render_textured_matches_the_atlas_colour(lifter):
    atlas = np.zeros((64, 64, 3), dtype=np.uint8)
    atlas[:32, :32], atlas[:32, 32:] = (255, 0, 0), (0, 255, 0)
    atlas[32:, :32], atlas[32:, 32:] = (0, 0, 255), (255, 255, 0)
    base = quadrant_mesh()
    handle = lifter.upload(MeshArrays(base.positions, base.faces, base.uv, atlas))
    try:
        camera = CameraSpec(0.0, 0.0, 30.0, 0.0)
        image = lifter.render_textured(handle, camera, (128, 128))
        faces = lifter.raster(handle, camera, (128, 128), None).face_id
        for row, col, color in ((48, 48, (255, 0, 0)), (48, 80, (0, 255, 0)), (80, 48, (0, 0, 255)), (80, 80, (255, 255, 0))):
            face = int(faces[row, col])
            uv_centroid = base.uv[base.faces[face].astype(np.int64)].mean(axis=0)
            atlas_color = atlas[int(uv_centroid[1] * 64), int(uv_centroid[0] * 64)]
            assert tuple(image[row, col]) == tuple(atlas_color) == color
        assert (image[0, 0] == 0).all()  # background is black like the conditioning image
        colors = np.array([[10 * k, 255 - 10 * k, 7] for k in range(8)], dtype=np.uint8)
        labels = lifter.render_labels(handle, camera, (128, 128), colors)
        covered = faces >= 0
        assert (labels[covered] == colors[faces[covered]]).all() and (labels[~covered] == 0).all()
    finally:
        lifter.release(handle)


def test_silhouette_fit_on_a_synthetic_textured_mesh(lifter):
    mesh = figure_mesh(subdivisions=2)
    handle = lifter.upload(mesh)
    try:
        truth = CameraSpec(43.0, 7.0, 30.0, 0.0)
        full = lifter.raster(handle, truth, (1024, 1024), None).face_id >= 0
        foreground = upstream_square_crop(full, 512)
        fit = lifter.fit_camera(handle, foreground, cameras.default_camera_grid(), CancellationToken())
        log.info("synthetic fit: %s, diagnostics %s", fit, lifter.last_fit)
        assert fit.accepted and fit.iou >= 0.9
        assert cameras.yaw_difference(fit.yaw_deg, truth.yaw_deg) <= 3.0 and abs(fit.pitch_deg - truth.pitch_deg) <= 3.0
        assert fit.method == "silhouette_grid_refine" and 756 < fit.candidates_tested <= 756 + 605
        framed = lifter.raster(handle, fit, (512, 512), fit.crop).face_id >= 0
        assert cameras.silhouette_iou(framed, foreground) == pytest.approx(fit.iou, abs=1e-6)
        textured = lifter.render_textured(handle, fit, (256, 256))
        assert textured.reshape(-1, 3).max(axis=0).min() > 0  # the atlas colours are visible
        cancelled = CancellationToken()
        cancelled.cancel("test")
        with pytest.raises(Exception, match="test"):
            lifter.fit_camera(handle, foreground, cameras.default_camera_grid(), cancelled)
    finally:
        lifter.release(handle)


def test_rejected_fit_falls_back_to_the_front_view(lifter):
    handle = lifter.upload(figure_mesh())
    try:
        foreground = np.zeros((128, 128), dtype=bool)
        foreground[10:20, 10:118] = True  # a thin bar no camera can explain
        grid = CameraGrid(yaws=(0.0, 90.0, 180.0, 270.0), pitches=(0.0,), fovs=(30.0,), refine_top_k=1)
        fit = lifter.fit_camera(handle, foreground, grid, CancellationToken())
        assert not fit.accepted and fit.iou < 0.6
        assert (fit.yaw_deg, fit.pitch_deg, fit.fov_deg) == cameras.FRONT_CAMERA
    finally:
        lifter.release(handle)


def test_cpu_fake_agrees_with_the_real_raster(lifter):
    mesh = figure_mesh()
    fake = FakeLabelLifter()
    real_handle, fake_handle = lifter.upload(mesh), fake.upload(mesh)
    try:
        for camera, crop in (
            (CameraSpec(0.0, 0.0, 30.0, 0.0), None),
            (CameraSpec(35.0, 20.0, 50.0, 0.0), None),
            (CameraSpec(200.0, -10.0, 10.0, 0.0), (0.1, 0.05, 0.9, 0.85)),
        ):
            real = lifter.raster(real_handle, camera, (96, 96), crop)
            cpu = fake.raster(fake_handle, camera, (96, 96), crop)
            both = (real.face_id >= 0) & (cpu.face_id >= 0)
            same = both & (real.face_id == cpu.face_id)
            assert cameras.silhouette_iou(real.face_id >= 0, cpu.face_id >= 0) >= 0.97
            assert same.sum() >= 0.97 * both.sum()
            np.testing.assert_allclose(real.depth[same], cpu.depth[same], rtol=1e-3)
            np.testing.assert_allclose(real.cos[same], cpu.cos[same], atol=2e-3)
    finally:
        lifter.release(real_handle)
        fake.release(fake_handle)


def _assert_pixels_inside_their_triangles(
    mesh: MeshArrays, face_id: np.ndarray, camera: CameraSpec, size: tuple[int, int], *, sample: int,
    only: np.ndarray | None = None, crop: tuple[float, float, float, float] | None = None,
) -> None:
    radius = cameras.bounding_radius(mesh.positions)
    clip, _ = cameras.view_clip_matrix(camera, radius, size, crop)
    mask = face_id >= 0 if only is None else only
    rows, cols = np.nonzero(mask)
    pick = np.random.default_rng(1).choice(len(rows), size=min(sample, len(rows)), replace=False)
    rows, cols = rows[pick], cols[pick]
    tri = mesh.faces[face_id[rows, cols]].astype(np.int64)
    corners = project(mesh.positions[tri.ravel()].astype(np.float64), clip, size).reshape(-1, 3, 2)
    p = np.stack([cols + 0.5, rows + 0.5], axis=1)
    a, b, c = corners[:, 0], corners[:, 1], corners[:, 2]

    def edge(u: np.ndarray, v: np.ndarray, q: np.ndarray) -> np.ndarray:
        return (v[:, 0] - u[:, 0]) * (q[:, 1] - u[:, 1]) - (v[:, 1] - u[:, 1]) * (q[:, 0] - u[:, 0])

    # nvdiffrast snaps vertices to 1/16 px; accept pixel centres within 0.1 px of their triangle
    sign = np.sign(edge(a, b, c))
    dists = [
        edge(u, v, p) * sign / np.maximum(np.linalg.norm(v - u, axis=1), 1e-12) for u, v in ((b, c), (c, a), (a, b))
    ]
    inside = (dists[0] >= -0.1) & (dists[1] >= -0.1) & (dists[2] >= -0.1)
    assert inside.mean() >= 0.999, f"{(~inside).sum()} of {len(inside)} sampled pixels outside their triangle"


# --------------------------------------------------------------------------------------------------
# real fixtures (camera-fit acceptance)
# --------------------------------------------------------------------------------------------------


def read_glb(path: Path) -> MeshArrays:
    """Raw accessors of a single-primitive GLB (positions, indices, TEXCOORD_0, baseColor image)."""
    data = path.read_bytes()
    magic, _version, _length = struct.unpack_from("<III", data, 0)
    assert magic == 0x46546C67, "not a GLB file"
    json_len, _ = struct.unpack_from("<II", data, 12)
    doc = json.loads(data[20 : 20 + json_len])
    bin_offset = 20 + json_len
    bin_len, _ = struct.unpack_from("<II", data, bin_offset)
    blob = memoryview(data)[bin_offset + 8 : bin_offset + 8 + bin_len]
    dtypes = {5126: np.float32, 5125: np.uint32, 5123: np.uint16, 5121: np.uint8}
    widths = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}

    def accessor(index: int) -> np.ndarray:
        acc = doc["accessors"][index]
        view = doc["bufferViews"][acc["bufferView"]]
        dtype = np.dtype(dtypes[acc["componentType"]])
        width = widths[acc["type"]]
        start = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
        stride = view.get("byteStride", dtype.itemsize * width)
        raw = np.frombuffer(blob, dtype=np.uint8, count=stride * (acc["count"] - 1) + dtype.itemsize * width, offset=start)
        if stride == dtype.itemsize * width:
            return raw.view(dtype).reshape(acc["count"], width).copy()
        return np.lib.stride_tricks.as_strided(
            raw.view(dtype), shape=(acc["count"], width), strides=(stride, dtype.itemsize)
        ).copy()

    prim = doc["meshes"][0]["primitives"][0]
    positions = accessor(prim["attributes"]["POSITION"]).astype(np.float32)
    faces = accessor(prim["indices"]).reshape(-1, 3).astype(np.uint32)
    uv = accessor(prim["attributes"]["TEXCOORD_0"]).astype(np.float32) if "TEXCOORD_0" in prim["attributes"] else None
    atlas = None
    material = doc["materials"][prim["material"]] if "material" in prim else None
    tex_info = (material or {}).get("pbrMetallicRoughness", {}).get("baseColorTexture")
    if tex_info is not None:
        texture = doc["textures"][tex_info["index"]]
        source = texture.get("source", texture.get("extensions", {}).get("EXT_texture_webp", {}).get("source"))
        view = doc["bufferViews"][doc["images"][source]["bufferView"]]
        start = view.get("byteOffset", 0)
        atlas = np.asarray(Image.open(io.BytesIO(bytes(blob[start : start + view["byteLength"]]))).convert("RGB"))
    return MeshArrays(positions=positions, faces=faces, uv=uv, atlas_rgb=atlas)


def conditioning_foreground(preview: Path) -> np.ndarray:
    """Foreground of an ``alpha_crop``/``background_removed`` job without labels (DESIGN.md section 4.6.3):
    ``preview_luma > 8``, a 5 px morphological close, then ``binary_fill_holes``."""
    import cv2
    from scipy import ndimage

    luma = np.asarray(Image.open(preview).convert("L"))
    mask = (luma > 8).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
    return ndimage.binary_fill_holes(mask.astype(bool))


def load_fixture_index() -> dict:
    """``test/part_aware/fixtures.json`` (its ``regenerate`` string holds unescaped Windows backslashes, which strict
    JSON rejects, so lone backslashes are escaped before parsing; the file itself is read-only here)."""
    text = FIXTURES_JSON.read_text(encoding="utf-8")
    return json.loads(re.sub(r'\\(?![\\"/bfnrtu])', r"\\\\", text))


def copy_fixture(case: str) -> Path:
    spec = load_fixture_index()["fixtures"][case]
    source = (PROJECT_ROOT / spec["dir"]).resolve()
    if source.name in EXCLUDED_JOB_IDS or "runtime" in source.relative_to(PROJECT_ROOT).parts[:2]:
        pytest.fail(f"refusing to read fixture {source}")
    if not (source / "model.glb").is_file():
        pytest.skip(f"fixture {case} is missing; regenerate it with the command in fixtures.json")
    target = RUNTIME_DIR / case
    target.mkdir(parents=True, exist_ok=True)
    for name in ("model.glb", "preview.png", "summary.json"):
        src, dst = source / name, target / name
        if not dst.is_file() or dst.stat().st_size != src.stat().st_size:
            shutil.copy2(src, dst)
    return target


@pytest.mark.parametrize("case", FIXTURE_CASES)
def test_camera_fit_on_real_fixtures(lifter, case):
    job_dir = copy_fixture(case)
    t0 = time.perf_counter()
    mesh = read_glb(job_dir / "model.glb")
    t_read = time.perf_counter() - t0
    foreground = conditioning_foreground(job_dir / "preview.png")
    torch = pytest.importorskip("torch")
    torch.cuda.synchronize()
    free_before = torch.cuda.mem_get_info()[0]
    t1 = time.perf_counter()
    handle = lifter.upload(mesh)
    t_upload = time.perf_counter() - t1
    try:
        fit = lifter.fit_camera(handle, foreground, cameras.default_camera_grid(), CancellationToken())
        diag = lifter.last_fit
        torch.cuda.synchronize()
        vram_delta_mb = (free_before - torch.cuda.mem_get_info()[0]) / 2**20
        size = (foreground.shape[1], foreground.shape[0])
        t2 = time.perf_counter()
        out = lifter.raster(handle, fit, size, fit.crop)
        t_raster = time.perf_counter() - t2
        silhouette = out.face_id >= 0
        overlay = np.zeros((*foreground.shape, 3), dtype=np.uint8)
        overlay[foreground & silhouette] = (255, 255, 255)
        overlay[foreground & ~silhouette] = (255, 60, 60)
        overlay[~foreground & silhouette] = (60, 160, 255)
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        Image.fromarray(overlay).save(OUTPUT_DIR / f"{case}_camera_fit_overlay.png")
        record = {
            "case": case,
            "faces": int(len(mesh.faces)),
            "vertices": int(len(mesh.positions)),
            "conditioning_size": list(size),
            "fit": {
                "yaw_deg": fit.yaw_deg, "pitch_deg": fit.pitch_deg, "fov_deg": fit.fov_deg, "distance": fit.distance,
                "crop": list(fit.crop), "iou": fit.iou, "accepted": fit.accepted, "method": fit.method,
                "candidates_tested": fit.candidates_tested,
            },
            "search": {
                "coarse": diag.coarse_count, "refine": diag.refine_count, "best": diag.best, "mirror": diag.mirror,
                "mirror_tie_break": diag.mirror_tie_break,
            },
            "timings_s": {
                "read_glb": round(t_read, 3), "upload": round(t_upload, 3),
                **{k: round(v, 3) for k, v in diag.timings.items()}, "raster_conditioning": round(t_raster, 3),
            },
            "raster_coverage_iou": cameras.silhouette_iou(silhouette, foreground),
            "device_vram_delta_mb_after_fit": round(vram_delta_mb, 1),
        }
        (OUTPUT_DIR / f"{case}_camera_fit.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
        log.info("camera fit %s: %s", case, json.dumps(record))
        assert fit.accepted and fit.iou >= 0.6, record
    finally:
        lifter.release(handle)
