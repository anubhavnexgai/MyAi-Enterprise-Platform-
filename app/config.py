"""Application settings loaded from environment / .env via pydantic-settings."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- Runtime ----
    app_name: str = "MyAi-Enterprise"
    app_env: str = "development"
    host: str = "0.0.0.0"
    port: int = 8002
    log_level: str = "INFO"

    # ---- Dev bypass ----
    # When True, unauthenticated requests are silently treated as the dev user
    # (no login). Default False so the real login page gates access; demo
    # accounts (app/api/auth_routes.py DEMO_ACCOUNTS) + Azure SSO are the ways in.
    dev_mode: bool = False
    dev_user_id: str = "dev.user"
    dev_user_email: str = "dev.user@nexgai.com"
    dev_user_name: str = "Dev User"
    dev_tenant_id: str = "nexgai"
    dev_user_roles: str = "admin,supervisor,agent"

    # ---- Security ----
    jwt_secret_key: str = "change-me-in-prod-this-is-only-for-local-dev"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 480
    session_secret: str = "change-me-too-please"

    cors_origins: str = "http://localhost:8002,http://127.0.0.1:8002"

    # ---- Database ----
    database_url: str = ""
    sqlite_path: str = "data/myai_enterprise.db"

    # ---- Azure AD / OIDC ----
    azure_tenant_id: str = ""
    azure_client_id: str = ""
    azure_client_secret: str = ""
    azure_redirect_uri: str = "http://localhost:8002/api/auth/sso/callback"

    # ---- LLM provider (cloud-portable) ----
    # llm_provider="ollama" -> uses ollama_base_url + ollama_model
    # llm_provider="openai_compat" -> uses llm_base_url + llm_api_key + llm_model
    # The "openai_compat" path works with vLLM, llama.cpp server, Together AI,
    # Anyscale, any future hosted NexgAI SLM endpoint that speaks OpenAI's API.
    llm_provider: str = "ollama"
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_timeout: int = 120

    # Legacy aliases kept for backwards compat in local dev
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b"
    ollama_timeout: int = 120

    # ---- Correctness spine (grounding verification, Pillar 1) ----
    # When on, data-derived chat answers get a cheap second LLM pass that flags
    # claims unsupported by the fetched connector context. Fail-open: a verifier
    # error never blocks the reply. Set false to skip the extra call entirely.
    grounding_verify_enabled: bool = True

    # ---- File / object storage ----
    # storage_backend="local" -> writes to ./data/uploads
    # storage_backend="s3" -> s3_bucket + AWS creds from env
    # storage_backend="azure_blob" -> azure_container + azure_storage_account
    storage_backend: str = "local"
    storage_local_dir: str = "data/uploads"
    s3_bucket: str = ""
    s3_region: str = "us-east-1"
    azure_storage_account: str = ""
    azure_container: str = "myai-enterprise"

    # ---- Odysseus integration (vendored feature suite, process-per-tenant) ----
    # The Odysseus feature suite (multi-provider chat, agent w/ shell+files,
    # cookbook, compare, documents, email, calendar, notes/tasks, voice, MCP) is
    # vendored under vendor/odysseus and run as ONE isolated subprocess per tenant
    # (own DATA_DIR + SQLite DB), reverse-proxied under /api/oui/*. The proxy
    # injects the MyAi creator identity via Odysseus's trusted-proxy impersonation
    # hook so per-creator owner-scoping isolates users within a tenant.
    # See app/odysseus_bridge/.
    odysseus_enabled: bool = True
    odysseus_dir: str = "vendor/odysseus"        # vendored upstream tree
    odysseus_python: str = ""                      # interpreter for instances; "" -> derive venv, else sys.executable
    odysseus_data_root: str = "data/odysseus"      # per-tenant subdirs created here
    odysseus_host: str = "127.0.0.1"               # instances bind loopback ONLY
    odysseus_port_base: int = 8610                 # instance ports allocated upward from here
    odysseus_internal_token: str = ""              # trusted-proxy shared secret; "" -> derive from jwt_secret_key
    odysseus_idle_timeout_s: int = 1800            # reap a tenant instance after this many idle seconds (0 = never)
    odysseus_boot_timeout_s: int = 90              # seconds to wait for an instance to become healthy
    # Embed the REAL Odysseus UI (Cookbook, Tasks, Gallery, etc.) via iframe to
    # the per-tenant instance on loopback. LOCAL deployments only: instances bind
    # 127.0.0.1 and run with LOCALHOST_BYPASS so the same-machine browser can load
    # them. For multi-user server deployments, use per-tenant subdomain routing
    # instead (see ODYSSEUS_INTEGRATION.md / Phase 12).
    odysseus_embed: bool = False
    # Force EVERY MyAi creator to be an admin in their per-tenant Odysseus
    # instance (full privileges incl. shell/bash, cookbook serve, raw model
    # endpoints). Escape hatch for pure single-user local boxes. Default False:
    # admin is granted by ROLE instead (see odysseus_admin_roles) so a normal
    # employee gets chat + agent (non-shell tools) but NOT shell or instance
    # management. Odysseus's DEFAULT_PRIVILEGES already deny can_use_bash to
    # non-admins, so this is the multi-employee shell gate.
    odysseus_creator_admin: bool = False
    # MyAi roles that earn Odysseus-admin (shell/computer-use + instance mgmt)
    # within their tenant instance. Comma-separated; matched case-insensitively
    # against the user's JWT roles.
    odysseus_admin_roles: str = "admin,super_admin,owner"

    @property
    def effective_llm_provider(self) -> str:
        """Returns the LLM provider to use, accounting for legacy ollama_base_url."""
        return (self.llm_provider or "ollama").lower()

    @property
    def effective_llm_base_url(self) -> str:
        if self.effective_llm_provider == "ollama":
            return self.ollama_base_url
        return self.llm_base_url

    @property
    def effective_llm_model(self) -> str:
        if self.effective_llm_provider == "ollama":
            return self.ollama_model
        return self.llm_model or self.ollama_model

    # ---- Connector OAuth ----
    # One Google OAuth client serves Gmail/Calendar/Drive; redirect URIs split
    # per provider so they can be relocated to separate clients later.
    google_client_id: str = ""
    google_client_secret: str = ""
    google_gmail_redirect_uri: str = "http://localhost:8002/api/connectors/google_gmail/callback"
    google_calendar_redirect_uri: str = (
        "http://localhost:8002/api/connectors/google_calendar/callback"
    )
    google_drive_redirect_uri: str = (
        "http://localhost:8002/api/connectors/google_drive/callback"
    )

    microsoft_client_id: str = ""
    microsoft_client_secret: str = ""
    microsoft_redirect_uri: str = (
        "http://localhost:8002/api/connectors/microsoft_graph/callback"
    )

    slack_client_id: str = ""
    slack_client_secret: str = ""
    slack_redirect_uri: str = "http://localhost:8002/api/connectors/slack/callback"

    notion_client_id: str = ""
    notion_client_secret: str = ""
    notion_redirect_uri: str = "http://localhost:8002/api/connectors/notion/callback"

    # Fernet key used to encrypt OAuth tokens at rest. Empty -> derive from
    # JWT_SECRET_KEY (dev only - rotate before any non-local deploy).
    fernet_key: str = ""

    # ---- Tenant config ----
    tenant_config_dir: str = "config/tenants"
    default_tenant_id: str = "nexgai"

    # ---- Derived helpers ----

    @field_validator("dev_user_roles")
    @classmethod
    def _strip_roles(cls, v: str) -> str:
        return v or ""

    @property
    def dev_user_role_list(self) -> List[str]:
        return [r.strip() for r in self.dev_user_roles.split(",") if r.strip()]

    @property
    def odysseus_admin_role_list(self) -> List[str]:
        return [r.strip().lower() for r in self.odysseus_admin_roles.split(",") if r.strip()]

    @property
    def cors_origin_list(self) -> List[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() in {"production", "prod", "uat"}

    @property
    def root_dir(self) -> Path:
        return ROOT_DIR

    @property
    def data_dir(self) -> Path:
        d = ROOT_DIR / "data"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def logs_dir(self) -> Path:
        d = ROOT_DIR / "logs"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def web_dir(self) -> Path:
        return ROOT_DIR / "web"

    # ---- Odysseus bridge helpers ----
    @property
    def odysseus_dir_path(self) -> Path:
        p = Path(self.odysseus_dir)
        return p if p.is_absolute() else (ROOT_DIR / p)

    @property
    def odysseus_data_root_path(self) -> Path:
        p = Path(self.odysseus_data_root)
        p = p if p.is_absolute() else (ROOT_DIR / p)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def odysseus_python_exe(self) -> str:
        """Interpreter used to launch Odysseus instances.

        Order: explicit ODYSSEUS_PYTHON -> dedicated venv inside the vendored
        tree (created by scripts/bootstrap_odysseus.*) -> "" (caller falls back
        to the current interpreter).
        """
        if self.odysseus_python:
            return self.odysseus_python
        venv = self.odysseus_dir_path / ".venv"
        win = venv / "Scripts" / "python.exe"
        nix = venv / "bin" / "python"
        if win.exists():
            return str(win)
        if nix.exists():
            return str(nix)
        return ""

    @property
    def odysseus_internal_token_value(self) -> str:
        """Shared secret for the trusted-proxy impersonation header.

        Derives a stable value from jwt_secret_key when not set explicitly so
        local dev works with zero config; set ODYSSEUS_INTERNAL_TOKEN in prod.
        """
        return self.odysseus_internal_token or (self.jwt_secret_key + "::odysseus-bridge")[:64]

    @property
    def tenant_config_path(self) -> Path:
        p = Path(self.tenant_config_dir)
        if not p.is_absolute():
            p = ROOT_DIR / p
        return p

    @property
    def resolved_database_url(self) -> str:
        """Return either the configured DATABASE_URL or the local SQLite fallback."""
        if self.database_url:
            return self.database_url
        sqlite_p = Path(self.sqlite_path)
        if not sqlite_p.is_absolute():
            sqlite_p = ROOT_DIR / sqlite_p
        sqlite_p.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite+aiosqlite:///{sqlite_p.as_posix()}"


@lru_cache
def get_settings() -> Settings:
    return Settings()


# Convenience module-level handle
settings = get_settings()
