"""Authentication: Azure AD OIDC + JWT + FastAPI middleware."""

from app.auth.jwt import (
    PlatformTokenClaims,
    create_access_token,
    decode_token,
    get_jwt_handler,
)
from app.auth.middleware import (
    AuthMiddleware,
    get_current_user,
    require_roles,
)
from app.auth.sso import AzureADProvider, get_sso_provider

__all__ = [
    "AuthMiddleware",
    "AzureADProvider",
    "PlatformTokenClaims",
    "create_access_token",
    "decode_token",
    "get_current_user",
    "get_jwt_handler",
    "get_sso_provider",
    "require_roles",
]
