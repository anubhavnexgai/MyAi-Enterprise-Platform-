"""Tenant registry.

Each tenant has a directory at ``config/tenants/<tenant_id>/`` containing at
minimum an ``sso.yaml``. The registry loads them at startup and exposes them
to other components (SSO, harvester gateway, audit, connectors).
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

import yaml
from pydantic import BaseModel, Field

from app.config import get_settings

logger = logging.getLogger(__name__)


class TenantSSOConfig(BaseModel):
    provider: str = "azure_ad"
    tenant_id: str = ""
    client_id: str = ""
    client_secret: str = ""
    redirect_uri: str = ""
    scopes: List[str] = Field(default_factory=lambda: ["openid", "profile", "email"])
    role_mapping: Dict[str, List[str]] = Field(default_factory=dict)


class TenantConfig(BaseModel):
    tenant_id: str
    display_name: str = ""
    enabled: bool = True
    database_url: Optional[str] = None
    sso: TenantSSOConfig = Field(default_factory=TenantSSOConfig)
    metadata: Dict[str, str] = Field(default_factory=dict)


class TenantRegistry:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self._cache: Dict[str, TenantConfig] = {}
        self._loaded = False

    def reload(self) -> None:
        self._cache.clear()
        if not self.base_dir.exists():
            logger.warning("Tenant config dir %s does not exist", self.base_dir)
            self._loaded = True
            return

        for tenant_dir in sorted(self.base_dir.iterdir()):
            if not tenant_dir.is_dir():
                continue
            tenant_id = tenant_dir.name
            sso_yaml = tenant_dir / "sso.yaml"
            sso_cfg = TenantSSOConfig()
            if sso_yaml.exists():
                try:
                    with sso_yaml.open("r", encoding="utf-8") as f:
                        raw = yaml.safe_load(f) or {}
                    sso_cfg = TenantSSOConfig(**raw)
                except Exception as e:
                    logger.error("Failed to load %s: %s", sso_yaml, e)

            tenant_yaml = tenant_dir / "tenant.yaml"
            extras: Dict[str, str] = {}
            display_name = tenant_id
            enabled = True
            db_url: Optional[str] = None
            if tenant_yaml.exists():
                try:
                    with tenant_yaml.open("r", encoding="utf-8") as f:
                        raw = yaml.safe_load(f) or {}
                    display_name = raw.get("display_name", tenant_id)
                    enabled = bool(raw.get("enabled", True))
                    db_url = raw.get("database_url")
                    extras = {str(k): str(v) for k, v in (raw.get("metadata") or {}).items()}
                except Exception as e:
                    logger.error("Failed to load %s: %s", tenant_yaml, e)

            self._cache[tenant_id] = TenantConfig(
                tenant_id=tenant_id,
                display_name=display_name,
                enabled=enabled,
                database_url=db_url,
                sso=sso_cfg,
                metadata=extras,
            )

        self._loaded = True
        logger.info("Loaded %d tenant configs: %s", len(self._cache), list(self._cache))

    def all(self) -> List[TenantConfig]:
        if not self._loaded:
            self.reload()
        return list(self._cache.values())

    def get(self, tenant_id: str) -> Optional[TenantConfig]:
        if not self._loaded:
            self.reload()
        return self._cache.get(tenant_id)

    def require(self, tenant_id: str) -> TenantConfig:
        cfg = self.get(tenant_id)
        if cfg is None:
            raise KeyError(f"Unknown tenant: {tenant_id}")
        return cfg


@lru_cache
def get_tenant_registry() -> TenantRegistry:
    reg = TenantRegistry(get_settings().tenant_config_path)
    reg.reload()
    return reg
