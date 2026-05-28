"""Auth routes - Azure AD SSO login + callback + dev login + /me."""

from __future__ import annotations

import logging
import secrets
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse

from app.auth.jwt import create_access_token
from app.auth.middleware import get_current_user
from app.auth.jwt import PlatformTokenClaims
from app.auth.sso import get_sso_provider
from app.config import get_settings
from app.services.audit import get_audit_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


# In-memory cache of SSO state -> "expected" payload. Fine for single-process
# dev; would move to Redis (or a signed cookie) in prod.
_SSO_STATE_CACHE: Dict[str, Dict[str, Any]] = {}


@router.get("/sso/login")
async def sso_login() -> RedirectResponse:
    provider = get_sso_provider()
    if not provider:
        raise HTTPException(
            status_code=503,
            detail="Azure AD is not configured. Set AZURE_* env vars or use DEV_MODE.",
        )
    auth = provider.authorization_url()
    _SSO_STATE_CACHE[auth["state"]] = {"created": True}
    return RedirectResponse(url=auth["url"])


@router.get("/sso/callback")
async def sso_callback(
    code: str = Query(...),
    state: str = Query(...),
) -> Response:
    if state not in _SSO_STATE_CACHE:
        raise HTTPException(status_code=400, detail="Unknown or expired state")
    _SSO_STATE_CACHE.pop(state, None)

    provider = get_sso_provider()
    if not provider:
        raise HTTPException(status_code=503, detail="SSO not configured")

    sso_user = await provider.handle_callback(code)

    settings = get_settings()
    tenant_id = settings.default_tenant_id
    token = create_access_token(
        user_id=sso_user.user_id,
        email=sso_user.email,
        username=sso_user.username,
        full_name=sso_user.full_name,
        tenant_id=tenant_id,
        roles=sso_user.roles or ["user"],
        sso_provider="azure_ad",
        sso_groups=sso_user.groups,
        picture=sso_user.picture,
    )

    await get_audit_service().log(
        tenant_id=tenant_id,
        user_id=sso_user.user_id,
        event_type="auth.sso_login",
        message=f"SSO login: {sso_user.email}",
        payload={"provider": "azure_ad"},
    )

    resp = RedirectResponse(url="/")
    resp.set_cookie(
        "access_token",
        token,
        httponly=True,
        samesite="lax",
        max_age=settings.access_token_expire_minutes * 60,
    )
    return resp


@router.post("/sso/logout")
async def sso_logout(response: Response) -> Dict[str, str]:
    response.delete_cookie("access_token")
    return {"status": "logged_out"}


@router.post("/dev-login")
async def dev_login() -> Dict[str, Any]:
    """DEV_MODE helper: returns a fresh token without touching Azure AD."""
    s = get_settings()
    if not s.dev_mode:
        raise HTTPException(status_code=403, detail="DEV_MODE is disabled")
    token = create_access_token(
        user_id=s.dev_user_id,
        email=s.dev_user_email,
        username=s.dev_user_email.split("@", 1)[0],
        full_name=s.dev_user_name,
        tenant_id=s.dev_tenant_id,
        roles=s.dev_user_role_list or ["user"],
        sso_provider="dev",
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": s.dev_user_id,
            "email": s.dev_user_email,
            "name": s.dev_user_name,
            "tenant_id": s.dev_tenant_id,
            "roles": s.dev_user_role_list,
        },
    }


@router.get("/me")
async def whoami(
    request: Request,
    user: PlatformTokenClaims = Depends(get_current_user),
) -> Dict[str, Any]:
    return {
        "id": user.sub,
        "email": user.email,
        "username": user.username,
        "full_name": user.full_name,
        "tenant_id": user.tenant_id,
        "roles": user.roles,
        "sso_provider": user.sso_provider,
    }
