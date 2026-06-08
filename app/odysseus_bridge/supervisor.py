"""Process-per-tenant supervisor for vendored Odysseus instances.

Each tenant gets ONE isolated Odysseus subprocess (own ``ODYSSEUS_DATA_DIR`` +
SQLite ``DATABASE_URL``), bound to loopback. MyAi reverse-proxies ``/api/oui/*``
to it (see :mod:`app.odysseus_bridge.proxy`), injecting the creator identity via
Odysseus's trusted-proxy impersonation hook (``X-Odysseus-Internal-Token`` +
``X-Odysseus-Owner``). Per-creator owner-scoping then isolates users within a
tenant, while the per-tenant data dir + DB give hard cross-tenant isolation.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import secrets
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional, Set

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

_SAFE_RE = re.compile(r"[^a-zA-Z0-9_.-]+")

# Per-instance service admin used ONLY to provision creator users. Odysseus's
# user-management routes authenticate via the session cookie (not the
# trusted-proxy impersonation header), so we maintain an admin session per
# instance. The password is derived deterministically so it survives instance
# restarts (the user is persisted in the instance's auth store).
ADMIN_USERNAME = "__myai_admin"
SESSION_COOKIE = "odysseus_session"


def _safe(name: str) -> str:
    """Filesystem/URL-safe slug for a tenant id."""
    return _SAFE_RE.sub("_", (name or "default").strip()) or "default"


@dataclass
class Instance:
    tenant_id: str
    port: int
    proc: subprocess.Popen
    data_dir: Path
    started_at: float
    last_used: float
    ready: bool = False
    provisioned: Set[str] = field(default_factory=set)  # Odysseus usernames ensured
    admin_cookie: Optional[str] = None
    admin_ready: bool = False

    @property
    def alive(self) -> bool:
        return self.proc.poll() is None

    @property
    def base_url(self) -> str:
        s = get_settings()
        return f"http://{s.odysseus_host}:{self.port}"


class OdysseusSupervisor:
    """Owns the lifecycle of per-tenant Odysseus subprocesses."""

    def __init__(self) -> None:
        self._instances: Dict[str, Instance] = {}
        self._locks: Dict[str, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()
        self._reaper: Optional[asyncio.Task] = None

    # ---- locking -------------------------------------------------------
    async def _tenant_lock(self, tenant_id: str) -> asyncio.Lock:
        async with self._locks_guard:
            lock = self._locks.get(tenant_id)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[tenant_id] = lock
            return lock

    # ---- port allocation ----------------------------------------------
    @staticmethod
    def _port_free(port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sk:
            sk.settimeout(0.3)
            return sk.connect_ex(("127.0.0.1", port)) != 0

    def _pick_port(self) -> int:
        s = get_settings()
        used = {i.port for i in self._instances.values() if i.alive}
        port = s.odysseus_port_base
        # bounded scan so a misconfiguration can't loop forever
        for _ in range(500):
            if port not in used and self._port_free(port):
                return port
            port += 1
        raise RuntimeError("No free port available for an Odysseus instance")

    # ---- lifecycle -----------------------------------------------------
    async def ensure_running(self, tenant_id: str) -> Instance:
        """Return a healthy instance for ``tenant_id``, launching if needed."""
        lock = await self._tenant_lock(tenant_id)
        async with lock:
            inst = self._instances.get(tenant_id)
            if inst and inst.alive:
                if inst.ready or await self._await_health(inst):
                    inst.ready = True
                    inst.last_used = time.time()
                    return inst
                # booted but never became healthy — recycle
                self._kill(inst)
            inst = self._spawn(tenant_id)
            self._instances[tenant_id] = inst
            if not await self._await_health(inst):
                tail = self._log_tail(tenant_id)
                self._kill(inst)
                raise RuntimeError(
                    f"Odysseus instance for tenant '{tenant_id}' failed to boot. "
                    f"Did you run scripts/bootstrap_odysseus? Recent log:\n{tail}"
                )
            inst.ready = True
            inst.last_used = time.time()
            return inst

    def _spawn(self, tenant_id: str) -> Instance:
        s = get_settings()
        # The instance runs with cwd = the per-tenant ROOT and code imported via
        # --app-dir/PYTHONPATH. This makes the THREE ways Odysseus modules derive
        # a data path all resolve into the same per-tenant directory:
        #   - CWD-relative "data/..." (30+ call sites) -> tenant_root/data  (cwd)
        #   - config.py DataConfig base_dir            -> tenant_root/data  (DATA_BASE_DIR)
        #   - __file__-based "<code>/data" (auth, secret_storage, ...)
        #        -> tenant_root/data  (ODYSSEUS_DATA_DIR, via bridge patches)
        # Code assets remain absolute (BASE_DIR/STATIC_DIR) so cwd!=code is safe.
        tenant_root = s.odysseus_data_root_path / _safe(tenant_id)
        data_dir = tenant_root / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        port = self._pick_port()

        code_dir = str(s.odysseus_dir_path)
        py = s.odysseus_python_exe or sys.executable
        env = os.environ.copy()
        # --- hard per-tenant isolation (all anchors -> tenant_root/data) ---
        env["ODYSSEUS_DATA_DIR"] = str(data_dir)
        env["DATA_BASE_DIR"] = str(tenant_root)
        env["DATABASE_URL"] = f"sqlite:///{(data_dir / 'app.db').as_posix()}"
        env["PYTHONPATH"] = code_dir + os.pathsep + env.get("PYTHONPATH", "")
        # --- trusted-proxy identity (MyAi is the only caller) ---
        env["ODYSSEUS_INTERNAL_TOKEN"] = s.odysseus_internal_token_value
        env["AUTH_ENABLED"] = "true"      # users exist; impersonation needs auth_manager
        if s.odysseus_embed:
            # Local embed mode: the same-machine browser loads the instance in an
            # iframe (http://127.0.0.1:<port>/...). Allow loopback browser requests
            # (LOCALHOST_BYPASS) and permit framing (ODYSSEUS_ALLOW_EMBED). The
            # proxy path still injects the creator identity for MyAi-native pages;
            # iframe traffic runs single-user-per-tenant. Instances bind loopback
            # only, so this is safe for local/single-user deployments.
            env["LOCALHOST_BYPASS"] = "true"
            env["ODYSSEUS_ALLOW_EMBED"] = "1"
        else:
            env["LOCALHOST_BYPASS"] = "false"  # never trust bare loopback; require the token
        # --- bind + LLM passthrough (instance reaches the same providers) ---
        env["HOST"] = s.odysseus_host
        env["PORT"] = str(port)
        if s.llm_api_key:
            env.setdefault("OPENAI_API_KEY", s.llm_api_key)

        log_path = s.logs_dir / f"odysseus_{_safe(tenant_id)}.log"
        log_fp = open(log_path, "ab", buffering=0)
        cmd = [
            py, "-m", "uvicorn", "app:app",
            "--host", s.odysseus_host, "--port", str(port),
            "--log-level", "warning",
            "--app-dir", code_dir,
        ]
        proc = subprocess.Popen(
            cmd,
            cwd=str(tenant_root),
            env=env,
            stdout=log_fp,
            stderr=subprocess.STDOUT,
        )
        logger.info(
            "Spawned Odysseus tenant=%s pid=%s port=%s data=%s python=%s",
            tenant_id, proc.pid, port, data_dir, py,
        )
        return Instance(
            tenant_id=tenant_id, port=port, proc=proc, data_dir=data_dir,
            started_at=time.time(), last_used=time.time(),
        )

    async def _await_health(self, inst: Instance) -> bool:
        s = get_settings()
        deadline = time.time() + s.odysseus_boot_timeout_s
        url = f"{inst.base_url}/api/auth/status"
        headers = {"X-Odysseus-Internal-Token": s.odysseus_internal_token_value}
        async with httpx.AsyncClient(timeout=3.0) as client:
            while time.time() < deadline:
                if not inst.alive:
                    return False  # crashed during boot
                try:
                    r = await client.get(url, headers=headers)
                    if r.status_code < 600:
                        return True
                except Exception:
                    pass
                await asyncio.sleep(0.5)
        return False

    def _admin_password(self, tenant_id: str) -> str:
        """Deterministic service-admin password (stable across restarts)."""
        raw = f"{get_settings().odysseus_internal_token_value}:{tenant_id}:admin"
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    async def _ensure_admin(self, inst: Instance) -> None:
        """Ensure the per-instance service admin exists and we hold a session.

        Odysseus user-management routes authenticate via the ``odysseus_session``
        cookie (not the impersonation header), so we run first-run setup once and
        keep an admin session cookie to create creator users.
        """
        if inst.admin_ready and inst.admin_cookie:
            return
        base = inst.base_url
        pw = self._admin_password(inst.tenant_id)
        async with httpx.AsyncClient(timeout=15.0) as client:
            configured = False
            try:
                st = await client.get(f"{base}/api/auth/status")
                configured = bool(st.json().get("configured")) if st.status_code == 200 else False
            except Exception:  # noqa: BLE001
                configured = False
            if not configured:
                await client.post(
                    f"{base}/api/auth/setup",
                    json={"username": ADMIN_USERNAME, "password": pw},
                )
            r = await client.post(
                f"{base}/api/auth/login",
                json={"username": ADMIN_USERNAME, "password": pw, "remember": True},
            )
            cookie = r.cookies.get(SESSION_COOKIE)
            if not cookie:
                raise RuntimeError(
                    f"admin login failed tenant={inst.tenant_id}: HTTP {r.status_code} {r.text[:160]}"
                )
            inst.admin_cookie = cookie
            inst.admin_ready = True

    async def ensure_user(self, inst: Instance, creator: str, is_admin: bool = False) -> None:
        """Idempotently provision an Odysseus user for a MyAi creator.

        The user's password is random and never used for login — request-time
        identity comes from the trusted-proxy impersonation header. The user
        only needs to *exist* so owner-scoping attributes its data correctly.

        ``is_admin`` controls whether the creator gets Odysseus admin (full
        privileges incl. shell/bash + instance management) or a normal,
        privilege-restricted user. The caller (proxy) decides this from the
        MyAi user's roles. NOTE: admin status is set at creation; a role change
        mid-life needs the instance to be recycled (or a future privilege sync).
        """
        username = (creator or "user").strip().lower()
        if username in inst.provisioned:
            return
        try:
            await self._ensure_admin(inst)
            url = f"{inst.base_url}/api/auth/users"
            body = {"username": username, "password": secrets.token_urlsafe(24), "is_admin": bool(is_admin)}
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.post(url, json=body, cookies={SESSION_COOKIE: inst.admin_cookie})
                if r.status_code == 401:
                    # session expired -> re-login once and retry
                    inst.admin_ready = False
                    inst.admin_cookie = None
                    await self._ensure_admin(inst)
                    r = await client.post(url, json=body, cookies={SESSION_COOKIE: inst.admin_cookie})
            # 200 = created; 409 = already exists — both mean "user is present"
            if r.status_code in (200, 409):
                inst.provisioned.add(username)
            else:
                logger.warning(
                    "provision user tenant=%s creator=%s -> HTTP %s: %s",
                    inst.tenant_id, username, r.status_code, r.text[:200],
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("provision user failed tenant=%s creator=%s: %s",
                           inst.tenant_id, username, exc)

    # ---- teardown ------------------------------------------------------
    def _kill(self, inst: Instance) -> None:
        try:
            inst.proc.terminate()
            try:
                inst.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                inst.proc.kill()
        except Exception:  # noqa: BLE001
            pass
        self._instances.pop(inst.tenant_id, None)
        logger.info("Stopped Odysseus tenant=%s pid=%s", inst.tenant_id, inst.proc.pid)

    def _log_tail(self, tenant_id: str, n: int = 1500) -> str:
        try:
            p = get_settings().logs_dir / f"odysseus_{_safe(tenant_id)}.log"
            data = p.read_bytes()[-n:]
            return data.decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            return "(no log)"

    async def shutdown_all(self) -> None:
        for inst in list(self._instances.values()):
            self._kill(inst)

    # ---- idle reaper ---------------------------------------------------
    async def reaper_loop(self) -> None:
        s = get_settings()
        if s.odysseus_idle_timeout_s <= 0:
            return
        while True:
            try:
                await asyncio.sleep(60)
                now = time.time()
                for inst in list(self._instances.values()):
                    if not inst.alive:
                        self._instances.pop(inst.tenant_id, None)
                        continue
                    if now - inst.last_used > s.odysseus_idle_timeout_s:
                        logger.info("Reaping idle Odysseus tenant=%s", inst.tenant_id)
                        self._kill(inst)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("Odysseus reaper error: %s", exc)


_supervisor: Optional[OdysseusSupervisor] = None


def get_supervisor() -> OdysseusSupervisor:
    global _supervisor
    if _supervisor is None:
        _supervisor = OdysseusSupervisor()
    return _supervisor
