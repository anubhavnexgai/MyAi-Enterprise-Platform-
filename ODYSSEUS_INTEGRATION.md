# Odysseus (+ Hermes) Integration — Status

This documents the integration of the Odysseus feature suite (and the prioritized
Hermes capabilities) into MyAi-Enterprise. Work is on branch
`feature/odysseus-integration`. Nothing is committed automatically.

## Architecture (the foundation)

- **Process-per-tenant.** The vendored Odysseus app (`vendor/odysseus/`, commit
  `e163384`) runs as **one isolated subprocess per tenant** on loopback, with an
  isolated data dir (`data/odysseus/<tenant>/data/`) + SQLite DB. Managed by
  `app/odysseus_bridge/supervisor.py` (spawn, health, auto-provision users, idle
  reaping).
- **Reverse proxy.** `app/odysseus_bridge/proxy.py` mounts a catch-all at
  **`/api/oui/{path}` → instance `/api/{path}`**, authenticated by MyAi, streaming
  (SSE-safe). It injects the MyAi creator identity via Odysseus's trusted-proxy
  impersonation hook, so per-creator owner-scoping isolates users within a tenant.
- **Own venv.** Odysseus runs in `vendor/odysseus/.venv` (created by
  `scripts/bootstrap_odysseus.ps1` / `.sh`) — no dependency clashes with MyAi.
- **Isolation verified.** All tenant data lands under the per-tenant dir; the
  shared vendor dir holds only caches. Bridge patches to vendored code are minimal
  and listed in `vendor/odysseus/UPSTREAM.md`.

Because the proxy is a catch-all, **every Odysseus backend route is already
reachable** at `/api/oui/*`. The per-feature work below is therefore mostly
MyAi-skinned frontend pages wired to those endpoints.

## Phase status

| Phase | Feature | Status | Notes |
|------|---------|--------|-------|
| 0 | Tenancy bridge + vendoring | ✅ Done, verified | process-per-tenant, isolation proven |
| 1 | Chat core (3-zone UI) | ✅ Done, verified | `web/pages/copilot.html`; live streaming proven via Ollama |
| 2 | Computer access (shell/files) | ✅ Functional | via chat **Agent mode + Shell toggle** (`allow_bash`); runs in the tenant instance. **Sandbox hardening (docker backend) deferred** — see Security below |
| 3 | Cookbook (local models) | ✅ Done | `web/pages/cookbook.html` — hardware scan, fit-ranked models, download, task status |
| 4 | Compare (blind A/B) | ✅ Done | `web/pages/compare.html` — dual streaming, vote + reveal |
| 5 | Documents | ✅ Done, verified | `web/pages/documents.html` — list/create/edit/save/delete (create persists) |
| 6 | Memory / Skills | ✅ Done, verified | `web/pages/memory.html` — memories (add/search/delete, verified) + skills view |
| 7 | Email (IMAP/SMTP + triage) | ✅ Done | `web/pages/email.html` — accounts, inbox, read, AI summary + reply, compose/send |
| 8 | Notes & Tasks + routines | ✅ Done, verified | `web/pages/notes.html`, `web/pages/tasks.html` — cron/daily/weekly, run-now, notifications (note + task create persist) |
| 9 | Calendar (CalDAV) | ✅ Done | `web/pages/calendar.html` — events list/create, CalDAV config + sync, range view |
| 10 | Voice / MCP / gateways | ◑ Partial | **Voice ✅** (mic→`/stt/transcribe` in chat). **MCP** reachable via proxy (`/api/oui/mcp/*`) + Odysseus's own config. **Messaging gateways deferred** — need external bot tokens + a separate gateway process (Hermes `gateway/`); config-time, not buildable without creds |
| 11 | PWA / mobile / trajectory | ◑ Partial | **PWA ✅** (`web/manifest.json`, `web/sw.js`, installable, responsive chat). **Trajectory export deferred** (Hermes training-data feature) |
| 12 | Port-into-core hardening | ⏳ Future | the deliberate "later" half of the hybrid plan: migrate hot modules to MyAi's shared Postgres + tenant columns, fold learnings into MyAi core, observability/audit parity |

## How to run

1. One-time: `pwsh scripts/bootstrap_odysseus.ps1` (creates the Odysseus venv).
2. Start MyAi: `python -m uvicorn app.main:app --port 8002`.
3. Open the app → **Copilot** for the Odysseus-style chat, or the **Workspace** /
   **Models** nav sections for Documents, Email, Calendar, Notes, Tasks, Memory,
   Compare, Cookbook. The chat icon-rail also links to each.
4. First request per tenant lazily spawns that tenant's Odysseus instance
   (~10–15s), then it's warm.
5. Add a model: chat → model picker → "Add a model / provider" (for local Ollama
   use base URL `http://localhost:11434/v1`).

## Security notes / deferred items (be aware before production)

- **Shell sandboxing (Phase 2/12).** Agent-mode shell currently executes inside the
  tenant's Odysseus subprocess on the host. For multi-tenant production this should
  be sandboxed (per-tenant Docker/Modal backend). Tracked for Phase 12. Keep the
  Shell toggle disabled for untrusted tenants until then.
- **Admin-route exposure within a tenant.** The proxy injects the internal token on
  every call, which satisfies Odysseus's `require_admin` — convenient (e.g. "add
  model") but means a tenant's creators can reach that tenant's admin routes.
  Cross-tenant isolation is unaffected (separate processes/DBs). Tighten in Phase 12.
- **Messaging gateways & trajectory export** are not built (need external creds /
  are training-data tooling); documented as follow-ups.

## Key files

- Bridge: `app/odysseus_bridge/{supervisor,proxy}.py`, `app/config.py` (odysseus_*),
  `app/main.py` (mount + reaper), `vendor/odysseus/` (+ `UPSTREAM.md`).
- Frontend: `web/pages/copilot.html` (+ documents/email/calendar/notes/tasks/memory/
  compare/cookbook), `web/app.js` (`window.oui` helper, ROUTES), `web/index.html`
  (nav + PWA), `web/styles.css` (`.ws-*`), `web/manifest.json`, `web/sw.js`.
