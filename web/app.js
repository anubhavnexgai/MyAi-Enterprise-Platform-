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
};

const view = document.getElementById("view");
const nav = document.getElementById("nav");

/* ---------- Mock data ---------- */
const MOCK = {
  kpis: [
    { label: "REQUESTS TODAY", value: "20", foot: "+12% vs yesterday" },
    { label: "AVG RESOLUTION", value: "16.5m", foot: "-3.2m vs target" },
    { label: "SLA COMPLIANCE", value: "94%", foot: "12 within SLA" },
    { label: "AI AUTO-RESOLVE", value: "55%", foot: "11 closed by AI" },
    { label: "PENDING REVIEW", value: "9", foot: "Needs supervisor", tone: "orange" },
    { label: "ESCALATED TODAY", value: "4", foot: "2 high priority" },
    { label: "FRAUD ALERTS TODAY", value: "4", foot: "1 confirmed", tone: "red" },
    { label: "PROACTIVE ALERTS", value: "12", foot: "AI initiated" },
    { label: "CHURN RISK CUSTOMERS", value: "27", foot: "Tracking" },
    { label: "CUSTOMERS SAVED", value: "11", foot: "This week", tone: "green" },
  ],

  retention: {
    pending: 1,
    active: 3,
    wonWeek: 11,
    lostWeek: 2,
    saveRate: "84%",
    avgDiscount: "10%",
    avgLevels: "3.4",
    competitors: 3,
    escalations: 1,
  },

  negotiations: [
    {
      id: "neg-001",
      name: "Sarah Mitchell",
      level: 5,
      competitor: "MONZO",
      status: "NEEDS APPROVAL",
      product: "Premier Current Account",
      fee: "£25/mo",
      tenure: "54 months",
      progress: 80,
      confidence: 62,
      incentives: [
        "35% discount",
        "12-month fee waiver",
        "GBP 100 retention credit",
        "1.5% cashback boost",
      ],
      thinking:
        "Customer has 54-month tenure with high credit utilization (88%). Competitor Monzo offers 1.5% interest with no fees. Recommend 35% discount + retention credit to match lifetime value. Confidence 62% — needs supervisor sign-off due to discount > 30%.",
      account: {
        Holder: "Sarah Mitchell",
        Product: "Premier Current Account",
        Fee: "£25/mo",
        Tenure: "54 months",
        Balance: "£12,420",
        LTV: "£8,300",
      },
      competitorThreat: {
        Brand: "Monzo Premium",
        Offer: "1.5% interest, no fees",
        SignupBonus: "£100 cashback",
        Risk: "High",
      },
      conversation: [
        { from: "user", text: "I've been looking at Monzo, their account is free and pays interest." },
        { from: "ai", text: "I understand, Sarah. Your Premier account has been with us for 54 months — let me see what we can do." },
        { from: "user", text: "Honestly the fee is the main thing. £25/mo adds up." },
        { from: "ai", text: "I can offer a 35% discount on your monthly fee for 12 months plus £100 retention credit. Would that change your mind?" },
      ],
      nextAction:
        "Approve 35% discount + £100 retention credit. Estimated lifetime value retained: £8,300. Risk if lost: high (Monzo offer is competitive).",
    },
    {
      id: "neg-002",
      name: "Daniel Okafor",
      level: 3,
      competitor: "STARLING",
      status: "IN PROGRESS",
      product: "Business Banking Plus",
      fee: "£18/mo",
      tenure: "27 months",
      progress: 45,
      confidence: 78,
      incentives: ["15% discount", "Free international transfers", "Priority support"],
      thinking:
        "Customer mentioned cash flow concerns. Starling Business offers similar features at £10/mo. Try 15% fee discount + free international transfers (high-value perk for this segment).",
      account: {
        Holder: "Daniel Okafor",
        Product: "Business Banking Plus",
        Fee: "£18/mo",
        Tenure: "27 months",
        Balance: "£42,180",
        LTV: "£4,900",
      },
      competitorThreat: {
        Brand: "Starling Business",
        Offer: "Lower monthly fee, free FX",
        SignupBonus: "—",
        Risk: "Medium",
      },
      conversation: [
        { from: "user", text: "Costs are tight this quarter, I'm thinking about switching." },
        { from: "ai", text: "Let's see if we can ease that. I can offer 15% off your monthly fee and free international transfers." },
      ],
      nextAction: "Continue negotiation — try free international transfers add-on before discount increases.",
    },
    {
      id: "neg-003",
      name: "Aisha Khan",
      level: 4,
      competitor: "REVOLUT",
      status: "AT RISK",
      product: "Travel Black Card",
      fee: "£32/mo",
      tenure: "18 months",
      progress: 65,
      confidence: 71,
      incentives: ["20% discount", "Airport lounge passes", "Travel insurance upgrade", "0% FX markup"],
      thinking:
        "High-spend traveller, Revolut Ultra is a credible threat. Offering lounge passes + travel insurance upgrade plays to her usage pattern; FX markup waiver matches the Revolut perk directly.",
      account: {
        Holder: "Aisha Khan",
        Product: "Travel Black Card",
        Fee: "£32/mo",
        Tenure: "18 months",
        Balance: "£3,810",
        LTV: "£6,100",
      },
      competitorThreat: {
        Brand: "Revolut Ultra",
        Offer: "0% FX markup, lounge access",
        SignupBonus: "3 months free",
        Risk: "High",
      },
      conversation: [
        { from: "user", text: "Revolut Ultra has free FX. Why should I stay?" },
        { from: "ai", text: "We can match the 0% FX markup and add lounge passes + a travel insurance upgrade as a thank-you for 18 months." },
      ],
      nextAction: "Send personalized travel perks summary + waive next month's fee as goodwill gesture.",
    },
  ],

  inbox: [
    { id: "t1", title: "Reply to Priti — sprint summary", priority: "High", customer: "Priti Padhy", source: "email", ago: "2h", conf: 92 },
    { id: "t2", title: "Confirm Wednesday 3pm 1:1 with Sarah", priority: "Medium", customer: "Sarah Chen", source: "calendar", ago: "4h", conf: 95 },
    { id: "t3", title: "Review PRD doc from Alex", priority: "Medium", customer: "Alex Rivera", source: "drive", ago: "6h", conf: 88 },
    { id: "t4", title: "Code review PR #142 — Life-Harness fix", priority: "High", customer: "GitHub", source: "github", ago: "yesterday", conf: 76 },
    { id: "t5", title: "Slack: @channel — sync at 2pm tomorrow?", priority: "Low", customer: "#eng", source: "slack", ago: "yesterday", conf: 90 },
    { id: "t6", title: "RSVP: Quarterly all-hands Thursday", priority: "Low", customer: "Calendar", source: "calendar", ago: "yesterday", conf: 99 },
    { id: "t7", title: "Expense report — May trips", priority: "Critical", customer: "Finance", source: "email", ago: "2d", conf: 82 },
    { id: "t8", title: "Onboard new intern — paperwork pending", priority: "Medium", customer: "HR", source: "email", ago: "3d", conf: 70 },
  ],

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
async function loadFragment(route) {
  const path = ROUTES[route] || ROUTES.dashboard;
  try {
    const r = await fetch(path);
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
  if (!ROUTES[route]) route = "dashboard";
  // highlight nav
  nav.querySelectorAll("a").forEach(a => a.classList.toggle("active", a.dataset.route === route));
  await loadFragment(route);

  switch (route) {
    case "dashboard":  initDashboard();  break;
    case "inbox":      initInbox();      break;
    case "copilot":    initCopilot();    break;
    case "logs":       initLogs();       break;
    case "connectors": initConnectors(); break;
    case "settings":   initSettings();   break;
  }
}

function currentRoute() {
  const h = location.hash.replace(/^#\/?/, "");
  return h || "dashboard";
}

window.addEventListener("hashchange", () => go(currentRoute()));
window.addEventListener("DOMContentLoaded", async () => {
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
  document.getElementById("userName").textContent = displayName;
  document.getElementById("userRole").textContent = connectedEmail || me.email || displayRole;
  document.getElementById("userAvatar").textContent =
    (displayName.split(" ").map(p => p[0]).join("").slice(0, 2) || "M").toUpperCase();
}

async function signOut() {
  await fetch("/api/auth/sso/logout", { method: "POST", credentials: "include" }).catch(() => {});
  // Clear local chat history + reload
  window._copilotHistory = [];
  location.reload();
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

/* =========================================================
   Dashboard
   ========================================================= */
async function initDashboard() {
  const data = (await safeFetchJson("/api/dashboard")) || MOCK;

  // Last updated
  const ts = document.getElementById("lastUpdated");
  if (ts) ts.textContent = new Date().toLocaleTimeString();

  // KPI tiles
  const kpiRow = document.getElementById("kpiRow");
  if (kpiRow) {
    kpiRow.innerHTML = (data.kpis || MOCK.kpis).map(k => `
      <div class="kpi ${k.tone || ""}">
        <div class="kpi-label">${k.label}</div>
        <div class="kpi-value">${k.value}</div>
        <div class="kpi-foot">${k.foot || ""}</div>
      </div>
    `).join("");
  }

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
  }

  // Live negotiations
  const list = document.getElementById("negList");
  const negs = data.negotiations || MOCK.negotiations;
  if (list) {
    list.innerHTML = negs.map(n => {
      const ribbon = _renderCompactLifecycle(n.lifecycle);
      const isEmpty = n.id === "EMPTY-1";
      const isTask = String(n.id || "").startsWith("TASK-");
      const tid = isTask ? n.task_id : null;
      const statusPill = (s) => (
        s === "RUNNING" ? "pill-blue" :
        s === "WAITING ON YOU" ? "pill-red" :
        s === "QUEUED" ? "pill-orange" :
        s === "IDLE" ? "pill-teal" :
        s === "CLEAR" ? "pill-green" : "pill-blue"
      );

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
          ` : isTask && n.status === "QUEUED" ? `
            <button class="btn btn-primary" data-act="run" data-tid="${tid}">
              <span class="material-symbols-rounded">play_arrow</span> Start
            </button>
            <button class="btn btn-danger-outline" data-act="cancel" data-tid="${tid}">
              <span class="material-symbols-rounded">cancel</span> Cancel
            </button>
          ` : ""}
          ${isTask ? `
            <button class="btn" data-act="open" data-tid="${tid}">
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
            location.hash = "#/inbox";
            // Pre-select the task on landing
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
    });
  }

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
  let tasks = backendTasks;
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

  // Compose with MyAi → copilot with a pre-filled prompt
  document.getElementById("composeBtn")?.addEventListener("click", () => {
    sessionStorage.setItem("copilotPrefill", "Help me draft a new email — ask me for the recipient, subject and key points.");
    location.hash = "#/copilot";
  });

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
        <div style="white-space:pre-wrap;line-height:1.55;color:var(--text-strong);font-size:14px;max-height:380px;overflow:auto;padding:10px;background:#FAFAFA;border:1px solid var(--border-dim);border-radius:8px">${escapeHtml(body)}</div>

        <div style="display:flex;gap:8px;margin-top:18px;flex-wrap:wrap">
          <button class="btn btn-primary" data-em-action="reply">
            <span class="material-symbols-rounded">smart_toy</span> Reply with MyAi
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
          <a class="btn" href="${externalLinkBase}${encodeURIComponent(msgId)}" target="_blank" rel="noreferrer">
            <span class="material-symbols-rounded">open_in_new</span> Open in ${accountLabel}
          </a>
        </div>
      </div>`;

    detail.querySelectorAll("[data-em-action]").forEach(btn => {
      btn.addEventListener("click", async () => {
        const a = btn.dataset.emAction;
        if (a === "reply") {
          sessionStorage.setItem("copilotPrefill",
            `Draft a reply to this ${accountLabel} email. Be concise and professional:\n\nFrom: ${from}\nSubject: ${subj}\n\n${(body || "").slice(0, 1200)}`);
          location.hash = "#/copilot";
          return;
        }
        if (a === "summarize") {
          sessionStorage.setItem("copilotPrefill",
            `Summarize this ${accountLabel} email in 3 bullets and tell me if it needs a reply:\n\nFrom: ${from}\nSubject: ${subj}\n\n${(body || "").slice(0, 1200)}`);
          location.hash = "#/copilot";
          return;
        }
        btn.disabled = true;
        btn.style.opacity = 0.5;
        const endpoint = a === "markread" ? "mark-read" : "archive";
        const res = await safeFetchJson(`/api/inbox/${apiPath}/${encodeURIComponent(msgId)}/${endpoint}`, {
          method: "POST",
        });
        btn.disabled = false; btn.style.opacity = 1;
        if (res && res.status === "ok") {
          tasks = tasks.filter(x => x.id !== id);
          selectedTaskId = null;
          renderTasks();
          detail.innerHTML = `<div class="empty-pane"><div>
            <span class="material-symbols-rounded">check_circle</span>
            <h3 style="margin:8px 0 4px;color:var(--text-strong)">Done</h3>
            <div>${a === "markread" ? "Marked as read" : "Archived"} in ${accountLabel}.</div>
          </div></div>`;
        } else {
          alert("That action failed — check Logs for details.");
        }
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
  if (!data) return;
  window._copilotCurrentThreadId = threadId;
  window._copilotHistory = (data.messages || []).map(m => ({ role: m.role, content: m.content }));

  const body = document.getElementById("copilotBody");
  if (!body) return;

  // Render messages
  body.style.justifyContent = "flex-start";
  body.style.alignItems = "stretch";
  body.innerHTML = `<div class="conv-thread" style="display:flex;flex-direction:column;gap:10px;width:100%;max-width:760px;margin:0 auto"></div>`;
  const thread = body.querySelector(".conv-thread");
  for (const m of data.messages || []) {
    const cls = m.role === "user" ? "user" : "ai";
    thread.insertAdjacentHTML("beforeend",
      `<div class="msg ${cls}" style="white-space:pre-wrap">${escapeHtml(m.content)}</div>`);
  }
  body.scrollTop = body.scrollHeight;
  refreshCopilotRail(threadId);
}

async function newCopilotThread() {
  const created = await safeFetchJson("/api/threads", {
    method: "POST",
    body: { title: "New chat" },
  });
  if (created && created.id) {
    window._copilotCurrentThreadId = created.id;
    window._copilotHistory = [];
    const body = document.getElementById("copilotBody");
    if (body) body.innerHTML = emptyCopilotHTML();
    initCopilot();
    refreshCopilotRail(created.id);
  }
}

async function deleteCurrentThread() {
  if (!window._copilotCurrentThreadId) return;
  if (!confirm("Delete this chat?")) return;
  await fetch(`/api/threads/${window._copilotCurrentThreadId}`, {
    method: "DELETE", credentials: "include",
  });
  window._copilotCurrentThreadId = null;
  window._copilotHistory = [];
  const body = document.getElementById("copilotBody");
  if (body) body.innerHTML = emptyCopilotHTML();
  initCopilot();
  refreshCopilotRail();
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
  await refreshCopilotRail(window._copilotCurrentThreadId);

  // If we came back to the page with an active thread, re-render the messages
  // so the user sees their chat continued (not an empty welcome screen).
  if (window._copilotCurrentThreadId) {
    await loadCopilotThread(window._copilotCurrentThreadId);
  }

  // Suggested action grid (only show when no conversation yet)
  const grid = document.getElementById("actionGrid");
  if (grid) {
    grid.innerHTML = COPILOT_ACTIONS.map(a => `
      <div class="action-card" data-t="${a.t}">
        <div class="action-icon"><span class="material-symbols-rounded">${a.icon}</span></div>
        <div class="action-text">
          <div class="t">${a.t}</div>
          <div class="d">${a.d}</div>
        </div>
      </div>
    `).join("");
    grid.querySelectorAll(".action-card").forEach(card => {
      card.addEventListener("click", () => sendCopilot(card.dataset.t));
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

async function sendCopilot(text) {
  const atts = window._copilotAttachments || [];
  const trimmed = (text || "").trim();

  // Allow attachment-only sends. If there's neither text nor attachments, bail.
  if (!trimmed && !atts.length) return;

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

    // Render with line breaks preserved
    thread.insertAdjacentHTML("beforeend",
      `<div class="msg ai" style="white-space:pre-wrap">${escapeHtml(reply)}</div>`);
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
        if (created && created.id) window._copilotCurrentThreadId = created.id;
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
let logState = { paused: false, autoscroll: true, filter: "All", rows: [], timer: null };

function _logType(r) {
  const ev = r.event_type || "";
  if (r.severity === "error" || ev.endsWith(".error")) return "error";
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
  document.getElementById("pauseBtn")?.addEventListener("click", e => {
    logState.paused = !logState.paused;
    e.currentTarget.innerHTML = logState.paused
      ? `<span class="material-symbols-rounded">play_arrow</span>Resume`
      : `<span class="material-symbols-rounded">pause</span>Pause`;
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
    if (logState.paused) return;
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
        <div class="log-row ${streaming && i === 0 ? "fade-in" : ""}" style="grid-template-columns:160px 100px 1fr 36px">
          <div class="ts">${escapeHtml(r.ts)}</div>
          <div><span class="pill ${b.cls}"><span class="material-symbols-rounded" style="font-size:13px">${b.icon}</span>${escapeHtml(r.event_type || r.type)}</span></div>
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
