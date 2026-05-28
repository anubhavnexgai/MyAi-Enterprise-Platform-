"""FastAPI entrypoint.

Mounts:
- ``/health``                 health probe (public)
- ``/docs`` / ``/openapi.json`` OpenAPI explorer (public)
- ``/api/*``                  REST surfaces (auth required, except /api/auth/*)
- ``/static/*``               raw static assets from ``web/``
- ``/``                       SPA shell (``web/index.html``)
- ``/pages/*``                page fragments loaded by the SPA router
- ``/app.js`` / ``/styles.css`` SPA bundle entry points (served from ``web/``)

Run with: ``python -m app.main`` (port 8002 by default).
"""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app import __version__
from app.api import register as register_api
from app.auth.middleware import AuthMiddleware
from app.config import get_settings
from app.storage.database import init_database, shutdown_database
from app.tenants.registry import get_tenant_registry

logger = logging.getLogger(__name__)


def _configure_logging(level_name: str) -> None:
    level = getattr(logging, level_name.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
        stream=sys.stdout,
    )
    # Tone down noisy libraries
    logging.getLogger("uvicorn.access").setLevel(max(level, logging.WARNING))
    logging.getLogger("httpx").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):  # pragma: no cover - exercised by manual run
    settings = get_settings()
    logger.info("Booting %s v%s (env=%s)", settings.app_name, __version__, settings.app_env)

    # Tenants -> DB -> caches
    registry = get_tenant_registry()
    logger.info(
        "Tenants loaded: %s",
        [t.tenant_id for t in registry.all()] or ["(none - falling back to DEV_TENANT_ID)"],
    )
    await init_database()

    if settings.dev_mode:
        logger.warning(
            "DEV_MODE is ON - every request is authenticated as %s on tenant %s",
            settings.dev_user_email,
            settings.dev_tenant_id,
        )

    try:
        yield
    finally:
        logger.info("Shutting down...")
        await shutdown_database()


def create_app() -> FastAPI:
    settings = get_settings()
    _configure_logging(settings.log_level)

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description="MyAi for NexgAI - multi-tenant enterprise copilot",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # ---- CORS ----
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list or ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Process-Time", "X-Dev-Auth"],
    )

    # ---- Session (used by SSO state) ----
    app.add_middleware(SessionMiddleware, secret_key=settings.session_secret)

    # ---- Auth (must come AFTER CORS so preflights are not rejected) ----
    app.add_middleware(AuthMiddleware)

    # ---- API routes ----
    register_api(app)

    # ---- Health ----
    @app.get("/health", tags=["meta"])
    async def health():
        from app.services.llm_client import get_llm_client
        client = get_llm_client()
        llm_up = await client.health_check()
        return {
            "status": "ok",
            "app": settings.app_name,
            "version": __version__,
            "env": settings.app_env,
            "dev_mode": settings.dev_mode,
            "llm_provider": client.provider,
            "llm_model": client.model,
            "llm": "up" if llm_up else "down",
        }

    # ---- Static + SPA ----
    web_dir: Path = settings.web_dir
    if not web_dir.exists():
        logger.warning("web/ directory missing at %s", web_dir)
    else:
        app.mount("/static", StaticFiles(directory=str(web_dir)), name="static")

        # SPA shell at /
        @app.get("/", include_in_schema=False)
        async def spa_root():
            idx = web_dir / "index.html"
            if not idx.exists():
                return JSONResponse(
                    {"detail": "web/index.html missing"}, status_code=500
                )
            return FileResponse(str(idx))

        # SPA bundle entry points - keep at root for clean URLs
        @app.get("/app.js", include_in_schema=False)
        async def spa_app_js():
            p = web_dir / "app.js"
            if not p.exists():
                return JSONResponse({"detail": "app.js missing"}, status_code=404)
            return FileResponse(str(p), media_type="application/javascript")

        @app.get("/styles.css", include_in_schema=False)
        async def spa_css():
            p = web_dir / "styles.css"
            if not p.exists():
                return JSONResponse({"detail": "styles.css missing"}, status_code=404)
            return FileResponse(str(p), media_type="text/css")

        # Page fragments loaded by the SPA router
        @app.get("/pages/{name}.html", include_in_schema=False)
        async def spa_page(name: str):
            p = web_dir / "pages" / f"{name}.html"
            if not p.exists():
                return JSONResponse(
                    {"detail": f"page '{name}' not found"}, status_code=404
                )
            return FileResponse(str(p), media_type="text/html")

        # Favicon (returns 204 if missing)
        @app.get("/favicon.ico", include_in_schema=False)
        async def favicon():
            p = web_dir / "favicon.ico"
            if p.exists():
                return FileResponse(str(p))
            return JSONResponse({}, status_code=204)

    return app


# Module-level app for `uvicorn app.main:app`
app = create_app()


def run() -> None:
    s = get_settings()
    uvicorn.run(
        "app.main:app",
        host=s.host,
        port=s.port,
        reload=s.app_env.lower() == "development",
        log_level=s.log_level.lower(),
    )


if __name__ == "__main__":
    run()
