"""Bearer-token authentication middleware for the collector API.

Authentication is **disabled by default** so local development, the bundled
test suite, and the existing Windows agent workflow keep working without
extra configuration.  Operators who expose the collector beyond
``localhost`` can enable it by setting ``AUTH_TOKEN`` to a non-empty value.

When enabled, requests must include the configured token either as:

* an ``Authorization: Bearer <token>`` header, or
* an ``X-Auth-Token: <token>`` header (useful for the static dashboard
  whose browser fetch API does not naturally include ``Authorization``).

The following endpoints are always public, even when authentication is
enabled, because orchestrators and dashboards depend on them:

* ``GET /health`` and ``GET /ready`` (liveness/readiness probes)
* ``GET /docs`` and ``GET /openapi.json`` are protected only when
  ``AUTH_PROTECT_DOCS=true`` is set; otherwise they remain public so the
  bundled dashboard and external documentation viewers keep working.

Tokens are compared with :func:`hmac.compare_digest` so a timing-attack
attacker cannot use response-time differences to guess the secret.
"""
from __future__ import annotations

import hmac
import logging
from typing import Iterable

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .config import Settings

logger = logging.getLogger("process_monitor.auth")

# Endpoints that are always public.  Trailing slashes are normalized; the
# matcher only checks the path, not query strings.
DEFAULT_PUBLIC_PATHS: tuple[str, ...] = (
    "/health",
    "/ready",
    "/api/health",
    "/api/ready",
    "/favicon.ico",
)


def _extract_token(request: Request) -> str | None:
    """Return the bearer token from the request, or ``None``."""
    auth_header = request.headers.get("authorization")
    if auth_header:
        scheme, _, value = auth_header.partition(" ")
        if scheme.lower() == "bearer" and value:
            return value.strip()
    custom = request.headers.get("x-auth-token")
    if custom:
        return custom.strip()
    return None


def _is_public(path: str, protected_docs: bool) -> bool:
    """Return ``True`` for endpoints that bypass authentication."""
    if path in DEFAULT_PUBLIC_PATHS:
        return True
    if not protected_docs and path in {"/docs", "/openapi.json", "/redoc"}:
        return True
    if path.startswith("/docs") or path.startswith("/openapi") or path.startswith("/redoc"):
        # ``/docs/oauth2-redirect`` etc. are served as part of the docs
        # surface and inherit the docs protection.
        return not protected_docs
    return False


class AuthMiddleware(BaseHTTPMiddleware):
    """Reject unauthenticated requests when ``AUTH_TOKEN`` is configured."""

    def __init__(self, app, settings: Settings, public_paths: Iterable[str] | None = None):
        super().__init__(app)
        self.settings = settings
        # ``public_paths`` is mostly here for tests that want to add new
        # always-public endpoints without touching the default list.
        self._extras = tuple(public_paths or ())

    async def dispatch(self, request: Request, call_next):
        if not self.settings.auth_enabled:
            return await call_next(request)
        path = request.url.path
        if path in self._extras or _is_public(path, self.settings.auth_protect_docs):
            return await call_next(request)
        provided = _extract_token(request)
        if not provided or not hmac.compare_digest(provided, self.settings.auth_token):
            # Authentication is enforced even on read-only paths so a
            # leaked collector URL is not a free data channel.
            logger.info("Rejected request without valid auth token", extra={"path": path})
            return JSONResponse(
                status_code=401,
                content={"error": {"code": "unauthorized", "message": "A valid auth token is required."}},
                headers={"WWW-Authenticate": "Bearer"},
            )
        return await call_next(request)


def install_auth(app, settings: Settings, public_paths: Iterable[str] | None = None) -> None:
    """Install the bearer-token middleware on a FastAPI app.

    Safe to call multiple times — only one :class:`AuthMiddleware` will be
    added to avoid duplicate 401 responses.
    """
    already = any(
        getattr(middleware.cls, "__name__", "") == "AuthMiddleware" for middleware in app.user_middleware
    )
    if already:
        return
    app.add_middleware(AuthMiddleware, settings=settings, public_paths=list(public_paths or []))
