# MyAi-Enterprise — Enterprise rollout (GCP models · shell sandbox · Teams)

This covers the three enterprise items. **GCP-hosted models work today via config.**
The **docker shell sandbox** and **Microsoft Teams gateway** require external
infrastructure (a Docker daemon, an Azure Bot registration) to activate, so they
are documented here with the exact steps + the config flags that switch them on.

---

## 1. GCP-hosted models  ✅ ready now (config only)

MyAi's LLM layer is provider-agnostic (`app/services/llm_client.py`). Any
OpenAI-compatible endpoint works — including a model you self-host on GCP
(vLLM / TGI / llama.cpp server on GKE or a Vertex AI OpenAI-compatible endpoint).
This is the **same mechanism** currently pointing at OpenRouter.

To switch the **native copilot/agent/research** to GCP, set in `.env`:

```dotenv
LLM_PROVIDER=openai_compat
LLM_BASE_URL=https://<your-gcp-model-endpoint>/v1     # must end in /v1
LLM_API_KEY=<token-if-required>
LLM_MODEL=<served-model-id>                            # e.g. meta-llama/Llama-3.3-70B-Instruct
# Optional resilience if the endpoint serves several models:
LLM_FALLBACK_MODELS=<model-b>,<model-c>
# Embeddings stay local unless your GCP endpoint serves an embeddings model:
EMBED_BASE_URL=http://localhost:11434
```

For the **chat / agent / research UI** (the Odysseus bridge), add the GCP endpoint
in the app: **Cookbook / Models** → add a model endpoint with the GCP base URL +
key (or `POST /api/oui/model-endpoints` with `base_url`, `api_key`). It appears in
the model picker alongside Ollama + OpenRouter.

Per-tenant token analytics, the free-only filter, and the 429 fallback all keep
working — they're keyed on the endpoint, not the provider. No code changes needed
to move from OpenRouter → GCP; just env + an endpoint row.

**To verify:** set the env vars, restart, then `POST /api/copilot/chat {"message":"hi"}`
and confirm the response `model` is your GCP model.

---

## 2. Docker shell sandbox  ⚙️ needs Docker daemon

**Why:** the agent's shell / computer-use runs in the per-tenant Odysseus
subprocess on the **host** (cwd = the tenant's data root). Cross-tenant isolation
is by process + data dir + the role-gated `can_use_bash` privilege (only
admin/owner roles get shell — see `app/odysseus_bridge/`). For untrusted
multi-employee shell on a shared box you want OS-level isolation: run each shell
command in a throwaway container.

**Approach (per-command sandbox):**
1. Install Docker on the host; pull a minimal image (e.g. `python:3.12-slim`).
2. Run each agent shell command as:
   ```
   docker run --rm --network=none --cpus=1 --memory=512m \
     --read-only --tmpfs /tmp \
     -v <tenant_data_root>:/work:rw -w /work \
     myai-sandbox:latest bash -lc "<command>"
   ```
   `--network=none` (no egress), `--read-only` + `--tmpfs` (no host writes outside
   the mount), cpu/mem caps, auto-remove. The tenant data dir is the only writable
   mount, so a command can't reach other tenants or the host FS.
3. Wire it where the bash tool executes — `vendor/odysseus` agent shell tool
   (search the vendored tree for the `subprocess`/`bash` execution path) — behind a
   flag, falling back to host exec when Docker is absent.

**Config flag (add to `app/config.py` / `.env`):**
```dotenv
SHELL_SANDBOX=docker          # off|docker   (default off → current host behavior)
SHELL_SANDBOX_IMAGE=myai-sandbox:latest
```
**Status:** documented + ready to wire; not enabled by default because it requires
a running Docker daemon (not available in the current dev box, so it's unverified
here). The role-gated `can_use_bash` privilege is the active safeguard until this
is switched on.

---

## 3. Microsoft Teams gateway  ⚙️ needs Azure Bot registration

**Why:** let employees talk to MyAi from Teams (DM the bot / @mention in a
channel) — the same agent, surfaced in Teams.

**Approach (Bot Framework inbound webhook):**
1. **Azure**: create an *Azure Bot* resource → note the App ID + password
   (client secret). Add the **Teams** channel. Set the messaging endpoint to
   `https://<your-host>/api/teams/messages`.
2. **Teams app manifest**: package a manifest with the bot ID; sideload or publish
   to your tenant's app catalog.
3. **MyAi endpoint** (`app/api/teams_routes.py`, to add): a `POST /api/teams/messages`
   that validates the Bot Framework JWT (`Authorization` bearer, JWKS from
   `https://login.botframework.com/v1/.well-known/openidconfiguration`), maps the
   Teams AAD user → a MyAi `(tenant_id, creator_id)`, runs the message through the
   same `run_agent` / copilot path, and replies via the Bot Connector API
   (`<serviceUrl>/v3/conversations/{id}/activities`).
4. Reuse the existing per-employee identity + autonomy + audit so Teams traffic
   shows in the super-admin analytics like web traffic.

**Config (add to `.env`):**
```dotenv
TEAMS_ENABLED=false
TEAMS_APP_ID=<azure-bot-app-id>
TEAMS_APP_PASSWORD=<azure-bot-secret>
```
**Status:** documented; not wired because it needs an Azure Bot registration +
public HTTPS endpoint (can't be created/tested from the dev box). The reusable
pieces (agent loop, identity, autonomy, analytics) are all in place — the gateway
is a thin adapter on top.

---

## 4. Microsoft / Entra SSO sign-in  ⚙️ needs Azure app registration

The "Sign in with Microsoft" button is **hidden in the UI for now** (demo accounts
only). The backend (`app/auth/sso.py`, `/api/auth/sso/login` + `/callback`) is
ready — it returns a clean `503 "Azure AD is not configured"` until creds are set.
There is **no code bug**; it just needs an Entra app registration:

1. **Azure Portal → Entra ID → App registrations → New registration.**
   - Redirect URI (Web): `http://localhost:8002/api/auth/sso/callback`
     (add your prod/ngrok callback too if needed).
   - Note the **Application (client) ID** and **Directory (tenant) ID**.
2. **Certificates & secrets → New client secret** → copy the value.
3. Set in `.env`:
   ```dotenv
   AZURE_TENANT_ID=<tenant-id>
   AZURE_CLIENT_ID=<client-id>
   AZURE_CLIENT_SECRET=<secret>
   AZURE_REDIRECT_URI=http://localhost:8002/api/auth/sso/callback
   ```
4. Restart, then re-show the button (un-hide it in `web/app.js` `showLoginScreen`).
   SSO users are auto-provisioned into the employee directory + analytics.

## Summary

| Item | State | To activate |
|------|-------|-------------|
| GCP-hosted models | **Ready** | Set `LLM_*` env to your GCP endpoint (+ add a Cookbook model endpoint for the chat UI) |
| Docker shell sandbox | Documented + flagged | Install Docker, build `myai-sandbox`, set `SHELL_SANDBOX=docker`, wire the bash tool |
| Teams gateway | Documented | Register an Azure Bot, add `/api/teams/messages`, set `TEAMS_*` |
