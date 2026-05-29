"""Persistent copilot chat threads."""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select, delete

from app.auth.jwt import PlatformTokenClaims
from app.auth.middleware import get_current_user
from app.storage.models import ChatMessage, ChatThread
from app.tenants.router import get_tenant_router

router = APIRouter(prefix="/api/threads", tags=["threads"])


class ThreadCreate(BaseModel):
    title: str = Field(default="New chat", max_length=256)


class MessageAppend(BaseModel):
    role: str = Field(..., pattern=r"^(user|assistant|system)$")
    content: str = Field(..., min_length=1)


def _thread_to_dict(t: ChatThread) -> dict:
    return {
        "id": t.id,
        "title": t.title,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
    }


def _msg_to_dict(m: ChatMessage) -> dict:
    return {
        "id": m.id,
        "role": m.role,
        "content": m.content,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


@router.get("", response_model=Dict[str, Any])
async def list_threads(
    request: Request,
    user: PlatformTokenClaims = Depends(get_current_user),
) -> Dict[str, Any]:
    router_db = get_tenant_router()
    async with router_db.session_for(user.tenant_id) as session:
        result = await session.execute(
            select(ChatThread)
            .where(ChatThread.tenant_id == user.tenant_id)
            .where(ChatThread.creator_id == user.sub)
            .order_by(ChatThread.updated_at.desc())
            .limit(100)
        )
        rows = result.scalars().all()
    return {"threads": [_thread_to_dict(r) for r in rows]}


@router.post("", response_model=Dict[str, Any], status_code=status.HTTP_201_CREATED)
async def create_thread(
    payload: ThreadCreate,
    request: Request,
    user: PlatformTokenClaims = Depends(get_current_user),
) -> Dict[str, Any]:
    router_db = get_tenant_router()
    async with router_db.session_for(user.tenant_id) as session:
        t = ChatThread(
            tenant_id=user.tenant_id,
            creator_id=user.sub,
            title=payload.title or "New chat",
        )
        session.add(t)
        await session.commit()
        await session.refresh(t)
    return _thread_to_dict(t)


@router.get("/{thread_id}", response_model=Dict[str, Any])
async def get_thread(
    thread_id: int,
    request: Request,
    user: PlatformTokenClaims = Depends(get_current_user),
) -> Dict[str, Any]:
    router_db = get_tenant_router()
    async with router_db.session_for(user.tenant_id) as session:
        t = (
            (
                await session.execute(
                    select(ChatThread)
                    .where(ChatThread.id == thread_id)
                    .where(ChatThread.tenant_id == user.tenant_id)
                    .where(ChatThread.creator_id == user.sub)
                )
            )
            .scalars()
            .first()
        )
        if not t:
            raise HTTPException(status_code=404, detail="Thread not found")
        msgs = (
            (
                await session.execute(
                    select(ChatMessage)
                    .where(ChatMessage.thread_id == thread_id)
                    .where(ChatMessage.tenant_id == user.tenant_id)
                    .where(ChatMessage.creator_id == user.sub)
                    .order_by(ChatMessage.created_at.asc())
                )
            )
            .scalars()
            .all()
        )
    return {
        "thread": _thread_to_dict(t),
        "messages": [_msg_to_dict(m) for m in msgs],
    }


@router.post("/{thread_id}/messages", response_model=Dict[str, Any])
async def append_message(
    thread_id: int,
    payload: MessageAppend,
    request: Request,
    user: PlatformTokenClaims = Depends(get_current_user),
) -> Dict[str, Any]:
    router_db = get_tenant_router()
    async with router_db.session_for(user.tenant_id) as session:
        t = (
            (
                await session.execute(
                    select(ChatThread)
                    .where(ChatThread.id == thread_id)
                    .where(ChatThread.tenant_id == user.tenant_id)
                    .where(ChatThread.creator_id == user.sub)
                )
            )
            .scalars()
            .first()
        )
        if not t:
            raise HTTPException(status_code=404, detail="Thread not found")

        # Auto-title from first user message
        if t.title in ("New chat", "", None) and payload.role == "user":
            t.title = payload.content[:80].strip()

        m = ChatMessage(
            tenant_id=user.tenant_id,
            creator_id=user.sub,
            thread_id=thread_id,
            role=payload.role,
            content=payload.content,
        )
        session.add(m)
        await session.commit()
        await session.refresh(m)
        await session.refresh(t)
    return {"thread": _thread_to_dict(t), "message": _msg_to_dict(m)}


@router.patch("/{thread_id}", response_model=Dict[str, Any])
async def rename_thread(
    thread_id: int,
    payload: ThreadCreate,
    request: Request,
    user: PlatformTokenClaims = Depends(get_current_user),
) -> Dict[str, Any]:
    router_db = get_tenant_router()
    async with router_db.session_for(user.tenant_id) as session:
        t = (
            (
                await session.execute(
                    select(ChatThread)
                    .where(ChatThread.id == thread_id)
                    .where(ChatThread.tenant_id == user.tenant_id)
                    .where(ChatThread.creator_id == user.sub)
                )
            )
            .scalars()
            .first()
        )
        if not t:
            raise HTTPException(status_code=404, detail="Thread not found")
        t.title = payload.title or t.title
        await session.commit()
        await session.refresh(t)
    return _thread_to_dict(t)


@router.delete("/{thread_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_thread(
    thread_id: int,
    request: Request,
    user: PlatformTokenClaims = Depends(get_current_user),
):
    router_db = get_tenant_router()
    async with router_db.session_for(user.tenant_id) as session:
        t = (
            (
                await session.execute(
                    select(ChatThread)
                    .where(ChatThread.id == thread_id)
                    .where(ChatThread.tenant_id == user.tenant_id)
                    .where(ChatThread.creator_id == user.sub)
                )
            )
            .scalars()
            .first()
        )
        if not t:
            raise HTTPException(status_code=404, detail="Thread not found")
        await session.execute(
            delete(ChatMessage)
            .where(ChatMessage.thread_id == thread_id)
            .where(ChatMessage.tenant_id == user.tenant_id)
            .where(ChatMessage.creator_id == user.sub)
        )
        await session.delete(t)
        await session.commit()
    return None
