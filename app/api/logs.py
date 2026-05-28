"""Audit log endpoints - recent + Server-Sent-Events stream."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from app.auth.jwt import PlatformTokenClaims
from app.auth.middleware import get_current_user
from app.services.audit import get_audit_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/logs", tags=["logs"])


@router.get("", response_model=List[Dict[str, Any]])
async def list_logs(
    request: Request,
    event_type: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    user: PlatformTokenClaims = Depends(get_current_user),
) -> List[Dict[str, Any]]:
    rows = await get_audit_service().list_recent(
        tenant_id=user.tenant_id,
        user_id=user.sub,
        limit=limit,
        event_type=event_type,
    )
    return [
        {
            "id": r.id,
            "event_type": r.event_type,
            "severity": r.severity,
            "message": r.message,
            "payload": r.payload,
            "tenant_id": r.tenant_id,
            "creator_id": r.creator_id,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@router.get("/stream")
async def stream_logs(
    request: Request,
    user: PlatformTokenClaims = Depends(get_current_user),
) -> StreamingResponse:
    audit = get_audit_service()

    async def gen() -> AsyncIterator[bytes]:
        # Send a hello so the client knows the stream is alive
        yield b': connected\n\n'
        try:
            async for row in audit.stream(tenant_id=user.tenant_id, user_id=user.sub):
                if await request.is_disconnected():
                    break
                yield f"data: {json.dumps(row, default=str)}\n\n".encode("utf-8")
        except asyncio.CancelledError:  # pragma: no cover
            pass

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
