"""FastAPI routers. ``api.register(app)`` mounts them all."""

from fastapi import FastAPI

from app.api import (
    admin,
    auth_routes,
    connectors,
    copilot,
    copilot_uploads,
    council,
    dashboard,
    files,
    inbox,
    insights,
    logs,
    preferences,
    threads,
)


def register(app: FastAPI) -> None:
    """Mount every API router on the given FastAPI app."""
    app.include_router(auth_routes.router)
    app.include_router(dashboard.router)
    app.include_router(inbox.router)
    app.include_router(copilot.router)
    app.include_router(copilot_uploads.router)
    app.include_router(connectors.router)
    app.include_router(files.router)
    app.include_router(logs.router)
    app.include_router(preferences.router)
    app.include_router(threads.router)
    app.include_router(insights.router)
    app.include_router(admin.router)
    app.include_router(council.router)


__all__ = ["register"]
