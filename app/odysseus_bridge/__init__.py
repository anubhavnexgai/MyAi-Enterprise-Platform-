"""Odysseus bridge — runs the vendored Odysseus feature suite as one isolated
subprocess per tenant and reverse-proxies it under ``/api/oui/*``.

Design (see plan: process-per-tenant + proxy):
- :mod:`app.odysseus_bridge.supervisor` owns subprocess lifecycle: it spawns one
  Odysseus instance per tenant on loopback with an isolated ``ODYSSEUS_DATA_DIR``
  + ``DATABASE_URL``, waits for health, idempotently provisions an Odysseus user
  per MyAi creator, and reaps idle instances.
- :mod:`app.odysseus_bridge.proxy` exposes the FastAPI router that authenticates
  via MyAi, resolves ``(tenant_id, creator)`` from ``request.state.user``, and
  streams the request to the tenant's instance, injecting the creator identity
  through Odysseus's trusted-proxy impersonation hook.

The only edit to vendored code is a single ``ODYSSEUS_DATA_DIR`` env hook in
``vendor/odysseus/core/constants.py`` (recorded in ``vendor/odysseus/UPSTREAM.md``).
"""

from app.odysseus_bridge.supervisor import get_supervisor

__all__ = ["get_supervisor"]
