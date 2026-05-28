"""JWT issue + validate.

Adapted from EAP's shared/auth/jwt_handler.py but trimmed down: no Redis
blacklist, no refresh-token store. Multi-tenant claims are first-class.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import List, Optional

import jwt
from jwt.exceptions import PyJWTError
from pydantic import BaseModel, Field

from app.config import get_settings

logger = logging.getLogger(__name__)


class PlatformTokenClaims(BaseModel):
    """Claims carried in MyAi-Enterprise JWTs.

    Every authenticated request has access to these via ``request.state.user``.
    """

    sub: str = Field(..., description="Subject (stable user id)")
    email: str = Field(..., description="User email")
    username: str = Field(..., description="Username (often the local part of email)")
    full_name: Optional[str] = None

    tenant_id: str = Field(..., description="Tenant id - drives DB routing and RBAC")
    roles: List[str] = Field(default_factory=list)

    sso_provider: Optional[str] = None
    sso_groups: List[str] = Field(default_factory=list)
    picture: Optional[str] = None

    iat: Optional[int] = None
    exp: Optional[int] = None
    token_type: str = Field(default="access")
    session_id: Optional[str] = None


class JWTHandler:
    """Stateless JWT issue / validate helper."""

    def __init__(self, secret: str, algorithm: str, access_minutes: int) -> None:
        self.secret = secret
        self.algorithm = algorithm
        self.access_minutes = access_minutes

    # ---- issue ---------------------------------------------------------------

    def create_access_token(
        self,
        *,
        user_id: str,
        email: str,
        username: str,
        tenant_id: str,
        roles: List[str],
        full_name: Optional[str] = None,
        sso_provider: Optional[str] = None,
        sso_groups: Optional[List[str]] = None,
        picture: Optional[str] = None,
        session_id: Optional[str] = None,
        ttl_minutes: Optional[int] = None,
    ) -> str:
        now = datetime.now(timezone.utc)
        exp = now + timedelta(minutes=ttl_minutes or self.access_minutes)

        claims = PlatformTokenClaims(
            sub=user_id,
            email=email,
            username=username,
            full_name=full_name,
            tenant_id=tenant_id,
            roles=list(roles or []),
            sso_provider=sso_provider,
            sso_groups=list(sso_groups or []),
            picture=picture,
            iat=int(now.timestamp()),
            exp=int(exp.timestamp()),
            token_type="access",
            session_id=session_id,
        )
        token = jwt.encode(claims.model_dump(exclude_none=True), self.secret, algorithm=self.algorithm)
        return token

    # ---- validate ------------------------------------------------------------

    def decode(self, token: str, *, expected_type: str = "access") -> PlatformTokenClaims:
        try:
            payload = jwt.decode(token, self.secret, algorithms=[self.algorithm])
        except PyJWTError as e:
            raise ValueError(f"Invalid token: {e}") from e

        if payload.get("token_type", "access") != expected_type:
            raise ValueError(f"Wrong token type: expected {expected_type}")

        # exp is enforced by pyjwt, but double check for clock skew safety
        if payload.get("exp") and int(payload["exp"]) < int(time.time()):
            raise ValueError("Token expired")

        return PlatformTokenClaims(**payload)


@lru_cache
def get_jwt_handler() -> JWTHandler:
    s = get_settings()
    return JWTHandler(
        secret=s.jwt_secret_key,
        algorithm=s.jwt_algorithm,
        access_minutes=s.access_token_expire_minutes,
    )


# Module-level convenience wrappers (used by main.py and auth routes)

def create_access_token(**kwargs) -> str:
    return get_jwt_handler().create_access_token(**kwargs)


def decode_token(token: str, *, expected_type: str = "access") -> PlatformTokenClaims:
    return get_jwt_handler().decode(token, expected_type=expected_type)
