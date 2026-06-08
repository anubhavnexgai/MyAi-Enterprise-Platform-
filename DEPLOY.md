# Deploying MyAi-Enterprise

This app runs zero-config for local dev (`DEV_MODE=true` auto-authenticates a
dev user). Production requires the steps below. The app is deploy-target
agnostic — everything is driven by env vars, so the same image runs on a VM,
Docker, or a managed container platform.

## 1. Boot it locally (dev)

```bash
./start.sh            # or: start.bat on Windows
# -> http://localhost:8002  (SPA + API + /docs + /health)
```

Local dev uses SQLite (`data/myai_enterprise.db`, auto-created) and local
Ollama (`qwen2.5:7b`). No secrets or OAuth needed.

## 2. Production checklist

Copy `.env.example` → `.env` and set:

**Security (all mandatory — defaults are dev-only):**
- `DEV_MODE=false` — turns on real JWT/SSO (every request is otherwise the dev user).
- `JWT_SECRET_KEY`, `SESSION_SECRET` — `python -c "import secrets; print(secrets.token_urlsafe(64))"`
- `FERNET_KEY` — `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` (encrypts OAuth tokens at rest; if unset it derives an ephemeral key from JWT_SECRET_KEY and tokens are lost on restart).
- `APP_ENV=production`, `CORS_ORIGINS=https://your-domain` (comma-separated).

**Auth (Azure AD SSO):** `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`, and `AZURE_REDIRECT_URI=https://your-domain/api/auth/sso/callback` (must match the App Registration exactly). Also fill `config/tenants/nexgai/sso.yaml`.

**Database:** `DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/myai_enterprise` (SQLite is single-writer — not for multi-process prod). Schema auto-creates on boot.

**LLM (cloud-hosted open-weights — the production path, no frontier API):**
```
LLM_PROVIDER=openai_compat
LLM_BASE_URL=https://slm.nexgai.cloud/v1   # vLLM / llama.cpp / hosted SLM
LLM_API_KEY=...
LLM_MODEL=...
```

**Connectors (OAuth):** set `GOOGLE_CLIENT_ID/SECRET`, `MICROSOFT_CLIENT_ID/SECRET`, and **every** `*_REDIRECT_URI` to `https://your-domain/...` (the localhost defaults will break prod OAuth).

## 3. Docker

```bash
docker compose up --build      # app + postgres
```
For real deploys: use a managed Postgres (drop the compose `postgres` service),
inject `.env` via your platform's secret store (not a committed file), and put
the app behind nginx/Traefik for TLS. The container healthcheck hits `/health`.

## 4. Known follow-ups (not blockers)

- SSO state is in-memory — move to Redis for multi-replica deploys (`app/api/auth_routes.py`).
- No Alembic — schema migrations are forward-only on boot; review schema changes before deploying.
- Rotate the Google OAuth client in `.env` before sharing the repo (it is gitignored and was never committed, so it is not exposed in git history).

## 5. Guardrails (built-in)

Every write action (send email, create event, delete, archive, mark-read) is
gated by the per-user **L1–L5 autonomy** level and audit-logged. L1 is fully
read-only; L2–L4 require explicit confirmation for risky actions; L5 is full
auto. Data-derived chat answers pass through a grounding verifier
(`GROUNDING_VERIFY_ENABLED`). The eval harness (`python -m eval.run_eval`)
gates this behaviour — run it in CI.
```
