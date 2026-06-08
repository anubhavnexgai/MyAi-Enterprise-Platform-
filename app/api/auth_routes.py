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


# Built-in demo accounts for evaluating the platform without Azure AD wired up.
# DEMO ONLY — passwords are intentionally simple and are surfaced by
# /api/auth/demo-accounts so the login page can show one-click sign-in cards.
# Replace with real SSO (Entra ID) for any non-demo deployment.
DEMO_ACCOUNTS: Dict[str, Dict[str, Any]] = {
    "admin@nexgai.com": {
        "password": "admin123",
        "user_id": "demo.superadmin",
        "username": "admin",
        "full_name": "Aanya Rao (Super Admin)",
        "roles": ["super_admin", "admin", "agent"],
        "label": "Super Admin",
        "blurb": "Full access + the org-wide usage analytics console.",
    },
    "user@nexgai.com": {
        "password": "user123",
        "user_id": "demo.user",
        "username": "user",
        "full_name": "Rohan Mehta",
        "roles": ["user", "agent"],
        "label": "Employee",
        "blurb": "Everyday assistant: chat, agent, email, calendar, research.",
    },
    # Personal / real account — NOT seeded with demo data (see demo_seed.
    # DEMO_USER_IDS). Starts empty; connect Google/Outlook to see YOUR real mail,
    # calendar & tasks. The connector OAuth determines the real mailbox, so the
    # login email below is just a label.
    "me@nexgai.com": {
        "password": "me123",
        "user_id": "real.me",
        "username": "anubhav",
        "full_name": "Anubhav Choudhury",
        "roles": ["super_admin", "admin", "agent"],
        "label": "Anubhav (my real account)",
        "blurb": "Empty until you connect Google/Outlook — then shows ONLY your real data.",
    },
}


@router.get("/demo-accounts")
async def demo_accounts() -> Dict[str, Any]:
    """List the built-in demo accounts (DEMO ONLY — includes passwords) so the
    login page can render one-click sign-in cards."""
    return {
        "accounts": [
            {
                "email": email,
                "password": a["password"],
                "label": a["label"],
                "full_name": a["full_name"],
                "blurb": a["blurb"],
                "is_admin": "super_admin" in a["roles"],
            }
            for email, a in DEMO_ACCOUNTS.items()
        ]
    }


@router.post("/login")
async def demo_login(payload: Dict[str, Any], response: Response) -> Dict[str, Any]:
    """Username/password login against the built-in demo accounts. Issues the
    same MyAi JWT cookie the SSO path does, so the rest of the app is identical."""
    email = str(payload.get("email", "")).strip().lower()
    password = str(payload.get("password", ""))
    acct = DEMO_ACCOUNTS.get(email)
    if not acct or not secrets.compare_digest(password, acct["password"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    s = get_settings()
    tenant_id = s.default_tenant_id
    token = create_access_token(
        user_id=acct["user_id"],
        email=email,
        username=acct["username"],
        full_name=acct["full_name"],
        tenant_id=tenant_id,
        roles=acct["roles"],
        sso_provider="demo",
    )

    # Surface the account in the employee directory so the super-admin sees it.
    from app.services.employees import upsert_employee
    await upsert_employee(
        tenant_id=tenant_id,
        user_id=acct["user_id"],
        email=email,
        full_name=acct["full_name"],
        roles=acct["roles"],
        touch_login=True,
    )

    # Demo accounts get a permissive autonomy level (L4 Guarded Auto) so every
    # feature — email actions, calendar, tasks — is usable out of the box. The
    # default (L1 Observe) hard-blocks all writes.
    try:
        from app.tenants.router import get_tenant_router
        from app.storage.models import UserPreference
        from sqlalchemy import select as _sel
        rdb = get_tenant_router()
        async with rdb.session_for(tenant_id) as _s:
            _r = await _s.execute(
                _sel(UserPreference)
                .where(UserPreference.tenant_id == tenant_id)
                .where(UserPreference.creator_id == acct["user_id"])
            )
            _pref = _r.scalars().first()
            if _pref is None:
                _s.add(UserPreference(tenant_id=tenant_id, creator_id=acct["user_id"], autonomy_level=4, data={}))
            else:
                _pref.autonomy_level = 4
            await _s.commit()
    except Exception as _exc:  # noqa: BLE001
        logger.warning("demo autonomy set failed: %s", _exc)

    # Demo accounts get a fresh, rich synthetic dataset (mail / tasks / calendar)
    # each login. The personal "My Real Account" is left untouched so it only
    # ever shows the user's own connected data.
    try:
        from app.services.demo_seed import is_demo_user, seed_demo_data
        if is_demo_user(acct["user_id"]):
            seeded = await seed_demo_data(tenant_id=tenant_id, user_id=acct["user_id"], recipient_name=acct["full_name"])
            logger.info("Seeded demo data for %s: %s", acct["user_id"], seeded)
    except Exception as _exc:  # noqa: BLE001
        logger.warning("demo data seed failed: %s", _exc)

    await get_audit_service().log(
        tenant_id=tenant_id,
        user_id=acct["user_id"],
        event_type="auth.login",
        message=f"Demo login: {email}",
        payload={"provider": "demo"},
    )

    response.set_cookie(
        "access_token",
        token,
        httponly=True,
        samesite="lax",
        max_age=s.access_token_expire_minutes * 60,
    )
    return {
        "ok": True,
        "user": {
            "id": acct["user_id"],
            "email": email,
            "full_name": acct["full_name"],
            "roles": acct["roles"],
            "tenant_id": tenant_id,
        },
    }


@router.post("/logout")
async def logout(response: Response) -> Dict[str, str]:
    """Clear the auth cookie (works for demo + SSO sessions alike)."""
    response.delete_cookie("access_token")
    return {"status": "logged_out"}


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

    # Provision (or refresh) the employee directory row so the super-admin can
    # see everyone who has signed in, and stamp last-login.
    from app.services.employees import upsert_employee
    await upsert_employee(
        tenant_id=tenant_id,
        user_id=sso_user.user_id,
        email=sso_user.email,
        full_name=sso_user.full_name,
        roles=sso_user.roles or ["user"],
        touch_login=True,
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
