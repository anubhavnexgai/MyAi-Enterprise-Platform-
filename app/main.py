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

# Load .env into os.environ BEFORE anything else imports.
# Connector code reads os.environ directly, so pydantic-settings caching
# is not enough on its own.
from dotenv import load_dotenv  # noqa: E402
load_dotenv()  # noqa: E402

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

    # Orphaned research tasks (in_progress when the last process died) would
    # linger as RUNNING on the dashboard — resolve them on boot.
    try:
        from app.api.copilot import cleanup_orphaned_research_tasks
        cleaned = await cleanup_orphaned_research_tasks()
        if cleaned:
            logger.info("Resolved %d orphaned research task(s)", cleaned)
    except Exception as exc:  # noqa: BLE001
        logger.warning("research task cleanup skipped: %s", exc)

    if settings.dev_mode:
        logger.warning(
            "DEV_MODE is ON - every request is authenticated as %s on tenant %s",
            settings.dev_user_email,
            settings.dev_tenant_id,
        )

    # Seed the employee directory + varied per-employee usage so the super-admin
    # analytics console has realistic multi-employee data. This is org-level demo
    # scaffolding (the colleagues) and is independent of per-account inbox data —
    # which is now seeded only for the demo accounts on login (see demo_seed
    # is_demo_user). Idempotent.
    try:
        from app.services.demo_seed import seed_demo_org
        info = await seed_demo_org(
            settings.dev_tenant_id, settings.dev_user_id,
            settings.dev_user_email, settings.dev_user_name,
            ",".join(settings.dev_user_role_list),
        )
        logger.info("Demo org seeded: %s", info)
    except Exception as exc:  # noqa: BLE001
        logger.warning("demo org seed skipped: %s", exc)

    # Start background workers: harvester (Gmail/Outlook/Calendar cache) and
    # lifecycle ticker (SLA breach + escalation).
    import asyncio
    from app.services.harvester_worker import harvester_loop
    from app.services.lifecycle_ticker import lifecycle_loop
    from app.services.research_scheduler import research_scheduler_loop

    workers = [
        asyncio.create_task(harvester_loop(), name="harvester"),
        asyncio.create_task(lifecycle_loop(), name="lifecycle"),
        asyncio.create_task(research_scheduler_loop(), name="research_scheduler"),
    ]

    # Odysseus bridge: start the idle-instance reaper. Instances themselves are
    # spawned lazily on first /api/oui/* request (see app/odysseus_bridge/).
    odysseus_sup = None
    if settings.odysseus_enabled:
        from app.odysseus_bridge import get_supervisor
        odysseus_sup = get_supervisor()
        workers.append(asyncio.create_task(odysseus_sup.reaper_loop(), name="odysseus_reaper"))

    try:
        yield
    finally:
        logger.info("Shutting down...")
        if odysseus_sup is not None:
            try:
                await odysseus_sup.shutdown_all()
            except Exception:  # noqa: BLE001
                pass
        for w in workers:
            w.cancel()
        for w in workers:
            try:
                await w
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
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

    # ---- Odysseus bridge proxy (/api/oui/*) ----
    # Vendored Odysseus feature suite, one isolated subprocess per tenant.
    if settings.odysseus_enabled:
        from app.odysseus_bridge.proxy import router as odysseus_router
        app.include_router(odysseus_router)

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

        # Service worker must be served from root so its scope covers the whole
        # SPA (a /static/ path would scope it to /static/ only). PWA: see
        # web/manifest.json + the registration in web/index.html.
        @app.get("/sw.js", include_in_schema=False)
        async def spa_sw():
            p = web_dir / "sw.js"
            if not p.exists():
                return JSONResponse({"detail": "sw.js missing"}, status_code=404)
            return FileResponse(str(p), media_type="application/javascript")

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
    dev = s.app_env.lower() == "development"
    kwargs: dict = {
        "host": s.host,
        "port": s.port,
        "reload": dev,
        "log_level": s.log_level.lower(),
    }
    if dev:
        # Watch ONLY code dirs. Otherwise the reloader thrashes on every write to
        # data/ (research JSON, the SQLite DB, learned skills, semantic memory) —
        # and a reload mid-request (e.g. an SSE research stream) hangs the server.
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent
        kwargs["reload_dirs"] = [str(root / "app"), str(root / "web")]
        kwargs["reload_excludes"] = ["*.db", "*.json", "*.log", "*.sqlite*"]
    uvicorn.run("app.main:app", **kwargs)


if __name__ == "__main__":
    run()
