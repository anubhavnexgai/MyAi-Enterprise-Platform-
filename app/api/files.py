"""File upload + understanding endpoints.

For now uploads are persisted to ``data/uploads/<tenant>/<user>/`` and a row is
recorded in ``uploaded_files`` with status='uploaded'. The "understanding"
step (OCR / parsing / summarisation) is a TODO - we just record a synthetic
summary so the UI can render something useful while we wire up the real parser.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, status
from fastapi.responses import Response
from sqlalchemy import select

from app.auth.jwt import PlatformTokenClaims
from app.auth.middleware import get_current_user
from app.config import get_settings
from app.models.schemas import FileUploadResponse
from app.services.audit import get_audit_service
from app.storage.models import UploadedFile
from app.tenants.router import get_tenant_router

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/files", tags=["files"])

MAX_UPLOAD_MB = 25


def _user_upload_dir(tenant_id: str, user_id: str) -> Path:
    base = get_settings().data_dir / "uploads" / tenant_id / user_id
    base.mkdir(parents=True, exist_ok=True)
    return base


@router.post(
    "/upload",
    response_model=FileUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    user: PlatformTokenClaims = Depends(get_current_user),
) -> FileUploadResponse:
    if file.filename is None or not file.filename.strip():
        raise HTTPException(status_code=400, detail="filename is required")

    user_dir = _user_upload_dir(user.tenant_id, user.sub)
    safe_name = Path(file.filename).name
    target = user_dir / f"{uuid.uuid4().hex[:8]}_{safe_name}"

    contents = await file.read()
    size = len(contents)
    if size > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(
            status_code=413,
            detail=f"File too large (>{MAX_UPLOAD_MB}MB)",
        )
    target.write_bytes(contents)

    router_db = get_tenant_router()
    async with router_db.session_for(user.tenant_id) as session:
        row = UploadedFile(
            tenant_id=user.tenant_id,
            creator_id=user.sub,
            filename=safe_name,
            content_type=file.content_type,
            size_bytes=size,
            storage_path=str(target),
            status="uploaded",
            summary=f"Uploaded {safe_name} ({size} bytes). Parsing pending.",
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)

    await get_audit_service().log(
        tenant_id=user.tenant_id,
        user_id=user.sub,
        event_type="file.uploaded",
        message=f"Uploaded {safe_name} ({size} bytes)",
        payload={"file_id": row.id, "size": size, "content_type": file.content_type},
    )

    return FileUploadResponse(
        id=row.id,
        filename=row.filename,
        content_type=row.content_type,
        size_bytes=row.size_bytes,
        status=row.status,
        summary=row.summary,
    )


@router.get("", response_model=List[Dict[str, Any]])
async def list_files(
    request: Request,
    user: PlatformTokenClaims = Depends(get_current_user),
) -> List[Dict[str, Any]]:
    router_db = get_tenant_router()
    async with router_db.session_for(user.tenant_id) as session:
        result = await session.execute(
            select(UploadedFile)
            .where(UploadedFile.tenant_id == user.tenant_id)
            .where(UploadedFile.creator_id == user.sub)
            .order_by(UploadedFile.created_at.desc())
        )
        rows = list(result.scalars().all())
    return [
        {
            "id": r.id,
            "filename": r.filename,
            "content_type": r.content_type,
            "size_bytes": r.size_bytes,
            "status": r.status,
            "summary": r.summary,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@router.delete("/{file_id}", response_class=Response, status_code=status.HTTP_204_NO_CONTENT)
async def delete_file(
    file_id: int,
    request: Request,
    user: PlatformTokenClaims = Depends(get_current_user),
) -> Response:
    router_db = get_tenant_router()
    async with router_db.session_for(user.tenant_id) as session:
        result = await session.execute(
            select(UploadedFile)
            .where(UploadedFile.id == file_id)
            .where(UploadedFile.tenant_id == user.tenant_id)
            .where(UploadedFile.creator_id == user.sub)
        )
        row = result.scalars().first()
        if not row:
            raise HTTPException(status_code=404, detail="File not found")
        try:
            Path(row.storage_path).unlink(missing_ok=True)
        except Exception:
            logger.exception("Failed to remove file from disk: %s", row.storage_path)
        await session.delete(row)
        await session.commit()

    await get_audit_service().log(
        tenant_id=user.tenant_id,
        user_id=user.sub,
        event_type="file.deleted",
        message=f"Deleted file #{file_id}",
        payload={"file_id": file_id},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
