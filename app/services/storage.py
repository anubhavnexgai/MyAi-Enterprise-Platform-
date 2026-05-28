"""Unified file storage. Local disk (dev) -> S3 or Azure Blob (cloud) via env.

    STORAGE_BACKEND=local      # local disk
    STORAGE_BACKEND=s3         # AWS S3
    STORAGE_BACKEND=azure_blob # Azure Blob Storage

All callers use the same interface — `put`, `get`, `delete`, `url` —
so swapping backends never touches application code.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)


class StorageBackend:
    """Abstract storage interface. All paths are user-scoped: tenant/user_id/key."""

    async def put(self, key: str, content: bytes, content_type: str = "application/octet-stream") -> str:
        raise NotImplementedError

    async def get(self, key: str) -> Optional[bytes]:
        raise NotImplementedError

    async def delete(self, key: str) -> bool:
        raise NotImplementedError

    async def url(self, key: str, expires_in: int = 3600) -> str:
        raise NotImplementedError


class LocalStorage(StorageBackend):
    def __init__(self, base_dir: Optional[Path] = None):
        self.base = Path(base_dir or settings.storage_local_dir)
        if not self.base.is_absolute():
            self.base = settings.root_dir / self.base
        self.base.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        # Defensive: never let key escape the base
        p = (self.base / key).resolve()
        if not str(p).startswith(str(self.base.resolve())):
            raise ValueError(f"Refused unsafe key: {key}")
        return p

    async def put(self, key: str, content: bytes, content_type: str = "application/octet-stream") -> str:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(p.write_bytes, content)
        return f"/files/{key}"

    async def get(self, key: str) -> Optional[bytes]:
        p = self._path(key)
        if not p.exists():
            return None
        return await asyncio.to_thread(p.read_bytes)

    async def delete(self, key: str) -> bool:
        p = self._path(key)
        if not p.exists():
            return False
        await asyncio.to_thread(p.unlink)
        return True

    async def url(self, key: str, expires_in: int = 3600) -> str:
        return f"/files/{key}"


class S3Storage(StorageBackend):
    def __init__(self):
        try:
            import boto3  # noqa: F401
        except ImportError:
            raise RuntimeError("boto3 not installed. pip install boto3")
        import boto3
        self.bucket = settings.s3_bucket
        self.client = boto3.client("s3", region_name=settings.s3_region)

    async def put(self, key: str, content: bytes, content_type: str = "application/octet-stream") -> str:
        await asyncio.to_thread(
            self.client.put_object,
            Bucket=self.bucket, Key=key, Body=content, ContentType=content_type,
        )
        return await self.url(key)

    async def get(self, key: str) -> Optional[bytes]:
        try:
            obj = await asyncio.to_thread(self.client.get_object, Bucket=self.bucket, Key=key)
            return obj["Body"].read()
        except Exception:
            return None

    async def delete(self, key: str) -> bool:
        try:
            await asyncio.to_thread(self.client.delete_object, Bucket=self.bucket, Key=key)
            return True
        except Exception:
            return False

    async def url(self, key: str, expires_in: int = 3600) -> str:
        return await asyncio.to_thread(
            self.client.generate_presigned_url,
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=expires_in,
        )


class AzureBlobStorage(StorageBackend):
    def __init__(self):
        try:
            from azure.storage.blob.aio import BlobServiceClient  # noqa: F401
        except ImportError:
            raise RuntimeError("azure-storage-blob not installed.")
        from azure.storage.blob.aio import BlobServiceClient
        self.client = BlobServiceClient(
            account_url=f"https://{settings.azure_storage_account}.blob.core.windows.net"
        )
        self.container = settings.azure_container

    async def put(self, key: str, content: bytes, content_type: str = "application/octet-stream") -> str:
        blob = self.client.get_blob_client(container=self.container, blob=key)
        await blob.upload_blob(content, overwrite=True, content_type=content_type)
        return await self.url(key)

    async def get(self, key: str) -> Optional[bytes]:
        try:
            blob = self.client.get_blob_client(container=self.container, blob=key)
            stream = await blob.download_blob()
            return await stream.readall()
        except Exception:
            return None

    async def delete(self, key: str) -> bool:
        try:
            blob = self.client.get_blob_client(container=self.container, blob=key)
            await blob.delete_blob()
            return True
        except Exception:
            return False

    async def url(self, key: str, expires_in: int = 3600) -> str:
        return f"https://{settings.azure_storage_account}.blob.core.windows.net/{self.container}/{key}"


_backend: Optional[StorageBackend] = None


def get_storage() -> StorageBackend:
    global _backend
    if _backend is None:
        backend = (settings.storage_backend or "local").lower()
        if backend == "s3":
            _backend = S3Storage()
        elif backend == "azure_blob":
            _backend = AzureBlobStorage()
        else:
            _backend = LocalStorage()
        logger.info("Storage backend initialised: %s", backend)
    return _backend


def user_scoped_key(tenant_id: str, user_id: str, *parts: str) -> str:
    """Build a storage key that includes tenant + user for guaranteed isolation."""
    safe_parts = [str(p).strip("/") for p in parts if p]
    return "/".join([tenant_id, user_id, *safe_parts])
