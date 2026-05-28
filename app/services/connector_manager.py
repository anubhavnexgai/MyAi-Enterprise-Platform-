"""Per-user OAuth connector manager.

All tokens are encrypted at rest via Fernet (key from FERNET_KEY env var) and
scoped by (tenant_id, user_id, provider) in the user_connections table. The
`state` parameter on each OAuth URL is an HMAC-signed payload binding the auth
flow to a specific user_id so a callback cannot leak tokens across users.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Provider catalog
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    display_name: str
    description: str
    icon: str  # short label / emoji-free string used by the UI
    authorize_url: str
    token_url: str
    revoke_url: str | None
    userinfo_url: str | None
    scopes: list[str] = field(default_factory=list)
    client_id_env: str = ""
    client_secret_env: str = ""
    redirect_uri_env: str = ""
    extra_authorize_params: dict[str, str] = field(default_factory=dict)


PROVIDERS: dict[str, ProviderSpec] = {
    "google_gmail": ProviderSpec(
        name="google_gmail",
        display_name="Gmail",
        description="Read and send email from your Gmail inbox.",
        icon="Gmail",
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        revoke_url="https://oauth2.googleapis.com/revoke",
        userinfo_url="https://openidconnect.googleapis.com/v1/userinfo",
        scopes=[
            "openid",
            "email",
            "profile",
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.send",
        ],
        client_id_env="GOOGLE_CLIENT_ID",
        client_secret_env="GOOGLE_CLIENT_SECRET",
        redirect_uri_env="GOOGLE_GMAIL_REDIRECT_URI",
        extra_authorize_params={
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
        },
    ),
    "google_calendar": ProviderSpec(
        name="google_calendar",
        display_name="Google Calendar",
        description="Read and create events on your Google Calendar.",
        icon="GCal",
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        revoke_url="https://oauth2.googleapis.com/revoke",
        userinfo_url="https://openidconnect.googleapis.com/v1/userinfo",
        scopes=[
            "openid",
            "email",
            "profile",
            "https://www.googleapis.com/auth/calendar",
            "https://www.googleapis.com/auth/calendar.events",
        ],
        client_id_env="GOOGLE_CLIENT_ID",
        client_secret_env="GOOGLE_CLIENT_SECRET",
        redirect_uri_env="GOOGLE_CALENDAR_REDIRECT_URI",
        extra_authorize_params={
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
        },
    ),
    "google_drive": ProviderSpec(
        name="google_drive",
        display_name="Google Drive",
        description="Search and read your Google Drive documents.",
        icon="Drive",
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        revoke_url="https://oauth2.googleapis.com/revoke",
        userinfo_url="https://openidconnect.googleapis.com/v1/userinfo",
        scopes=[
            "openid",
            "email",
            "profile",
            "https://www.googleapis.com/auth/drive.readonly",
            "https://www.googleapis.com/auth/drive.file",
        ],
        client_id_env="GOOGLE_CLIENT_ID",
        client_secret_env="GOOGLE_CLIENT_SECRET",
        redirect_uri_env="GOOGLE_DRIVE_REDIRECT_URI",
        extra_authorize_params={
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
        },
    ),
    "microsoft_graph": ProviderSpec(
        name="microsoft_graph",
        display_name="Microsoft 365",
        description="Outlook mail, Calendar, OneDrive — one connection for M365.",
        icon="M365",
        authorize_url="https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        token_url="https://login.microsoftonline.com/common/oauth2/v2.0/token",
        revoke_url=None,
        userinfo_url="https://graph.microsoft.com/v1.0/me",
        scopes=[
            "openid",
            "email",
            "profile",
            "offline_access",
            "User.Read",
            "Mail.Read",
            "Mail.Send",
            "Calendars.ReadWrite",
            "Files.Read.All",
        ],
        client_id_env="MICROSOFT_CLIENT_ID",
        client_secret_env="MICROSOFT_CLIENT_SECRET",
        redirect_uri_env="MICROSOFT_REDIRECT_URI",
        extra_authorize_params={"response_mode": "query"},
    ),
    "slack": ProviderSpec(
        name="slack",
        display_name="Slack",
        description="Send and read messages from your Slack workspace.",
        icon="Slack",
        authorize_url="https://slack.com/oauth/v2/authorize",
        token_url="https://slack.com/api/oauth.v2.access",
        revoke_url="https://slack.com/api/auth.revoke",
        userinfo_url=None,
        scopes=["chat:write", "channels:read", "channels:history", "users:read"],
        client_id_env="SLACK_CLIENT_ID",
        client_secret_env="SLACK_CLIENT_SECRET",
        redirect_uri_env="SLACK_REDIRECT_URI",
    ),
    "notion": ProviderSpec(
        name="notion",
        display_name="Notion",
        description="Search and read your Notion workspace.",
        icon="Notion",
        authorize_url="https://api.notion.com/v1/oauth/authorize",
        token_url="https://api.notion.com/v1/oauth/token",
        revoke_url=None,
        userinfo_url=None,
        scopes=[],
        client_id_env="NOTION_CLIENT_ID",
        client_secret_env="NOTION_CLIENT_SECRET",
        redirect_uri_env="NOTION_REDIRECT_URI",
        extra_authorize_params={"owner": "user"},
    ),
}


def get_provider(name: str) -> ProviderSpec:
    spec = PROVIDERS.get(name)
    if not spec:
        raise ValueError(f"Unknown connector provider: {name}")
    return spec


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ConnectorError(Exception):
    """Generic connector error."""


class NotConnectedError(ConnectorError):
    """Raised (or signalled via None) when the user has not connected a provider."""


# ---------------------------------------------------------------------------
# Crypto helpers
# ---------------------------------------------------------------------------


def _load_fernet() -> Fernet:
    key = os.environ.get("FERNET_KEY", "").strip()
    if not key:
        # Derive a stable dev key from JWT_SECRET_KEY so local dev works without
        # extra setup. In production, FERNET_KEY MUST be set.
        seed = os.environ.get("JWT_SECRET_KEY", "myai-enterprise-dev-fallback").encode()
        key = base64.urlsafe_b64encode(hashlib.sha256(seed).digest()).decode()
        logger.warning(
            "FERNET_KEY not set; deriving an ephemeral key from JWT_SECRET_KEY. "
            "Set FERNET_KEY in production."
        )
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except Exception as exc:  # pragma: no cover - defensive
        raise ConnectorError(f"Invalid FERNET_KEY: {exc}") from exc


def _hmac_secret() -> bytes:
    return os.environ.get(
        "SESSION_SECRET",
        os.environ.get("JWT_SECRET_KEY", "myai-enterprise-state-secret"),
    ).encode()


def _sign_state(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    sig = hmac.new(_hmac_secret(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def _verify_state(state: str) -> dict[str, Any]:
    try:
        body, sig = state.split(".", 1)
    except ValueError as exc:
        raise ConnectorError("Malformed state parameter") from exc
    expected = hmac.new(_hmac_secret(), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise ConnectorError("State signature mismatch (possible CSRF)")
    padded = body + "=" * (-len(body) % 4)
    raw = base64.urlsafe_b64decode(padded.encode())
    payload = json.loads(raw)
    # Reject states older than 15 minutes.
    if time.time() - float(payload.get("ts", 0)) > 900:
        raise ConnectorError("State token expired")
    return payload


# ---------------------------------------------------------------------------
# Storage (SQLite via aiosqlite — async)
# ---------------------------------------------------------------------------


_DDL = """
CREATE TABLE IF NOT EXISTS user_connections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    account_label TEXT,
    access_token_encrypted BLOB,
    refresh_token_encrypted BLOB,
    expires_at TIMESTAMP,
    scopes TEXT,
    connected_at TIMESTAMP,
    last_refreshed_at TIMESTAMP,
    UNIQUE(user_id, tenant_id, provider)
);
"""


class ConnectorManager:
    """Per-user OAuth connection manager.

    Tokens stored encrypted, scoped to (tenant_id, user_id, provider).
    """

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path or os.environ.get(
            "SQLITE_PATH", "data/myai_enterprise.db"
        )
        # Resolve to absolute path so the manager works regardless of cwd.
        if not os.path.isabs(self._db_path):
            self._db_path = os.path.abspath(self._db_path)
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._fernet = _load_fernet()
        self._initialised = False

    # -- low-level db helpers ------------------------------------------------

    async def _conn(self):
        import aiosqlite

        conn = await aiosqlite.connect(self._db_path)
        conn.row_factory = aiosqlite.Row
        if not self._initialised:
            await conn.executescript(_DDL)
            await conn.commit()
            self._initialised = True
        return conn

    # -- crypto helpers ------------------------------------------------------

    def _encrypt(self, value: str | None) -> bytes | None:
        if value is None:
            return None
        return self._fernet.encrypt(value.encode())

    def _decrypt(self, value: bytes | None) -> str | None:
        if not value:
            return None
        try:
            return self._fernet.decrypt(value).decode()
        except InvalidToken:
            logger.error("Failed to decrypt token (key rotation needed?)")
            return None

    # -- OAuth URL ----------------------------------------------------------

    async def get_auth_url(
        self,
        provider: str,
        user_id: str,
        tenant_id: str,
        redirect_uri: str | None = None,
    ) -> str:
        spec = get_provider(provider)
        client_id = os.environ.get(spec.client_id_env, "").strip()
        if not client_id:
            raise ConnectorError(
                f"{spec.display_name} is not configured: set {spec.client_id_env} "
                f"and {spec.client_secret_env} in the environment."
            )
        final_redirect = (
            redirect_uri
            or os.environ.get(spec.redirect_uri_env, "").strip()
            or f"http://localhost:8002/api/connectors/{provider}/callback"
        )
        state = _sign_state(
            {
                "u": user_id,
                "t": tenant_id,
                "p": provider,
                "n": secrets.token_urlsafe(12),
                "ts": time.time(),
            }
        )
        params = {
            "client_id": client_id,
            "redirect_uri": final_redirect,
            "response_type": "code",
            "scope": " ".join(spec.scopes) if spec.scopes else "",
            "state": state,
        }
        params.update(spec.extra_authorize_params)
        # Drop empty scope param for providers like Notion that take no scope.
        if not params["scope"]:
            params.pop("scope")
        return f"{spec.authorize_url}?{urlencode(params)}"

    # -- OAuth callback -----------------------------------------------------

    async def handle_callback(
        self,
        provider: str,
        code: str,
        state: str,
        redirect_uri: str | None = None,
    ) -> dict[str, Any]:
        """Exchange the auth code for tokens and persist them.

        Returns the parsed token payload (without secrets) plus user_id/tenant_id
        derived from the signed state token. The caller does NOT need to pass
        user_id — it is recovered from `state` to prevent cross-user attacks.
        """
        spec = get_provider(provider)
        payload = _verify_state(state)
        if payload.get("p") != provider:
            raise ConnectorError("State/provider mismatch")
        user_id = payload["u"]
        tenant_id = payload["t"]

        client_id = os.environ.get(spec.client_id_env, "").strip()
        client_secret = os.environ.get(spec.client_secret_env, "").strip()
        final_redirect = (
            redirect_uri
            or os.environ.get(spec.redirect_uri_env, "").strip()
            or f"http://localhost:8002/api/connectors/{provider}/callback"
        )
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": final_redirect,
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                spec.token_url,
                data=data,
                headers={"Accept": "application/json"},
            )
            if resp.status_code >= 400:
                logger.error(
                    "Token exchange failed for %s: %s %s",
                    provider,
                    resp.status_code,
                    resp.text[:300],
                )
                raise ConnectorError(
                    f"Token exchange failed ({resp.status_code}): {resp.text[:200]}"
                )
            token_payload = resp.json()

            account_label = None
            if spec.userinfo_url and token_payload.get("access_token"):
                try:
                    ui = await client.get(
                        spec.userinfo_url,
                        headers={
                            "Authorization": f"Bearer {token_payload['access_token']}"
                        },
                    )
                    if ui.status_code < 400:
                        info = ui.json()
                        account_label = (
                            info.get("email")
                            or info.get("mail")
                            or info.get("userPrincipalName")
                            or info.get("name")
                        )
                except Exception as exc:  # pragma: no cover
                    logger.debug("userinfo fetch failed: %s", exc)

        access_token = token_payload.get("access_token")
        refresh_token = token_payload.get("refresh_token")
        expires_in = int(token_payload.get("expires_in", 3600))
        scope_str = token_payload.get("scope") or " ".join(spec.scopes)
        expires_at = datetime.now(timezone.utc).timestamp() + expires_in

        await self._upsert(
            user_id=user_id,
            tenant_id=tenant_id,
            provider=provider,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            scopes=scope_str,
            account_label=account_label,
        )

        return {
            "provider": provider,
            "user_id": user_id,
            "tenant_id": tenant_id,
            "account_label": account_label,
            "scopes": scope_str,
        }

    async def _upsert(
        self,
        *,
        user_id: str,
        tenant_id: str,
        provider: str,
        access_token: str | None,
        refresh_token: str | None,
        expires_at: float,
        scopes: str,
        account_label: str | None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        conn = await self._conn()
        try:
            # Preserve existing refresh_token if the provider didn't return a new one.
            existing = await conn.execute(
                "SELECT refresh_token_encrypted FROM user_connections "
                "WHERE user_id=? AND tenant_id=? AND provider=?",
                (user_id, tenant_id, provider),
            )
            row = await existing.fetchone()
            rt_blob = self._encrypt(refresh_token) if refresh_token else (
                row["refresh_token_encrypted"] if row else None
            )
            at_blob = self._encrypt(access_token)
            await conn.execute(
                """
                INSERT INTO user_connections
                    (user_id, tenant_id, provider, account_label,
                     access_token_encrypted, refresh_token_encrypted,
                     expires_at, scopes, connected_at, last_refreshed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, tenant_id, provider) DO UPDATE SET
                    account_label = excluded.account_label,
                    access_token_encrypted = excluded.access_token_encrypted,
                    refresh_token_encrypted = excluded.refresh_token_encrypted,
                    expires_at = excluded.expires_at,
                    scopes = excluded.scopes,
                    last_refreshed_at = excluded.last_refreshed_at
                """,
                (
                    user_id,
                    tenant_id,
                    provider,
                    account_label,
                    at_blob,
                    rt_blob,
                    expires_at,
                    scopes,
                    now,
                    now,
                ),
            )
            await conn.commit()
        finally:
            await conn.close()

    # -- token retrieval (with refresh) -------------------------------------

    async def get_token(
        self,
        provider: str,
        user_id: str,
        tenant_id: str = "nexgai",
    ) -> str | None:
        """Return a fresh access token for the user, refreshing if necessary.

        Returns None if the user has not connected this provider.
        """
        conn = await self._conn()
        try:
            cur = await conn.execute(
                "SELECT * FROM user_connections "
                "WHERE user_id=? AND tenant_id=? AND provider=?",
                (user_id, tenant_id, provider),
            )
            row = await cur.fetchone()
        finally:
            await conn.close()
        if not row:
            return None

        expires_at = float(row["expires_at"] or 0)
        # Refresh if within 60s of expiry.
        if expires_at - time.time() > 60:
            return self._decrypt(row["access_token_encrypted"])

        refresh_token = self._decrypt(row["refresh_token_encrypted"])
        if not refresh_token:
            # Token expired and we have no refresh token — best effort: return
            # the (expired) access token so the caller can decide what to do.
            return self._decrypt(row["access_token_encrypted"])

        # Perform refresh.
        spec = get_provider(provider)
        client_id = os.environ.get(spec.client_id_env, "").strip()
        client_secret = os.environ.get(spec.client_secret_env, "").strip()
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        }
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    spec.token_url,
                    data=data,
                    headers={"Accept": "application/json"},
                )
                if resp.status_code >= 400:
                    logger.error(
                        "Refresh failed for %s/%s: %s",
                        provider,
                        user_id,
                        resp.text[:300],
                    )
                    return None
                token_payload = resp.json()
        except Exception as exc:
            logger.error("Refresh request failed: %s", exc)
            return None

        new_access = token_payload.get("access_token")
        new_refresh = token_payload.get("refresh_token") or refresh_token
        expires_in = int(token_payload.get("expires_in", 3600))
        new_expires_at = datetime.now(timezone.utc).timestamp() + expires_in

        await self._upsert(
            user_id=user_id,
            tenant_id=tenant_id,
            provider=provider,
            access_token=new_access,
            refresh_token=new_refresh,
            expires_at=new_expires_at,
            scopes=row["scopes"] or "",
            account_label=row["account_label"],
        )
        return new_access

    # -- revoke -------------------------------------------------------------

    async def revoke(
        self, provider: str, user_id: str, tenant_id: str = "nexgai"
    ) -> bool:
        spec = get_provider(provider)
        conn = await self._conn()
        try:
            cur = await conn.execute(
                "SELECT access_token_encrypted, refresh_token_encrypted "
                "FROM user_connections "
                "WHERE user_id=? AND tenant_id=? AND provider=?",
                (user_id, tenant_id, provider),
            )
            row = await cur.fetchone()
            if not row:
                return False
            access_token = self._decrypt(row["access_token_encrypted"])

            if spec.revoke_url and access_token:
                try:
                    async with httpx.AsyncClient(timeout=10.0) as client:
                        await client.post(
                            spec.revoke_url,
                            data={"token": access_token},
                            headers={
                                "Content-Type": "application/x-www-form-urlencoded"
                            },
                        )
                except Exception as exc:  # pragma: no cover
                    logger.debug("Remote revoke failed (continuing): %s", exc)

            await conn.execute(
                "DELETE FROM user_connections "
                "WHERE user_id=? AND tenant_id=? AND provider=?",
                (user_id, tenant_id, provider),
            )
            await conn.commit()
            return True
        finally:
            await conn.close()

    # -- listing ------------------------------------------------------------

    async def list_connections(
        self, user_id: str, tenant_id: str = "nexgai"
    ) -> list[dict[str, Any]]:
        """List every provider with the user's connection status."""
        conn = await self._conn()
        try:
            cur = await conn.execute(
                "SELECT provider, account_label, scopes, connected_at, "
                "       expires_at, last_refreshed_at "
                "FROM user_connections "
                "WHERE user_id=? AND tenant_id=?",
                (user_id, tenant_id),
            )
            rows = {r["provider"]: r for r in await cur.fetchall()}
        finally:
            await conn.close()

        results: list[dict[str, Any]] = []
        for name, spec in PROVIDERS.items():
            r = rows.get(name)
            client_id = os.environ.get(spec.client_id_env, "").strip()
            results.append(
                {
                    "provider": name,
                    "display_name": spec.display_name,
                    "description": spec.description,
                    "icon": spec.icon,
                    "scopes": spec.scopes,
                    "configured": bool(client_id),
                    "connected": r is not None,
                    "account_label": r["account_label"] if r else None,
                    "connected_at": r["connected_at"] if r else None,
                    "last_refreshed_at": r["last_refreshed_at"] if r else None,
                    "granted_scopes": (r["scopes"] if r else None),
                }
            )
        return results

    async def get_connection(
        self, provider: str, user_id: str, tenant_id: str = "nexgai"
    ) -> dict[str, Any] | None:
        conn = await self._conn()
        try:
            cur = await conn.execute(
                "SELECT provider, account_label, scopes, connected_at, "
                "       expires_at, last_refreshed_at "
                "FROM user_connections "
                "WHERE user_id=? AND tenant_id=? AND provider=?",
                (user_id, tenant_id, provider),
            )
            row = await cur.fetchone()
        finally:
            await conn.close()
        if not row:
            return None
        return dict(row)


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------


_singleton: ConnectorManager | None = None


def get_connector_manager() -> ConnectorManager:
    global _singleton
    if _singleton is None:
        _singleton = ConnectorManager()
    return _singleton
