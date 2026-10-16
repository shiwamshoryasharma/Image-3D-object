"""Serving the built frontend at "/" with SPA fallback, without shadowing /api."""

from __future__ import annotations

from helpers import assert_error


def _make_dist(root):
    dist = root / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>Img3D</title><div id=root></div>", encoding="utf-8")
    (dist / "assets" / "app-1234.js").write_text("console.log('img3d')", encoding="utf-8")
    return dist


def test_frontend_served_with_spa_fallback(make_app, runtime_dir):
    dist = _make_dist(runtime_dir)
    h = make_app(frontend_dist_dir=dist)
    client = h.client

    index = client.get("/")
    assert index.status_code == 200 and "Img3D" in index.text
    assert index.headers["cache-control"] == "no-cache"

    asset = client.get("/assets/app-1234.js")
    assert asset.status_code == 200
    assert "immutable" in asset.headers["cache-control"]

    route = client.get("/jobs/job_123/viewer")  # client-side route -> index.html
    assert route.status_code == 200 and "Img3D" in route.text

    assert client.get("/assets/missing.js").status_code == 404  # real missing files stay 404

    # The API is never shadowed by the SPA.
    assert client.get("/api/v1/health").json()["status"] == "ok"
    assert_error(client.get("/api/v1/unknown"), 404, "not_found")
    assert_error(client.get("/api"), 404, "not_found")
    assert_error(client.get("/api/whatever/deep"), 404, "not_found")


def test_no_frontend_mount_without_dist(client):
    assert_error(client.get("/"), 404, "not_found")
