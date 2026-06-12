/* =========================================================
   MyAi for NexgAI — Frontend Router + Page Logic
   Vanilla JS, hash-based routing, fragments from pages/*.html
   ========================================================= */

const ROUTES = {
  dashboard: "pages/dashboard.html",
  inbox: "pages/inbox.html",
  copilot: "pages/copilot.html",
  logs: "pages/logs.html",
  connectors: "pages/connectors.html",
  settings: "pages/settings.html",
  admin: "pages/admin.html",
  // Odysseus-backed workspace features (served via the /api/oui/* bridge proxy).
  // These page fragments self-initialize via their own inline scripts, so they
  // need no entry in the go() switch.
  documents: "pages/documents.html",
  email: "pages/email.html",
  calendar: "pages/calendar.html",
  tasks: "pages/tasks.html",
  memory: "pages/memory.html",
  research: "pages/research.html",
  agents: "pages/agents.html",
};

// Shared helper for the Odysseus-backed workspace pages: thin wrappers around
// the /api/oui/* bridge proxy. Cookie auth (credentials:include) is added by the
// browser; the proxy injects the per-tenant creator identity upstream.
window.oui = {
  base: "/api/oui",
  async get(path) {
    try { const r = await fetch(this.base + path, { credentials: "include" }); if (!r.ok) return null; return await r.json(); }
    catch { return null; }
  },
  async postForm(path, obj) {
    const f = new FormData();
    for (const k in obj) { if (obj[k] != null) f.append(k, obj[k]); }
    const r = await fetch(this.base + path, { method: "POST", credentials: "include", body: f });
    return { ok: r.ok, status: r.status, json: await r.json().catch(() => null) };
  },
  async postJson(path, obj, method) {
    const r = await fetch(this.base + path, {
      method: method || "POST", credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: obj == null ? undefined : JSON.stringify(obj),
    });
    return { ok: r.ok, status: r.status, json: await r.json().catch(() => null) };
  },
  del(path) { return this.postJson(path, null, "DELETE"); },
};

const view = document.getElementById("view");
const nav = document.getElementById("nav");

/* ---------- Mock data ---------- */
const MOCK = {
  // Offline fallback only — shown when /api/dashboard is unreachable. Neutral,
  // personal-assistant framing (no demo/bank content).
  kpis: [
    { label: "UNREAD EMAILS", value: "—", foot: "Connect Gmail / Outlook" },
    { label: "MEETINGS TODAY", value: "—", foot: "Connect Calendar" },
    { label: "OPEN TASKS", value: "—", foot: "Nothing loaded" },
    { label: "DUE TODAY", value: "—", foot: "Nothing loaded" },
    { label: "DRAFTS WAITING", value: "—", foot: "Nothing loaded" },
    { label: "COMPLETED TODAY", value: "—", foot: "Nothing loaded" },
  ],

  retention: {
    active: "—", wonWeek: "—", lostWeek: "—", saveRate: "—",
    avgDiscount: "—", avgLevels: "—", competitors: "—", escalations: "—",
  },

  negotiations: [],

  inbox: [],

  copilotRecents: [
    { group: "Today", items: [
      { title: "Draft sprint update for Priti", time: "10:42" },
      { title: "Inbox triage — top 5 unread", time: "09:18" },
    ]},
    { group: "Yesterday", items: [
      { title: "Calendar — move 1:1 to Wednesday", time: "17:55" },
      { title: "Summarize PRD doc from Drive", time: "14:02" },
      { title: "Reminder set: code review at 3pm", time: "11:30" },
    ]},
    { group: "Earlier this week", items: [
      { title: "Plan Q3 sprint priorities", time: "Mon" },
      { title: "Compare two design proposals", time: "Mon" },
      { title: "Research AI agent frameworks", time: "Sun" },
    ]},
  ],

  copilotActions: [
    { icon: "mail",          t: "Summarize my inbox",  d: "Top emails that need a reply" },
    { icon: "event",         t: "What's on my calendar?", d: "Today's meetings + free time" },
    { icon: "edit_note",     t: "Draft a status update", d: "From your recent work" },
    { icon: "search",        t: "Search my Drive",      d: "Find a file or doc" },
    { icon: "alarm",         t: "Set a reminder",       d: "Remind me later about X" },
    { icon: "task_alt",      t: "Run a task overnight", d: "Background goal — wake to results" },
    { icon: "image",         t: "Read my screen",       d: "Analyze a screenshot" },
    { icon: "tips_and_updates", t: "Plan my day",       d: "Help me prioritize" },
  ],
};

const TYPE_BADGES = {
  info:    { cls: "pill-blue",   icon: "info" },
  success: { cls: "pill-green",  icon: "check_circle" },
  error:   { cls: "pill-red",    icon: "error" },
  warn:    { cls: "pill-orange", icon: "warning" },
};

/* ---------- API helpers (with mock fallback) ---------- */
async function safeFetchJson(url, options = {}) {
  try {
    const opts = { ...options };
    if (opts.body && typeof opts.body !== "string") {
      opts.body = JSON.stringify(opts.body);
      opts.headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
    }
    const r = await fetch(url, opts);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    if (r.status === 204) return { ok: true };
    return await r.json();
  } catch {
    return null;
  }
}

/* ---------- Router ---------- */
// Bump this to force every page fragment to refetch (defeats SW + HTTP cache,
// which otherwise serve stale pages/*.html even when the server has new ones).
const ASSET_BUILD = "20260612d";
async function loadFragment(route) {
  const path = ROUTES[route] || ROUTES.dashboard;
  try {
    const r = await fetch(path + (path.includes("?") ? "&" : "?") + "v=" + ASSET_BUILD, { cache: "no-store" });
    if (!r.ok) throw new Error("fragment 404");
    view.innerHTML = await r.text();
    // Inline <script> tags inserted via innerHTML do not execute automatically.
    // Re-create them so per-page bootstrap (connectors, settings, etc.) runs.
    view.querySelectorAll("script").forEach(old => {
      const s = document.createElement("script");
      for (const a of old.attributes) s.setAttribute(a.name, a.value);
      s.text = old.text;
      old.parentNode.replaceChild(s, old);
    });
  } catch {
    view.innerHTML = `<div class="card card-pad">
      <h2 class="card-title">Page not available offline</h2>
      <p class="card-sub">Could not load ${path}. Serve the directory via a static HTTP server (e.g. <code>python -m http.server</code>) to view this app.</p>
    </div>`;
  }
}

async function go(route) {
  // Notes is a slide-over panel beside the chat (not a full page) — open the
  // drawer and keep the current view underneath.
  if (route === "notes") { if (window.openNotesDrawer) window.openNotesDrawer(); return; }
  if (!ROUTES[route]) route = "dashboard";
  // Close any open modal/menu on navigation — otherwise a left-open modal
  // backdrop stays on top and silently blocks every click on the new page.
  closeModal();
  document.getElementById("userMenu")?.classList.remove("open");
  // highlight nav
  nav.querySelectorAll("a").forEach(a => a.classList.toggle("active", a.dataset.route === route));
  await loadFragment(route);

  // Page-enter animation: retrigger the fade/slide on every navigation.
  view.classList.remove("page-enter");
  void view.offsetWidth; // reflow so the animation restarts
  view.classList.add("page-enter");

  switch (route) {
    case "dashboard":  initDashboard();  break;
    case "inbox":      initInbox();      break;
    case "copilot":    initCopilot();    break;
    case "logs":       initLogs();       break;
    case "admin":      initAdmin();      break;
    case "connectors": initConnectors(); break;
    case "settings":   initSettings();   break;
  }
}

function currentRoute() {
  const h = location.hash.replace(/^#\/?/, "");
  return h || "dashboard";
}

/* ---------- Command palette (⌘K / Ctrl+K) ---------- */
const PALETTE_ITEMS = [
  { icon: "dashboard", label: "Dashboard", hint: "Overview", run: () => (location.hash = "#/dashboard") },
  { icon: "smart_toy", label: "Copilot", hint: "Chat", run: () => (location.hash = "#/copilot") },
  { icon: "groups", label: "Agents Council", hint: "Multi-agent", run: () => (location.hash = "#/agents") },
  { icon: "travel_explore", label: "Deep Research", hint: "Research", run: () => (location.hash = "#/research") },
  { icon: "mail", label: "Email", run: () => (location.hash = "#/email") },
  { icon: "calendar_month", label: "Calendar", run: () => (location.hash = "#/calendar") },
  { icon: "sticky_note_2", label: "Notes", run: () => (window.openNotesDrawer ? window.openNotesDrawer() : (location.hash = "#/notes")) },
  { icon: "checklist", label: "Tasks & Routines", run: () => (location.hash = "#/tasks") },
  { icon: "monitoring", label: "Logs", run: () => (location.hash = "#/logs") },
  { icon: "hub", label: "Connectors", run: () => (location.hash = "#/connectors") },
  { icon: "settings", label: "Settings", run: () => (location.hash = "#/settings") },
  { icon: "add_comment", label: "New chat", hint: "Action", run: () => { location.hash = "#/copilot"; setTimeout(() => document.getElementById("ocNewChat")?.click(), 250); } },
  { icon: "edit", label: "Compose email", hint: "Action", run: () => (window.openComposeEmail ? window.openComposeEmail() : (location.hash = "#/email")) },
  { icon: "description", label: "New document", hint: "Action", run: () => window.openDocsDrawer && window.openDocsDrawer() },
];

function openCommandPalette() {
  if (document.getElementById("cmdkBackdrop")) return;
  const bd = document.createElement("div");
  bd.id = "cmdkBackdrop";
  bd.className = "cmdk-backdrop";
  bd.innerHTML =
    '<div class="cmdk" role="dialog" aria-label="Command palette">' +
    '<div class="cmdk-input"><span class="material-symbols-rounded">search</span>' +
    '<input id="cmdkq" placeholder="Search pages and actions…" autocomplete="off" /></div>' +
    '<div class="cmdk-list" id="cmdkList"></div>' +
    '<div class="cmdk-foot"><span><b>↑↓</b> navigate</span><span><b>↵</b> open</span><span><b>esc</b> close</span></div></div>';
  document.body.appendChild(bd);
  const q = bd.querySelector("#cmdkq"), list = bd.querySelector("#cmdkList");
  let filtered = PALETTE_ITEMS.slice(), sel = 0;
  const render = () => {
    list.innerHTML = filtered.map((it, i) =>
      '<div class="cmdk-row' + (i === sel ? " sel" : "") + '" data-i="' + i + '">' +
      '<span class="material-symbols-rounded">' + it.icon + "</span>" +
      '<span class="cmdk-label">' + escapeHtml(it.label) + "</span>" +
      (it.hint ? '<span class="cmdk-hint">' + escapeHtml(it.hint) + "</span>" : "") +
      "</div>").join("") || '<div class="cmdk-empty">No matches</div>';
    const cur = list.querySelector(".cmdk-row.sel");
    if (cur) cur.scrollIntoView({ block: "nearest" });
  };
  const close = () => { bd.remove(); document.removeEventListener("keydown", onKey, true); };
  const choose = (i) => { const it = filtered[i]; close(); if (it) try { it.run(); } catch (e) {} };
  const onKey = (e) => {
    if (e.key === "Escape") { e.preventDefault(); close(); }
    else if (e.key === "ArrowDown") { e.preventDefault(); sel = Math.min(sel + 1, filtered.length - 1); render(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); sel = Math.max(sel - 1, 0); render(); }
    else if (e.key === "Enter") { e.preventDefault(); choose(sel); }
  };
  q.addEventListener("input", () => {
    const s = q.value.toLowerCase().trim();
    filtered = s ? PALETTE_ITEMS.filter(it => (it.label + " " + (it.hint || "")).toLowerCase().includes(s)) : PALETTE_ITEMS.slice();
    sel = 0; render();
  });
  list.addEventListener("click", e => { const r = e.target.closest(".cmdk-row"); if (r) choose(+r.dataset.i); });
  bd.addEventListener("mousedown", e => { if (e.target === bd) close(); });
  document.addEventListener("keydown", onKey, true);
  render(); q.focus();
}

window.addEventListener("hashchange", () => go(currentRoute()));
window.addEventListener("DOMContentLoaded", async () => {
  // ⌘K / Ctrl+K opens the command palette from anywhere.
  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && (e.key === "k" || e.key === "K")) {
      e.preventDefault();
      if (!document.body.classList.contains("login-active")) openCommandPalette();
    }
  });
  // Auth gate: if not signed in, show the login screen and stop booting the app.
  const _me0 = await safeFetchJson("/api/auth/me");
  if (!_me0) { await showLoginScreen(); return; }

  if (!location.hash) location.hash = "#/dashboard";

  // Hydrate sidebar with the REAL signed-in user, then load route.
  await hydrateUserSidebar();
  go(currentRoute());

  document.getElementById("userBtn").addEventListener("click", e => {
    if (e.target.closest(".user-menu")) return;
    document.getElementById("userMenu").classList.toggle("open");
  });
  document.addEventListener("click", e => {
    if (!e.target.closest("#userBtn")) {
      document.getElementById("userMenu")?.classList.remove("open");
    }
  });

  // Wire profile menu actions.
  document.getElementById("userMenu")?.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-menu]");
    if (!btn) return;
    const action = btn.dataset.menu;
    document.getElementById("userMenu").classList.remove("open");
    switch (action) {
      case "profile":       location.hash = "#/settings"; break;
      case "notifications": showNotifications(); break;
      case "help":          showHelp(); break;
      case "signout":       signOut(); break;
    }
  });
});

/* ---------- Identity + sidebar hydration ---------- */
window._me = null;

async function hydrateUserSidebar() {
  const me = await safeFetchJson("/api/auth/me");
  // If unauthenticated and not in DEV_MODE, show "Sign in" instead of leaving "Loading…"
  if (!me) {
    document.getElementById("userName").textContent = "Sign in";
    document.getElementById("userRole").textContent = "Not signed in";
    document.getElementById("userAvatar").textContent = "?";
    return;
  }

  // Prefer a connected Gmail account label for the display email
  let connectedEmail = null;
  const conns = await safeFetchJson("/api/connectors");
  if (conns && conns.connectors) {
    const gm = conns.connectors.find(c => c.provider === "google_gmail" && c.connected);
    if (gm && gm.account_label) connectedEmail = gm.account_label;
  }

  const displayName = me.full_name || me.username || me.email || "Me";
  const displayRole = me.roles && me.roles.length
    ? me.roles[0].charAt(0).toUpperCase() + me.roles[0].slice(1) + " · " + (me.tenant_id || "")
    : (me.tenant_id || "");

  window._me = { ...me, connectedEmail };
  // Reveal the Admin Console nav only for super-admins.
  if (Array.isArray(me.roles) && me.roles.includes("super_admin")) {
    const navAdmin = document.getElementById("navAdmin");
    if (navAdmin) navAdmin.style.display = "";
  }
  document.getElementById("userName").textContent = displayName;
  document.getElementById("userRole").textContent = connectedEmail || me.email || displayRole;
  document.getElementById("userAvatar").textContent =
    (displayName.split(" ").map(p => p[0]).join("").slice(0, 2) || "M").toUpperCase();
}

async function signOut() {
  await fetch("/api/auth/logout", { method: "POST", credentials: "include" }).catch(() => {});
  // Clear local chat history + reload
  window._copilotHistory = [];
  location.reload();
}

/* ---------- Login screen (shown when not authenticated) ---------- */
async function showLoginScreen() {
  document.body.classList.add("login-active");
  const data = await safeFetchJson("/api/auth/demo-accounts");
  const accts = (data && data.accounts) || [];

  const demoCards = accts.map(a => `
    <button type="button" class="login-demo" data-email="${escapeHtml(a.email)}" data-pw="${escapeHtml(a.password)}">
      <span class="login-demo-badge ${a.is_admin ? "is-admin" : ""}">${a.is_admin ? "★ Super Admin" : "Employee"}</span>
      <span class="login-demo-name">${escapeHtml(a.full_name)}</span>
      <span class="login-demo-blurb">${escapeHtml(a.blurb || "")}</span>
      <span class="login-demo-cred">${escapeHtml(a.email)} · ${escapeHtml(a.password)}</span>
    </button>`).join("");

  const o = document.createElement("div");
  o.id = "loginOverlay";
  o.innerHTML = `
    <div class="login-card ui-pop">
      <div class="login-brand">
        <div class="login-logo">M</div>
        <div>
          <div class="login-title">MyAi <span>Enterprise</span></div>
          <div class="login-sub">Your personal AI workforce</div>
        </div>
      </div>

      ${accts.length ? `<div class="login-demolabel">Quick sign-in — demo accounts</div>
      <div class="login-demos">${demoCards}</div>
      <div class="login-or"><span>or sign in manually</span></div>` : ""}

      <form id="loginForm" autocomplete="on">
        <label class="login-l">Email</label>
        <input id="loginEmail" type="email" class="login-input" placeholder="you@nexgai.com" required />
        <label class="login-l">Password</label>
        <input id="loginPw" type="password" class="login-input" placeholder="••••••••" required />
        <div id="loginErr" class="login-err"></div>
        <button id="loginBtn" type="submit" class="login-submit">Sign in</button>
      </form>
    </div>`;
  document.body.appendChild(o);

  const emailEl = o.querySelector("#loginEmail");
  const pwEl = o.querySelector("#loginPw");
  const errEl = o.querySelector("#loginErr");
  const btn = o.querySelector("#loginBtn");

  o.querySelectorAll(".login-demo").forEach(b => b.addEventListener("click", () => {
    emailEl.value = b.dataset.email;
    pwEl.value = b.dataset.pw;
    o.querySelector("#loginForm").requestSubmit();
  }));

  o.querySelector("#loginForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    errEl.textContent = "";
    btn.disabled = true; btn.textContent = "Signing in…";
    try {
      const r = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ email: emailEl.value.trim(), password: pwEl.value }),
      });
      if (r.ok) { location.reload(); return; }
      const j = await r.json().catch(() => ({}));
      errEl.textContent = j.detail || "Invalid email or password.";
    } catch (_) {
      errEl.textContent = "Network error. Is the server running?";
    }
    btn.disabled = false; btn.textContent = "Sign in";
  });
}

function showHelp() {
  openModal(`
    <div class="modal-head">
      <div><span style="font-weight:700;font-size:17px;color:var(--text-strong)">Help &amp; docs</span></div>
      <button class="modal-close" data-close><span class="material-symbols-rounded">close</span></button>
    </div>
    <div class="modal-body">
      <p>MyAi is your personal AI assistant for NexgAI. Here's how to get the most out of it:</p>
      <ul style="margin:12px 0;padding-left:20px;line-height:1.7">
        <li><b>Dashboard</b> — your KPIs (unread emails, meetings, open tasks) at a glance.</li>
        <li><b>Inbox</b> — every email/calendar item that needs your attention. Use the autonomy slider to control how much the AI does automatically.</li>
        <li><b>Copilot</b> — chat with MyAi. Ask things like "summarize my inbox", "what's on my calendar tomorrow", or "draft a status update".</li>
        <li><b>Connectors</b> — connect Gmail, Google Calendar, Microsoft 365 (Outlook) so MyAi can read your real data.</li>
        <li><b>Settings</b> — change your preferences, notifications, theme.</li>
      </ul>
      <p style="color:var(--text-muted);font-size:13px">Tip: try asking the copilot "what can you do?" — it will list every tool it currently has access to.</p>
    </div>
    <div class="modal-foot">
      <button class="btn" data-close>Close</button>
    </div>
  `);
}

async function showNotifications() {
  // Use the audit log as the notifications feed
  const data = await safeFetchJson("/api/logs?limit=20");
  const items = Array.isArray(data) ? data : (data?.entries || []);
  const list = items.length ? items.slice(0, 10).map(r => `
    <div style="padding:10px 12px;border-bottom:1px solid var(--border-dim)">
      <div style="font-weight:600;color:var(--text-strong);font-size:13px">${escapeHtml(r.event_type || "event")}</div>
      <div style="font-size:12px;color:var(--text-muted);margin-top:2px">${escapeHtml((r.message || "").slice(0, 140))}</div>
      <div style="font-size:11px;color:var(--text-muted);margin-top:4px">${new Date(r.created_at).toLocaleString()}</div>
    </div>
  `).join("") : `<div style="padding:24px;text-align:center;color:var(--text-muted)">No notifications yet</div>`;

  openModal(`
    <div class="modal-head">
      <div><span style="font-weight:700;font-size:17px;color:var(--text-strong)">Notifications</span></div>
      <button class="modal-close" data-close><span class="material-symbols-rounded">close</span></button>
    </div>
    <div class="modal-body" style="padding:0">
      ${list}
    </div>
    <div class="modal-foot">
      <button class="btn" data-close>Close</button>
    </div>
  `);
}

/* ---------- Modal ---------- */
function openModal(html) {
  const modal = document.getElementById("modal");
  const backdrop = document.getElementById("modalBackdrop");
  modal.innerHTML = html;
  backdrop.classList.add("open");
  modal.querySelectorAll("[data-close]").forEach(b => b.addEventListener("click", closeModal));
  backdrop.addEventListener("click", e => { if (e.target === backdrop) closeModal(); }, { once: true });
}
function closeModal() {
  document.getElementById("modalBackdrop").classList.remove("open");
}

/* ---------- Toast ---------- */
function toast(message, tone = "success") {
  let host = document.getElementById("toastHost");
  if (!host) {
    host = document.createElement("div");
    host.id = "toastHost";
    Object.assign(host.style, {
      position: "fixed", right: "20px", bottom: "20px", zIndex: "9999",
      display: "flex", flexDirection: "column", gap: "8px",
    });
    document.body.appendChild(host);
  }
  const colors = { success: "var(--green,#10B981)", error: "var(--red,#EF4444)", info: "var(--accent,#0891B2)" };
  const el = document.createElement("div");
  Object.assign(el.style, {
    background: "#fff", color: "var(--text-strong,#111)",
    borderLeft: `4px solid ${colors[tone] || colors.info}`,
    boxShadow: "0 6px 24px rgba(0,0,0,.14)", borderRadius: "10px",
    padding: "12px 16px", fontSize: "13px", maxWidth: "340px",
    transition: "opacity .3s, transform .3s", opacity: "0", transform: "translateY(8px)",
  });
  el.textContent = message;
  host.appendChild(el);
  requestAnimationFrame(() => { el.style.opacity = "1"; el.style.transform = "translateY(0)"; });
  setTimeout(() => { el.style.opacity = "0"; el.style.transform = "translateY(8px)"; setTimeout(() => el.remove(), 320); }, 3600);
}

/* Animate a numeric stat from 0 to its rendered value (keeps prefixes/suffixes
   like "%" or "$"). No-op for non-numeric text or reduced-motion users. */
function countUp(el, dur = 650) {
  if (window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const txt = (el.textContent || "").trim();
  const m = txt.match(/^([^\d-]*)([\d,]+(?:\.\d+)?)(\D*)$/);
  if (!m) return;
  const target = parseFloat(m[2].replace(/,/g, ""));
  if (!isFinite(target) || target === 0) return;
  const dec = (m[2].split(".")[1] || "").length;
  const t0 = performance.now();
  (function tick(t) {
    const p = Math.min(1, (t - t0) / dur), e = 1 - Math.pow(1 - p, 3); // ease-out cubic
    el.textContent = m[1] + (target * e).toFixed(dec).replace(/\B(?=(\d{3})+(?!\d))/g, ",") + m[3];
    if (p < 1) requestAnimationFrame(tick);
  })(t0);
}

/* ---------- Compose & send email (real Gmail/Outlook send, autonomy-gated) ---------- */
async function openComposeEmail(prefill = {}) {
  // Decide which providers can send (connected).
  const conns = await safeFetchJson("/api/connectors");
  const byProv = Object.fromEntries((conns?.connectors || []).map(c => [c.provider, c]));
  const gmailOn = !!byProv["google_gmail"]?.connected;
  const outlookOn = !!byProv["microsoft_graph"]?.connected;

  if (!gmailOn && !outlookOn) {
    openModal(`
      <div class="modal-head"><div><span style="font-weight:700;font-size:17px;color:var(--text-strong)">Compose email</span></div>
        <button class="modal-close" data-close><span class="material-symbols-rounded">close</span></button></div>
      <div class="modal-body"><p>Connect Gmail or Microsoft 365 on the
        <a href="#/connectors" data-close>Connectors</a> page before sending email.</p></div>
      <div class="modal-foot"><button class="btn" data-close>Close</button></div>`);
    return;
  }

  const providerOptions = [
    gmailOn ? `<option value="gmail">Gmail${byProv["google_gmail"]?.account_label ? " · " + byProv["google_gmail"].account_label : ""}</option>` : "",
    outlookOn ? `<option value="outlook">Outlook${byProv["microsoft_graph"]?.account_label ? " · " + byProv["microsoft_graph"].account_label : ""}</option>` : "",
  ].join("");

  openModal(`
    <div class="modal-head">
      <div><span style="font-weight:700;font-size:17px;color:var(--text-strong)">Compose email</span></div>
      <button class="modal-close" data-close><span class="material-symbols-rounded">close</span></button>
    </div>
    <div class="modal-body">
      <label style="font-size:12px;color:var(--text-muted);font-weight:600">FROM</label>
      <select id="ceProvider" class="input" style="width:100%;margin:4px 0 12px">${providerOptions}</select>
      <label style="font-size:12px;color:var(--text-muted);font-weight:600">TO</label>
      <input id="ceTo" class="input" style="width:100%;margin:4px 0 12px" placeholder="name@example.com" value="${escapeHtml(prefill.to || "")}" />
      <label style="font-size:12px;color:var(--text-muted);font-weight:600">SUBJECT</label>
      <input id="ceSubject" class="input" style="width:100%;margin:4px 0 12px" placeholder="Subject" value="${escapeHtml(prefill.subject || "")}" />
      <label style="font-size:12px;color:var(--text-muted);font-weight:600">BODY</label>
      <textarea id="ceBody" class="input" style="width:100%;min-height:160px;margin:4px 0 4px;resize:vertical" placeholder="Write your message — or click 'Draft with AI'">${escapeHtml(prefill.body || "")}</textarea>
      <div id="ceStatus" style="font-size:12px;color:var(--text-muted);min-height:16px"></div>
      <div id="ceOptions" style="display:none;margin-top:8px"></div>
    </div>
    <div class="modal-foot">
      <button class="btn btn-ghost" data-close>Cancel</button>
      <button class="btn" id="ceDraft"><span class="material-symbols-rounded">smart_toy</span> Draft with AI</button>
      <button class="btn btn-primary" id="ceSend"><span class="material-symbols-rounded">send</span> Send</button>
    </div>`);

  // Style inputs to match the app (the .input class may not exist; add minimal styling).
  document.querySelectorAll("#ceTo,#ceSubject,#ceBody,#ceProvider").forEach(el => {
    el.style.border = "1px solid var(--border-dim,#E2E8F0)";
    el.style.borderRadius = "8px"; el.style.padding = "9px 11px";
    el.style.fontSize = "14px"; el.style.fontFamily = "inherit"; el.style.background = "#fff";
  });

  const statusEl = document.getElementById("ceStatus");

  // Draft with AI — ask the copilot to write the body from a short brief. When
  // replying to an email (prefill.replyContext), draft a reply to that message.
  // Draft TWO distinct versions (different tone/length) and let the user pick.
  function _draftBrief(styleLine) {
    const to = document.getElementById("ceTo").value.trim();
    const subject = document.getElementById("ceSubject").value.trim();
    const notes = document.getElementById("ceBody").value.trim();
    const rc = prefill.replyContext;
    if (rc) {
      return `Write a professional REPLY to the email below. ${styleLine} ` +
        `${notes ? "Points to include: " + notes + ". " : ""}` +
        `Return ONLY the reply body text — no subject line, no placeholders like [Name], sign it off naturally.\n\n` +
        `--- Original email ---\nFrom: ${rc.from}\nSubject: ${rc.subject}\n\n${(rc.body || "").slice(0, 1500)}`;
    }
    return `Write a professional email body${to ? " to " + to : ""}` +
      `${subject ? " with subject '" + subject + "'" : ""}. ${styleLine} ` +
      `${notes ? "Notes/points to include: " + notes : "Ask is implied by the subject."} ` +
      `Return ONLY the email body text, no subject line, no placeholders.`;
  }

  const _DRAFT_VARIANTS = [
    { key: "A", tag: "Concise & direct", style: "Keep it short, crisp and to the point — a few sentences." },
    { key: "B", tag: "Warm & detailed", style: "Make it warm and a little more detailed, with a friendly tone." },
  ];

  function _renderDraftOptions(drafts) {
    const box = document.getElementById("ceOptions");
    if (!box) return;
    box.style.display = "block";
    box.innerHTML =
      `<div style="font-size:12px;color:var(--text-muted);font-weight:600;margin-bottom:6px">Pick a version (it goes into the body — you can still edit it):</div>` +
      drafts.map((d, i) => `
        <div class="ce-draft-opt" data-i="${i}" style="border:1px solid var(--border);border-radius:10px;padding:10px 12px;margin-bottom:8px;background:#fff">
          <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:6px">
            <span class="pill pill-teal" style="font-size:11px">Option ${d.key} · ${escapeHtml(d.tag)}</span>
            <button class="btn btn-primary ce-pick" data-i="${i}" style="padding:4px 12px;font-size:12.5px">
              <span class="material-symbols-rounded" style="font-size:15px">check</span> Use this
            </button>
          </div>
          <div style="font-size:13px;color:var(--text);white-space:pre-wrap;line-height:1.5;max-height:150px;overflow:auto">${escapeHtml(d.text)}</div>
        </div>`).join("");

    const pick = (i) => {
      document.getElementById("ceBody").value = drafts[i].text;
      box.querySelectorAll(".ce-draft-opt").forEach(el => {
        el.style.borderColor = parseInt(el.dataset.i, 10) === i ? "var(--accent)" : "var(--border)";
        el.style.background = parseInt(el.dataset.i, 10) === i ? "var(--accent-softer)" : "#fff";
      });
      statusEl.textContent = `Using Option ${drafts[i].key} — review and edit before sending.`;
    };
    box.querySelectorAll(".ce-pick").forEach(btn =>
      btn.addEventListener("click", () => pick(parseInt(btn.dataset.i, 10))));
    box.querySelectorAll(".ce-draft-opt").forEach(el =>
      el.addEventListener("click", (e) => { if (!e.target.closest(".ce-pick")) pick(parseInt(el.dataset.i, 10)); }));
  }

  async function runDraft() {
    const optBox = document.getElementById("ceOptions");
    if (optBox) { optBox.style.display = "none"; optBox.innerHTML = ""; }
    statusEl.textContent = "Drafting two versions…";
    const results = await Promise.all(_DRAFT_VARIANTS.map(v =>
      safeFetchJson("/api/copilot/chat", { method: "POST", body: { message: _draftBrief(v.style), history: [] } })
        .then(r => (r && r.reply ? r.reply.trim() : null))
        .catch(() => null)
    ));
    const drafts = _DRAFT_VARIANTS
      .map((v, i) => ({ ...v, text: results[i] }))
      .filter(d => d.text);

    if (!drafts.length) {
      statusEl.textContent = "Couldn't draft right now — write it yourself or try again.";
      return;
    }
    // Prefill the body with the first option so a send works even if they don't pick.
    document.getElementById("ceBody").value = drafts[0].text;
    if (drafts.length === 1) {
      statusEl.textContent = "Draft ready — review and edit before sending.";
      return;
    }
    statusEl.textContent = "Two versions ready — pick the one you prefer below.";
    _renderDraftOptions(drafts);
  }
  document.getElementById("ceDraft").addEventListener("click", runDraft);

  // "Draft reply" from the inbox opens this modal and drafts immediately, in place.
  if (prefill.autoDraft) runDraft();

  // Send — calls the autonomy-gated endpoint; handles the confirm path.
  document.getElementById("ceSend").addEventListener("click", async () => {
    const provider = document.getElementById("ceProvider").value;
    const to = document.getElementById("ceTo").value.trim();
    const subject = document.getElementById("ceSubject").value.trim();
    const bodyText = document.getElementById("ceBody").value;
    if (!to || !subject) { statusEl.textContent = "To and Subject are required."; return; }
    await _doSendEmail(provider, { to, subject, body: bodyText }, false, statusEl);
  });
}

async function _doSendEmail(provider, payload, confirmed, statusEl) {
  statusEl.textContent = confirmed ? "Sending (confirmed)…" : "Sending…";
  let resp;
  try {
    resp = await fetch(`/api/inbox/${provider}/send`, {
      method: "POST", credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...payload, confirm: confirmed }),
    });
  } catch (e) {
    statusEl.textContent = "Network error — try again.";
    return;
  }
  if (resp.ok) {
    closeModal();
    toast(`Email sent to ${payload.to}.`, "success");
    return;
  }
  const err = await resp.json().catch(() => ({}));
  const detail = err.detail;
  // Autonomy gate: 403 with needs_confirmation → ask the user, then retry confirmed.
  if (resp.status === 403 && detail && typeof detail === "object" && detail.blocked_by_autonomy) {
    if (detail.needs_confirmation && !confirmed) {
      if (confirm(`You're at autonomy level L${detail.level}. Send this email to ${payload.to} now?`)) {
        return _doSendEmail(provider, payload, true, statusEl);
      }
      statusEl.textContent = "Cancelled.";
      return;
    }
    statusEl.textContent = `Blocked at L${detail.level} (Observe). Raise the autonomy slider on the Inbox page to send.`;
    return;
  }
  statusEl.textContent = `Send failed: ${typeof detail === "string" ? detail : ("HTTP " + resp.status)}`;
}

/* =========================================================
   Dashboard
   ========================================================= */
async function initDashboard() {
  const data = (await safeFetchJson("/api/dashboard")) || MOCK;

  // Last updated
  const ts = document.getElementById("lastUpdated");
  if (ts) ts.textContent = new Date().toLocaleTimeString();

  // Retention substats
  const r = data.retention || MOCK.retention;
  const subWrap = document.getElementById("retentionSubstats");
  if (subWrap) {
    const cells = [
      [r.active, "Active tasks"],
      [r.wonWeek, "Resolved (Week)"],
      [r.lostWeek, "Slipped"],
      [r.saveRate, "On-time"],
      [r.avgDiscount, "Avg response"],
      [r.avgLevels, "Avg actions"],
      [r.competitors, "Pending review"],
      [r.escalations, "Waiting on you"],
    ];
    subWrap.innerHTML = cells.map(([v, l]) => `
      <div class="substat">
        <div class="substat-val">${v}</div>
        <div class="substat-label">${l}</div>
      </div>
    `).join("");
    subWrap.querySelectorAll(".substat-val").forEach(el => countUp(el));
  }

  // Active tasks (backend key is "negotiations" for legacy reasons)
  const list = document.getElementById("negList");
  const allNegs = data.negotiations || MOCK.negotiations;

  // Status filter chips above the list — clicking one filters the task list
  // (replaces the old passive KPI tiles).
  let taskFilter = "all";
  const _statusOrder = ["WAITING ON YOU", "RUNNING", "QUEUED", "PAUSED"];
  const _titleCase = (s) => String(s || "").toLowerCase().replace(/\b\w/g, c => c.toUpperCase());
  function renderTaskFilters() {
    const bar = document.getElementById("taskFilters");
    if (!bar) return;
    const real = allNegs.filter(n => n.id !== "EMPTY-1");
    const counts = {};
    real.forEach(n => { counts[n.status] = (counts[n.status] || 0) + 1; });
    const chips = [["all", "All", real.length]];
    _statusOrder.forEach(s => { if (counts[s]) chips.push([s, _titleCase(s), counts[s]]); });
    bar.innerHTML = chips.map(([k, lbl, n]) =>
      `<button class="task-filter${k === taskFilter ? " active" : ""}" data-f="${escapeHtml(k)}">${escapeHtml(lbl)}<span class="tf-count">${n}</span></button>`).join("");
    bar.querySelectorAll(".task-filter").forEach(b => b.onclick = () => {
      taskFilter = b.dataset.f; renderTaskFilters();
      paintTasks(taskFilter === "all" ? allNegs : allNegs.filter(n => n.status === taskFilter));
    });
  }

  function paintTasks(negs) {
  if (list && !negs.length) {
    list.innerHTML = `
      <div style="padding:30px 18px;text-align:center;color:var(--text-muted)">
        <span class="material-symbols-rounded" style="font-size:40px;color:var(--text-muted)">task_alt</span>
        <h3 style="margin:10px 0 4px;color:var(--text-strong)">${taskFilter === "all" ? "No active tasks" : "No tasks match this filter"}</h3>
        <p style="font-size:13px">When MyAi is working on something for you — or something needs your approval — it shows up here.</p>
      </div>`;
  } else if (list) {
    list.innerHTML = negs.map(n => {
      const ribbon = _renderCompactLifecycle(n.lifecycle);
      const isEmpty = n.id === "EMPTY-1";
      const isTask = String(n.id || "").startsWith("TASK-");
      const tid = isTask ? n.task_id : null;
      const isAlert = n.source === "research_alert";
      const statusPill = (s) => (
        s === "RUNNING" ? "pill-blue" :
        s === "WAITING ON YOU" ? "pill-red" :
        s === "QUEUED" ? "pill-orange" :
        s === "PAUSED" ? "pill-orange" :
        s === "IDLE" ? "pill-teal" :
        s === "CLEAR" ? "pill-green" : "pill-blue"
      );

      // Research watch alerts are notifications, not runnable tasks — render them
      // distinctly with just a Dismiss action.
      if (isAlert) {
        return `
        <div class="neg-card neg-alert" data-id="${n.id}" data-title="${escapeHtml(n.name || "")}" data-detail="${escapeHtml(n.product || "")}" data-sid="${escapeHtml(n.session_id || "")}" style="border-left:3px solid var(--accent);cursor:pointer">
          <div class="neg-head">
            <span class="neg-name"><span class="material-symbols-rounded" style="font-size:17px;vertical-align:-3px;color:var(--accent)">notifications_active</span> ${escapeHtml(n.name)}</span>
            <span class="pill pill-red">NEW</span>
          </div>
          <div class="neg-sub neg-clamp" style="white-space:pre-wrap">${escapeHtml(n.product || "")}</div>
          <div style="font-size:11px;color:var(--accent);margin-top:6px"><span class="material-symbols-rounded" style="font-size:13px;vertical-align:-2px">unfold_more</span> Click to read more</div>
          <div class="neg-actions">
            <button class="btn btn-danger-outline" data-act="cancel" data-tid="${tid}">
              <span class="material-symbols-rounded">done</span> Dismiss
            </button>
          </div>
        </div>`;
      }

      return `
      <div class="neg-card" data-id="${n.id}">
        <div class="neg-head">
          <span class="neg-name">${escapeHtml(n.name)}</span>
          <span class="pill pill-purple">${escapeHtml(n.competitor || "")}</span>
          <span class="pill ${statusPill(n.status)}">${escapeHtml(n.status)}</span>
        </div>
        <div class="neg-sub">${escapeHtml(n.product || "")}</div>
        ${ribbon}
        <div class="ai-think">
          <b>Assistant says</b>${escapeHtml(n.thinking || "")}
        </div>
        ${isEmpty ? "" : `
        <div class="neg-actions">
          ${isTask && n.status === "WAITING ON YOU" ? `
            <button class="btn btn-success" data-act="approve" data-tid="${tid}">
              <span class="material-symbols-rounded">check</span> Approve
            </button>
            <button class="btn btn-danger-outline" data-act="cancel" data-tid="${tid}">
              <span class="material-symbols-rounded">cancel</span> Cancel
            </button>
          ` : isTask && n.status === "RUNNING" ? `
            <button class="btn" data-act="pause" data-tid="${tid}">
              <span class="material-symbols-rounded">pause</span> Pause
            </button>
            <button class="btn btn-danger-outline" data-act="cancel" data-tid="${tid}">
              <span class="material-symbols-rounded">cancel</span> Cancel
            </button>
          ` : isTask && n.status === "PAUSED" ? `
            <button class="btn btn-primary" data-act="resume" data-tid="${tid}">
              <span class="material-symbols-rounded">play_circle</span> Resume
            </button>
            <button class="btn btn-danger-outline" data-act="cancel" data-tid="${tid}">
              <span class="material-symbols-rounded">cancel</span> Cancel
            </button>
          ` : isTask && n.status === "QUEUED" ? `
            <button class="btn btn-primary" data-act="run" data-tid="${tid}">
              <span class="material-symbols-rounded">play_arrow</span> Start
            </button>
            <button class="btn btn-danger-outline" data-act="cancel" data-tid="${tid}">
              <span class="material-symbols-rounded">cancel</span> Cancel
            </button>
          ` : ""}
          ${isTask ? `
            <button class="btn" data-act="open" data-tid="${tid}" data-source="${escapeHtml(n.source || "")}" data-sid="${escapeHtml(n.session_id || "")}">
              <span class="material-symbols-rounded">open_in_new</span> Open
            </button>
          ` : ""}
        </div>`}
      </div>
    `;}).join("");

    list.querySelectorAll(".neg-card").forEach(card => {
      card.querySelectorAll("button[data-act]").forEach(btn => {
        btn.addEventListener("click", async (e) => {
          e.stopPropagation();
          const act = btn.dataset.act;
          const tid = btn.dataset.tid;
          if (act === "open") {
            // Research tasks open their report in the Copilot, not the inbox.
            if (btn.dataset.source === "research" && btn.dataset.sid) {
              sessionStorage.setItem("openResearchSession", btn.dataset.sid);
              location.hash = "#/copilot";
              return;
            }
            location.hash = "#/inbox";
            sessionStorage.setItem("inboxOpenTaskId", String(tid));
            return;
          }
          btn.disabled = true;
          btn.style.opacity = "0.5";
          const endpoint = (act === "approve" || act === "cancel" || act === "run" ||
                            act === "pause" || act === "resume" || act === "retry")
            ? `/api/inbox/tasks/${tid}/${act === "cancel" ? "" : act}`
            : null;
          if (act === "cancel") {
            await safeFetchJson(`/api/inbox/tasks/${tid}`, { method: "DELETE" });
          } else if (endpoint) {
            await safeFetchJson(endpoint, { method: "POST" });
          }
          // Refresh the dashboard
          initDashboard();
        });
      });

      // Research-alert cards: click anywhere on the body to reveal the full
      // finding in a modal (the Dismiss button stops propagation).
      if (card.classList.contains("neg-alert")) {
        card.addEventListener("click", () => {
          const title = card.dataset.title || "Research finding";
          const detail = card.dataset.detail || "";
          const sid = card.dataset.sid || "";
          openModal(`
            <div class="modal-head"><h3 style="display:flex;align-items:center;gap:8px"><span class="material-symbols-rounded" style="color:var(--accent)">notifications_active</span>${escapeHtml(title)}</h3><button class="icon-btn" data-close><span class="material-symbols-rounded">close</span></button></div>
            <div class="modal-body" style="white-space:pre-wrap;font-size:14px;line-height:1.6">${renderMarkdownSafe(detail)}</div>
            <div class="modal-foot">${sid ? '<button class="btn btn-primary" id="alertOpenReport"><span class="material-symbols-rounded">open_in_new</span>Open full report</button>' : ""}<button class="btn" data-close>Close</button></div>
          `);
          const orb = document.getElementById("alertOpenReport");
          if (orb) orb.onclick = () => { sessionStorage.setItem("openResearchSession", sid); location.hash = "#/copilot"; closeModal(); };
        });
      }

      // Make the whole card clickable to open it (the buttons stopPropagation,
      // so this only fires on the card body). Fixes "nothing happens when I
      // click the card".
      const openBtn = card.querySelector('button[data-act="open"]');
      if (openBtn) {
        card.style.cursor = "pointer";
        card.addEventListener("click", () => {
          if (openBtn.dataset.source === "research" && openBtn.dataset.sid) {
            sessionStorage.setItem("openResearchSession", openBtn.dataset.sid);
            location.hash = "#/copilot";
          } else {
            sessionStorage.setItem("inboxOpenTaskId", String(openBtn.dataset.tid));
            location.hash = "#/inbox";
          }
        });
      }
    });
  }
  }  // end paintTasks

  renderTaskFilters();
  paintTasks(taskFilter === "all" ? allNegs : allNegs.filter(n => n.status === taskFilter));

  // Refresh button
  document.getElementById("refreshBtn")?.addEventListener("click", () => initDashboard());
}

function openNegotiationModal(n) {
  const acctRows = Object.entries(n.account).map(([k, v]) => `<div class="kv"><span>${k}</span><b>${v}</b></div>`).join("");
  const threatRows = Object.entries(n.competitorThreat).map(([k, v]) => `<div class="kv"><span>${k}</span><b>${v}</b></div>`).join("");
  const conv = n.conversation.map(m => `<div class="msg ${m.from}">${m.text}</div>`).join("");

  openModal(`
    <div class="modal-head">
      <div>
        <div style="display:flex;align-items:center;gap:10px;">
          <span style="font-weight:700;font-size:17px;color:var(--text-strong)">${n.name}</span>
          <span class="pill pill-purple">Level ${n.level}</span>
          <span class="pill">vs ${n.competitor}</span>
          <span class="pill ${n.status === "NEEDS APPROVAL" ? "pill-orange" : n.status === "AT RISK" ? "pill-red" : "pill-blue"}">${n.status}</span>
        </div>
        <div class="card-sub" style="margin-top:4px">${n.product} · ${n.fee} · ${n.tenure} tenure</div>
      </div>
      <button class="modal-close" data-close>
        <span class="material-symbols-rounded">close</span>
      </button>
    </div>
    <div class="modal-body">
      <div class="duo-grid" style="margin-bottom:16px">
        <div class="muted-card">
          <h4>Account</h4>
          ${acctRows}
        </div>
        <div class="muted-card">
          <h4>Competitor Threat</h4>
          ${threatRows}
        </div>
      </div>

      <div class="muted-card" style="margin-bottom:14px">
        <h4>AI Strategy</h4>
        <div class="progress"><span style="width:${n.progress}%"></span></div>
        <div class="progress-row">
          <span>Negotiation Level <b>${n.level}</b> of 5</span>
          <span><b>${n.confidence}%</b> Confidence</span>
        </div>
      </div>

      <div class="ai-think">
        <b>AI Reasoning</b>${n.thinking}
      </div>

      <div style="margin:14px 0 6px;font-weight:700;color:var(--text-strong);font-size:13px;text-transform:uppercase;letter-spacing:.06em">Incentives Offered</div>
      <div class="chips">${n.incentives.map(i => `<span class="chip">${i}</span>`).join("")}</div>

      <div style="margin:18px 0 6px;font-weight:700;color:var(--text-strong);font-size:13px;text-transform:uppercase;letter-spacing:.06em">Conversation</div>
      <div class="conv">${conv}</div>

      <div class="callout-yellow">
        <b>Recommended Next Action</b>${n.nextAction}
      </div>
    </div>
    <div class="modal-foot">
      <button class="btn btn-ghost" data-close>Close</button>
      <button class="btn btn-primary">
        <span class="material-symbols-rounded">support_agent</span>
        Take over
      </button>
      <button class="btn btn-success">
        <span class="material-symbols-rounded">check</span>
        Approve ${n.incentives[0]}
      </button>
    </div>
  `);
}

/* =========================================================
   Inbox
   ========================================================= */
const LEVELS = [
  { code: "L1", title: "Observe",       color: "red",    desc: "AI watches and surfaces insights — zero actions taken." },
  { code: "L2", title: "Draft Assist",  color: "orange", desc: "AI drafts replies for humans to review and send." },
  { code: "L3", title: "Augmented",     color: "yellow", desc: "AI suggests actions and prefills, humans approve each step." },
  { code: "L4", title: "Guarded Auto",  color: "green",  desc: "AI auto-resolves low-risk tasks; high-risk goes to humans." },
  { code: "L5", title: "Autonomous",    color: "green",  desc: "AI handles end-to-end with audit trail; human-on-the-loop." },
];

// Standalone autonomy control — used on the Dashboard (autonomy card markup with
// ids autonomySlider/autonomyLevels/autonomyToggle/levelBadge/autonomyOneLine).
async function initAutonomy() {
  const slider = document.getElementById("autonomySlider");
  if (!slider) return;
  let activeLevel = 0;
  const prefs = await safeFetchJson("/api/preferences");
  if (prefs && Number.isInteger(prefs.autonomy_level)) {
    activeLevel = Math.max(0, Math.min(4, prefs.autonomy_level - 1));
  }
  slider.innerHTML = LEVELS.map((_, i) => {
    const left = (i / (LEVELS.length - 1)) * 100;
    return `<div class="slider-node ${i === activeLevel ? "active" : ""}" data-level="${i}" style="left:${left}%"></div>`;
  }).join("");
  slider.querySelectorAll(".slider-node").forEach(node => node.addEventListener("click", () => setLevel(parseInt(node.dataset.level))));
  const levelsWrap = document.getElementById("autonomyLevels");
  function renderLevels() {
    if (!levelsWrap) return;
    levelsWrap.innerHTML = LEVELS.map((lv, i) => `
      <div class="level ${i === activeLevel ? "active" : ""}" data-level="${i}">
        <div class="level-title"><span class="dot" style="color:var(--${lv.color})"></span>${lv.code} — ${lv.title}</div>
        <div class="level-desc">${lv.desc}</div>
      </div>`).join("");
    levelsWrap.querySelectorAll(".level").forEach(el => el.addEventListener("click", () => setLevel(parseInt(el.dataset.level))));
  }
  renderLevels();
  document.getElementById("autonomyToggle")?.addEventListener("click", () => {
    const el = document.getElementById("autonomyLevels");
    if (el) el.style.display = (el.style.display === "none" || !el.style.display) ? "grid" : "none";
  });
  function setLevel(i, persist = true) {
    activeLevel = i;
    slider.querySelectorAll(".slider-node").forEach((n, idx) => n.classList.toggle("active", idx === i));
    renderLevels();
    const lv = LEVELS[i];
    const b = document.getElementById("levelBadge");
    if (b) { b.className = `pill pill-${lv.color}`; b.textContent = `${lv.code} — ${lv.title}`; }
    const oneLine = document.getElementById("autonomyOneLine");
    if (oneLine) oneLine.innerHTML = `<b>${lv.code} — ${lv.title}.</b> ${lv.desc}`;
    if (persist) safeFetchJson("/api/preferences", { method: "PUT", body: { autonomy_level: i + 1 } });
  }
  setLevel(activeLevel, false);
}

async function initInbox() {
  const data = (await safeFetchJson("/api/inbox")) || {};
  // Backend returns {tasks: [...]} which includes real Gmail + Calendar items
  // when those connectors are connected.
  const backendTasks = (data.tasks || []).map(t => ({
    id: String(t.id),
    title: t.title,
    priority: (t.priority || "medium").charAt(0).toUpperCase() + (t.priority || "medium").slice(1),
    customer: t.from_name || (t.summary || "").split(" — ")[0] || "—",
    source: t.source || "manual",
    ago: t.created_at ? _relTime(t.created_at) : "—",
    _real: true,
    _backend: t,
  }));
  // Inbox is email-only — calendar items and background tasks live on the
  // Dashboard, not here.
  let tasks = backendTasks.filter(t => t.source === "email");
  let activePrio = "All";
  let activeSource = "all";
  let activeAccount = "all"; // all | gmail | outlook
  let activeLevel = 0; // L1
  let selectedTaskId = null;

  // Account switcher (Gmail / Outlook) — always shows both even when one is
  // disconnected so the user knows the option exists.
  const conns = await safeFetchJson("/api/connectors");
  const connByProv = Object.fromEntries((conns?.connectors || []).map(c => [c.provider, c]));
  const opts = [
    { key: "all", label: "All", connected: true, account: "Combined" },
    {
      key: "gmail", label: "Gmail",
      connected: !!connByProv["google_gmail"]?.connected,
      account: connByProv["google_gmail"]?.account_label || "Not connected",
    },
    {
      key: "outlook", label: "Outlook",
      connected: !!connByProv["microsoft_graph"]?.connected,
      account: connByProv["microsoft_graph"]?.account_label || "Not connected",
    },
  ];
  const switcher = document.getElementById("accountSwitcher");
  if (switcher) {
    switcher.innerHTML = opts.map(o => `
      <button class="pill-filter ${o.key === activeAccount ? "active" : ""} ${o.connected ? "" : "is-disabled"}"
              data-acct="${o.key}" title="${escapeHtml(o.account)}"
              style="${o.connected ? "" : "opacity:.55"}">
        ${o.label}${o.connected || o.key === "all" ? "" : " <span style='font-size:11px;opacity:.75'>(connect)</span>"}
      </button>
    `).join("");
    switcher.querySelectorAll("[data-acct]").forEach(b => {
      b.addEventListener("click", () => {
        const acct = b.dataset.acct;
        const opt = opts.find(o => o.key === acct);
        if (acct !== "all" && !opt?.connected) {
          location.hash = "#/connectors";
          return;
        }
        switcher.querySelectorAll("[data-acct]").forEach(x => x.classList.remove("active"));
        b.classList.add("active");
        activeAccount = acct;
        renderTasks();
      });
    });
  }

  // Load persisted autonomy level
  const prefs = await safeFetchJson("/api/preferences");
  if (prefs && Number.isInteger(prefs.autonomy_level)) {
    activeLevel = Math.max(0, Math.min(4, prefs.autonomy_level - 1));
  }

  // Slider nodes
  const slider = document.getElementById("autonomySlider");
  if (slider) {
    slider.innerHTML = LEVELS.map((_, i) => {
      const left = (i / (LEVELS.length - 1)) * 100;
      return `<div class="slider-node ${i === activeLevel ? "active" : ""}" data-level="${i}" style="left:${left}%"></div>`;
    }).join("");
    slider.querySelectorAll(".slider-node").forEach(node => {
      node.addEventListener("click", () => setLevel(parseInt(node.dataset.level)));
    });
  }

  // Level cards
  const levelsWrap = document.getElementById("autonomyLevels");
  function renderLevels() {
    levelsWrap.innerHTML = LEVELS.map((lv, i) => `
      <div class="level ${i === activeLevel ? "active" : ""}" data-level="${i}">
        <div class="level-title">
          <span class="dot" style="color:var(--${lv.color})"></span>
          ${lv.code} — ${lv.title}
        </div>
        <div class="level-desc">${lv.desc}</div>
      </div>
    `).join("");
    levelsWrap.querySelectorAll(".level").forEach(el => {
      el.addEventListener("click", () => setLevel(parseInt(el.dataset.level)));
    });
  }
  renderLevels();

  // Collapsible level details (declutter — slider + one-line by default)
  document.getElementById("autonomyToggle")?.addEventListener("click", () => {
    const el = document.getElementById("autonomyLevels");
    if (el) el.style.display = (el.style.display === "none" || !el.style.display) ? "grid" : "none";
  });

  function setLevel(i, persist = true) {
    activeLevel = i;
    slider.querySelectorAll(".slider-node").forEach((n, idx) => n.classList.toggle("active", idx === i));
    renderLevels();
    const lv = LEVELS[i];
    const b = document.getElementById("levelBadge");
    if (b) {
      b.className = `pill pill-${lv.color === "yellow" ? "yellow" : lv.color}`;
      b.textContent = `${lv.code} — ${lv.title}`;
    }
    const oneLine = document.getElementById("autonomyOneLine");
    if (oneLine) oneLine.innerHTML = `<b>${lv.code} — ${lv.title}.</b> ${lv.desc}`;
    const banner = document.getElementById("modeBanner");
    if (banner) banner.innerHTML = `<span class="material-symbols-rounded">info</span> Active mode: ${lv.code} — ${lv.title}. ${lv.desc}`;

    if (persist) {
      // Save autonomy to backend so it survives reloads + drives gating.
      safeFetchJson("/api/preferences", {
        method: "PUT",
        body: { autonomy_level: i + 1 },
      });
    }
  }
  setLevel(activeLevel, false);

  // Priority filter
  document.querySelectorAll(".pill-filter[data-prio]").forEach(p => {
    p.addEventListener("click", () => {
      document.querySelectorAll(".pill-filter[data-prio]").forEach(x => x.classList.remove("active"));
      p.classList.add("active");
      activePrio = p.dataset.prio;
      renderTasks();
    });
  });

  // Source filter
  document.querySelectorAll(".pill-filter[data-src]").forEach(p => {
    p.addEventListener("click", () => {
      document.querySelectorAll(".pill-filter[data-src]").forEach(x => x.classList.remove("active"));
      p.classList.add("active");
      activeSource = p.dataset.src;
      renderTasks();
    });
  });

  // Compose with MyAi → real compose-and-send modal (with optional AI drafting)
  document.getElementById("composeBtn")?.addEventListener("click", () => openComposeEmail());

  // Render task list
  const list = document.getElementById("taskList");
  const detail = document.getElementById("taskDetail");
  const counter = document.getElementById("taskCount");

  function renderTasks() {
    let filtered = tasks;
    if (activePrio !== "All") filtered = filtered.filter(t => t.priority === activePrio);
    if (activeSource !== "all") filtered = filtered.filter(t => {
      if (activeSource === "manual") return !["email", "calendar"].includes(t.source);
      return t.source === activeSource;
    });
    if (activeAccount !== "all") {
      filtered = filtered.filter(t => (t._backend?.account || "gmail") === activeAccount || t.source !== "email");
    }
    counter.textContent = `Showing ${filtered.length} of ${tasks.length}`;
    if (!filtered.length) {
      const nothingConnected = data.sources && data.sources.gmail === 0 && data.sources.db === 0;
      list.innerHTML = `
        <div class="empty-detail" style="padding:30px 18px;text-align:center">
          <span class="material-symbols-rounded" style="font-size:42px;color:var(--text-muted)">inbox</span>
          <h3 style="margin:10px 0 4px">${nothingConnected ? "Connect your inbox" : "All caught up"}</h3>
          <p style="font-size:13px;color:var(--text-muted)">
            ${nothingConnected
              ? `Connect Gmail on the <a href="#/connectors">Connectors</a> page to see real emails here.`
              : `Nothing waiting on you right now. Anything new will appear automatically.`}
          </p>
        </div>`;
      return;
    }
    list.innerHTML = filtered.map(t => {
      const ico = sourceIcon(t.source);
      const sourceLabel = ({email:"Email", calendar:"Calendar", manual:"Task", agent:"Agent"})[t.source] || t.source;
      return `
      <div class="task ${t.id === selectedTaskId ? "active" : ""}" data-id="${t.id}">
        <div class="task-row">
          <div class="task-title">${escapeHtml(t.title || "(no subject)")}</div>
          <span class="pill ${t.priority === "Critical" ? "pill-red" : t.priority === "High" ? "pill-orange" : t.priority === "Medium" ? "pill-blue" : "pill-teal"}">${t.priority}</span>
        </div>
        <div class="task-meta">
          <span class="material-symbols-rounded">${ico}</span>${escapeHtml(t.customer)}
          <span style="opacity:.5">·</span>
          ${sourceLabel}
          <span style="opacity:.5">·</span>
          ${t.ago}
        </div>
      </div>
    `;}).join("");
    list.querySelectorAll(".task").forEach(el => {
      el.addEventListener("click", () => selectTask(el.dataset.id));
    });
  }

  async function selectTask(id) {
    selectedTaskId = id;
    renderTasks();
    const t = tasks.find(x => x.id === id) || {};
    const backend = t._backend || {};

    // Show loading state
    detail.innerHTML = `<div class="card-body" style="padding:40px;text-align:center;color:var(--text-muted)">Loading…</div>`;

    // Branch by source: real email / calendar / DB task
    if (id.startsWith("gmail:")) {
      return renderMailDetail(id, t, backend, "gmail");
    }
    if (id.startsWith("outlook:")) {
      return renderMailDetail(id, t, backend, "outlook");
    }
    if (id.startsWith("cal:")) {
      return renderCalendarDetail(id, t, backend);
    }
    // DB-backed task
    const numId = parseInt(String(id).replace(/\D/g, ""), 10) || 1;
    const data = await safeFetchJson(`/api/inbox/tasks/${numId}`) || {};
    const d = { ...t, ...backend, ...data };
    renderDbTaskDetail(d, numId, t, id);
  }

  async function renderMailDetail(id, t, backend, kind) {
    // kind = "gmail" | "outlook"
    const prefix = kind + ":";
    const msgId = backend.external_id || id.replace(new RegExp("^" + prefix), "");
    const apiPath = kind === "gmail" ? "gmail" : "outlook";
    const externalLinkBase = kind === "gmail"
      ? `https://mail.google.com/mail/u/0/#inbox/`
      : `https://outlook.office.com/mail/inbox/id/`;
    const accountLabel = kind === "gmail" ? "Gmail" : "Outlook";

    const full = await safeFetchJson(`/api/inbox/${apiPath}/${encodeURIComponent(msgId)}`);
    const subj = (full && full.subject) || backend.title || "(no subject)";
    const from = (full && full.from) || backend.from_name || "";
    const date = (full && full.date) || "";
    const body = (full && (full.body || full.snippet)) || backend.summary || "";

    detail.innerHTML = `
      <div class="card-head">
        <div style="min-width:0;flex:1">
          <h3 class="card-title" style="word-break:break-word">${escapeHtml(subj)}</h3>
          <div class="card-sub">From ${escapeHtml(from)}${date ? " · " + escapeHtml(date) : ""}</div>
        </div>
        <span class="pill pill-blue">${accountLabel}</span>
      </div>
      <div class="card-body">
        <div id="mailSuggest" style="background:#F0F9FF;border:1px solid #BAE6FD;border-radius:8px;padding:10px 12px;margin-bottom:12px">
          <div style="display:flex;align-items:center;gap:6px;font-weight:700;color:var(--accent);font-size:12.5px"><span class="material-symbols-rounded" style="font-size:16px">smart_toy</span>MyAi suggestion</div>
          <div id="mailSuggestBody" style="margin-top:4px;color:var(--text-muted);font-size:13px;line-height:1.5"><span class="material-symbols-rounded" style="font-size:14px;vertical-align:-2px;animation:spin 1s linear infinite">progress_activity</span> Reading this email…</div>
        </div>
        <div style="white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;line-height:1.55;color:var(--text-strong);font-size:14px;max-height:380px;overflow:auto;padding:10px;background:#FAFAFA;border:1px solid var(--border-dim);border-radius:8px">${escapeHtml(body)}</div>

        <div style="display:flex;gap:8px;margin-top:18px;flex-wrap:wrap">
          <button class="btn btn-primary" data-em-action="reply">
            <span class="material-symbols-rounded">smart_toy</span> Draft reply
          </button>
          <button class="btn" data-em-action="replysend">
            <span class="material-symbols-rounded">send</span> Reply &amp; send
          </button>
          <button class="btn" data-em-action="summarize">
            <span class="material-symbols-rounded">summarize</span> Summarize
          </button>
          <button class="btn" data-em-action="markread">
            <span class="material-symbols-rounded">mark_email_read</span> Mark read
          </button>
          <button class="btn" data-em-action="archive">
            <span class="material-symbols-rounded">archive</span> Archive
          </button>
          <button class="btn btn-danger-outline" data-em-action="delete">
            <span class="material-symbols-rounded">delete</span> Delete
          </button>
          <a class="btn" href="${externalLinkBase}${encodeURIComponent(msgId)}" target="_blank" rel="noreferrer">
            <span class="material-symbols-rounded">open_in_new</span> Open in ${accountLabel}
          </a>
        </div>
      </div>`;

    // MyAi suggestion: instant if the background harvester already computed it;
    // otherwise fetch on demand (and the backend caches it for next time).
    const applySuggestion = (sg, action) => {
      const el = document.getElementById("mailSuggestBody");
      if (el) el.textContent = sg || "No suggestion available.";
      const map = { reply: "replysend", archive: "archive", ignore: "markread" };
      const target = action && map[action];
      if (target) {
        const b = detail.querySelector(`[data-em-action="${target}"]`);
        if (b) { b.classList.add("btn-primary"); b.style.outline = "2px solid var(--accent)"; }
      }
    };
    if (backend && backend.suggestion) {
      applySuggestion(backend.suggestion, backend.suggestion_action);
    } else {
      safeFetchJson("/api/copilot/suggest", {
        method: "POST",
        body: { subject: subj, sender: from, body: (body || "").slice(0, 3000),
                message_id: msgId, account: apiPath },
      }).then(res => applySuggestion(res && res.suggestion, res && res.action))
        .catch(() => {
          const el = document.getElementById("mailSuggestBody");
          if (el) el.textContent = "Couldn't analyze this email right now.";
        });
    }

    detail.querySelectorAll("[data-em-action]").forEach(btn => {
      btn.addEventListener("click", async () => {
        const a = btn.dataset.emAction;
        if (a === "reply") {
          // Draft the reply in place — open the compose modal pre-filled and
          // auto-generate the AI reply there. No redirect to chat.
          openComposeEmail({
            to: _extractEmail(from),
            subject: /^re:/i.test(subj) ? subj : `Re: ${subj}`,
            replyContext: { from, subject: subj, body },
            autoDraft: true,
          });
          return;
        }
        if (a === "summarize") {
          // Summarize inline in the suggestion box — no redirect to chat.
          const el = document.getElementById("mailSuggestBody");
          if (el) el.innerHTML = `<span class="material-symbols-rounded" style="font-size:14px;vertical-align:-2px;animation:spin 1s linear infinite">progress_activity</span> Summarizing…`;
          const brief = `Summarize this ${accountLabel} email in 3 short bullets and say whether it needs a reply:\n\nFrom: ${from}\nSubject: ${subj}\n\n${(body || "").slice(0, 1500)}`;
          const resp = await safeFetchJson("/api/copilot/chat", { method: "POST", body: { message: brief, history: [] } });
          if (el) el.textContent = (resp && resp.reply) ? resp.reply.trim() : "Couldn't summarize right now.";
          return;
        }
        if (a === "replysend") {
          openComposeEmail({
            to: _extractEmail(from),
            subject: /^re:/i.test(subj) ? subj : `Re: ${subj}`,
            body: `\n\n---\nOn ${date || "a previous date"}, ${from} wrote:\n> ${(body || "").slice(0, 800).replace(/\n/g, "\n> ")}`,
          });
          return;
        }
        if (a === "delete") {
          if (!confirm("Delete this email? It moves to Trash / Deleted Items (recoverable).")) return;
          await _doMailMutation(apiPath, msgId, "delete", id, accountLabel, btn, detail, () => {
            tasks = tasks.filter(x => x.id !== id); selectedTaskId = null; renderTasks();
          });
          return;
        }
        // mark-read / archive
        await _doMailMutation(apiPath, msgId, a === "markread" ? "mark-read" : "archive", id, accountLabel, btn, detail, () => {
          tasks = tasks.filter(x => x.id !== id); selectedTaskId = null; renderTasks();
        });
      });
    });
  }

  function renderCalendarDetail(id, t, backend) {
    const title = backend.title || t.title;
    const summary = backend.summary || t.customer;
    detail.innerHTML = `
      <div class="card-head">
        <div><h3 class="card-title">${escapeHtml(title)}</h3>
        <div class="card-sub">${escapeHtml(summary)}</div></div>
        <span class="pill pill-blue">Calendar</span>
      </div>
      <div class="card-body">
        <div style="display:flex;gap:8px;margin-top:8px;flex-wrap:wrap">
          <button class="btn btn-primary" data-cal-action="prep">
            <span class="material-symbols-rounded">smart_toy</span> Prep with MyAi
          </button>
          <button class="btn" data-cal-action="reschedule">
            <span class="material-symbols-rounded">schedule</span> Ask MyAi to reschedule
          </button>
          <a class="btn" href="https://calendar.google.com/calendar/u/0/r" target="_blank" rel="noreferrer">
            <span class="material-symbols-rounded">open_in_new</span> Open in Calendar
          </a>
        </div>
      </div>`;
    detail.querySelectorAll("[data-cal-action]").forEach(btn => {
      btn.addEventListener("click", () => {
        const a = btn.dataset.calAction;
        const prompt = a === "prep"
          ? `Help me prep for: ${title}. ${summary}. What should I review and what should I aim to get out of it?`
          : `I need to reschedule "${title}" (${summary}). Suggest 3 alternative slots that fit my week.`;
        sessionStorage.setItem("copilotPrefill", prompt);
        location.hash = "#/copilot";
      });
    });
  }

  function _renderLifecycleRibbon(lc) {
    if (!lc) return "";
    const stages = lc.stages || ["open","in_progress","blocked","done"];
    const idx = lc.stage_index ?? 0;
    const breached = !!lc.breached;
    const remaining = lc.remaining?.human || "";
    const dueStr = lc.due_at ? new Date(lc.due_at).toLocaleString() : "—";
    const stageLabels = { open: "Open", in_progress: "Running", blocked: "Waiting on you", done: "Done" };

    const dots = stages.map((s, i) => {
      const reached = i <= idx;
      const color = reached
        ? (s === "done" ? "var(--success)" : s === "blocked" ? "var(--warn,#F59E0B)" : "var(--accent)")
        : "var(--text-muted)";
      const label = stageLabels[s] || s;
      const conn = i < stages.length - 1
        ? `<div style="flex:1;height:2px;background:${i < idx ? color : 'var(--border-dim)'}"></div>`
        : "";
      return `
        <div style="display:flex;align-items:center;gap:0;flex:1">
          <div style="display:flex;flex-direction:column;align-items:center;min-width:80px">
            <div style="width:14px;height:14px;border-radius:50%;background:${color};border:2px solid var(--bg-soft);box-shadow:0 0 0 2px ${color}33"></div>
            <div style="font-size:11px;color:${reached ? 'var(--text-strong)' : 'var(--text-muted)'};margin-top:6px;font-weight:${reached ? '600' : '500'}">${label}</div>
          </div>
          ${conn}
        </div>`;
    }).join("");

    const dueColor = breached ? "var(--red)" : "var(--text-strong)";
    const dueLabel = breached
      ? `<span style="color:${dueColor};font-weight:700">Overdue</span>`
      : (remaining ? `Due in <b style="color:${dueColor}">${escapeHtml(remaining)}</b>` : `Due <b>${escapeHtml(dueStr)}</b>`);

    const escalations = lc.escalation_count > 0
      ? `<span class="pill pill-red" style="font-size:11px">${lc.escalation_count} escalation${lc.escalation_count > 1 ? "s" : ""}</span>`
      : "";

    const assignee = lc.assignee_id && lc.assignee_id !== "me"
      ? lc.assignee_id : "You";

    return `
      <div class="muted-card" style="margin-bottom:14px;background:#FAFBFD">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:10px">
          <div style="display:flex;align-items:center;gap:8px;font-size:12px;color:var(--text-muted)">
            <span class="material-symbols-rounded" style="font-size:16px;color:var(--accent)">flag</span>
            Lifecycle · Assignee: <b style="color:var(--text-strong)">${escapeHtml(assignee)}</b>
            ${escalations}
          </div>
          <div style="font-size:12.5px;color:var(--text-strong)">${dueLabel}</div>
        </div>
        <div style="display:flex;align-items:center">${dots}</div>
      </div>`;
  }

  function renderDbTaskDetail(d, numId, t, id) {

    const stepIcon = (s) => ({
      done: "check_circle", running: "sync", failed: "error",
      pending: "radio_button_unchecked", skipped: "remove_circle"
    })[s] || "radio_button_unchecked";
    const stepColor = (s) => ({
      done: "var(--success)", running: "var(--accent)", failed: "var(--error)",
      pending: "var(--text-muted)", skipped: "var(--text-muted)"
    })[s] || "var(--text-muted)";

    const progress = d.progress || {done: 0, total: 1, label: ""};
    const pct = progress.total ? Math.round(progress.done / progress.total * 100) : 0;

    detail.innerHTML = `
      <div class="card-head">
        <div>
          <h3 class="card-title">${d.title || t.title || "Task #" + numId}</h3>
          <div class="card-sub">${(d.summary || t.customer || "")} · ${d.source || t.source || "agent"} · ${d.priority || t.priority || "normal"} · status: <b>${d.status || "open"}</b></div>
        </div>
        <span class="pill ${d.priority === "Critical" || t.priority === "Critical" ? "pill-red" : (d.priority === "High" || t.priority === "High") ? "pill-orange" : "pill-blue"}">${d.priority || t.priority || "normal"}</span>
      </div>
      <div class="card-body">
        ${_renderLifecycleRibbon(d.lifecycle)}
        ${d.ai_strategy ? `
        <div class="muted-card" style="margin-bottom:14px">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
            <h4 style="margin:0">AI Strategy</h4>
            <span style="font-size:13px;color:var(--accent);font-weight:600">${Math.round((d.ai_confidence || 0) * 100)}% confidence</span>
          </div>
          <div style="color:var(--text-strong);font-weight:600;margin-bottom:8px">${d.ai_strategy}</div>
          <div class="progress"><span style="width:${pct}%"></span></div>
          <div style="margin-top:6px;font-size:12px;color:var(--text-muted)">${progress.label} · ${progress.done}/${progress.total} steps</div>
        </div>
        ` : ""}

        ${d.ai_reasoning ? `
        <div class="ai-think">
          <b>AI Reasoning</b>${d.ai_reasoning}
        </div>
        ` : ""}

        ${d.steps && d.steps.length ? `
        <div style="margin:14px 0 6px;font-weight:700;color:var(--text-strong);font-size:13px;text-transform:uppercase;letter-spacing:.06em">Steps</div>
        <div class="step-list">
          ${d.steps.map(s => `
            <div class="step-row" style="display:flex;gap:10px;align-items:flex-start;padding:8px 10px;border-bottom:1px solid var(--border-dim)">
              <span class="material-symbols-rounded" style="color:${stepColor(s.status)};font-size:18px;margin-top:2px">${stepIcon(s.status)}</span>
              <div style="flex:1">
                <div style="font-weight:600;color:var(--text-strong);font-size:13px">${s.description}</div>
                <div style="font-size:11px;color:var(--text-muted);margin-top:2px">${s.tool || ""} · <span style="color:${stepColor(s.status)}">${s.status}</span></div>
              </div>
            </div>
          `).join("")}
        </div>
        ` : ""}

        ${d.incentives_offered && d.incentives_offered.length ? `
        <div style="margin:14px 0 6px;font-weight:700;color:var(--text-strong);font-size:13px;text-transform:uppercase;letter-spacing:.06em">Incentives offered</div>
        <div class="chips">
          ${d.incentives_offered.map(i => `<span class="chip ${i.tone === "good" ? "chip-good" : ""}">${i.label}</span>`).join("")}
        </div>
        ` : ""}

        ${d.conversation && d.conversation.length ? `
        <div style="margin:14px 0 6px;font-weight:700;color:var(--text-strong);font-size:13px;text-transform:uppercase;letter-spacing:.06em">Conversation</div>
        <div class="convo">
          ${d.conversation.map(m => `
            <div class="msg msg-${m.role}">
              ${m.name ? `<div class="msg-from">${m.name}</div>` : ""}
              <div class="msg-text">${m.text}</div>
            </div>
          `).join("")}
        </div>
        ` : ""}

        ${d.recommended_action ? `
        <div class="recommendation">
          <b>Recommended next action</b>
          ${d.recommended_action.label}
        </div>
        ` : ""}

        <div style="display:flex;gap:8px;margin-top:18px;flex-wrap:wrap">
          <button class="btn btn-success" data-action="approve" data-task="${numId}"><span class="material-symbols-rounded">check</span>Approve</button>
          <button class="btn btn-primary" data-action="run" data-task="${numId}"><span class="material-symbols-rounded">play_arrow</span>Run now</button>
          <button class="btn" data-action="pause" data-task="${numId}"><span class="material-symbols-rounded">pause</span>Pause</button>
          <button class="btn" data-action="resume" data-task="${numId}"><span class="material-symbols-rounded">play_circle</span>Resume</button>
          <button class="btn" data-action="retry" data-task="${numId}"><span class="material-symbols-rounded">refresh</span>Retry</button>
          <button class="btn" data-action="chat" data-task="${numId}"><span class="material-symbols-rounded">chat</span>Add to chat</button>
          <button class="btn btn-danger-outline" data-action="cancel" data-task="${numId}"><span class="material-symbols-rounded">cancel</span>Cancel</button>
        </div>
      </div>
    `;

    detail.querySelectorAll("[data-action]").forEach(btn => {
      btn.addEventListener("click", async () => {
        const action = btn.dataset.action;
        const taskId = btn.dataset.task;
        if (action === "chat") {
          location.hash = "#/copilot";
          return;
        }
        if (action === "cancel") {
          if (!confirm("Cancel this task?")) return;
          await safeFetchJson(`/api/inbox/tasks/${taskId}`, { method: "DELETE" });
          tasks = tasks.filter(x => x.id !== id);
          selectedTaskId = null;
          renderTasks();
          detail.innerHTML = `<div class="empty-detail"><span class="material-symbols-rounded">inbox</span><h3>Select a task</h3><p>Pick a task on the left to see full details, AI thinking, and suggested actions.</p></div>`;
          return;
        }
        // Other actions hit the action endpoints
        btn.disabled = true;
        btn.style.opacity = 0.5;
        const result = await safeFetchJson(`/api/inbox/tasks/${taskId}/${action}`, { method: "POST" });
        btn.disabled = false;
        btn.style.opacity = 1;
        if (result && result.status) {
          // Refresh detail to show new status
          selectTask(id);
        }
      });
    });
  }

  renderTasks();

  // If dashboard linked here with a specific task id, open it
  const preopen = sessionStorage.getItem("inboxOpenTaskId");
  if (preopen) {
    sessionStorage.removeItem("inboxOpenTaskId");
    const wantId = String(preopen);
    const match = tasks.find(x => String(x.id) === wantId || String(x._backend?.id) === wantId);
    if (match) selectTask(match.id);
  }

  document.getElementById("refreshInbox")?.addEventListener("click", () => initInbox());
}

function _renderCompactLifecycle(lc) {
  if (!lc || !lc.stages) return "";
  const stages = lc.stages;
  const idx = lc.stage_index ?? 0;
  const breached = !!lc.breached;
  const remaining = lc.remaining?.human || "";
  const escCount = lc.escalation_count || 0;

  const dots = stages.map((s, i) => {
    const reached = i <= idx;
    const color = reached
      ? (s === "done" ? "var(--success)" : s === "blocked" ? "#F59E0B" : "var(--accent)")
      : "var(--border)";
    return `<div title="${s}" style="width:8px;height:8px;border-radius:50%;background:${color};flex-shrink:0"></div>`;
  }).join(`<div style="flex:1;height:1.5px;background:var(--border-dim);margin:0 4px;max-width:60px"></div>`);

  const dueLabel = breached
    ? `<span style="color:var(--red);font-weight:700">Overdue · ${escCount > 0 ? escCount + ' escalation' + (escCount > 1 ? 's' : '') : ''}</span>`
    : (remaining ? `Due in <b>${remaining}</b>` : "");

  return `
    <div style="display:flex;align-items:center;justify-content:space-between;gap:10px;padding:8px 0;border-top:1px solid var(--border-dim);margin-top:8px">
      <div style="display:flex;align-items:center;flex:1;max-width:320px">${dots}</div>
      <div style="font-size:12px;color:var(--text-muted)">${dueLabel}</div>
    </div>`;
}

function sourceIcon(s) {
  return {
    phone: "call", email: "mail", chat: "chat", web: "language",
    calendar: "event", drive: "description", slack: "tag", github: "code",
    manual: "edit_note", agent: "smart_toy", rule: "rule",
  }[s] || "help";
}

// Pull a bare email out of a "Name <email@x>" header.
function _extractEmail(s) {
  const m = String(s || "").match(/<([^>]+)>/);
  if (m) return m[1].trim();
  const m2 = String(s || "").match(/[\w.+-]+@[\w.-]+\.\w+/);
  return m2 ? m2[0] : String(s || "").trim();
}

// Confirm-aware mail mutation (mark-read / archive / delete). Handles the
// autonomy gate: a 403 with needs_confirmation prompts the user, then retries.
async function _doMailMutation(apiPath, msgId, endpoint, id, accountLabel, btn, detail, onSuccess, confirmed = false) {
  if (btn) { btn.disabled = true; btn.style.opacity = 0.5; }
  let resp;
  try {
    resp = await fetch(`/api/inbox/${apiPath}/${encodeURIComponent(msgId)}/${endpoint}?confirm=${confirmed}`,
      { method: "POST", credentials: "include" });
  } catch {
    if (btn) { btn.disabled = false; btn.style.opacity = 1; }
    toast("Network error — try again.", "error");
    return;
  }
  if (resp.ok) {
    if (typeof onSuccess === "function") onSuccess();
    const label = ({ "mark-read": "Marked as read", "archive": "Archived", "delete": "Deleted" })[endpoint] || "Done";
    if (detail) {
      detail.innerHTML = `<div class="empty-pane"><div>
        <span class="material-symbols-rounded">check_circle</span>
        <h3 style="margin:8px 0 4px;color:var(--text-strong)">Done</h3>
        <div>${label} in ${accountLabel}.</div></div></div>`;
    }
    toast(`${label} in ${accountLabel}.`, "success");
    return;
  }
  const err = await resp.json().catch(() => ({}));
  const d = err.detail;
  if (resp.status === 403 && d && typeof d === "object" && d.blocked_by_autonomy) {
    if (d.needs_confirmation && !confirmed) {
      if (confirm(`You're at autonomy level L${d.level}. ${endpoint.replace("-", " ")} this email now?`)) {
        return _doMailMutation(apiPath, msgId, endpoint, id, accountLabel, btn, detail, onSuccess, true);
      }
      if (btn) { btn.disabled = false; btn.style.opacity = 1; }
      return;
    }
    if (btn) { btn.disabled = false; btn.style.opacity = 1; }
    toast(`Blocked at L${d.level} (Observe). Raise the autonomy slider to act.`, "error");
    return;
  }
  if (btn) { btn.disabled = false; btn.style.opacity = 1; }
  toast(`Action failed: ${typeof d === "string" ? d : ("HTTP " + resp.status)}`, "error");
}

function _relTime(iso) {
  if (!iso) return "—";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  const diff = (Date.now() - t) / 1000;
  if (diff < 60) return "now";
  if (diff < 3600) return Math.floor(diff / 60) + "m";
  if (diff < 86400) return Math.floor(diff / 3600) + "h";
  if (diff < 86400 * 7) return Math.floor(diff / 86400) + "d";
  return new Date(t).toLocaleDateString();
}
function guessCategory(t) {
  if (/card|fraud|block/i.test(t)) return "Card services";
  if (/loan|mortgage|rate/i.test(t)) return "Lending";
  if (/dispute|charge/i.test(t))  return "Disputes";
  if (/address|update/i.test(t))  return "KYC update";
  if (/travel/i.test(t))          return "Travel notification";
  return "General inquiry";
}
function suggestedFor(t) {
  if (/block|lost/i.test(t.title)) return "Freeze card + ship replacement";
  if (/dispute/i.test(t.title))    return "Open chargeback case";
  if (/loan|rate/i.test(t.title))  return "Send personalized rate sheet";
  if (/address/i.test(t.title))    return "Update KYC profile";
  if (/travel/i.test(t.title))     return "Add travel flag";
  if (/limit/i.test(t.title))      return "Run credit check + recommend limit";
  if (/standing order/i.test(t.title)) return "Notify customer + retry tomorrow";
  return "Acknowledge and route";
}

/* =========================================================
   Copilot
   ========================================================= */
window._copilotCurrentThreadId = null;
window._copilotHistory = window._copilotHistory || [];

async function refreshCopilotRail(activeId = null) {
  const rail = document.getElementById("railList");
  if (!rail) return;
  const data = await safeFetchJson("/api/threads");
  const threads = (data && data.threads) || [];

  if (!threads.length) {
    rail.innerHTML = `<div style="padding:16px;font-size:13px;color:var(--text-muted);text-align:center">
      No chats yet. Click <b>New Chat</b> to start one.
    </div>`;
    return;
  }

  // Group by Today / Yesterday / Earlier
  const now = Date.now();
  const groups = { "Today": [], "Yesterday": [], "Earlier this week": [], "Older": [] };
  for (const t of threads) {
    const age = (now - Date.parse(t.updated_at)) / 86400000;
    if (age < 1)        groups["Today"].push(t);
    else if (age < 2)   groups["Yesterday"].push(t);
    else if (age < 7)   groups["Earlier this week"].push(t);
    else                groups["Older"].push(t);
  }

  rail.innerHTML = Object.entries(groups)
    .filter(([_, items]) => items.length)
    .map(([label, items]) => `
      <div class="rail-group-title">${label}</div>
      ${items.map(it => `
        <div class="rail-item ${activeId === it.id ? "active" : ""}" data-thread="${it.id}">
          <div>${escapeHtml(it.title || "Untitled")}</div>
          <div class="rail-item-time">${new Date(it.updated_at).toLocaleTimeString([], {hour:"2-digit",minute:"2-digit"})}</div>
        </div>
      `).join("")}
    `).join("");

  rail.querySelectorAll(".rail-item").forEach(el => {
    el.addEventListener("click", () => loadCopilotThread(parseInt(el.dataset.thread, 10)));
  });
}

async function loadCopilotThread(threadId) {
  const data = await safeFetchJson(`/api/threads/${threadId}`);
  if (!data) return false;
  window._copilotCurrentThreadId = threadId;
  sessionStorage.setItem("copilotThreadId", String(threadId));
  window._copilotHistory = (data.messages || []).map(m => ({ role: m.role, content: m.content }));

  const body = document.getElementById("copilotBody");
  if (!body) return;

  // Render messages
  body.style.justifyContent = "flex-start";
  body.style.alignItems = "stretch";
  body.innerHTML = `<div class="conv-thread" style="display:flex;flex-direction:column;gap:10px;width:100%;max-width:760px;margin:0 auto"></div>`;
  const thread = body.querySelector(".conv-thread");
  const msgs = data.messages || [];
  for (const m of msgs) {
    const isUser = m.role === "user";
    // User text is escaped (no markdown); AI replies render markdown like live chat.
    const inner = isUser
      ? `<div class="msg user" style="white-space:pre-wrap">${escapeHtml(m.content)}</div>`
      : `<div class="msg ai">${renderMarkdownSafe(m.content)}</div>`;
    thread.insertAdjacentHTML("beforeend", inner);
  }
  // If this is a research thread whose run hasn't finished (no report saved yet),
  // re-attach the live research panel so progress keeps showing after navigation.
  const rSid = sessionStorage.getItem("copilotResearchSid");
  const rThread = sessionStorage.getItem("copilotResearchThread");
  const hasAssistant = msgs.some(m => m.role === "assistant");
  if (rSid && String(rThread) === String(threadId) && !hasAssistant) {
    const q = sessionStorage.getItem("copilotResearchQuery") || "Research";
    const { reportEl, addStage } = _buildResearchPanel(thread, q, body);
    _attachResearchStream(rSid, addStage, reportEl, body);
  }
  body.scrollTop = body.scrollHeight;
  refreshCopilotRail(threadId);
  return true;
}

function newCopilotThread() {
  // Don't pre-create an empty thread (that left blank "New chat" rows in the
  // rail and wiped the suggestions grid). Reset to the welcome + suggestions;
  // the thread is created lazily on the first message (see sendCopilot).
  window._copilotCurrentThreadId = null;
  window._copilotHistory = [];
  _clearCopilotViewState();
  const body = document.getElementById("copilotBody");
  if (body) body.innerHTML = emptyCopilotHTML();
  initCopilot();            // currentThreadId is null → renders suggestion cards
  refreshCopilotRail(null);
}

async function deleteCurrentThread() {
  if (!window._copilotCurrentThreadId) return;
  if (!confirm("Delete this chat?")) return;
  await fetch(`/api/threads/${window._copilotCurrentThreadId}`, {
    method: "DELETE", credentials: "include",
  });
  window._copilotCurrentThreadId = null;
  window._copilotHistory = [];
  _clearCopilotViewState();
  const body = document.getElementById("copilotBody");
  if (body) body.innerHTML = emptyCopilotHTML();
  initCopilot();
  refreshCopilotRail();
}

// Forget which conversation/research to restore (used by New Chat + delete).
function _clearCopilotViewState() {
  window._copilotActiveResearch = null;
  ["copilotThreadId", "copilotResearchSid", "copilotResearchQuery",
   "copilotResearchThread", "copilotLastView", "openResearchSession"].forEach(k => sessionStorage.removeItem(k));
}

async function renameCurrentThread() {
  if (!window._copilotCurrentThreadId) return;
  const next = prompt("Rename chat:");
  if (!next) return;
  await safeFetchJson(`/api/threads/${window._copilotCurrentThreadId}`, {
    method: "PATCH",
    body: { title: next },
  });
  refreshCopilotRail(window._copilotCurrentThreadId);
}

async function exportCurrentThread() {
  if (!window._copilotCurrentThreadId) return;
  const data = await safeFetchJson(`/api/threads/${window._copilotCurrentThreadId}`);
  if (!data) return;
  const md = [`# ${data.thread.title}`, ""]
    .concat((data.messages || []).map(m => `**${m.role}**:\n\n${m.content}\n`))
    .join("\n");
  const blob = new Blob([md], { type: "text/markdown" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${(data.thread.title || "chat").replace(/[^\w-]+/g, "_")}.md`;
  a.click();
  URL.revokeObjectURL(url);
}

async function showCopilotInsights() {
  const s = await safeFetchJson("/api/insights");
  const v = (x) => (x === null || x === undefined) ? "—" : x;
  openModal(`
    <div class="modal-head">
      <div><span style="font-weight:700;font-size:17px;color:var(--text-strong)">Copilot insights</span></div>
      <button class="modal-close" data-close><span class="material-symbols-rounded">close</span></button>
    </div>
    <div class="modal-body">
      <div class="duo-grid" style="grid-template-columns:repeat(2,1fr);gap:12px">
        <div class="kpi"><div class="kpi-label">ACTIONS TODAY</div><div class="kpi-value">${v(s?.actions_today)}</div></div>
        <div class="kpi"><div class="kpi-label">ACTIONS THIS WEEK</div><div class="kpi-value">${v(s?.actions_week)}</div></div>
        <div class="kpi"><div class="kpi-label">CHAT TURNS TODAY</div><div class="kpi-value">${v(s?.chat_messages_today)}</div></div>
        <div class="kpi"><div class="kpi-label">TIME SAVED</div><div class="kpi-value">${v(s?.time_saved_minutes)} min</div></div>
        <div class="kpi"><div class="kpi-label">GMAIL UNREAD</div><div class="kpi-value">${v(s?.gmail_unread)}</div></div>
        <div class="kpi"><div class="kpi-label">GMAIL DRAFTS</div><div class="kpi-value">${v(s?.gmail_drafts)}</div></div>
      </div>
      <p style="color:var(--text-muted);font-size:12px;margin-top:14px">Generated ${s ? new Date(s.generated_at).toLocaleString() : "—"}</p>
    </div>
    <div class="modal-foot"><button class="btn" data-close>Close</button></div>
  `);
}

function showCopilotHistory() {
  const rail = document.getElementById("railList");
  // Just scroll/focus the rail — already showing thread history.
  if (rail) {
    rail.scrollTop = 0;
    rail.style.transition = "outline 0.4s";
    rail.style.outline = "2px solid var(--accent)";
    setTimeout(() => rail.style.outline = "none", 600);
  }
}

function showCopilotMore() {
  const tid = window._copilotCurrentThreadId;
  openModal(`
    <div class="modal-head">
      <div><span style="font-weight:700;font-size:17px;color:var(--text-strong)">Chat options</span></div>
      <button class="modal-close" data-close><span class="material-symbols-rounded">close</span></button>
    </div>
    <div class="modal-body">
      <button class="btn" id="moreRename" ${tid?"":"disabled"} style="width:100%;justify-content:flex-start;margin-bottom:8px">
        <span class="material-symbols-rounded">edit</span> Rename chat</button>
      <button class="btn" id="moreExport" ${tid?"":"disabled"} style="width:100%;justify-content:flex-start;margin-bottom:8px">
        <span class="material-symbols-rounded">download</span> Export as Markdown</button>
      <button class="btn btn-danger-outline" id="moreDelete" ${tid?"":"disabled"} style="width:100%;justify-content:flex-start">
        <span class="material-symbols-rounded">delete</span> Delete chat</button>
      ${tid ? "" : `<p style="font-size:12px;color:var(--text-muted);margin-top:10px">Send a message first to create a chat.</p>`}
    </div>
    <div class="modal-foot"><button class="btn" data-close>Close</button></div>
  `);
  document.getElementById("moreRename")?.addEventListener("click", () => { closeModal(); renameCurrentThread(); });
  document.getElementById("moreExport")?.addEventListener("click", () => { closeModal(); exportCurrentThread(); });
  document.getElementById("moreDelete")?.addEventListener("click", () => { closeModal(); deleteCurrentThread(); });
}

const COPILOT_ACTIONS = [
  { icon: "mail",          t: "Summarize my inbox",      d: "Top emails that need a reply" },
  { icon: "event",         t: "What's on my calendar?",  d: "Today's meetings + free time" },
  { icon: "edit_note",     t: "Draft a status update",   d: "From your recent work" },
  { icon: "search",        t: "Search my Drive",         d: "Find a file or doc" },
  { icon: "alarm",         t: "Set a reminder",          d: "Remind me later about X" },
  { icon: "task_alt",      t: "Run a task overnight",    d: "Background goal — wake to results" },
  { icon: "image",         t: "Read my screen",          d: "Analyze a screenshot" },
  { icon: "tips_and_updates", t: "Plan my day",          d: "Help me prioritize" },
];

async function initCopilot() {
  // The copilot page was rebuilt as the Odysseus-style 3-zone chat (see
  // web/pages/copilot.html), which self-initializes via its own inline script.
  // When that root is present, the legacy initializer below must not run (its
  // element lookups would target a DOM that no longer exists).
  if (document.getElementById("ody-chat-root")) return;
  // Recover state from sessionStorage — window globals can be lost across
  // navigation, so sessionStorage is the reliable source of truth.
  if (!window._copilotCurrentThreadId) {
    const saved = sessionStorage.getItem("copilotThreadId");
    if (saved) window._copilotCurrentThreadId = saved;
  }
  await refreshCopilotRail(window._copilotCurrentThreadId);

  // Restore what the user was looking at, before the suggestions grid so the
  // welcome screen isn't shown over it. Research is a normal saved thread now, so
  // restore is THREAD-FIRST (robust); the live-session path is only a fallback
  // for a dashboard "Open" or a thread that couldn't load.
  const openSid = sessionStorage.getItem("openResearchSession");   // Dashboard "Open"
  const researchSid = sessionStorage.getItem("copilotResearchSid");
  if (openSid) {
    sessionStorage.removeItem("openResearchSession");
    await showResearchSession(openSid);
  } else if (window._copilotCurrentThreadId) {
    const ok = await loadCopilotThread(window._copilotCurrentThreadId);
    if (!ok) {
      // Stale/deleted thread id — clear it and fall back gracefully.
      window._copilotCurrentThreadId = null;
      sessionStorage.removeItem("copilotThreadId");
      if (researchSid) await showResearchSession(researchSid);
    }
  } else if (researchSid) {
    await showResearchSession(researchSid);
  }

  // Suggested actions — personalized from the background-precomputed inbox/
  // calendar enrichments, with a static fallback. Shown when no conversation yet.
  const grid = document.getElementById("actionGrid");
  if (grid) {
    let cards;
    const data = await safeFetchJson("/api/copilot/suggestions");
    if (data && Array.isArray(data.suggestions) && data.suggestions.length) {
      cards = data.suggestions.map(s => ({ icon: s.icon || "bolt", t: s.title, d: s.sub || "", prompt: s.prompt }));
    } else {
      cards = COPILOT_ACTIONS.map(a => ({ icon: a.icon, t: a.t, d: a.d, prompt: a.t }));
    }
    grid.innerHTML = cards.map((c, i) => `
      <div class="action-card" data-i="${i}">
        <div class="action-icon"><span class="material-symbols-rounded">${c.icon}</span></div>
        <div class="action-text">
          <div class="t">${escapeHtml(c.t)}</div>
          <div class="d">${escapeHtml(c.d)}</div>
        </div>
      </div>
    `).join("");
    grid.querySelectorAll(".action-card").forEach(el => {
      const c = cards[parseInt(el.dataset.i, 10)];
      el.addEventListener("click", () => {
        // The Research card shows a few one-tap topic chips above the input
        // (click one or type your own) instead of just prefilling.
        if (c.prompt && /^research\s*$/i.test(c.prompt)) {
          showResearchSuggestions();
        } else if (c.prompt && c.prompt.endsWith(" ")) {
          // Other trailing-space prompts just prefill and wait for the user.
          const inp = document.getElementById("copilotInput");
          if (inp) { inp.value = c.prompt; inp.focus(); }
        } else {
          sendCopilot(c.prompt);
        }
      });
    });
  }

  // Wire header buttons
  document.getElementById("newChatBtn")?.addEventListener("click", newCopilotThread);

  // Page header buttons + chat header buttons (Insights, history, more)
  document.querySelectorAll(".page-header .btn, .copilot-head .icon-btn").forEach(btn => {
    // dedupe by label / icon name
  });

  const insightsBtn = [...document.querySelectorAll(".page-header .btn")]
    .find(b => /insights/i.test(b.textContent));
  insightsBtn?.addEventListener("click", showCopilotInsights);

  const headIcons = document.querySelectorAll(".copilot-head .icon-btn");
  headIcons.forEach(b => {
    const ico = b.querySelector(".material-symbols-rounded")?.textContent?.trim();
    if (ico === "history")    b.addEventListener("click", showCopilotHistory);
    if (ico === "more_horiz") b.addEventListener("click", showCopilotMore);
  });

  const input = document.getElementById("copilotInput");
  const sendBtn = document.getElementById("copilotSend");
  if (input && !input.dataset.bound) {
    input.dataset.bound = "1";
    input.addEventListener("keydown", e => { if (e.key === "Enter") sendCopilot(input.value); });
  }
  if (sendBtn && !sendBtn.dataset.bound) {
    sendBtn.dataset.bound = "1";
    sendBtn.addEventListener("click", () => sendCopilot(input?.value));
  }

  // Chat search — filter the history rail by title as the user types.
  const railSearch = document.querySelector(".rail-search");
  if (railSearch && !railSearch.dataset.bound) {
    railSearch.dataset.bound = "1";
    railSearch.addEventListener("input", () => {
      const q = railSearch.value.trim().toLowerCase();
      document.querySelectorAll("#railList .rail-item").forEach(it => {
        const t = (it.querySelector("div")?.textContent || "").toLowerCase();
        it.style.display = !q || t.includes(q) ? "" : "none";
      });
      document.querySelectorAll("#railList .rail-group-title").forEach(g => {
        // hide a group label if every item under it is hidden
        let n = g.nextElementSibling, anyVisible = false;
        while (n && n.classList.contains("rail-item")) {
          if (n.style.display !== "none") anyVisible = true;
          n = n.nextElementSibling;
        }
        g.style.display = anyVisible ? "" : "none";
      });
    });
  }

  // Voice dictation via the Web Speech API (free, browser-native). If the
  // browser doesn't support it, hide the mic button instead of lying.
  const micBtn = [...document.querySelectorAll(".copilot-input .icon-btn")]
    .find(b => /mic/.test(b.querySelector(".material-symbols-rounded")?.textContent || ""));
  if (micBtn && !micBtn.dataset.bound) {
    micBtn.dataset.bound = "1";
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) {
      micBtn.style.display = "none";
    } else {
      micBtn.title = "Voice input";
      let rec = null, listening = false;
      micBtn.addEventListener("click", () => {
        if (listening && rec) { rec.stop(); return; }
        rec = new SR();
        rec.lang = "en-US"; rec.interimResults = true; rec.continuous = false;
        listening = true;
        micBtn.classList.add("primary");
        micBtn.querySelector(".material-symbols-rounded").textContent = "mic";
        const base = input.value ? input.value + " " : "";
        rec.onresult = (e) => {
          let txt = "";
          for (let i = e.resultIndex; i < e.results.length; i++) txt += e.results[i][0].transcript;
          input.value = base + txt;
        };
        rec.onend = () => {
          listening = false; micBtn.classList.remove("primary");
          micBtn.querySelector(".material-symbols-rounded").textContent = "mic";
        };
        rec.onerror = () => { listening = false; micBtn.classList.remove("primary"); toast("Voice input unavailable.", "error"); };
        rec.start();
      });
    }
  }

  // Attach button → file picker. Idempotent: handlers are bound only once
  // per element so navigating back to the page doesn't trigger duplicates.
  const fileInput = document.getElementById("copilotFile");
  const attachBtn = document.getElementById("copilotAttach");
  if (attachBtn && !attachBtn.dataset.bound) {
    attachBtn.dataset.bound = "1";
    attachBtn.addEventListener("click", () => fileInput?.click());
  }
  if (fileInput && !fileInput.dataset.bound) {
    fileInput.dataset.bound = "1";
    fileInput.addEventListener("change", async () => {
      if (!fileInput.files) return;
      for (const f of fileInput.files) {
        await uploadCopilotFile(f);
      }
      fileInput.value = "";
    });
  }

  // Drag-and-drop ANYWHERE on the copilot page. We wire to .copilot-main so
  // dropping into the chat area, the input row, or the empty welcome state
  // all work.
  const dropTarget = document.querySelector(".copilot-main") || document.getElementById("copilotBody");
  if (dropTarget) {
    let depth = 0;
    let overlay = null;
    const showOverlay = () => {
      if (overlay) return;
      overlay = document.createElement("div");
      overlay.className = "drop-overlay";
      overlay.innerHTML = `<div><span class="material-symbols-rounded" style="font-size:44px;color:var(--accent)">cloud_upload</span><div style="font-weight:700;color:var(--text-strong);margin-top:8px">Drop to attach</div><div style="font-size:13px;color:var(--text-muted)">Screenshots, PDFs, docs, text</div></div>`;
      Object.assign(overlay.style, {
        position: "absolute", inset: "0",
        background: "rgba(255,255,255,.92)",
        border: "3px dashed var(--accent)",
        borderRadius: "12px",
        display: "flex", alignItems: "center", justifyContent: "center",
        zIndex: "100", pointerEvents: "none", textAlign: "center",
      });
      dropTarget.style.position = "relative";
      dropTarget.appendChild(overlay);
    };
    const hideOverlay = () => {
      if (overlay) { overlay.remove(); overlay = null; }
    };
    dropTarget.addEventListener("dragenter", e => {
      e.preventDefault(); depth++; showOverlay();
    });
    dropTarget.addEventListener("dragover", e => {
      e.preventDefault();
      if (e.dataTransfer) e.dataTransfer.dropEffect = "copy";
    });
    dropTarget.addEventListener("dragleave", e => {
      e.preventDefault(); depth = Math.max(0, depth - 1);
      if (depth === 0) hideOverlay();
    });
    dropTarget.addEventListener("drop", async e => {
      e.preventDefault(); depth = 0; hideOverlay();
      const files = e.dataTransfer?.files || [];
      for (const f of files) await uploadCopilotFile(f);
    });
  }

  // Paste a screenshot directly with Ctrl+V
  const pasteHandler = async (e) => {
    if (location.hash !== "#/copilot") return;
    const items = e.clipboardData?.items || [];
    for (const item of items) {
      if (item.type && item.type.startsWith("image/")) {
        const file = item.getAsFile();
        if (file) {
          // Give it a real-looking name so the LLM sees something useful
          const named = new File([file], `pasted-${Date.now()}.png`, { type: file.type });
          await uploadCopilotFile(named);
        }
      }
    }
  };
  document.removeEventListener("paste", window._copilotPasteHandler || (() => {}));
  window._copilotPasteHandler = pasteHandler;
  document.addEventListener("paste", pasteHandler);

  // Pick up any prefilled prompt from another page (Inbox → "Reply with MyAi" etc.)
  const prefill = sessionStorage.getItem("copilotPrefill");
  if (prefill) {
    sessionStorage.removeItem("copilotPrefill");
    setTimeout(() => sendCopilot(prefill), 60);
  }
}

// Queue of attachments to attach to the next user message
window._copilotAttachments = window._copilotAttachments || [];

async function uploadCopilotFile(file) {
  const row = document.getElementById("attachmentRow");
  const isImage = (file.type || "").startsWith("image/");

  // Build a blob URL for image previews — frees on send.
  const previewUrl = isImage ? URL.createObjectURL(file) : null;

  // Build the staged pill / thumbnail
  const stage = document.createElement("div");
  stage.className = "attach-stage";
  Object.assign(stage.style, {
    display: "inline-flex", alignItems: "center", gap: "8px",
    background: "var(--bg-soft)", border: "1px solid var(--border-dim)",
    borderRadius: "10px", padding: "6px 10px", maxWidth: "260px",
  });
  if (isImage) {
    stage.innerHTML = `
      <img src="${previewUrl}" style="width:32px;height:32px;object-fit:cover;border-radius:6px;border:1px solid var(--border-dim)" />
      <div style="min-width:0;flex:1">
        <div style="font-size:12.5px;color:var(--text-strong);font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${escapeHtml(file.name)}</div>
        <div style="font-size:11px;color:var(--text-muted)">Uploading…</div>
      </div>
      <span data-rm style="cursor:pointer;opacity:.7;font-size:14px">×</span>
    `;
  } else {
    stage.innerHTML = `
      <span class="material-symbols-rounded" style="font-size:22px;color:var(--accent)">description</span>
      <div style="min-width:0;flex:1">
        <div style="font-size:12.5px;color:var(--text-strong);font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${escapeHtml(file.name)}</div>
        <div style="font-size:11px;color:var(--text-muted)">Uploading…</div>
      </div>
      <span data-rm style="cursor:pointer;opacity:.7;font-size:14px">×</span>
    `;
  }
  if (row) { row.style.display = "flex"; row.appendChild(stage); }

  const fd = new FormData();
  fd.append("file", file);
  let data = null;
  try {
    const r = await fetch("/api/copilot/upload", {
      method: "POST", body: fd, credentials: "include",
    });
    if (!r.ok) throw new Error("HTTP " + r.status);
    data = await r.json();
  } catch (e) {
    stage.style.borderColor = "var(--red)";
    stage.querySelector("[data-rm]")?.click?.();
    return;
  }

  // Attach the preview info for inline render in the user bubble on send
  data._previewUrl = previewUrl;
  data._isImage = isImage;

  const sizeEl = stage.querySelector("div > div:nth-child(2)");
  if (sizeEl) sizeEl.textContent = `${Math.round(data.size_bytes / 1024)} KB`;
  window._copilotAttachments.push(data);

  stage.querySelector("[data-rm]").addEventListener("click", () => {
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    stage.remove();
    window._copilotAttachments = window._copilotAttachments.filter(x => x !== data);
    if (!window._copilotAttachments.length && row) row.style.display = "none";
  });
}

function _renderAttachmentChips(atts) {
  if (!atts || !atts.length) return "";
  const items = atts.map(a => {
    if (a._isImage && a._previewUrl) {
      return `<img src="${a._previewUrl}" alt="${escapeHtml(a.filename)}" style="max-width:280px;max-height:280px;border-radius:10px;border:1px solid var(--border-dim);display:block" />`;
    }
    return `<div style="display:inline-flex;align-items:center;gap:8px;background:#fff;border:1px solid var(--border-dim);border-radius:10px;padding:8px 12px;color:var(--text-strong)">
      <span class="material-symbols-rounded" style="font-size:18px;color:var(--accent)">description</span>
      <div>
        <div style="font-size:12.5px;font-weight:600">${escapeHtml(a.filename)}</div>
        <div style="font-size:11px;color:var(--text-muted)">${Math.round(a.size_bytes/1024)} KB</div>
      </div>
    </div>`;
  }).join("");
  return `<div style="display:flex;flex-direction:column;gap:6px;margin-top:6px">${items}</div>`;
}

function emptyCopilotHTML() {
  return `
    <div class="copilot-empty">
      <div class="avatar-lg">M</div>
      <div class="hi">Hi, I'm MyAi</div>
      <div class="sub">Your AI Assistant · How can I help you today?</div>
    </div>
    <div class="action-grid" id="actionGrid"></div>
  `;
}

/* ---------- Deep Research panel (live progress + cited markdown report) ---------- */

// Minimal, safe markdown subset (escape first, then headings/bold/links/lists).
function renderMarkdownSafe(md) {
  const lines = escapeHtml(md || "").split(/\r?\n/);
  const inline = s => s
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
      (m, t, u) => `<a href="${u}" target="_blank" rel="noreferrer" style="color:var(--accent)">${t}</a>`)
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  let html = "", inList = false;
  for (const raw of lines) {
    const line = raw.trim();
    const bullet = line.match(/^[-*]\s+(.*)/);
    const head = line.match(/^(#{1,6})\s+(.*)/);
    if (bullet) { if (!inList) { html += "<ul style='margin:6px 0 6px 18px'>"; inList = true; } html += `<li>${inline(bullet[1])}</li>`; continue; }
    if (inList) { html += "</ul>"; inList = false; }
    if (!line) continue;
    if (head) { const lvl = Math.min(head[1].length + 2, 6); html += `<h${lvl} style="margin:12px 0 4px">${inline(head[2])}</h${lvl}>`; continue; }
    html += `<p style="margin:6px 0">${inline(line)}</p>`;
  }
  if (inList) html += "</ul>";
  return html;
}

function _stageLabel(stage, detail) {
  if (stage === "done") return "Done";
  if (stage === "error") return "Error: " + (detail || "");
  if (detail && detail !== "complete") return detail;
  return stage;
}

async function _pollResearch(sessionId, addStage, finish) {
  let seen = 0;
  const tick = async () => {
    const st = await safeFetchJson(`/api/copilot/research/status/${sessionId}`);
    if (st && Array.isArray(st.events)) {
      for (let i = seen; i < st.events.length; i++) addStage(_stageLabel(st.events[i].stage, st.events[i].detail));
      seen = st.events.length;
    }
    if (st && st.done) { finish(); return; }
    setTimeout(tick, 1500);
  };
  tick();
}

function _downloadText(filename, text) {
  const blob = new Blob([text], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function _renderResearchResult(sessionId, reportEl, body) {
  const r = await safeFetchJson(`/api/copilot/research/result/${sessionId}`);
  if (!r || !r.ready) { reportEl.innerHTML = `<div style="color:#b91c1c">Research finished but no report was produced.</div>`; return; }
  const md = r.report || "";  // full report (with Sources) → used for download
  // Hide the "## Sources" list in the chat view; inline citations stay clickable.
  const display = md.split(/\n#{1,6}\s*sources\b/i)[0].replace(/\s+$/, "");
  let html = renderMarkdownSafe(display);
  if (r.partial) html = `<div style="font-size:12px;color:#b45309;margin-bottom:6px">⚠️ Partial result — the time budget was reached, so this may be incomplete.</div>` + html;
  const fname = "research-" + ((r.query || "report").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40) || "report") + ".md";
  reportEl.innerHTML = `
    <div class="research-report" style="line-height:1.55;font-size:14px;color:var(--text-strong)">${html}</div>
    <div style="margin-top:12px;display:flex;gap:8px;flex-wrap:wrap">
      <button class="btn" data-research-dl style="font-size:12px"><span class="material-symbols-rounded" style="font-size:15px;vertical-align:-3px">download</span> Download .md</button>
      <button class="btn" data-research-copy style="font-size:12px"><span class="material-symbols-rounded" style="font-size:15px;vertical-align:-3px">content_copy</span> Copy</button>
    </div>`;
  const dl = reportEl.querySelector("[data-research-dl]");
  if (dl) dl.addEventListener("click", () => _downloadText(fname, md));
  const cp = reportEl.querySelector("[data-research-copy]");
  if (cp) cp.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(md); toast("Report copied to clipboard.", "success"); }
    catch { toast("Couldn't copy.", "error"); }
  });
  if (body) body.scrollTop = body.scrollHeight;
  // The backend saved the report into the chat thread, so it's now a normal saved
  // chat — drop the live-session markers so restore goes through the thread.
  sessionStorage.removeItem("copilotResearchSid");
  sessionStorage.removeItem("copilotResearchThread");
  sessionStorage.setItem("copilotLastView", "chat");
  if (window._copilotCurrentThreadId) refreshCopilotRail(window._copilotCurrentThreadId);
}

// Active research sessions this tab knows about, so navigating away and back can
// restore the panel (window survives SPA navigation; only the DOM is rebuilt).
window._researchSessions = window._researchSessions || {};  // sid -> {query}

function _buildResearchPanel(thread, query, body) {
  const pid = "rp" + Math.random().toString(36).slice(2, 9);
  thread.insertAdjacentHTML("beforeend", `
    <div class="msg ai" id="${pid}" style="max-width:100%;width:100%">
      <div style="display:flex;align-items:center;gap:8px;font-weight:700;color:var(--accent)">
        <span class="material-symbols-rounded">travel_explore</span> Deep research
        <span style="font-weight:500;color:var(--text-muted);font-size:12px;overflow:hidden;text-overflow:ellipsis">— ${escapeHtml(query)}</span>
      </div>
      <div id="${pid}-stages" style="margin-top:8px;font-size:12.5px;color:var(--text-muted);display:flex;flex-direction:column;gap:3px"></div>
      <div id="${pid}-report" style="margin-top:10px"></div>
    </div>`);
  body.scrollTop = body.scrollHeight;
  const stagesEl = document.getElementById(`${pid}-stages`);
  const reportEl = document.getElementById(`${pid}-report`);
  const addStage = txt => {
    stagesEl.insertAdjacentHTML("beforeend", `<div><span class="material-symbols-rounded" style="font-size:13px;vertical-align:-2px;color:var(--accent)">arrow_right</span> ${escapeHtml(txt)}</div>`);
    body.scrollTop = body.scrollHeight;
  };
  return { stagesEl, reportEl, addStage };
}

// Attach to a research session (live or finished): replay past stages, then
// stream remaining ones via SSE (poll fallback), and render the final report.
async function _attachResearchStream(sid, addStage, reportEl, body) {
  // Finished result first — it's persisted to disk, so it survives a server
  // restart even when the in-memory session (status/stream) is already gone.
  const res = await safeFetchJson(`/api/copilot/research/result/${sid}`);
  if (res && res.ready) { await _renderResearchResult(sid, reportEl, body); return; }
  const st = await safeFetchJson(`/api/copilot/research/status/${sid}`);
  if (!st) {  // neither a live session nor a saved result
    reportEl.innerHTML = `<div style="color:#b91c1c">This research is no longer available.</div>`;
    return;
  }
  (st.events || []).forEach(ev => addStage(_stageLabel(ev.stage, ev.detail)));
  if (st.done) { await _renderResearchResult(sid, reportEl, body); return; }
  let seen = (st.events || []).length;
  await new Promise(resolve => {
    let done = false;
    const finish = async () => { if (done) return; done = true; await _renderResearchResult(sid, reportEl, body); resolve(); };
    let es;
    try {
      es = new EventSource(`/api/copilot/research/stream/${sid}`);
      let i = 0;
      es.onmessage = e => {
        let ev; try { ev = JSON.parse(e.data); } catch { return; }
        // Skip events we already replayed from status.
        if (!ev._final) { if (i++ < seen) return; addStage(_stageLabel(ev.stage, ev.detail)); }
        if (ev._final) { es.close(); finish(); }
      };
      es.onerror = () => { try { es.close(); } catch (_) {} _pollResearch(sid, addStage, finish); };
    } catch (e) {
      _pollResearch(sid, addStage, finish);
    }
  });
}

// Start a deep-research run in its OWN new chat thread (preserving the current
// conversation as its own rail entry). The finished report is saved into the
// thread by the backend, so it persists like any chat.
// Curated pool of research starters. Clicking "Research a topic" surfaces a few
// of these as one-tap chips above the input; the user can also just type their
// own. Rotated each time so the suggestions feel fresh.
const RESEARCH_SUGGESTIONS = [
  "the latest open-source LLM releases and how they compare",
  "AI agent frameworks for enterprise workflows in 2026",
  "best practices for retrieval-augmented generation (RAG)",
  "the current state of AI regulation and the EU AI Act",
  "how leading companies are deploying AI copilots internally",
  "vector databases compared — pgvector vs Pinecone vs Qdrant",
  "small language models that run well on local hardware",
  "the most-funded AI startups this year and what they build",
  "prompt-injection risks and defenses for LLM apps",
  "multimodal AI models and their real-world use cases",
];

function hideResearchSuggestions() {
  const row = document.getElementById("researchSuggestRow");
  if (row) row.style.display = "none";
}

function showResearchSuggestions() {
  const row = document.getElementById("researchSuggestRow");
  const chips = document.getElementById("researchSuggestChips");
  if (!row || !chips) return;

  // Rotate through the pool so a repeat click shows different ideas.
  const start = parseInt(sessionStorage.getItem("researchSuggestCursor") || "0", 10) || 0;
  const picks = [];
  for (let k = 0; k < 3; k++) picks.push(RESEARCH_SUGGESTIONS[(start + k) % RESEARCH_SUGGESTIONS.length]);
  sessionStorage.setItem("researchSuggestCursor", String((start + 3) % RESEARCH_SUGGESTIONS.length));

  chips.innerHTML = picks.map((p, i) => `
    <button type="button" class="research-chip" data-i="${i}"
      style="border:1px solid var(--border);background:var(--bg-elev);color:var(--text-strong);
             border-radius:999px;padding:6px 12px;font-size:12.5px;cursor:pointer;line-height:1.2;
             max-width:340px;text-align:left;transition:background .12s,border-color .12s">
      ${escapeHtml(p.charAt(0).toUpperCase() + p.slice(1))}
    </button>`).join("");

  chips.querySelectorAll(".research-chip").forEach(btn => {
    btn.addEventListener("mouseenter", () => { btn.style.background = "var(--accent-soft)"; btn.style.borderColor = "var(--accent)"; });
    btn.addEventListener("mouseleave", () => { btn.style.background = "var(--bg-elev)"; btn.style.borderColor = "var(--border)"; });
    btn.addEventListener("click", () => {
      const topic = picks[parseInt(btn.dataset.i, 10)];
      hideResearchSuggestions();
      const inp = document.getElementById("copilotInput");
      if (inp) inp.value = "";
      runResearchAsNewChat("Research " + topic);
    });
  });

  row.style.display = "flex";

  // Prefill the input so typing your own topic is one keystroke away, and hide
  // the chips as soon as the user starts editing it themselves.
  const inp = document.getElementById("copilotInput");
  if (inp) {
    inp.value = "Research ";
    inp.focus();
    if (!inp.dataset.researchHideBound) {
      inp.dataset.researchHideBound = "1";
      inp.addEventListener("input", () => {
        if (inp.value.trim() && inp.value.trim().toLowerCase() !== "research") hideResearchSuggestions();
      });
    }
  }
}

async function runResearchAsNewChat(rawText) {
  hideResearchSuggestions();
  const query = rawText.replace(/^research[:\s-]*/i, "").trim() || rawText;
  window._copilotCurrentThreadId = null;
  window._copilotHistory = [];
  const body = document.getElementById("copilotBody");
  if (!body) return;
  body.style.justifyContent = "flex-start";
  body.style.alignItems = "stretch";
  body.innerHTML = `<div class="conv-thread" style="display:flex;flex-direction:column;gap:10px;width:100%;max-width:760px;margin:0 auto"></div>`;
  const thread = body.querySelector(".conv-thread");
  thread.insertAdjacentHTML("beforeend",
    `<div class="msg user" style="max-width:80%"><div>${escapeHtml(rawText)}</div></div>`);
  body.scrollTop = body.scrollHeight;
  // Create the backing chat thread so this research shows in the rail + persists.
  try {
    const created = await safeFetchJson("/api/threads", { method: "POST", body: { title: "Research: " + query.slice(0, 70) } });
    if (created && created.id) {
      window._copilotCurrentThreadId = created.id;
      sessionStorage.setItem("copilotThreadId", String(created.id));
      sessionStorage.setItem("copilotResearchThread", String(created.id));
      await safeFetchJson(`/api/threads/${created.id}/messages`, { method: "POST", body: { role: "user", content: rawText } });
      refreshCopilotRail(created.id);
    }
  } catch (e) { /* non-fatal */ }
  window._copilotHistory.push({ role: "user", content: rawText });
  await runResearchPanel(query, thread, body);
}

async function runResearchPanel(query, thread, body) {
  const { reportEl, addStage } = _buildResearchPanel(thread, query, body);
  let sessionId;
  const tid = window._copilotCurrentThreadId;
  const startUrl = "/api/copilot/research/start" + (tid ? `?thread_id=${tid}` : "");
  try {
    const start = await safeFetchJson(startUrl, { method: "POST", body: { message: query, history: [] } });
    sessionId = start && start.session_id;
  } catch (e) { /* handled below */ }
  if (!sessionId) { reportEl.innerHTML = `<div style="color:#b91c1c">Couldn't start research. Try again.</div>`; return; }
  window._researchSessions[sessionId] = { query };
  // Persist so navigating away and back restores THIS view (not a window global).
  sessionStorage.setItem("copilotResearchSid", sessionId);
  sessionStorage.setItem("copilotResearchQuery", query);
  sessionStorage.setItem("copilotLastView", "research");
  await _attachResearchStream(sessionId, addStage, reportEl, body);
}

// Render a research session into a fresh Copilot conversation (used by the
// Dashboard "Open" button and when returning to the page mid-research).
async function showResearchSession(sid) {
  const body = document.getElementById("copilotBody");
  if (!body) return;
  body.style.justifyContent = "flex-start";
  body.style.alignItems = "stretch";
  body.innerHTML = `<div class="conv-thread" style="display:flex;flex-direction:column;gap:10px;width:100%;max-width:760px;margin:0 auto"></div>`;
  const thread = body.querySelector(".conv-thread");
  const query = (window._researchSessions[sid] && window._researchSessions[sid].query)
    || sessionStorage.getItem("copilotResearchQuery") || "Research";
  const { reportEl, addStage } = _buildResearchPanel(thread, query, body);
  sessionStorage.setItem("copilotResearchSid", sid);
  sessionStorage.setItem("copilotLastView", "research");
  await _attachResearchStream(sid, addStage, reportEl, body);
}

// Does a "research ..." message also ask for a FOLLOW-ON action (open an app,
// type/save the result, click, email it…)? Such compound tasks must go to the
// agent loop (which can chain deep_research + computer use), not the
// research-only panel. Conservative on purpose so plain research isn't diverted
// ("research the history of open source" must NOT match).
function _hasFollowupAction(text) {
  const t = (text || "").toLowerCase();
  return (
    /\bthen\b\s+\w*\s*(open|save|type|write|put|paste|create|launch|go to|click|email|send|search|copy)/.test(t) ||
    /\b(open|launch|start)\s+(the\s+|my\s+)?(note|notes|notepad|notes app|word|excel|powerpoint|browser|chrome|edge|file explorer|explorer|terminal|calculator|settings|app)\b/.test(t) ||
    /\bsave\s+(it|them|this|that|the\s+(report|research|result|notes?|file))\b/.test(t) ||
    /\b(type|paste|write)\s+(it|them|this|that|the)\b/.test(t) ||
    /\bput\s+(it|them|this|that)\s+(in|into|on)\b/.test(t)
  );
}

// Small badge listing which specialists the lead agent used (multi-agent runs).
function _agentsBadge(data) {
  const agents = (data && data.orchestrated && Array.isArray(data.agents_used)) ? data.agents_used : [];
  if (!agents.length) return "";
  const seen = [];
  for (const a of agents) if (!seen.includes(a)) seen.push(a);
  const chips = seen.map(a =>
    `<span style="display:inline-flex;align-items:center;gap:3px;background:var(--accent-soft);color:var(--accent-hover);border-radius:999px;padding:2px 9px;font-size:11px;font-weight:600">
       ${escapeHtml(a.charAt(0).toUpperCase() + a.slice(1))}</span>`).join(" ");
  return `<div style="display:flex;align-items:center;flex-wrap:wrap;gap:6px;margin-bottom:8px;font-size:11.5px;color:var(--text-muted)">
            <span class="material-symbols-rounded" style="font-size:15px;color:var(--accent)">hub</span>
            <span style="font-weight:600">Lead agent coordinated:</span> ${chips}
          </div>`;
}

async function sendCopilot(text) {
  const atts = window._copilotAttachments || [];
  const trimmed = (text || "").trim();

  // Allow attachment-only sends. If there's neither text nor attachments, bail.
  if (!trimmed && !atts.length) return;
  hideResearchSuggestions();

  // If the user just dropped a file with no prompt, fall back to a sensible default.
  let effectiveText = trimmed;
  if (!trimmed && atts.length) {
    const hasImage = atts.some(a => a._isImage);
    const hasDoc = atts.some(a => !a._isImage);
    effectiveText = hasImage && hasDoc
      ? "What's in these? Summarise and tell me anything actionable."
      : hasImage
        ? "What's in this screenshot? Describe it and flag anything I should act on."
        : "Read this and give me a tight summary plus next steps.";
  }

  // Deep research opens its OWN new chat (so the current conversation is kept as
  // its own rail entry) and is saved + downloadable. Triggered by "Research ...".
  // BUT if the message is a COMPOUND task ("research X and then open Notes and
  // save it") we must NOT hijack it to the research-only panel — that would drop
  // the action. Send it to the agent loop, which has deep_research AND the
  // computer-use tools and can chain them.
  if (/^research\s+\S/i.test(effectiveText) && !atts.length && !_hasFollowupAction(effectiveText)) {
    document.getElementById("copilotInput").value = "";
    await runResearchAsNewChat(effectiveText);
    return;
  }

  const body = document.getElementById("copilotBody");
  body.style.justifyContent = "flex-start";
  body.style.alignItems = "stretch";
  if (!body.querySelector(".conv-thread")) {
    body.innerHTML = `<div class="conv-thread" style="display:flex;flex-direction:column;gap:10px;width:100%;max-width:760px;margin:0 auto"></div>`;
  }
  const thread = body.querySelector(".conv-thread");

  // User bubble with optional inline attachments. We keep the previews alive
  // for the chat lifetime — clicking 'New Chat' clears them via thread reload.
  const bubbleParts = [];
  if (trimmed) bubbleParts.push(`<div>${escapeHtml(trimmed)}</div>`);
  else if (atts.length) bubbleParts.push(`<div style="font-style:italic;opacity:.85">${atts.length === 1 ? "Sent an attachment" : `Sent ${atts.length} attachments`}</div>`);
  bubbleParts.push(_renderAttachmentChips(atts));

  thread.insertAdjacentHTML("beforeend",
    `<div class="msg user" style="max-width:80%">${bubbleParts.join("")}</div>`);
  document.getElementById("copilotInput").value = "";
  body.scrollTop = body.scrollHeight;

  // typing indicator (left side, italic, lower opacity)
  thread.insertAdjacentHTML("beforeend",
    `<div class="msg ai typing-bubble" id="typingBubble">… thinking</div>`);
  body.scrollTop = body.scrollHeight;

  // Normal chat → this becomes the view to restore on return (not research).
  sessionStorage.setItem("copilotLastView", "chat");

  // Build a history that includes any attached files as a system message
  const history = window._copilotHistory.slice(-20);
  if (atts.length) {
    const ctx = atts.map(a =>
      `ATTACHED FILE: ${a.filename} (${a.content_type || 'unknown'}, ${a.size_bytes} bytes)\n` +
      `--- begin extracted content ---\n${a.extracted_text}\n--- end ---`
    ).join("\n\n");
    history.push({
      role: "system",
      content: "The user has attached the following files for this turn. Read them and use them to answer:\n\n" + ctx,
    });
    // Clear the attachment row after sending
    const row = document.getElementById("attachmentRow");
    if (row) { row.innerHTML = ""; row.style.display = "none"; }
    window._copilotAttachments = [];
  }

  try {
    const resp = await fetch("/api/copilot/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({
        message: effectiveText,
        history,
      }),
    });
    const typing = document.getElementById("typingBubble");
    if (typing) typing.remove();

    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      thread.insertAdjacentHTML("beforeend",
        `<div class="msg ai" style="background:#FEE2E2;color:#7F1D1D;border-color:#FCA5A5">Error: ${escapeHtml(err.detail || ('HTTP ' + resp.status))}</div>`);
      return;
    }
    const data = await resp.json();
    const reply = (data.reply || "").trim() || "(no reply)";

    // Render the model's markdown (bold, links, lists, headings) — same safe
    // renderer the research panel uses, so chat formatting is consistent.
    // When the lead orchestrator fanned the goal out to specialists, show which
    // ones worked on it as a small badge above the answer.
    thread.insertAdjacentHTML("beforeend",
      `<div class="msg ai">${_agentsBadge(data)}${renderMarkdownSafe(reply)}</div>`);
    body.scrollTop = body.scrollHeight;

    // Persist turn so the LLM keeps context within this chat
    const persistedUser = atts.length
      ? `${effectiveText}\n\n[Attached: ${atts.map(a => a.filename).join(", ")}]`
      : effectiveText;
    window._copilotHistory.push({ role: "user", content: persistedUser });
    window._copilotHistory.push({ role: "assistant", content: reply });

    // Persist to backend thread (create one on first message)
    try {
      if (!window._copilotCurrentThreadId) {
        const created = await safeFetchJson("/api/threads", {
          method: "POST",
          body: { title: text.slice(0, 80) },
        });
        if (created && created.id) {
          window._copilotCurrentThreadId = created.id;
          sessionStorage.setItem("copilotThreadId", String(created.id));
        }
      }
      const tid = window._copilotCurrentThreadId;
      if (tid) {
        await safeFetchJson(`/api/threads/${tid}/messages`, {
          method: "POST",
          body: { role: "user", content: text },
        });
        await safeFetchJson(`/api/threads/${tid}/messages`, {
          method: "POST",
          body: { role: "assistant", content: reply },
        });
        refreshCopilotRail(tid);
      }
    } catch {/* non-fatal */}
  } catch (e) {
    const typing = document.getElementById("typingBubble");
    if (typing) typing.remove();
    thread.insertAdjacentHTML("beforeend",
      `<div class="msg ai" style="background:#FEE2E2;color:#7F1D1D">Connection failed: ${escapeHtml(e.message || String(e))}</div>`);
  }
}

// (New chat is wired inside initCopilot via newCopilotThread)

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;" }[c]));
}

/* =========================================================
   Logs — real audit log only, no synthetic events
   ========================================================= */
let logState = { autoscroll: true, filter: "All", rows: [], timer: null };

function _logType(r) {
  const ev = r.event_type || "";
  if (r.severity === "error" || ev.endsWith(".error") || ev.endsWith(".failed")) return "error";
  if (r.payload && r.payload.ok === false)              return "error";
  if (ev.includes("warn"))                              return "warn";
  if (ev.startsWith("auth.") || ev.endsWith(".login"))  return "info";
  return "success";
}

async function fetchRealLogs() {
  const data = await safeFetchJson("/api/logs?limit=200");
  if (!Array.isArray(data)) return [];
  return data.map(r => ({
    ts: r.created_at ? new Date(r.created_at).toLocaleTimeString([], {hour12:false}) + "." + (Date.parse(r.created_at) % 1000 + "").padStart(3,"0") : "",
    type: _logType(r),
    event_type: r.event_type,
    message: r.message,
    severity: r.severity,
    raw: r,
  }));
}

/* =========================================================
   Admin Console (super-admin only) — per-employee usage
   ========================================================= */
async function initAdmin() {
  const view = document.getElementById("view");

  async function load() {
    const [usage, emps] = await Promise.all([
      safeFetchJson("/api/admin/usage?days=7"),
      safeFetchJson("/api/admin/employees"),
    ]);
    if (!usage || !emps) {
      const rows = document.getElementById("adminEmpRows");
      if (rows) rows.innerHTML = `<div style="padding:40px;text-align:center;color:var(--text-muted)">
        You need the <b>super_admin</b> role to view this page.</div>`;
      return;
    }
    renderKpis(usage.totals || {});
    renderTrend(usage.trend || []);
    window._adminEmployees = emps.employees || [];
    renderEmployees(window._adminEmployees);
    const t = document.getElementById("adminTenant");
    if (t) t.textContent = usage.tenant_id || "—";
    const g = document.getElementById("adminGenerated");
    if (g) g.textContent = "updated " + new Date().toLocaleTimeString();
  }

  function renderKpis(tot) {
    const el = document.getElementById("adminKpis");
    if (!el) return;
    const cards = [
      { label: "EMPLOYEES", value: tot.employees ?? "—" },
      { label: "ACTIVE TODAY", value: tot.active_today ?? "—" },
      { label: "ACTIVE THIS WEEK", value: tot.active_week ?? "—" },
      { label: "TOTAL CHATS", value: tot.chats ?? "—" },
      { label: "TOKENS USED", value: (tot.tokens ?? tot.est_tokens) != null ? fmtNum(tot.tokens ?? tot.est_tokens) : "—" },
    ];
    el.innerHTML = cards.map(c => `
      <div class="kpi">
        <div class="kpi-label">${c.label}</div>
        <div class="kpi-value">${c.value}</div>
      </div>`).join("");
  }

  function renderTrend(trend) {
    const el = document.getElementById("adminTrend");
    if (!el) return;
    if (!trend.length) { el.innerHTML = `<span style="color:var(--text-muted);font-size:13px">No activity yet.</span>`; return; }
    const max = Math.max(1, ...trend.map(d => d.events));
    el.innerHTML = trend.map(d => {
      const h = Math.round((d.events / max) * 64) + 2;
      const day = new Date(d.date + "T00:00:00").toLocaleDateString(undefined, { weekday: "short" });
      return `<div style="flex:1;display:flex;flex-direction:column;align-items:center;gap:4px" title="${d.date}: ${d.events} events (${d.chats} chats)">
        <div style="width:100%;max-width:46px;height:${h}px;background:var(--accent);border-radius:5px 5px 0 0;opacity:.85"></div>
        <div style="font-size:11px;color:var(--text-muted)">${day}</div>
        <div style="font-size:11px;color:var(--text-strong);font-weight:600">${d.events}</div>
      </div>`;
    }).join("");
  }

  function renderEmployees(list) {
    const rows = document.getElementById("adminEmpRows");
    if (!rows) return;
    if (!list.length) { rows.innerHTML = `<div style="padding:30px;text-align:center;color:var(--text-muted)">No employees yet.</div>`; return; }
    rows.innerHTML = list.map(e => {
      const initials = (e.full_name || e.email || "?").split(" ").map(p => p[0]).join("").slice(0, 2).toUpperCase();
      const topTool = (e.tools_used && e.tools_used.length) ? e.tools_used.slice(0, 2).join(", ") : "—";
      const last = e.last_active ? timeAgo(e.last_active) : "never";
      const toks = (e.tokens ?? e.est_tokens);
      return `
      <div class="admin-emp-row" style="display:grid;grid-template-columns:1.6fr 1fr 80px 80px 96px 1.1fr 140px;gap:12px;align-items:center;padding:11px 16px;border-bottom:1px solid var(--border);font-size:13px">
        <div style="display:flex;align-items:center;gap:10px;min-width:0">
          <div class="avatar" style="width:30px;height:30px;font-size:12px;flex:none">${initials}</div>
          <div style="min-width:0">
            <div style="font-weight:600;color:var(--text-strong);white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${escapeHtml(e.full_name || e.email)}</div>
            <div style="color:var(--text-muted);font-size:11.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${escapeHtml(e.email)}</div>
          </div>
        </div>
        <div>${(e.roles || []).map(r => `<span class="pill pill-teal" style="font-size:10.5px;margin:1px">${escapeHtml(r)}</span>`).join("")}</div>
        <div style="text-align:right;font-weight:600;color:var(--text-strong)">${e.chats}</div>
        <div style="text-align:right;color:var(--text-strong)">${e.actions}</div>
        <div style="text-align:right;color:var(--text-strong)">${toks != null ? fmtNum(toks) : "—"}</div>
        <div style="color:var(--text-muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${escapeHtml(topTool)}</div>
        <div style="color:var(--text-muted)">${last}</div>
      </div>`;
    }).join("");
  }

  document.getElementById("adminRefreshBtn")?.addEventListener("click", load);
  const search = document.getElementById("adminEmpSearch");
  if (search) search.addEventListener("input", () => {
    const q = search.value.trim().toLowerCase();
    const filtered = (window._adminEmployees || []).filter(e =>
      !q || (e.full_name || "").toLowerCase().includes(q) || (e.email || "").toLowerCase().includes(q));
    renderEmployees(filtered);
  });

  await load();
}

function fmtNum(n) {
  n = Number(n) || 0;
  if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
  if (n >= 1e3) return (n / 1e3).toFixed(1) + "k";
  return String(n);
}

function timeAgo(iso) {
  const d = new Date(iso); const s = (Date.now() - d.getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return Math.floor(s / 60) + "m ago";
  if (s < 86400) return Math.floor(s / 3600) + "h ago";
  if (s < 604800) return Math.floor(s / 86400) + "d ago";
  return d.toLocaleDateString();
}

async function initLogs() {
  const rows = document.getElementById("logRows");
  if (!rows) return;

  logState.rows = await fetchRealLogs();
  renderLogs();

  // Filter pills
  document.querySelectorAll(".pill-filter[data-log]").forEach(p => {
    p.addEventListener("click", () => {
      document.querySelectorAll(".pill-filter[data-log]").forEach(x => x.classList.remove("active"));
      p.classList.add("active");
      logState.filter = p.dataset.log;
      renderLogs();
    });
  });

  // Auto-scroll switch
  document.getElementById("autoscrollSwitch")?.addEventListener("click", e => {
    logState.autoscroll = !logState.autoscroll;
    e.currentTarget.classList.toggle("on", logState.autoscroll);
  });
  document.getElementById("exportBtn")?.addEventListener("click", () => {
    const blob = new Blob([JSON.stringify(logState.rows.map(r => r.raw), null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = "myai-logs.json"; a.click();
    URL.revokeObjectURL(url);
  });

  // Poll the real audit log every 4 seconds
  if (logState.timer) clearInterval(logState.timer);
  logState.timer = setInterval(async () => {
    logState.rows = await fetchRealLogs();
    renderLogs(true);
  }, 4000);
}

function renderLogs(streaming = false) {
  const rows = document.getElementById("logRows");
  if (!rows) return;
  const filtered = logState.rows.filter(r => {
    if (logState.filter === "All") return true;
    if (logState.filter === "Tool Calls") return /(gmail|outlook|calendar|drive)/i.test(r.event_type || "");
    if (logState.filter === "LLM Queries") return /copilot\.chat/i.test(r.event_type || "");
    if (logState.filter === "Errors")     return r.type === "error";
    if (logState.filter === "Auth Events")return /^auth\./i.test(r.event_type || "");
    return true;
  });

  if (!filtered.length) {
    rows.innerHTML = `<div style="padding:40px;text-align:center;color:var(--text-muted)">
      No activity yet${logState.filter === "All" ? "" : ` matching "${logState.filter}"`}. Use the copilot or inbox to generate some.
    </div>`;
  } else {
    rows.innerHTML = filtered.map((r, i) => {
      const b = TYPE_BADGES[r.type] || TYPE_BADGES.info;
      return `
        <div class="log-row ${streaming && i === 0 ? "fade-in" : ""}" style="grid-template-columns:150px 185px 1fr 40px">
          <div class="ts">${escapeHtml(r.ts)}</div>
          <div class="tp"><span class="pill ${b.cls}"><span class="material-symbols-rounded" style="font-size:13px">${b.icon}</span>${escapeHtml(r.event_type || r.type)}</span></div>
          <div class="ev">${escapeHtml(r.message || "")}</div>
          <div class="stat"><span class="material-symbols-rounded" style="font-size:18px;color:${r.type === "error" ? "var(--red)" : "var(--green)"}">${r.type === "error" ? "error" : "check_circle"}</span></div>
        </div>
      `;
    }).join("");
  }

  // Side stats — real numbers
  const total = logState.rows.length || 1;
  const ok = logState.rows.filter(r => r.type !== "error").length;
  const errs = logState.rows.filter(r => r.type === "error").length;
  // Events per minute over last 5 min
  const now = Date.now();
  const last5 = logState.rows.filter(r => r.raw?.created_at && (now - Date.parse(r.raw.created_at)) < 5 * 60_000);
  const epm = Math.round(last5.length / 5);
  document.getElementById("statEpm")  && (document.getElementById("statEpm").textContent = String(epm));
  document.getElementById("statSuccess") && (document.getElementById("statSuccess").textContent = Math.round((ok / total) * 100) + "%");
  document.getElementById("statErrors")  && (document.getElementById("statErrors").textContent  = String(errs));
  // Top event_type
  const counts = {};
  logState.rows.forEach(r => { counts[r.event_type || "?"] = (counts[r.event_type || "?"] || 0) + 1; });
  const top = Object.entries(counts).sort((a,b) => b[1] - a[1])[0]?.[0] || "—";
  document.getElementById("statTopTool") && (document.getElementById("statTopTool").textContent = top);

  if (logState.autoscroll) rows.scrollTop = 0;
}

/* =========================================================
   Connectors / Settings (stubs)
   ========================================================= */
function initConnectors() {
  // The connectors.html fragment has its own inline script that calls /api/connectors
  // and renders into #conn-grid. We no longer render mock data here.
  const grid = document.getElementById("connectorGrid");
  if (!grid) return;
  // legacy mock path (only fires if some old fragment still uses #connectorGrid)
  const items = [
    { name: "Salesforce CRM", status: "Connected", ago: "synced 2m ago" },
    { name: "Twilio Voice",   status: "Connected", ago: "synced 6m ago" },
    { name: "Stripe",         status: "Connected", ago: "synced 11m ago" },
    { name: "Zendesk",        status: "Disconnected", ago: "—" },
    { name: "Slack",          status: "Connected", ago: "synced 1m ago" },
    { name: "Confluence",     status: "Pending",   ago: "auth required" },
  ];
  grid.innerHTML = items.map(c => `
    <div class="connector-card">
      <h4>${c.name}</h4>
      <div class="meta">${c.ago}</div>
      <div style="display:flex;justify-content:space-between;align-items:center">
        <span class="pill ${c.status === "Connected" ? "pill-green" : c.status === "Pending" ? "pill-orange" : "pill-red"}">${c.status}</span>
        <button class="btn btn-sm">Manage</button>
      </div>
    </div>
  `).join("");
}

function initSettings() { /* static markup is fine */ }

/* Collapsible sidebar + collapsible nav sections */
(function () {
  const app = document.querySelector(".app");
  const btn = document.getElementById("sbCollapse");
  if (app && btn) {
    if (localStorage.getItem("sbCollapsed") === "1") app.classList.add("sb-collapsed");
    btn.addEventListener("click", () => {
      app.classList.toggle("sb-collapsed");
      localStorage.setItem("sbCollapsed", app.classList.contains("sb-collapsed") ? "1" : "0");
    });
  }
  const nav = document.getElementById("nav");
  if (nav) {
    nav.querySelectorAll(".nav-label").forEach((lbl) => {
      const chev = document.createElement("span");
      chev.className = "material-symbols-rounded nl-chev";
      chev.textContent = "expand_more";
      lbl.appendChild(chev);
      lbl.addEventListener("click", () => {
        lbl.classList.toggle("collapsed");
        const hide = lbl.classList.contains("collapsed");
        let el = lbl.nextElementSibling;
        while (el && !el.classList.contains("nav-label")) {
          if (el.tagName === "A") el.classList.toggle("nav-hidden", hide);
          el = el.nextElementSibling;
        }
      });
    });
  }
})();

/* =========================================================
   Documents slide-over editor (global). Opens from the chat
   icon-rail and can be opened programmatically when the agent
   creates/opens a document. Uses the Odysseus documents backend
   via the /api/oui/* proxy (window.oui).
   ========================================================= */
(function () {
  const drawer = document.getElementById("docsDrawer");
  if (!drawer) return;
  const backdrop = document.getElementById("docsBackdrop");
  const titleEl = document.getElementById("docsTitle");
  const bodyEl = document.getElementById("docsBody");
  const langEl = document.getElementById("docsLang");
  const tabsEl = document.getElementById("docsTabs");
  const countEl = document.getElementById("docsCount");
  const libPanel = document.getElementById("docsLibPanel");
  const libList = document.getElementById("docsLibList");
  const libSearch = document.getElementById("docsLibSearch");
  const moreMenu = document.getElementById("docsMoreMenu");
  const aiMenu = document.getElementById("docsAiMenu");
  const esc = window.escapeHtml, toast = window.toast || console.log;
  const LANGS = ["markdown","plaintext","python","javascript","typescript","json","html","css","sql","bash","yaml","java","go","rust","cpp"];
  let tabs = [];      // {id, title, lang, content, saved}
  let active = null;  // index into tabs

  langEl.innerHTML = LANGS.map(l => `<option value="${l}">${l}</option>`).join("");

  const cur = () => (active != null ? tabs[active] : null);
  function syncFromEditor() { const c = cur(); if (c) { c.title = titleEl.value; c.content = bodyEl.value; c.lang = langEl.value; } }
  const wordCount = (s) => (String(s || "").trim().match(/\S+/g) || []).length;
  const updateCount = () => { countEl.textContent = wordCount(bodyEl.value) + " words"; };
  const rel = (d) => { if (!d) return ""; const t = new Date(d); if (isNaN(t)) return ""; const s = (Date.now() - t) / 1000; if (s < 60) return "just now"; if (s < 3600) return Math.floor(s / 60) + "m ago"; if (s < 86400) return Math.floor(s / 3600) + "h ago"; return t.toLocaleDateString(); };

  function renderTabs() {
    tabsEl.innerHTML = tabs.map((t, i) => `<div class="docs-tab ${i === active ? "active" : ""}" data-i="${i}"><span class="material-symbols-rounded" style="font-size:14px">${t.lang === "markdown" ? "description" : "code"}</span><span class="docs-tab-t">${esc(t.title || "Untitled")}${t.saved ? "" : " •"}</span><span class="docs-tab-x" data-x="${i}">×</span></div>`).join("");
    tabsEl.querySelectorAll(".docs-tab").forEach(el => el.onclick = (e) => { if (e.target.dataset.x !== undefined) return; switchTo(+el.dataset.i); });
    tabsEl.querySelectorAll(".docs-tab-x").forEach(el => el.onclick = (e) => { e.stopPropagation(); closeTab(+el.dataset.x); });
  }
  function showTab() { const c = cur(); if (!c) { titleEl.value = ""; bodyEl.value = ""; updateCount(); return; } titleEl.value = c.title || ""; bodyEl.value = c.content || ""; langEl.value = c.lang || "markdown"; updateCount(); }
  function switchTo(i) { syncFromEditor(); active = i; renderTabs(); showTab(); }
  function closeTab(i) { tabs.splice(i, 1); if (!tabs.length) active = null; else if (active >= tabs.length) active = tabs.length - 1; else if (active > i) active--; renderTabs(); showTab(); }
  function addTab(t) { syncFromEditor(); tabs.push(t); active = tabs.length - 1; renderTabs(); showTab(); }

  async function openDoc(id) {
    const ex = tabs.findIndex(t => t.id === id); if (ex >= 0) { switchTo(ex); return; }
    const doc = await window.oui.get("/document/" + id);
    if (!doc) { toast("Could not open document", "error"); return; }
    addTab({ id: doc.id, title: doc.title || "Untitled", lang: doc.language || "markdown", content: doc.current_content || doc.content || "", saved: true });
  }
  function newDoc() { addTab({ id: null, title: "", lang: "markdown", content: "", saved: false }); titleEl.focus(); }

  async function save() {
    syncFromEditor(); const c = cur(); if (!c) return;
    const title = (c.title || "").trim() || "Untitled", content = c.content || "", lang = c.lang || "markdown";
    if (!c.id) {
      const r = await window.oui.postJson("/document", { title, language: lang, content });
      if (r.ok && r.json) { c.id = r.json.id; c.saved = true; toast("Saved", "success"); renderTabs(); } else toast("Save failed", "error");
    } else {
      const r = await window.oui.postJson("/document/" + c.id, { content }, "PUT");
      if (r.ok) { await window.oui.postJson("/document/" + c.id, { title, language: lang }, "PATCH"); c.saved = true; toast("Saved", "success"); renderTabs(); }
      else toast("Save failed", "error");
    }
  }

  async function loadLib(q) {
    const d = await window.oui.get("/documents/library?limit=100" + (q ? "&search=" + encodeURIComponent(q) : ""));
    let docs = (d && d.documents) || [];
    if (q) { const ql = q.toLowerCase(); docs = docs.filter(x => (x.title || "").toLowerCase().includes(ql)); }
    libList.innerHTML = docs.length ? docs.map(x => `<div class="docs-lib-card" data-id="${x.id}"><div class="docs-lib-t">${esc(x.title || "Untitled")}</div><div class="docs-lib-m">${esc(x.language || "markdown")} · ${esc(rel(x.updated_at || x.created_at))}</div></div>`).join("") : '<div class="ws-empty" style="padding:20px">No documents yet.</div>';
    libList.querySelectorAll(".docs-lib-card").forEach(el => el.onclick = () => { openDoc(el.dataset.id); libPanel.hidden = true; });
  }

  function wrap(before, after) { const s = bodyEl.selectionStart, e = bodyEl.selectionEnd, v = bodyEl.value, sel = v.slice(s, e) || "text"; bodyEl.value = v.slice(0, s) + before + sel + after + v.slice(e); bodyEl.focus(); bodyEl.selectionStart = s + before.length; bodyEl.selectionEnd = s + before.length + sel.length; syncFromEditor(); updateCount(); }
  const FMT = { bold: () => wrap("**", "**"), italic: () => wrap("*", "*"), code: () => wrap("`", "`"), h2: () => wrap("## ", ""), ul: () => wrap("- ", ""), link: () => wrap("[", "](https://)") };

  const AI_PROMPTS = {
    rewrite: t => `Rewrite the following text to be clearer and more polished. Return ONLY the rewritten text, no preamble:\n\n${t}`,
    improve: t => `Improve the grammar, flow and clarity of this text. Return ONLY the improved text:\n\n${t}`,
    shorten: t => `Make this text more concise while keeping the meaning. Return ONLY the shortened text:\n\n${t}`,
    longer: t => `Expand this text with more detail and explanation. Return ONLY the expanded text:\n\n${t}`,
    continue: t => `Continue writing naturally from where this text ends. Return ONLY the continuation:\n\n${t}`,
    summarize: t => `Summarize the following document in a few concise bullet points:\n\n${t}`,
    explain: t => `Explain the following clearly and simply:\n\n${t}`,
  };
  async function aiAction(kind) {
    const s = bodyEl.selectionStart, e = bodyEl.selectionEnd, v = bodyEl.value, sel = v.slice(s, e);
    const target = (kind === "summarize" || kind === "continue") ? v : (sel || v);
    if (!target.trim()) { toast("Nothing to work on", "info"); return; }
    toast("AI working…", "info");
    let out = "";
    try {
      const r = await fetch("/api/copilot/chat", { method: "POST", headers: { "Content-Type": "application/json" }, credentials: "include", body: JSON.stringify({ message: AI_PROMPTS[kind](target) }) });
      const j = await r.json(); out = (j.reply || j.response || "").trim();
    } catch (_) { }
    if (!out) { toast("AI failed (free model busy — try again)", "error"); return; }
    if (kind === "continue") bodyEl.value = v + (v.endsWith("\n") ? "" : "\n") + out;
    else if (kind === "summarize") bodyEl.value = "## Summary\n" + out + "\n\n" + v;
    else if (kind === "explain") bodyEl.value = v.slice(0, e) + "\n\n> " + out.replace(/\n/g, "\n> ") + "\n" + v.slice(e);
    else if (sel) bodyEl.value = v.slice(0, s) + out + v.slice(e);
    else bodyEl.value = out;
    syncFromEditor(); updateCount(); toast("AI done", "success");
  }

  async function reopenCurrent() { const c = cur(); if (!c || !c.id) return; const doc = await window.oui.get("/document/" + c.id); if (doc) { c.content = doc.current_content || doc.content || ""; c.title = doc.title || c.title; showTab(); } }
  async function showVersions() {
    const c = cur(); if (!c || !c.id) { toast("Save the document first", "info"); return; }
    const d = await window.oui.get("/document/" + c.id + "/versions");
    const vs = (d && (d.versions || d)) || [];
    const rows = (Array.isArray(vs) ? vs : []).map(v => { const n = v.version ?? v.version_number ?? v.num; return `<div class="docs-ver"><div><b>v${n}</b> <span style="color:var(--text-muted);font-size:11px">${esc(v.source || "")} · ${esc(rel(v.created_at))}</span></div><button class="btn btn-sm" data-rv="${n}">Restore</button></div>`; }).join("") || '<div class="ws-empty">No versions yet.</div>';
    window.openModal('<div class="modal-head"><h3>Version history</h3><button class="icon-btn" data-close><span class="material-symbols-rounded">close</span></button></div><div class="modal-body">' + rows + "</div>");
    document.querySelectorAll("[data-rv]").forEach(b => b.onclick = async () => { const r = await window.oui.postJson("/document/" + c.id + "/restore/" + b.dataset.rv, {}); if (r.ok) { toast("Restored v" + b.dataset.rv, "success"); window.closeModal(); await reopenCurrent(); } else toast("Restore failed", "error"); });
  }

  function openDrawer() { drawer.classList.add("open"); backdrop.classList.add("open"); drawer.setAttribute("aria-hidden", "false"); }
  function close() { drawer.classList.remove("open"); backdrop.classList.remove("open"); drawer.setAttribute("aria-hidden", "true"); libPanel.hidden = true; moreMenu.hidden = true; aiMenu.hidden = true; }
  window.openDocsDrawer = async function (docId) { openDrawer(); if (docId) await openDoc(docId); else if (!tabs.length) newDoc(); else { renderTabs(); showTab(); } };

  document.getElementById("docsClose").onclick = close;
  backdrop.onclick = close;
  document.getElementById("docsNew").onclick = newDoc;
  document.getElementById("docsSave").onclick = save;
  titleEl.addEventListener("input", () => { const c = cur(); if (c) { c.title = titleEl.value; c.saved = false; } });
  langEl.addEventListener("change", () => { const c = cur(); if (c) { c.lang = langEl.value; c.saved = false; } });
  bodyEl.addEventListener("input", () => { const c = cur(); if (c) c.saved = false; updateCount(); });
  bodyEl.addEventListener("keydown", (e) => { if ((e.ctrlKey || e.metaKey) && e.key === "s") { e.preventDefault(); save(); } });
  document.getElementById("docsToolbar").querySelectorAll("[data-fmt]").forEach(b => b.onclick = () => FMT[b.dataset.fmt] && FMT[b.dataset.fmt]());
  document.getElementById("docsAiBtn").onclick = (e) => { e.stopPropagation(); aiMenu.hidden = !aiMenu.hidden; moreMenu.hidden = true; };
  aiMenu.querySelectorAll("[data-ai]").forEach(b => b.onclick = () => { aiMenu.hidden = true; aiAction(b.dataset.ai); });
  document.getElementById("docsLib").onclick = () => { libPanel.hidden = !libPanel.hidden; if (!libPanel.hidden) { loadLib(""); libSearch.value = ""; libSearch.focus(); } };
  document.getElementById("docsLibClose").onclick = () => { libPanel.hidden = true; };
  let lt; libSearch.oninput = () => { clearTimeout(lt); lt = setTimeout(() => loadLib(libSearch.value.trim()), 250); };
  document.getElementById("docsMore").onclick = (e) => { e.stopPropagation(); moreMenu.hidden = !moreMenu.hidden; aiMenu.hidden = true; };
  moreMenu.querySelectorAll("[data-more]").forEach(b => b.onclick = async () => {
    moreMenu.hidden = true; const a = b.dataset.more, c = cur();
    if (a === "versions") showVersions();
    else if (a === "duplicate") { if (!c) return; syncFromEditor(); addTab({ id: null, title: (c.title || "Untitled") + " copy", lang: c.lang, content: c.content, saved: false }); toast("Duplicated (unsaved)", "info"); }
    else if (a === "export") { if (!c) return; syncFromEditor(); const ext = c.lang === "markdown" ? "md" : (c.lang === "plaintext" ? "txt" : c.lang); const blob = new Blob([c.content || ""], { type: "text/plain" }); const u = URL.createObjectURL(blob); const link = document.createElement("a"); link.href = u; link.download = (c.title || "document").replace(/[^\w.-]+/g, "_") + "." + ext; link.click(); URL.revokeObjectURL(u); }
    else if (a === "delete") { if (!c) return; if (c.id) { await window.oui.del("/document/" + c.id); toast("Deleted", "info"); } closeTab(tabs.indexOf(c)); }
  });
  document.addEventListener("click", (e) => { if (!e.target.closest("#docsMore") && !e.target.closest("#docsMoreMenu")) moreMenu.hidden = true; if (!e.target.closest("#docsAiBtn") && !e.target.closest("#docsAiMenu")) aiMenu.hidden = true; });

  // ---- Preview / syntax highlighting (markdown via marked, code via hljs) ----
  let previewOn = false, _docLibsP = null;
  const previewEl = document.getElementById("docsPreview");
  function _loadScript(src) { return new Promise(r => { const s = document.createElement("script"); s.src = src; s.onload = r; s.onerror = r; document.head.appendChild(s); }); }
  function ensureDocLibs() {
    if (_docLibsP) return _docLibsP;
    const jobs = [];
    if (!window.marked) jobs.push(_loadScript("https://cdn.jsdelivr.net/npm/marked/marked.min.js"));
    if (!window.DOMPurify) jobs.push(_loadScript("https://cdn.jsdelivr.net/npm/dompurify@3/dist/purify.min.js"));
    if (!window.hljs) {
      jobs.push(_loadScript("https://cdn.jsdelivr.net/gh/highlightjs/cdn-release/build/highlight.min.js"));
      const l = document.createElement("link"); l.rel = "stylesheet"; l.href = "https://cdn.jsdelivr.net/gh/highlightjs/cdn-release/build/styles/github.min.css"; document.head.appendChild(l);
    }
    _docLibsP = Promise.all(jobs).catch(() => {});
    return _docLibsP;
  }
  async function renderPreview() {
    const c = cur(); if (!c) { previewEl.innerHTML = ""; return; }
    await ensureDocLibs();
    const content = bodyEl.value || "";
    if ((c.lang || "markdown") === "markdown" && window.marked) {
      const html = window.marked.parse(content);
      previewEl.innerHTML = window.DOMPurify ? window.DOMPurify.sanitize(html) : html;
      if (window.hljs) previewEl.querySelectorAll("pre code").forEach(b => { try { window.hljs.highlightElement(b); } catch (_) {} });
    } else {
      previewEl.innerHTML = "";
      const pre = document.createElement("pre"), code = document.createElement("code");
      code.className = "language-" + (c.lang || "plaintext"); code.textContent = content;
      pre.appendChild(code); previewEl.appendChild(pre);
      if (window.hljs) { try { window.hljs.highlightElement(code); } catch (_) {} }
    }
  }
  function setPreview(on) {
    previewOn = on; previewEl.hidden = !on; bodyEl.style.display = on ? "none" : "";
    const btn = document.getElementById("docsPreviewBtn");
    btn.classList.toggle("active", on);
    btn.innerHTML = on ? '<span class="material-symbols-rounded">edit</span>Edit' : '<span class="material-symbols-rounded">visibility</span>Preview';
    if (on) renderPreview();
  }
  document.getElementById("docsPreviewBtn").onclick = () => { syncFromEditor(); setPreview(!previewOn); };
  // Re-render the preview when switching tabs while it's open.
  const _origShowTab = showTab;
  showTab = function () { _origShowTab(); if (previewOn) renderPreview(); };
})();

/* =========================================================
   Notes side panel (opens beside the chat, Odysseus-style).
   Note / To-do / Draw composer + colors + tags + reminders.
   ========================================================= */
(function () {
  const drawer = document.getElementById("notesDrawer");
  if (!drawer) return;
  const el = (id) => document.getElementById(id);
  const COLORS = { "": "var(--bg-elev)", "#FEF9C3": "#FEF9C3", "#D1FAE5": "#D1FAE5", "#DBEAFE": "#DBEAFE", "#FEE2E2": "#FEE2E2", "#EDE9FE": "#EDE9FE", "#FCE7F3": "#FCE7F3", "#FEF3C7": "#FEF3C7" };
  const PENS = ["#111827", "#EF4444", "#0891B2", "#10B981", "#F59E0B"];
  let filter = "all", ctype = "note", ccolor = "", citems = [], penColor = "#111827", bound = false, pendingImg = null;
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  window.openNotesDrawer = function () { drawer.classList.add("open"); drawer.setAttribute("aria-hidden", "false"); document.body.classList.add("notes-open"); if (!bound) wire(); load(); };
  function close() { drawer.classList.remove("open"); drawer.setAttribute("aria-hidden", "true"); document.body.classList.remove("notes-open"); }

  function setPreview() {
    const p = el("ndImgPreview");
    if (pendingImg) { p.style.display = "block"; p.innerHTML = '<img src="' + pendingImg + '" style="width:100%;border-radius:var(--r-sm)"><button class="icon-btn" id="ndImgRm" style="position:absolute;top:4px;right:4px;background:rgba(0,0,0,.5);color:#fff"><span class="material-symbols-rounded">close</span></button>'; el("ndImgRm").onclick = () => { pendingImg = null; setPreview(); }; }
    else { p.style.display = "none"; p.innerHTML = ""; }
  }
  function resetComposer() {
    el("ndFull").style.display = "none"; el("ndCollapsed").style.display = "flex";
    el("ndTitle").value = ""; el("ndContent").value = ""; el("ndTags").value = ""; el("ndDue").value = ""; citems = []; renderItems(); clearCanvas();
    pendingImg = null; setPreview();
  }
  function renderItems() {
    const e = el("ndTodoBody");
    e.innerHTML = citems.map((it, i) => '<div class="nd-todo-item"><input type="checkbox" data-i="' + i + '"><input type="text" data-i="' + i + '" value="' + esc(it.text) + '" placeholder="Item…"></div>').join("") + '<button class="btn btn-sm" id="ndAddItem" style="margin-top:4px"><span class="material-symbols-rounded">add</span>Item</button>';
    const add = el("ndAddItem"); if (add) add.onclick = () => { citems.push({ text: "", done: false }); renderItems(); };
    e.querySelectorAll("input[type=text]").forEach(inp => inp.oninput = () => { citems[+inp.dataset.i].text = inp.value; });
  }
  function initCanvas() {
    const cv = el("ndCanvas"); const ctx = cv.getContext("2d"); clearCanvas();
    el("ndPen").innerHTML = PENS.map(c => '<span class="nd-sw' + (c === penColor ? " sel" : "") + '" data-c="' + c + '" style="background:' + c + '"></span>').join("");
    el("ndPen").querySelectorAll(".nd-sw").forEach(s => s.onclick = () => { penColor = s.dataset.c; el("ndPen").querySelectorAll(".nd-sw").forEach(x => x.classList.remove("sel")); s.classList.add("sel"); });
    let drawing = false;
    const P = (ev) => { const r = cv.getBoundingClientRect(); const t = ev.touches ? ev.touches[0] : ev; return { x: (t.clientX - r.left) * (cv.width / r.width), y: (t.clientY - r.top) * (cv.height / r.height) }; };
    const start = (ev) => { drawing = true; const p = P(ev); ctx.beginPath(); ctx.moveTo(p.x, p.y); ev.preventDefault(); };
    const move = (ev) => { if (!drawing) return; const p = P(ev); ctx.lineTo(p.x, p.y); ctx.strokeStyle = penColor; ctx.lineWidth = +el("ndSize").value; ctx.lineCap = "round"; ctx.lineJoin = "round"; ctx.stroke(); ev.preventDefault(); };
    const end = () => { drawing = false; };
    cv.onmousedown = start; cv.onmousemove = move; window.addEventListener("mouseup", end);
    cv.ontouchstart = start; cv.ontouchmove = move; cv.ontouchend = end;
    el("ndClear").onclick = clearCanvas;
  }
  function clearCanvas() { const cv = el("ndCanvas"); if (!cv) return; const c = cv.getContext("2d"); c.clearRect(0, 0, cv.width, cv.height); c.fillStyle = "#fff"; c.fillRect(0, 0, cv.width, cv.height); }

  async function save() {
    const title = el("ndTitle").value, content = el("ndContent").value, due = el("ndDue").value, tags = el("ndTags").value.trim();
    const body = { title, color: ccolor };
    if (ctype === "todo") { const items = citems.filter(x => x.text.trim()); body.note_type = "checklist"; if (items.length) body.items = items; }
    else if (ctype === "draw") { body.note_type = "note"; body.image_url = el("ndCanvas").toDataURL("image/png"); body.content = content; }
    else { body.note_type = "note"; body.content = content; if (pendingImg) body.image_url = pendingImg; }
    if (due) body.due_date = new Date(due).toISOString();
    if (tags) body.label = tags.replace(/^#/, "");
    const r = await window.oui.postJson("/notes", body);
    if (r.ok) { toast("Saved", "success"); resetComposer(); load(); } else toast("Save failed", "error");
  }

  async function load() {
    const d = await window.oui.get("/notes"); let arr = (d && d.notes) || [];
    if (filter === "archived") arr = arr.filter(n => n.archived); else arr = arr.filter(n => !n.archived);
    if (filter === "reminders") arr = arr.filter(n => n.due_date);
    arr.sort((a, b) => (b.pinned ? 1 : 0) - (a.pinned ? 1 : 0));
    const board = el("ndBoard");
    if (!arr.length) { board.innerHTML = '<div class="ws-empty">No notes here.</div>'; return; }
    board.innerHTML = "";
    for (const n of arr) {
      const card = document.createElement("div"); card.className = "nd-card"; card.style.background = COLORS[n.color] || "var(--bg-elev)";
      let chk = ""; if (Array.isArray(n.items) && n.items.length) chk = n.items.map((it, i) => '<label style="display:flex;gap:6px;align-items:flex-start;font-size:12.5px;margin-top:4px"><input type="checkbox" data-i="' + i + '" ' + (it.done ? "checked" : "") + '><span style="' + (it.done ? "text-decoration:line-through;color:var(--text-soft)" : "") + '">' + esc(it.text) + "</span></label>").join("");
      card.innerHTML = (n.pinned ? '<span class="material-symbols-rounded" style="position:absolute;top:6px;left:8px;color:var(--accent);font-size:15px">push_pin</span>' : "") +
        '<div class="nd-acts"><button class="icon-btn" data-a="pin"><span class="material-symbols-rounded" style="color:' + (n.pinned ? "var(--accent)" : "var(--text-soft)") + '">push_pin</span></button>' +
        '<button class="icon-btn" data-a="arch"><span class="material-symbols-rounded">' + (n.archived ? "unarchive" : "archive") + '</span></button>' +
        '<button class="icon-btn" data-a="del"><span class="material-symbols-rounded">delete</span></button></div>' +
        (n.title ? '<div class="nd-ttl">' + esc(n.title) + "</div>" : "") + (n.content ? '<div class="nd-bd">' + esc(n.content) + "</div>" : "") + chk +
        (n.image_url ? '<img src="' + esc(n.image_url) + '" alt="">' : "") +
        (n.label ? '<div style="margin-top:8px"><span class="ws-tag">#' + esc(n.label) + "</span></div>" : "") +
        (n.due_date ? '<div class="nd-due"><span class="material-symbols-rounded" style="font-size:13px">notifications</span>' + esc(String(n.due_date).slice(0, 16).replace("T", " ")) + "</div>" : "");
      card.querySelector('[data-a="del"]').onclick = async () => { await window.oui.del("/notes/" + n.id); load(); };
      card.querySelector('[data-a="pin"]').onclick = async () => { await window.oui.postForm("/notes/" + n.id + "/pin", { pinned: (!n.pinned) }); load(); };
      card.querySelector('[data-a="arch"]').onclick = async () => { await window.oui.postForm("/notes/" + n.id + "/archive", {}); load(); };
      card.querySelectorAll("input[type=checkbox]").forEach(cb => cb.onchange = async () => { await window.oui.postForm("/notes/" + n.id + "/items/" + cb.dataset.i + "/toggle", {}); });
      board.appendChild(card);
    }
  }

  function wire() {
    bound = true;
    el("ndClose").onclick = close;
    el("ndArchiveBtn").onclick = () => { filter = (filter === "archived") ? "all" : "archived"; el("ndChips").querySelectorAll(".nd-chip").forEach(c => c.classList.toggle("active", c.dataset.f === filter)); load(); };
    el("ndViewBtn").onclick = () => { el("ndBoard").classList.toggle("list"); el("ndViewBtn").querySelector(".material-symbols-rounded").textContent = el("ndBoard").classList.contains("list") ? "view_agenda" : "grid_view"; };
    el("ndChips").querySelectorAll(".nd-chip").forEach(c => c.onclick = () => { el("ndChips").querySelectorAll(".nd-chip").forEach(x => x.classList.remove("active")); c.classList.add("active"); filter = c.dataset.f; load(); });
    el("ndCollapsed").onclick = () => { el("ndCollapsed").style.display = "none"; el("ndFull").style.display = "flex"; el("ndTitle").focus(); };
    el("ndCancel").onclick = resetComposer;
    el("ndSave").onclick = save;
    el("ndImgBtn").onclick = () => el("ndFile").click();
    el("ndFile").onchange = (e) => { const f = e.target.files[0]; if (!f) return; const r = new FileReader(); r.onload = () => { pendingImg = r.result; setPreview(); }; r.readAsDataURL(f); e.target.value = ""; };
    el("ndSwatches").innerHTML = Object.keys(COLORS).map(c => '<div class="nd-sw' + (c === "" ? " sel" : "") + '" data-c="' + c + '" style="background:' + COLORS[c] + '"></div>').join("");
    el("ndSwatches").querySelectorAll(".nd-sw").forEach(s => s.onclick = () => { el("ndSwatches").querySelectorAll(".nd-sw").forEach(x => x.classList.remove("sel")); s.classList.add("sel"); ccolor = s.dataset.c; });
    drawer.querySelectorAll(".nd-typetoggle button").forEach(b => b.onclick = () => {
      drawer.querySelectorAll(".nd-typetoggle button").forEach(x => x.classList.remove("active")); b.classList.add("active"); ctype = b.dataset.t;
      el("ndNoteBody").style.display = ctype === "note" ? "" : "none";
      el("ndTodoBody").style.display = ctype === "todo" ? "" : "none";
      el("ndDrawBody").style.display = ctype === "draw" ? "" : "none";
      if (ctype === "todo" && !citems.length) { citems.push({ text: "", done: false }); renderItems(); }
      if (ctype === "draw") initCanvas();
    });
  }
})();
