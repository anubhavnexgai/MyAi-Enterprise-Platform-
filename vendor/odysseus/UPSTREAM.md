# Vendored: Odysseus

This directory is a vendored copy of the upstream Odysseus repository, run by the
MyAi bridge (`app/odysseus_bridge/`) as one isolated subprocess per tenant and
reverse-proxied under `/api/oui/*`.

- **Upstream:** https://github.com/pewdiepie-archdaemon/odysseus
- **Vendored commit:** `e163384015ef0ba7fd8573a4cb0069d294e0933b` (`e163384`)
- **Vendored on:** 2026-06-04
- **Method:** `git archive HEAD | tar -x` (tracked files only; no `.git`).

## How it runs in MyAi

The bridge does **not** import this code into MyAi's Python process. Each tenant
gets its own `uvicorn app:app` subprocess (cwd = this directory, its own venv at
`./.venv`), with `ODYSSEUS_DATA_DIR` + `DATABASE_URL` pointed at a per-tenant
data directory under `data/odysseus/<tenant>/`. Identity is injected by the proxy
via Odysseus's built-in trusted-proxy impersonation hook
(`X-Odysseus-Internal-Token` + `X-Odysseus-Owner`). See `app/odysseus_bridge/`.

Set up the venv with `scripts/bootstrap_odysseus.ps1` (Windows) or
`scripts/bootstrap_odysseus.sh` (macOS/Linux).

## Bridge patches (keep this list exhaustive)

To ease future `git archive` re-pulls, edits to vendored files are kept to the
absolute minimum and recorded here. Re-apply these after any upstream refresh.

All patches honor the `ODYSSEUS_DATA_DIR` environment variable (falling back to
the upstream `BASE_DIR/data` default when unset, so standalone Odysseus behaves
identically). They exist because several modules derive their *own* data path
independently of `core/constants.DATA_DIR`; without these, tenant subprocesses
would share those files. Each is marked inline with `# [MyAi bridge patch]`.

The instance is launched with **cwd = the per-tenant data root** (+ `--app-dir`/
`PYTHONPATH` pointing at this code), which transparently isolates the 30+
*CWD-relative* `data/...` call sites for free. The patches below cover the paths
that are NOT cwd-relative (they derive from `__file__` or a config `base_dir`):

1. **`src/constants.py`** — `DATA_DIR` (PRIMARY constants module; most data
   modules import from here: memory, skills, presets, personal_docs, uploads).
1b. **`core/constants.py`** — `DATA_DIR` (secondary copy used by `app.py` et al).
1c. **`app.py`** — `StaticFiles` mount uses absolute `STATIC_DIR` instead of the
    CWD-relative `"static"`, so the instance can run with cwd=tenant-root.
2. **`core/auth.py`** — `DEFAULT_AUTH_PATH` (`auth.json`; `sessions.json` derives
   from the same dir). *Critical: per-tenant user + session isolation.*
3. **`src/secret_storage.py`** — `_KEY_PATH` (`.app_key`). *Critical: per-tenant
   Fernet encryption key.*
4. **`src/integrations.py`** — `DATA_FILE` (`integrations.json`) and the
   `settings.json` path in the Miniflux migration helper.
5. **`routes/contacts_routes.py`** — `DATA_DIR` (`contacts.json`, `settings.json`);
   also adds `import os`.
6. **`routes/email_helpers.py`** — `DATA_DIR` (`settings.json`, mail attachments).
7. **`routes/document_routes.py`** — the `_DATA_DIR` for compose/mail attachments.
8. **`src/rag_singleton.py`** — `persist_dir` for the per-tenant RAG vector store.

Intentionally NOT patched (correctly shared across tenants):
- `src/mcp_manager.py`, `src/builtin_mcp.py` — `base_dir` locates built-in MCP
  server *scripts* in the code tree (code, not tenant data).
- `src/embeddings.py`, `src/rag_singleton.py`, `routes/embedding_routes.py` —
  local embedding-model cache (read-only weights; sharing saves disk).
- `routes/emoji_routes.py` (emoji cache), `services/hwfit/models.py`
  (static model catalog) — read-only, no tenant data.
- Per-tenant SQLite (`app.db`) isolation is handled by `DATABASE_URL`, set by the
  supervisor; the raw `sqlite3` callers in email/task routes inherit it.

- `routes/model_routes.py` `_fetch_models`: surface admin-`pinned_models` in the
  picker even on unprobed endpoints, and for any `openrouter.ai` endpoint expose
  ONLY free models (`id` ends `:free`). This is the free-only enforcement so a
  paid OpenRouter model can never be selected, regardless of what a probe cached.
- `src/llm_core.py` `_apply_openrouter_free_fallback`: for `openrouter.ai`
  targets with a `:free` primary model, inject OpenRouter's `models` fallback
  array (curated free list) into the sync/async/stream payloads so a 429 on one
  free model auto-falls-back to the next (Deep Research resilience). Never routes
  to a paid model.

All other integration lives in `app/odysseus_bridge/` and is driven by
environment variables + the existing upstream trusted-proxy impersonation path in
`app.py`'s `AuthMiddleware`.
