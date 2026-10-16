"""Optional single-user authentication (UI_USERNAME / UI_PASSWORD from backend/.env)."""

from __future__ import annotations

import logging
import threading
import time
from collections import deque

from config.settings import Settings
from core.exceptions import AppError, AuthenticationError
from core.security import IssuedToken, TokenClaims, TokenManager, verify_credentials
from schemas.auth import AuthStatus

logger = logging.getLogger(__name__)

_FAILURE_WINDOW_S = 300.0
_MAX_FAILURES_PER_WINDOW = 10


class TooManyLoginAttemptsError(AppError):
    status_code = 429
    code = "too_many_requests"


class _LoginThrottle:
    """Per-client sliding window of failed logins (simple brute-force protection)."""

    def __init__(self) -> None:
        self._failures: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, client: str) -> None:
        with self._lock:
            window = self._prune(client)
            if len(window) >= _MAX_FAILURES_PER_WINDOW:
                retry_after = int(_FAILURE_WINDOW_S - (time.monotonic() - window[0])) + 1
                raise TooManyLoginAttemptsError(
                    "Too many failed login attempts; try again later", details={"retry_after_seconds": retry_after}
                )

    def record_failure(self, client: str) -> None:
        with self._lock:
            self._prune(client).append(time.monotonic())

    def reset(self, client: str) -> None:
        with self._lock:
            self._failures.pop(client, None)

    def _prune(self, client: str) -> deque[float]:
        window = self._failures.setdefault(client, deque())
        cutoff = time.monotonic() - _FAILURE_WINDOW_S
        while window and window[0] < cutoff:
            window.popleft()
        return window


class AuthService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        secret = settings.auth_secret.get_secret_value().encode("utf-8") if settings.auth_secret else None
        self._tokens = TokenManager(secret, ttl_seconds=settings.auth_token_ttl_hours * 3600.0)
        self._throttle = _LoginThrottle()
        if settings.auth_required and not settings.has_ui_credentials:
            logger.warning("Authentication is enabled but UI_USERNAME/UI_PASSWORD are not set; logins will fail")

    @property
    def required(self) -> bool:
        return self._settings.auth_required

    def login(self, username: str, password: str, *, client: str = "unknown") -> IssuedToken:
        self._throttle.check(client)
        settings = self._settings
        if not settings.has_ui_credentials:
            raise AuthenticationError("Authentication is not configured on this server")
        assert settings.ui_username is not None and settings.ui_password is not None
        if not verify_credentials(
            username, password, settings.ui_username.get_secret_value(), settings.ui_password.get_secret_value()
        ):
            self._throttle.record_failure(client)
            logger.warning("Failed login attempt from %s", client)
            raise AuthenticationError("Invalid username or password")
        self._throttle.reset(client)
        issued = self._tokens.issue(username)
        logger.info("User logged in from %s", client)
        return issued

    def logout(self, token: str | None) -> None:
        if self._tokens.revoke(token):
            logger.info("Token revoked (logout)")

    def authenticate(self, token: str | None) -> TokenClaims:
        """Validate a bearer token (raises :class:`AuthenticationError`)."""
        return self._tokens.verify(token)

    def authorize(self, token: str | None) -> TokenClaims | None:
        """``None`` when auth is disabled; otherwise the claims of a valid token (or 401)."""
        if not self.required:
            return None
        return self.authenticate(token)

    def status(self, token: str | None) -> AuthStatus:
        claims: TokenClaims | None = None
        if token:
            try:
                claims = self.authenticate(token)
            except AuthenticationError:
                claims = None
        return AuthStatus(
            auth_required=self.required,
            authenticated=claims is not None or not self.required,
            username=claims.subject if claims else None,
        )
