"""FastAPI routes for the connector framework.

All routes are tenant + user scoped via `request.state.user`. The OAuth
callback recovers the user_id from the signed `state` parameter (not from the
session) so the flow is safe even if the callback hits a fresh session.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.services.connector_manager import (
    ConnectorError,
    PROVIDERS,
    get_connector_manager,
    get_provider,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/connectors", tags=["connectors"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _current_user(request: Request) -> tuple[str, str]:
    """Return (user_id, tenant_id) for the request.

    Falls back to dev-mode defaults when AuthMiddleware hasn't populated
    request.state.user yet (e.g. running stand-alone). Production deployments
    should ensure request.state.user is always set before this is called.
    """
    user_obj = getattr(request.state, "user", None)
    if user_obj is not None:
        # PlatformTokenClaims uses `sub` for the stable user id; fall back to
        # legacy fields if a different claims type is in use.
        user_id = (
            getattr(user_obj, "sub", None)
            or getattr(user_obj, "user_id", None)
            or getattr(user_obj, "id", None)
            or getattr(user_obj, "email", None)
        )
        tenant_id = getattr(user_obj, "tenant_id", None) or "nexgai"
        if user_id:
            return user_id, tenant_id
    # Dev fallback.
    import os

    return (
        os.environ.get("DEV_USER_ID", "dev.user"),
        os.environ.get("DEV_TENANT_ID", "nexgai"),
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


_VISIBLE_PROVIDERS = {"google_gmail", "google_calendar", "microsoft_graph"}


@router.get("")
async def list_connectors(request: Request) -> dict[str, Any]:
    """List available connectors with the current user's connection status."""
    user_id, tenant_id = _current_user(request)
    manager = get_connector_manager()
    connections = await manager.list_connections(user_id, tenant_id)
    # Trim to the providers we actively support in the UI.
    visible = [c for c in connections if c["provider"] in _VISIBLE_PROVIDERS]
    return {"user_id": user_id, "tenant_id": tenant_id, "connectors": visible}


@router.get("/{provider}/connect")
async def begin_connect(provider: str, request: Request) -> dict[str, Any]:
    """Return the OAuth consent URL for the given provider."""
    if provider not in PROVIDERS:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider}")
    user_id, tenant_id = _current_user(request)
    manager = get_connector_manager()
    try:
        url = await manager.get_auth_url(provider, user_id, tenant_id)
    except ConnectorError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"auth_url": url, "provider": provider}


@router.get("/{provider}/callback")
async def oauth_callback(
    provider: str,
    request: Request,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
) -> HTMLResponse:
    """OAuth redirect target. Exchanges the auth code, stores the tokens,
    then returns a small HTML page that signals success back to the opener.
    """
    if provider not in PROVIDERS:
        return _result_page(
            ok=False, provider=provider, message=f"Unknown provider: {provider}"
        )
    if error:
        return _result_page(
            ok=False,
            provider=provider,
            message=f"Authorization denied: {error_description or error}",
        )
    if not code or not state:
        return _result_page(
            ok=False,
            provider=provider,
            message="Missing code or state in callback.",
        )

    manager = get_connector_manager()
    try:
        result = await manager.handle_callback(provider, code=code, state=state)
    except ConnectorError as exc:
        logger.warning("Callback failed for %s: %s", provider, exc)
        return _result_page(ok=False, provider=provider, message=str(exc))
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Unexpected callback failure")
        return _result_page(
            ok=False, provider=provider, message=f"Unexpected error: {exc}"
        )

    spec = get_provider(provider)
    return _result_page(
        ok=True,
        provider=provider,
        message=(
            f"Connected {spec.display_name}"
            + (f" as {result.get('account_label')}" if result.get("account_label") else "")
            + ". You can close this window."
        ),
    )


@router.delete("/{provider}")
async def disconnect(provider: str, request: Request) -> dict[str, Any]:
    if provider not in PROVIDERS:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider}")
    user_id, tenant_id = _current_user(request)
    manager = get_connector_manager()
    removed = await manager.revoke(provider, user_id, tenant_id)
    return {"provider": provider, "disconnected": removed}


@router.get("/{provider}/status")
async def connector_status(provider: str, request: Request) -> dict[str, Any]:
    """Lightweight status endpoint used by the frontend polling loop."""
    if provider not in PROVIDERS:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider}")
    user_id, tenant_id = _current_user(request)
    manager = get_connector_manager()
    info = await manager.get_connection(provider, user_id, tenant_id)
    return {
        "provider": provider,
        "connected": info is not None,
        "account_label": info.get("account_label") if info else None,
        "connected_at": info.get("connected_at") if info else None,
    }


# ---------------------------------------------------------------------------
# Helper: small HTML success/failure page (no template engine needed)
# ---------------------------------------------------------------------------


def _result_page(*, ok: bool, provider: str, message: str) -> HTMLResponse:
    color = "#0ea5a5" if ok else "#c2410c"
    title = "Connected" if ok else "Connection failed"
    js_status = "ok" if ok else "error"
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>{title} — MyAi</title>
  <style>
    html, body {{
      margin: 0; height: 100%;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #f8fafc; color: #0f172a;
      display: flex; align-items: center; justify-content: center;
    }}
    .card {{
      background: #ffffff;
      border: 1px solid #e2e8f0;
      border-radius: 14px;
      padding: 32px 36px;
      max-width: 460px;
      box-shadow: 0 1px 3px rgba(15, 23, 42, 0.05);
      text-align: center;
    }}
    h1 {{ margin: 0 0 12px 0; font-size: 22px; color: {color}; }}
    p  {{ margin: 6px 0; line-height: 1.45; color: #475569; }}
    code {{
      background: #f1f5f9; padding: 2px 6px; border-radius: 4px;
      font-size: 13px; color: #334155;
    }}
    button {{
      margin-top: 18px; padding: 8px 16px;
      background: #0ea5a5; color: #ffffff; border: none;
      border-radius: 8px; font-size: 14px; cursor: pointer;
    }}
  </style>
</head>
<body>
  <div class="card">
    <h1>{title}</h1>
    <p>{message}</p>
    <p><code>{provider}</code></p>
    <button onclick="window.close()">Close window</button>
  </div>
  <script>
    try {{
      if (window.opener && !window.opener.closed) {{
        window.opener.postMessage(
          {{ type: 'myai:connector', status: '{js_status}', provider: '{provider}' }},
          '*'
        );
      }}
    }} catch (e) {{}}
    // Auto-close after 2.5s on success.
    if ('{js_status}' === 'ok') {{
      setTimeout(() => {{ try {{ window.close(); }} catch (e) {{}} }}, 2500);
    }}
  </script>
</body>
</html>"""
    return HTMLResponse(content=html, status_code=200 if ok else 400)
