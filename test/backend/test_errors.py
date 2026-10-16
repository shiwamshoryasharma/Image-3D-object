"""The uniform error body for every failure path."""

from __future__ import annotations

from helpers import assert_error


def test_unknown_api_route_is_404_json(client):
    error = assert_error(client.get("/api/v1/does-not-exist"), 404, "not_found")
    assert error["details"] is None


def test_method_not_allowed(client):
    assert_error(client.put("/api/v1/health"), 405, "method_not_allowed")


def test_request_validation_error_shape(client):
    response = client.post("/api/v1/jobs", json={"settings": {"resolution": "big"}})
    error = assert_error(response, 422, "validation_error")
    assert isinstance(error["details"], list) and error["details"]
    for item in error["details"]:
        assert set(item) >= {"loc", "msg", "type"}
        assert "input" not in item


def test_validation_errors_never_echo_passwords(client):
    marker = "SuperSecretMarker"
    response = client.post(
        "/api/v1/auth/login", json={"username": "u", "password": marker * 100, "extra": "x"}
    )
    error = assert_error(response, 422, "validation_error")
    assert all("input" not in item for item in error["details"])
    assert marker not in response.text


def test_malformed_json_body(client):
    response = client.post(
        "/api/v1/reference-sets", content=b"{not json", headers={"Content-Type": "application/json"}
    )
    assert_error(response, 422, "validation_error")


def test_app_error_not_found(client):
    assert_error(client.get("/api/v1/reference-sets/rs_000000000000"), 404, "not_found")
    assert_error(client.get("/api/v1/jobs/job_000000000000"), 404, "not_found")
    # Malformed ids are simply not found (never touch the filesystem).
    assert_error(client.get("/api/v1/reference-sets/..%2F..%2Fetc"), 404, "not_found")


def test_unhandled_exception_is_500_internal_error(make_app):
    h = make_app(raise_server_exceptions=False)

    async def boom() -> None:
        raise RuntimeError("secret internal detail")

    h.app.add_api_route("/api/v1/_test/boom", boom, methods=["GET"])
    response = h.client.get("/api/v1/_test/boom")
    error = assert_error(response, 500, "internal_error")
    assert error["message"] == "Internal server error"
    assert "secret internal detail" not in response.text


def test_cors_preflight_allows_dev_origin(client):
    response = client.options(
        "/api/v1/system",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_openapi_available_in_development(client):
    response = client.get("/api/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/api/v1/jobs" in paths and "/api/v1/reference-sets/{set_id}/images" in paths
