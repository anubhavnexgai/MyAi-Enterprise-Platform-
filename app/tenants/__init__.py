"""Multi-tenant config + DB routing."""

from app.tenants.registry import TenantConfig, TenantRegistry, get_tenant_registry
from app.tenants.router import TenantRouter, get_tenant_router

__all__ = [
    "TenantConfig",
    "TenantRegistry",
    "TenantRouter",
    "get_tenant_registry",
    "get_tenant_router",
]
