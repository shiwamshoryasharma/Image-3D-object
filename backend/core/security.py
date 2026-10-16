"""Authentication primitives: constant-time credential checks and HMAC-signed bearer tokens.

Token format: ``base64url(json_payload).base64url(hmac_sha256(secret, payload_part))``. Payload carries the
subject, expiry and a random ``jti`` used for revocation (logout). The signing secret comes from
``IMG3D_AUTH_SECRET`` or is random per process (tokens then become invalid after a restart).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from core.exceptions import AuthenticationError

_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")
_MAX_TOKEN_LENGTH = 2048


@dataclass(frozen=True, slots=True)
class TokenClaims:
    subject: str
    expires_at: datetime
    token_id: str


@dataclass(frozen=True, slots=True)
class IssuedToken:
    token: str
    expires_at: datetime


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def constant_time_equals(provided: str, expected: str) -> bool:
    """Compare two strings in constant time (with respect to their content)."""
    return hmac.compare_digest(
        hashlib.sha256(provided.encode("utf-8")).digest(),
        hashlib.sha256(expected.encode("utf-8")).digest(),
    )


def verify_credentials(username: str, password: str, expected_username: str, expected_password: str) -> bool:
    """Check both fields without short-circuiting, so timing does not reveal which one was wrong."""
    user_ok = constant_time_equals(username, expected_username)
    password_ok = constant_time_equals(password, expected_password)
    return user_ok & password_ok


class TokenManager:
    """Issues, verifies and revokes bearer tokens. Thread-safe."""

    def __init__(self, secret: bytes | None, ttl_seconds: float) -> None:
        self._secret = secret or secrets.token_bytes(32)
        self._ttl = ttl_seconds
        self._revoked: dict[str, float] = {}
        self._lock = threading.Lock()

    def issue(self, subject: str) -> IssuedToken:
        expires = time.time() + self._ttl
        payload = {"sub": subject, "exp": int(expires), "jti": secrets.token_hex(12)}
        body = _b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        token = f"{body}.{self._sign(body)}"
        return IssuedToken(token=token, expires_at=datetime.fromtimestamp(int(expires), UTC))

    def verify(self, token: str | None) -> TokenClaims:
        """Return the claims of a valid token or raise :class:`AuthenticationError`."""
        if not token:
            raise AuthenticationError("Authentication required")
        if len(token) > _MAX_TOKEN_LENGTH or not _TOKEN_PATTERN.match(token):
            raise AuthenticationError("Invalid token")
        body, _, signature = token.partition(".")
        if not hmac.compare_digest(signature, self._sign(body)):
            raise AuthenticationError("Invalid token")
        try:
            payload = json.loads(_b64decode(body))
            subject = str(payload["sub"])
            expires = float(payload["exp"])
            token_id = str(payload["jti"])
        except (ValueError, KeyError, TypeError) as exc:
            raise AuthenticationError("Invalid token") from exc
        if expires <= time.time():
            raise AuthenticationError("Token expired")
        with self._lock:
            if token_id in self._revoked:
                raise AuthenticationError("Token revoked")
        return TokenClaims(subject=subject, expires_at=datetime.fromtimestamp(expires, UTC), token_id=token_id)

    def revoke(self, token: str | None) -> bool:
        """Revoke a valid token. Returns ``False`` if the token was not valid."""
        try:
            claims = self.verify(token)
        except AuthenticationError:
            return False
        with self._lock:
            self._purge_expired_locked()
            self._revoked[claims.token_id] = claims.expires_at.timestamp()
        return True

    def _purge_expired_locked(self) -> None:
        now = time.time()
        for token_id in [tid for tid, exp in self._revoked.items() if exp <= now]:
            del self._revoked[token_id]

    def _sign(self, body: str) -> str:
        return _b64encode(hmac.new(self._secret, body.encode("utf-8"), hashlib.sha256).digest())
