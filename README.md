# MyAi for NexgAI — Enterprise Edition

Multi-tenant, per-user enterprise build of MyAi. Each user inside a tenant gets
their own copilot grounded on their own harvested data (email, calendar, files,
CRM). The frontend gives supervisors a dashboard + retention center and gives
agents an inbox + copilot chat.

This repo is the **backend foundation** — a FastAPI service that:

- Authenticates users via Azure AD OIDC (with a `DEV_MODE` bypass for local work)
- Routes every request through a tenant-aware auth middleware that injects
  `request.state.user`
- Exposes REST endpoints for the dashboard, inbox, copilot chat, connectors
  (Gmail / Calendar OAuth), file upload, and audit log streaming
- Talks to a per-tenant Postgres (or SQLite locally) through a single
  `harvester_gateway.get_user_data()` choke point that **always** scopes
  queries by `tenant_id` + `creator_id`
- Reuses the MyAi agent core (`app/agent/`) for the copilot loop, falling back
  to a thin Ollama passthrough if the full agent fails to initialise locally

## Quick start

```bash
cd C:\Users\anubh\MyAi-Enterprise
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python -m app.main
```

Open <http://localhost:8002/> for the SPA shell and <http://localhost:8002/docs>
for the OpenAPI explorer. With `DEV_MODE=true` (the default in `.env.example`)
every request is auto-authenticated as `dev.user@nexgai.com` on tenant `nexgai`,
so you can click through the app without touching Azure AD.

## Layout

```
app/
  main.py              FastAPI entry, mounts SPA + /api + /static + /health
  config.py            pydantic-settings, loads .env
  auth/                Azure AD OIDC, JWT validation, FastAPI middleware
  tenants/             Per-tenant DB router + tenant registry
  api/                 REST surfaces (dashboard, inbox, copilot, ...)
  agent/               MyAi agent core (copied from C:\Users\anubh\Downloads\myai)
  services/            ollama_client, harvester_gateway, connector_manager, audit
  models/              Pydantic request/response schemas
  storage/             Async DB engine + sqlite/postgres bootstrap
web/                   SPA shell (index.html + app.js + pages/*.html)
config/tenants/        Per-tenant SSO + harvester config (nexgai/sso.yaml)
data/                  Local SQLite + audit log
```

## Ports

- `8001` — MyAi personal (existing)
- `8002` — MyAi-Enterprise (this repo)

## Hard rules

1. Every API route runs through `AuthMiddleware`. No route may query data
   without `user_id` + `tenant_id` in the WHERE clause.
2. All harvester reads go through `services/harvester_gateway.get_user_data()`.
3. Frontend pages live in `web/pages/*.html` and are loaded by the SPA router —
   the backend never renders HTML.

## What's not in this repo (by design)

- The frontend page implementations (separate agent owns those)
- The actual harvester pipeline (we just read its outputs)
- Cloud deployment configuration (local-only for now)
