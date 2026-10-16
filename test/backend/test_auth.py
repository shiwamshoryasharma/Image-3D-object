"""Authentication on/off, tokens, ?token= for file endpoints and the WebSocket."""

from __future__ import annotations

import pytest
from helpers import assert_error, create_job, create_set_with_image, png_bytes, upload, wait_for_job, ws_receive
from starlette.websockets import WebSocketDisconnect

USER = "alice"
PASSWORD = "correct horse battery staple"


@pytest.fixture
def auth_app(make_app):
    return make_app(ui_username=USER, ui_password=PASSWORD, auth_enabled=None)


def _login(client) -> str:
    response = client.post("/api/v1/auth/login", json={"username": USER, "password": PASSWORD})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_at"].endswith("Z")
    return body["token"]


def test_auth_disabled_status(client):
    assert client.get("/api/v1/auth/status").json() == {
        "auth_required": False,
        "authenticated": True,
        "username": None,
    }
    assert client.get("/api/v1/system").status_code == 200


def test_auth_auto_enabled_when_credentials_present(auth_app):
    client = auth_app.client
    assert client.get("/api/v1/auth/status").json() == {
        "auth_required": True,
        "authenticated": False,
        "username": None,
    }
    assert client.get("/api/v1/system").status_code == 401


def test_explicit_auth_disabled_overrides_credentials(make_app):
    h = make_app(ui_username=USER, ui_password=PASSWORD, auth_enabled=False)
    assert h.client.get("/api/v1/system").status_code == 200


def test_health_stays_public(auth_app):
    assert auth_app.client.get("/api/v1/health").status_code == 200


def test_protected_endpoints_return_401_shape(auth_app):
    client = auth_app.client
    for method, path in [
        ("get", "/api/v1/system"),
        ("get", "/api/v1/reference-sets"),
        ("post", "/api/v1/reference-sets"),
        ("get", "/api/v1/jobs"),
        ("get", "/api/v1/jobs/job_000000000000"),
    ]:
        response = getattr(client, method)(path)
        error = assert_error(response, 401, "unauthorized")
        assert response.headers.get("www-authenticate") == "Bearer"
        assert error["message"]


def test_login_wrong_credentials(auth_app):
    client = auth_app.client
    assert_error(client.post("/api/v1/auth/login", json={"username": USER, "password": "nope"}), 401, "unauthorized")
    assert_error(client.post("/api/v1/auth/login", json={"username": "bob", "password": PASSWORD}), 401, "unauthorized")


def test_login_then_access_and_logout(auth_app):
    client = auth_app.client
    token = _login(client)
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/v1/system", headers=headers).status_code == 200
    status = client.get("/api/v1/auth/status", headers=headers).json()
    assert status == {"auth_required": True, "authenticated": True, "username": USER}

    assert client.post("/api/v1/auth/logout", headers=headers).status_code == 204
    assert_error(client.get("/api/v1/system", headers=headers), 401, "unauthorized")


def test_invalid_and_tampered_tokens(auth_app):
    client = auth_app.client
    token = _login(client)
    body, _, sig = token.partition(".")
    for bad in ("garbage", f"{body}.{sig[:-2]}xx", f"{body}x.{sig}", "a.b.c", "x" * 5000):
        assert_error(client.get("/api/v1/system", headers={"Authorization": f"Bearer {bad}"}), 401, "unauthorized")
    # Non-ASCII tokens can only arrive via the query string; they must be rejected, not crash.
    ref_set, image = create_set_with_image(client, headers={"Authorization": f"Bearer {token}"})
    assert_error(client.get(image["urls"]["file"] + "?token=%C3%A9.%C3%A9"), 401, "unauthorized")


def test_token_query_param_only_for_file_endpoints(auth_app):
    client = auth_app.client
    token = _login(client)
    headers = {"Authorization": f"Bearer {token}"}
    ref_set, image = create_set_with_image(client, headers=headers)

    # JSON endpoints do not accept ?token=
    assert client.get(f"/api/v1/reference-sets/{ref_set['id']}?token={token}").status_code == 401
    # File endpoints do.
    file_url = image["urls"]["file"]
    assert client.get(file_url).status_code == 401
    assert client.get(f"{file_url}?token={token}").status_code == 200
    assert client.get(f"{image['urls']['thumbnail']}?token={token}").status_code == 200


def test_upload_requires_auth_before_reading_body(auth_app):
    client = auth_app.client
    token = _login(client)
    ref_set = client.post(
        "/api/v1/reference-sets", json={}, headers={"Authorization": f"Bearer {token}"}
    ).json()
    assert_error(upload(client, ref_set["id"], png_bytes()), 401, "unauthorized")


def test_job_file_endpoints_accept_query_token(auth_app):
    client = auth_app.client
    token = _login(client)
    headers = {"Authorization": f"Bearer {token}"}
    ref_set, _ = create_set_with_image(client, headers=headers)
    client.headers.update(headers)
    job = create_job(client, ref_set["id"]).json()
    wait_for_job(client, job["id"], {"completed"})
    client.headers.pop("Authorization")
    assert client.get(f"/api/v1/jobs/{job['id']}/model.glb").status_code == 401
    assert client.get(f"/api/v1/jobs/{job['id']}/model.glb?token={token}").status_code == 200
    assert client.get(f"/api/v1/jobs/{job['id']}/preview?token={token}").status_code == 200
    assert client.get(f"/api/v1/jobs/{job['id']}/export/glb?token={token}").status_code == 200
    assert client.get(f"/api/v1/jobs/{job['id']}/result?token={token}").status_code == 401


def test_websocket_requires_token(auth_app):
    client = auth_app.client
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/api/v1/ws") as ws:
            ws_receive(ws, timeout=2)
    assert exc_info.value.code == 4401

    token = _login(client)
    with client.websocket_connect(f"/api/v1/ws?token={token}") as ws:
        hello = ws_receive(ws)
        assert hello["type"] == "hello"
        assert hello["data"]["system"]["auth_required"] is True


def test_login_throttling(auth_app):
    client = auth_app.client
    for _ in range(10):
        client.post("/api/v1/auth/login", json={"username": USER, "password": "wrong"})
    assert_error(
        client.post("/api/v1/auth/login", json={"username": USER, "password": PASSWORD}), 429, "too_many_requests"
    )


def test_auth_secret_makes_tokens_survive_restart(make_app, make_settings):
    settings = make_settings(ui_username=USER, ui_password=PASSWORD, auth_enabled=True, auth_secret="s" * 32)
    first = make_app(settings=settings)
    token = _login(first.client)
    second = make_app(settings=settings)
    assert second.client.get("/api/v1/system", headers={"Authorization": f"Bearer {token}"}).status_code == 200
