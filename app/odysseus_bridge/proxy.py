"""Reverse proxy for the per-tenant Odysseus instances.

Mounts ``/api/oui/{path}`` -> ``http://<loopback>:<instance-port>/api/{path}``.

The caller is authenticated by MyAi's ``AuthMiddleware`` (this prefix is NOT in
PUBLIC_PREFIXES). We resolve ``(tenant_id, creator)`` from ``request.state.user``,
ensure the tenant's instance is running and the creator is provisioned, then
stream the request through — injecting the trusted-proxy identity headers and
stripping anything that would (a) trip Odysseus's ``_is_trusted_loopback`` guard
(``x-forwarded-*``/``forwarded``) or (b) leak MyAi's own auth (cookie/bearer).
"""

from __future__ import annotations

import json
import logging
import re

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.auth.middleware import get_current_user
from app.config import get_settings
from app.odysseus_bridge.supervisor import get_supervisor
from app.services.usage import (
    FEATURE_AGENT,
    FEATURE_CHAT,
    FEATURE_RESEARCH,
    record_usage_event,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/oui", tags=["odysseus"])


def _form_field(body: bytes, name: str) -> str | None:
    """Best-effort pull of a single multipart/form-data field value from a raw
    body. Used to label usage events (model, mode) without consuming the request
    stream we forward upstream. Fragile by design; wrapped by callers in try."""
    try:
        m = re.search(
            rb'name="' + re.escape(name.encode()) + rb'"\r\n\r\n(.*?)\r\n--',
            body, re.DOTALL,
        )
        return m.group(1).decode("utf-8", "replace").strip() if m else None
    except Exception:  # noqa: BLE001
        return None

# Hop-by-hop headers (RFC 7230) plus framing headers we must not forward verbatim.
_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length",
}
# Headers that would either trip Odysseus's loopback-trust guard or smuggle
# MyAi's own auth into the instance.
_DROP_TO_UPSTREAM = _HOP | {
    "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto", "x-real-ip",
    "forwarded", "cookie", "authorization",
}


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def proxy(path: str, request: Request):
    s = get_settings()
    if not s.odysseus_enabled:
        return JSONResponse({"detail": "Odysseus integration is disabled"}, status_code=503)

    user = await get_current_user(request)
    tenant_id = getattr(user, "tenant_id", None) or s.default_tenant_id
    creator = (getattr(user, "sub", None) or getattr(user, "email", None) or "user")
    # Role-gated Odysseus admin: only elevated MyAi roles earn shell/computer-use
    # + instance management within the tenant instance. Everyone else is a normal
    # owner-scoped user (Odysseus denies can_use_bash to non-admins). The master
    # switch odysseus_creator_admin forces admin for pure single-user local boxes.
    roles = [str(r).lower() for r in (getattr(user, "roles", None) or [])]
    is_admin = s.odysseus_creator_admin or any(
        r in roles for r in s.odysseus_admin_role_list
    )

    sup = get_supervisor()
    try:
        inst = await sup.ensure_running(tenant_id)
        await sup.ensure_user(inst, creator, is_admin=is_admin)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Odysseus proxy could not reach instance tenant=%s: %s", tenant_id, exc)
        return JSONResponse(
            {"detail": f"Odysseus instance unavailable: {exc}"}, status_code=502
        )

    # Embed handshake: return the tenant instance's loopback URL so the SPA can
    # iframe the REAL Odysseus UI (Cookbook/Tasks/etc.) directly. Local embed only.
    if path == "_embed":
        return JSONResponse({
            "url": inst.base_url if s.odysseus_embed else None,
            "embed": bool(s.odysseus_embed),
        })

    upstream_url = f"{inst.base_url}/api/{path}"
    fwd_headers = {
        k: v for k, v in request.headers.items() if k.lower() not in _DROP_TO_UPSTREAM
    }
    fwd_headers["X-Odysseus-Internal-Token"] = s.odysseus_internal_token_value
    fwd_headers["X-Odysseus-Owner"] = creator.strip().lower()

    body = await request.body()
    client = httpx.AsyncClient(timeout=None)
    try:
        upstream_req = client.build_request(
            request.method,
            upstream_url,
            params=request.query_params,
            headers=fwd_headers,
            content=body,
        )
        upstream_resp = await client.send(upstream_req, stream=True)
    except Exception as exc:  # noqa: BLE001
        await client.aclose()
        logger.warning("Odysseus proxy send failed tenant=%s path=%s: %s", tenant_id, path, exc)
        return JSONResponse({"detail": f"Odysseus upstream error: {exc}"}, status_code=502)

    resp_headers = {
        k: v for k, v in upstream_resp.headers.items() if k.lower() not in _HOP
    }

    # Count each deep-research run so it shows up per-employee. Token accounting
    # for the multi-round research engine is approximate (counted as a run);
    # precise per-round token capture is a follow-up.
    if path == "research/start" and upstream_resp.status_code < 400:
        rmodel = None
        try:
            rmodel = (json.loads(body) or {}).get("model")
        except Exception:  # noqa: BLE001
            pass
        await record_usage_event(
            tenant_id=tenant_id, creator_id=creator, feature=FEATURE_RESEARCH,
            model=rmodel, input_tokens=0, output_tokens=0, metadata={"event": "start"},
        )

    # Usage accounting: the chat/agent SSE stream emits a final
    #   data: {"type": "metrics", "data": {input_tokens, output_tokens, ...}}
    # frame. Sniff it as it flows through so the super-admin dashboard sees real
    # per-employee tokens for bridged chat/agent. (Pure passthrough otherwise.)
    sniff_usage = path == "chat_stream"
    if not sniff_usage:
        async def _stream():
            try:
                async for chunk in upstream_resp.aiter_raw():
                    yield chunk
            finally:
                await upstream_resp.aclose()
                await client.aclose()

        return StreamingResponse(
            _stream(),
            status_code=upstream_resp.status_code,
            headers=resp_headers,
            media_type=upstream_resp.headers.get("content-type"),
        )

    mode = (_form_field(body, "mode") or "chat").strip().lower()
    feature = FEATURE_AGENT if mode == "agent" else FEATURE_CHAT
    model = _form_field(body, "model")

    async def _stream_metered():
        metrics: dict = {}
        buf = b""
        try:
            async for chunk in upstream_resp.aiter_raw():
                yield chunk
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip()
                    if not line.startswith(b"data:"):
                        continue
                    try:
                        obj = json.loads(line[5:].strip())
                    except Exception:  # noqa: BLE001
                        continue
                    if isinstance(obj, dict) and obj.get("type") == "metrics":
                        data = obj.get("data")
                        if isinstance(data, dict):
                            metrics.update(data)
        finally:
            await upstream_resp.aclose()
            await client.aclose()
            in_t = int(metrics.get("input_tokens") or 0)
            out_t = int(metrics.get("output_tokens") or 0)
            if in_t or out_t:
                rt = metrics.get("response_time")
                await record_usage_event(
                    tenant_id=tenant_id,
                    creator_id=creator,
                    feature=feature,
                    model=model or metrics.get("model"),
                    input_tokens=in_t,
                    output_tokens=out_t,
                    elapsed_ms=int(float(rt) * 1000) if rt else None,
                    metadata={"tokens_per_second": metrics.get("tokens_per_second")},
                )

    return StreamingResponse(
        _stream_metered(),
        status_code=upstream_resp.status_code,
        headers=resp_headers,
        media_type=upstream_resp.headers.get("content-type"),
    )
