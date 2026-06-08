"""FastAPI authentication middleware.

Every request is funneled through ``AuthMiddleware``. On success it sets
``request.state.user`` to a ``PlatformTokenClaims`` instance, which downstream
endpoints (and ``services.harvester_gateway``) rely on for tenant + user scoping.

In DEV_MODE the middleware fabricates a synthetic user so contributors can run
the stack without Azure AD wired up.
"""

from __future__ import annotations

import logging
import time
from typing import Callable, Iterable, List, Optional, Set

from fastapi import HTTPException, Request, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.auth.jwt import PlatformTokenClaims, decode_token
from app.config import get_settings

logger = logging.getLogger(__name__)


# Routes that must remain reachable without authentication.
PUBLIC_PREFIXES: tuple[str, ...] = (
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/static",
    "/web",
    "/favicon.ico",
    "/api/auth/sso/login",
    "/api/auth/sso/callback",
    "/api/auth/sso/logout",
    "/api/auth/dev-login",
    "/api/auth/login",
    "/api/auth/logout",
    "/api/auth/demo-accounts",
)

# Exact public paths (the SPA shell + page fragments need to load before login)
PUBLIC_EXACT: Set[str] = {
    "/",
}


def _is_public(path: str, extra_prefixes: Iterable[str] = ()) -> bool:
    if path in PUBLIC_EXACT:
        return True
    for prefix in PUBLIC_PREFIXES:
        if path.startswith(prefix):
            return True
    for prefix in extra_prefixes:
        if path.startswith(prefix):
            return True
    # The SPA loads `/pages/*.html` and `/app.js` directly off the web root
    if path.startswith("/pages/") or path in {"/app.js", "/styles.css", "/sw.js", "/manifest.json"}:
        return True
    return False


def _dev_user() -> PlatformTokenClaims:
    s = get_settings()
    return PlatformTokenClaims(
        sub=s.dev_user_id,
        email=s.dev_user_email,
        username=s.dev_user_email.split("@", 1)[0],
        full_name=s.dev_user_name,
        tenant_id=s.dev_tenant_id,
        roles=s.dev_user_role_list or ["user"],
        sso_provider="dev",
    )


class AuthMiddleware(BaseHTTPMiddleware):
    """Validate the bearer/cookie JWT and inject ``request.state.user``."""

    def __init__(self, app, public_prefixes: Optional[List[str]] = None) -> None:
        super().__init__(app)
        self.extra_public = tuple(public_prefixes or ())
        self.settings = get_settings()

    async def dispatch(self, request: Request, call_next: Callable):
        start = time.time()
        path = request.url.path

        if _is_public(path, self.extra_public):
            response = await call_next(request)
            response.headers["X-Process-Time"] = f"{(time.time() - start):.3f}"
            return response

        token = self._extract_token(request)

        # DEV_MODE: accept anything (even missing token). The synthesised user
        # is bound to the configured DEV_TENANT_ID so the rest of the stack
        # still respects multi-tenant scoping.
        if not token and self.settings.dev_mode:
            request.state.user = _dev_user()
            request.state.token = None
            response = await call_next(request)
            response.headers["X-Process-Time"] = f"{(time.time() - start):.3f}"
            response.headers["X-Dev-Auth"] = "true"
            return response

        if not token:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Authentication required"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        try:
            claims = decode_token(token, expected_type="access")
        except ValueError as e:
            # In DEV_MODE, an invalid token still falls back to the dev user
            if self.settings.dev_mode:
                logger.warning("Invalid token in DEV_MODE, falling back to dev user: %s", e)
                request.state.user = _dev_user()
                request.state.token = None
                response = await call_next(request)
                response.headers["X-Process-Time"] = f"{(time.time() - start):.3f}"
                return response
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": f"Invalid token: {e}"},
                headers={"WWW-Authenticate": "Bearer"},
            )
        except Exception as e:  # pragma: no cover - defensive
            logger.exception("Unexpected auth error")
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={"detail": f"Authentication error: {e}"},
            )

        request.state.user = claims
        request.state.token = token
        response = await call_next(request)
        response.headers["X-Process-Time"] = f"{(time.time() - start):.3f}"
        return response

    @staticmethod
    def _extract_token(request: Request) -> Optional[str]:
        auth = request.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        cookie_tok = request.cookies.get("access_token")
        if cookie_tok:
            return cookie_tok
        return None


# ---- FastAPI dependencies ----


async def get_current_user(request: Request) -> PlatformTokenClaims:
    """Return ``request.state.user``, falling back to dev user when configured.

    Routes that want explicit Depends() can use this. The middleware will have
    already populated state for non-public routes.
    """
    user = getattr(request.state, "user", None)
    if user is None:
        if get_settings().dev_mode:
            return _dev_user()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def require_roles(required: List[str]) -> Callable:
    async def _check(request: Request) -> PlatformTokenClaims:
        user = await get_current_user(request)
        if not any(r in user.roles for r in required):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires one of roles: {', '.join(required)}",
            )
        return user

    return _check
