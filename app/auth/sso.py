"""Azure AD OIDC provider.

Minimal version of EAP's sso_provider.py focused on the Azure AD authorization-code
flow. The provider is constructed lazily so the app can still boot without
Azure credentials (DEV_MODE handles that case in middleware.py).
"""

from __future__ import annotations

import logging
import secrets
from functools import lru_cache
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel, Field

from app.config import get_settings

logger = logging.getLogger(__name__)


class SSOUser(BaseModel):
    user_id: str
    email: str
    username: str
    full_name: Optional[str] = None
    given_name: Optional[str] = None
    family_name: Optional[str] = None
    groups: List[str] = Field(default_factory=list)
    roles: List[str] = Field(default_factory=list)
    picture: Optional[str] = None
    provider: str = "azure_ad"
    raw: Dict[str, Any] = Field(default_factory=dict)


class AzureADProvider:
    """Azure AD authorization-code (OIDC) flow."""

    def __init__(
        self,
        *,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        scopes: Optional[List[str]] = None,
    ) -> None:
        if not all([tenant_id, client_id, client_secret, redirect_uri]):
            raise ValueError("Azure AD provider requires tenant/client id, secret, and redirect URI")
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.scopes = scopes or ["openid", "profile", "email", "User.Read"]

        base = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0"
        self.authorize_endpoint = f"{base}/authorize"
        self.token_endpoint = f"{base}/token"
        self.graph_me_endpoint = "https://graph.microsoft.com/v1.0/me"

    # ---- step 1: authorize URL ----------------------------------------------

    def authorization_url(self, *, state: Optional[str] = None) -> Dict[str, str]:
        state = state or secrets.token_urlsafe(32)
        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "response_mode": "query",
            "scope": " ".join(self.scopes),
            "state": state,
        }
        url = f"{self.authorize_endpoint}?{urlencode(params)}"
        return {"url": url, "state": state}

    # ---- step 2: token exchange + userinfo ----------------------------------

    async def exchange_code(self, code: str) -> Dict[str, Any]:
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "scope": " ".join(self.scopes),
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(self.token_endpoint, data=data)
        if resp.status_code != 200:
            logger.error("Azure AD token exchange failed: %s", resp.text[:500])
            raise ValueError(f"Token exchange failed: HTTP {resp.status_code}")
        return resp.json()

    async def fetch_userinfo(self, access_token: str) -> SSOUser:
        headers = {"Authorization": f"Bearer {access_token}"}
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(self.graph_me_endpoint, headers=headers)
        if resp.status_code != 200:
            logger.error("Graph /me call failed: %s", resp.text[:500])
            raise ValueError(f"Userinfo fetch failed: HTTP {resp.status_code}")
        data = resp.json()
        email = data.get("mail") or data.get("userPrincipalName") or ""
        return SSOUser(
            user_id=data.get("id") or email,
            email=email,
            username=(email.split("@", 1)[0] if email else data.get("id", "user")),
            full_name=data.get("displayName"),
            given_name=data.get("givenName"),
            family_name=data.get("surname"),
            provider="azure_ad",
            raw=data,
        )

    async def handle_callback(self, code: str) -> SSOUser:
        tokens = await self.exchange_code(code)
        access_token = tokens.get("access_token")
        if not access_token:
            raise ValueError("Azure AD did not return an access token")
        return await self.fetch_userinfo(access_token)


@lru_cache
def get_sso_provider() -> Optional[AzureADProvider]:
    """Return an AzureADProvider if configured, else None.

    Routes that need SSO should check for None and surface a clear error
    rather than crashing at import time.
    """
    s = get_settings()
    if not (s.azure_tenant_id and s.azure_client_id and s.azure_client_secret):
        logger.info("Azure AD not configured - SSO disabled (DEV_MODE expected)")
        return None
    try:
        return AzureADProvider(
            tenant_id=s.azure_tenant_id,
            client_id=s.azure_client_id,
            client_secret=s.azure_client_secret,
            redirect_uri=s.azure_redirect_uri,
        )
    except Exception as e:  # pragma: no cover - defensive
        logger.error("Failed to initialise Azure AD provider: %s", e)
        return None
