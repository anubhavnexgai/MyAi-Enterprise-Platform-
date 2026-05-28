"""FastAPI routers. ``api.register(app)`` mounts them all."""

from fastapi import FastAPI

from app.api import auth_routes, connectors, copilot, dashboard, files, inbox, logs


def register(app: FastAPI) -> None:
    """Mount every API router on the given FastAPI app."""
    app.include_router(auth_routes.router)
    app.include_router(dashboard.router)
    app.include_router(inbox.router)
    app.include_router(copilot.router)
    app.include_router(connectors.router)
    app.include_router(files.router)
    app.include_router(logs.router)


__all__ = ["register"]
