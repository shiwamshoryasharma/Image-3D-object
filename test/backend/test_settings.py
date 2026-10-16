"""Settings parsing, path resolution, secret handling and log redaction."""

from __future__ import annotations

import json
import logging
import secrets
from pathlib import Path

import pytest

from config.settings import BACKEND_DIR, Settings
from core.logging import SecretRegistry, configure_logging, flush_logging

TEST_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Remove the IMG3D_* overrides set by test/conftest.py so defaults are observable."""
    import os

    for key in list(os.environ):
        if key.startswith("IMG3D_"):
            monkeypatch.delenv(key)


def test_defaults_match_contract():
    s = Settings(_env_file=None)
    assert s.app_name == "Img3D" and s.app_version == "1.0.0"
    assert s.environment == "development"
    assert (s.host, s.port) == ("127.0.0.1", 8000)
    assert s.cors_origins == ["http://localhost:5173", "http://127.0.0.1:5173"]
    assert s.model_dir == (BACKEND_DIR / ".model" / "TRELLIS.2-4B").resolve()
    assert s.aux_model_root == (BACKEND_DIR / ".model").resolve()
    assert s.trellis_source_dir == (BACKEND_DIR / "third_party" / "TRELLIS.2").resolve()
    assert s.hf_home == (BACKEND_DIR / ".model" / ".hf_cache").resolve()
    assert s.engine == "trellis2" and s.device == "cuda"
    assert s.low_vram is True and s.attention_backend == "auto"
    assert s.max_upload_mb == 25 and s.max_image_pixels == 64_000_000
    assert s.min_image_side == 64 and s.max_detail_images == 8 and s.max_queue_size == 16
    assert s.job_retention_days == 14 and s.max_stored_jobs == 200 and s.reference_set_retention_days == 30
    assert s.auth_token_ttl_hours == 12 and s.auth_secret is None
    assert s.frontend_dist_dir == (BACKEND_DIR.parent / "frontend" / "dist").resolve()
    assert s.glb_webp_textures is True and s.shutdown_timeout_s == 20
    assert s.backend_dir == BACKEND_DIR
    assert s.paths.root == s.runtime_dir


def test_env_overrides_and_cors_formats(monkeypatch):
    monkeypatch.setenv("IMG3D_PORT", "9001")
    monkeypatch.setenv("IMG3D_CORS_ORIGINS", "http://a.test, http://b.test")
    monkeypatch.setenv("IMG3D_LOW_VRAM", "false")
    monkeypatch.setenv("IMG3D_MODEL_DIR", "custom/model")
    s = Settings(_env_file=None)
    assert s.port == 9001
    assert s.cors_origins == ["http://a.test", "http://b.test"]
    assert s.low_vram is False
    assert s.model_dir == (BACKEND_DIR / "custom" / "model").resolve()  # relative to backend/, not CWD

    monkeypatch.setenv("IMG3D_CORS_ORIGINS", '["http://c.test"]')
    assert Settings(_env_file=None).cors_origins == ["http://c.test"]


def test_unprefixed_secrets_and_auth_auto(monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "hf_dummy_value_for_tests")
    monkeypatch.setenv("UI_USERNAME", "someone")
    monkeypatch.setenv("UI_PASSWORD", "pw-123456")
    s = Settings(_env_file=None)
    assert s.hf_token is not None and s.hf_token.get_secret_value() == "hf_dummy_value_for_tests"
    assert s.auth_required is True
    assert "pw-123456" not in repr(s)
    assert "hf_dummy_value_for_tests" not in repr(s)
    assert "pw-123456" not in json.dumps(s.safe_summary())

    monkeypatch.setenv("IMG3D_AUTH_ENABLED", "false")
    assert Settings(_env_file=None).auth_required is False
    monkeypatch.delenv("IMG3D_AUTH_ENABLED")
    monkeypatch.setenv("UI_PASSWORD", "")
    assert Settings(_env_file=None).auth_required is False


def test_invalid_values_rejected():
    with pytest.raises(ValueError):
        Settings(_env_file=None, log_level="LOUD")
    with pytest.raises(ValueError):
        Settings(_env_file=None, attention_backend="magic")
    with pytest.raises(ValueError):
        Settings(_env_file=None, environment="staging")


def test_secrets_are_redacted_from_logs():
    log_dir = TEST_ROOT / ".logs" / f"redaction-{secrets.token_hex(3)}"
    password = "Sup3r-Secret-Passw0rd"
    s = Settings(_env_file=None, log_dir=log_dir, ui_username="u", ui_password=password, hf_token="hf_SECRETTOKEN123")
    try:
        configure_logging(s)
        logging.getLogger("img3d.test").warning("leak attempt %s / %s", password, "hf_SECRETTOKEN123")
        flush_logging()
        app_log = (log_dir / "img3d.log").read_text("utf-8")
        error_log = (log_dir / "error.log").read_text("utf-8")
        for text in (app_log, error_log):
            assert "leak attempt" in text
            assert password not in text
            assert "hf_SECRETTOKEN123" not in text
            assert "***" in text
        record = json.loads(app_log.strip().splitlines()[-1])
        assert record["level"] == "WARNING" and record["logger"] == "img3d.test"
    finally:
        SecretRegistry.clear()
        for handler in list(logging.getLogger().handlers):
            if getattr(handler, "_img3d_handler", False):
                logging.getLogger().removeHandler(handler)
                handler.close()


def test_safe_summary_has_no_secret_fields():
    s = Settings(_env_file=None, hf_token="hf_x", ui_password="p" * 8, auth_secret="z" * 32)
    summary = json.dumps(s.safe_summary())
    assert "hf_x" not in summary and "pppppppp" not in summary and "z" * 32 not in summary


def test_token_query_values_and_bearer_tokens_are_redacted():
    # The exact line uvicorn logs for a WebSocket handshake (path WITH query string).
    line = '127.0.0.1:5123 - "WebSocket /api/v1/ws?job_id=job_0123456789ab&token=eyJzdWIiOiJ1In0.c2lnbmF0dXJl" [accepted]'
    redacted = SecretRegistry.redact(line)
    assert "eyJzdWIiOiJ1In0" not in redacted
    assert "token=***" in redacted and "job_id=job_0123456789ab" in redacted
    assert SecretRegistry.redact("/api/v1/jobs/x/model.glb?token=abc.def") == "/api/v1/jobs/x/model.glb?token=***"
    assert SecretRegistry.redact("Authorization: Bearer eyJhbGciOi.payload-sig") == "Authorization: Bearer ***"
    # Unrelated text is untouched.
    assert SecretRegistry.redact("tokens=5 and bearer of news") == "tokens=5 and bearer of news"


def test_json_log_lines_redact_json_escaped_secrets():
    from core.logging import JsonFormatter

    secret = r'pa"ss\w0rd-é'  # quote and backslash are escaped in JSON log lines
    SecretRegistry.register([secret])
    try:
        record = logging.LogRecord("img3d.test", logging.WARNING, __file__, 1, "value=%s", (secret,), None)
        payload = json.loads(JsonFormatter().format(record))
        assert payload["message"] == "value=***"
    finally:
        SecretRegistry.clear()
