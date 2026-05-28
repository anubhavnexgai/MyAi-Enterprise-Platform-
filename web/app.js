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
    { id: "t1", title: "Card blocked — international transaction flagged", priority: "Critical", customer: "Sarah Mitchell", source: "phone", ago: "2m", conf: 92 },
    { id: "t2", title: "Dispute charge from 'TX-AERO'", priority: "High", customer: "Daniel Okafor", source: "email", ago: "8m", conf: 81 },
    { id: "t3", title: "Loan rate inquiry — first-time buyer", priority: "Medium", customer: "Priya Raman", source: "chat", ago: "12m", conf: 88 },
    { id: "t4", title: "Update home address after relocation", priority: "Low", customer: "James Wong", source: "web", ago: "21m", conf: 96 },
    { id: "t5", title: "Travel notification — Japan, 3 weeks", priority: "Low", customer: "Aisha Khan", source: "chat", ago: "34m", conf: 99 },
    { id: "t6", title: "Mortgage redemption statement request", priority: "High", customer: "Robert Hayes", source: "email", ago: "41m", conf: 74 },
    { id: "t7", title: "Standing order failed — insufficient funds", priority: "Critical", customer: "Lucia Conti", source: "phone", ago: "47m", conf: 90 },
    { id: "t8", title: "Increase credit card limit to £8,000", priority: "Medium", customer: "Marcus Reid", source: "web", ago: "1h", conf: 67 },
  ],

  copilotRecents: [
    { group: "Today", items: [
      { title: "Refund flow for declined POS", time: "10:42" },
      { title: "Explain SCA exemption rules", time: "09:18" },
    ]},
    { group: "Yesterday", items: [
      { title: "Draft response to fraud claim", time: "17:55" },
      { title: "Loan eligibility quick check", time: "14:02" },
      { title: "Card replacement timeline", time: "11:30" },
    ]},
    { group: "Earlier this week", items: [
      { title: "Mortgage rate comparison", time: "Mon" },
      { title: "Customer churn-risk scoring", time: "Mon" },
      { title: "AML escalation playbook", time: "Sun" },
    ]},
  ],

  copilotActions: [
    { icon: "account_balance_wallet", t: "Account balance",    d: "Get the latest balance" },
    { icon: "send_money",             t: "Transfer money",      d: "Send to a payee or account" },
    { icon: "credit_card_off",        t: "Block lost card",     d: "Freeze and reissue" },
    { icon: "percent",                t: "Loan rates",          d: "Compare current rates" },
    { icon: "home",                   t: "Update address",      d: "Change registered address" },
    { icon: "report",                 t: "Dispute transaction", d: "Open a chargeback" },
    { icon: "credit_score",           t: "Credit card limit",   d: "Request a limit change" },
    { icon: "support_agent",          t: "Talk to a banker",    d: "Hand off to a human" },
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
window.addEventListener("DOMContentLoaded", () => {
  if (!location.hash) location.hash = "#/dashboard";
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
});

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
      [r.active, "Active"],
      [r.wonWeek, "Won (Week)"],
      [r.lostWeek, "Lost (Week)"],
      [r.saveRate, "Save Rate"],
      [r.avgDiscount, "Avg Discount"],
      [r.avgLevels, "Avg Levels"],
      [r.competitors, "Competitors"],
      [r.escalations, "Escalations"],
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
    list.innerHTML = negs.map(n => `
      <div class="neg-card" data-id="${n.id}">
        <div class="neg-head">
          <span class="neg-name">${n.name}</span>
          <span class="pill pill-purple">Level ${n.level}</span>
          <span class="pill">vs ${n.competitor}</span>
          <span class="pill ${n.status === "NEEDS APPROVAL" ? "pill-orange" : n.status === "AT RISK" ? "pill-red" : "pill-blue"}">${n.status}</span>
        </div>
        <div class="neg-sub">${n.product} - ${n.fee} - ${n.tenure} tenure</div>
        <div class="progress"><span style="width:${n.progress}%"></span></div>
        <div class="progress-row">
          <span>Final Incentive</span>
          <span><b>${n.confidence}%</b> Confidence</span>
        </div>
        <div class="chips">${n.incentives.map(i => `<span class="chip">${i}</span>`).join("")}</div>
        <div class="ai-think">
          <b>AI Thinking</b>${n.thinking}
        </div>
        <div class="neg-actions">
          <button class="btn btn-success" data-act="approve">
            <span class="material-symbols-rounded">check</span>
            Approve ${n.incentives[0]}
          </button>
          <button class="btn btn-primary" data-act="takeover">
            <span class="material-symbols-rounded">support_agent</span>
            Take Over
          </button>
        </div>
      </div>
    `).join("");

    list.querySelectorAll(".neg-card").forEach(card => {
      card.addEventListener("click", e => {
        // ignore button clicks
        if (e.target.closest("button")) return;
        const id = card.dataset.id;
        const n = negs.find(x => x.id === id);
        if (n) openNegotiationModal(n);
      });
      card.querySelectorAll("button").forEach(btn => {
        btn.addEventListener("click", () => {
          const act = btn.dataset.act;
          btn.disabled = true;
          btn.innerHTML = act === "approve"
            ? `<span class="material-symbols-rounded">check_circle</span> Approved`
            : `<span class="material-symbols-rounded">person</span> Took Over`;
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
  const data = (await safeFetchJson("/api/inbox")) || MOCK;
  let tasks = data.inbox || MOCK.inbox;
  let activeFilter = "All";
  let activeLevel = 0; // L1
  let selectedTaskId = null;

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

  function setLevel(i) {
    activeLevel = i;
    slider.querySelectorAll(".slider-node").forEach((n, idx) => n.classList.toggle("active", idx === i));
    renderLevels();
    // update badge
    const lv = LEVELS[i];
    const b = document.getElementById("levelBadge");
    if (b) {
      b.className = `pill pill-${lv.color === "yellow" ? "yellow" : lv.color}`;
      b.textContent = `${lv.code} — ${lv.title}`;
    }
    const banner = document.getElementById("modeBanner");
    if (banner) banner.innerHTML = `<span class="material-symbols-rounded">info</span> Active mode: ${lv.code} — ${lv.title}. ${lv.desc}`;
  }
  setLevel(activeLevel);

  // Priority filter
  document.querySelectorAll(".pill-filter[data-prio]").forEach(p => {
    p.addEventListener("click", () => {
      document.querySelectorAll(".pill-filter[data-prio]").forEach(x => x.classList.remove("active"));
      p.classList.add("active");
      activeFilter = p.dataset.prio;
      renderTasks();
    });
  });

  // Render task list
  const list = document.getElementById("taskList");
  const detail = document.getElementById("taskDetail");
  const counter = document.getElementById("taskCount");

  function renderTasks() {
    const filtered = activeFilter === "All"
      ? tasks
      : tasks.filter(t => t.priority === activeFilter);
    counter.textContent = `Showing ${filtered.length} of ${tasks.length} tasks`;
    list.innerHTML = filtered.map(t => `
      <div class="task ${t.id === selectedTaskId ? "active" : ""}" data-id="${t.id}">
        <div class="task-row">
          <div class="task-title">${t.title}</div>
          <span class="pill ${t.priority === "Critical" ? "pill-red" : t.priority === "High" ? "pill-orange" : t.priority === "Medium" ? "pill-blue" : "pill-teal"}">${t.priority}</span>
        </div>
        <div class="task-meta">
          <span class="material-symbols-rounded">person</span>${t.customer}
          <span style="opacity:.5">·</span>
          <span class="material-symbols-rounded">${sourceIcon(t.source)}</span>${t.source}
          <span style="opacity:.5">·</span>
          ${t.ago} ago
        </div>
        <div class="task-conf">AI Confidence ${t.conf}%</div>
      </div>
    `).join("");
    list.querySelectorAll(".task").forEach(el => {
      el.addEventListener("click", () => selectTask(el.dataset.id));
    });
  }

  async function selectTask(id) {
    selectedTaskId = id;
    renderTasks();
    const t = tasks.find(x => x.id === id) || {};

    // Show loading state
    detail.innerHTML = `<div class="card-body" style="padding:40px;text-align:center;color:var(--text-muted)">Loading task details…</div>`;

    // Fetch full detail from backend (numeric ID, fall back to mock)
    const numId = parseInt(String(id).replace(/\D/g, ""), 10) || 1;
    const data = await safeFetchJson(`/api/inbox/tasks/${numId}`) || {};
    const d = { ...t, ...data };

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

  // Simulate email button
  document.getElementById("simEmailBtn")?.addEventListener("click", () => {
    const newT = {
      id: "t" + (tasks.length + 100),
      title: "New email — payment delayed",
      priority: "High",
      customer: "Auto Test User",
      source: "email",
      ago: "now",
      conf: 70,
    };
    tasks = [newT, ...tasks];
    renderTasks();
  });

  document.getElementById("refreshInbox")?.addEventListener("click", () => initInbox());
}

function sourceIcon(s) {
  return { phone: "call", email: "mail", chat: "chat", web: "language" }[s] || "help";
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
async function initCopilot() {
  // Build recents
  const rail = document.getElementById("railList");
  if (rail) {
    rail.innerHTML = MOCK.copilotRecents.map(g => `
      <div class="rail-group-title">${g.group}</div>
      ${g.items.map((it, i) => `
        <div class="rail-item ${i === 0 && g.group === "Today" ? "active" : ""}">
          <div>${it.title}</div>
          <div class="rail-item-time">${it.time}</div>
        </div>
      `).join("")}
    `).join("");
    rail.querySelectorAll(".rail-item").forEach(el => {
      el.addEventListener("click", () => {
        rail.querySelectorAll(".rail-item").forEach(x => x.classList.remove("active"));
        el.classList.add("active");
      });
    });
  }

  // Suggested action grid
  const grid = document.getElementById("actionGrid");
  if (grid) {
    grid.innerHTML = MOCK.copilotActions.map(a => `
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

  document.getElementById("newChatBtn")?.addEventListener("click", () => {
    document.getElementById("copilotBody").innerHTML = emptyCopilotHTML();
    initCopilot();
  });

  const input = document.getElementById("copilotInput");
  const sendBtn = document.getElementById("copilotSend");
  input?.addEventListener("keydown", e => { if (e.key === "Enter") sendCopilot(input.value); });
  sendBtn?.addEventListener("click", () => sendCopilot(input?.value));
}

function emptyCopilotHTML() {
  return `
    <div class="copilot-empty">
      <div class="avatar-lg">M</div>
      <div class="hi">Hi, I'm Max</div>
      <div class="sub">Your AI Assistant · How can I help you today?</div>
    </div>
    <div class="action-grid" id="actionGrid"></div>
  `;
}

function sendCopilot(text) {
  if (!text || !text.trim()) return;
  const body = document.getElementById("copilotBody");
  body.style.justifyContent = "flex-start";
  body.style.alignItems = "stretch";
  if (!body.querySelector(".conv-thread")) {
    body.innerHTML = `<div class="conv-thread" style="display:flex;flex-direction:column;gap:10px;width:100%;max-width:760px;margin:0 auto"></div>`;
  }
  const thread = body.querySelector(".conv-thread");
  thread.insertAdjacentHTML("beforeend", `<div class="msg user" style="background:var(--bg-subtle);color:var(--text-strong)">${escapeHtml(text)}</div>`);
  document.getElementById("copilotInput").value = "";
  setTimeout(() => {
    thread.insertAdjacentHTML("beforeend", `<div class="msg ai" style="background:var(--accent);color:white">Here's what I found regarding "${escapeHtml(text)}". I can walk you through the next steps or take action on your behalf — let me know which.</div>`);
    body.scrollTop = body.scrollHeight;
  }, 500);
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;" }[c]));
}

/* =========================================================
   Logs
   ========================================================= */
let logState = { paused: false, autoscroll: true, filter: "All", rows: [], ws: null };

const LOG_USERS = ["Jigar Patel", "Sarah M.", "AI Agent", "Daniel O.", "Aisha K.", "System"];
const LOG_EVENTS = [
  { type: "info",    text: "LLM query: customer churn classification" },
  { type: "success", text: "Tool call: send_email (template=retention_offer)" },
  { type: "success", text: "Auth: user signed in via SSO" },
  { type: "warn",    text: "Tool call: refund initiated — pending approval" },
  { type: "error",   text: "Tool call: external API timeout (kyc.verify)" },
  { type: "info",    text: "LLM query: summarize last 14 days of activity" },
  { type: "success", text: "Tool call: card_freeze applied" },
  { type: "info",    text: "WebSocket connected" },
  { type: "warn",    text: "Rate limit: 80% of LLM budget consumed" },
  { type: "success", text: "Tool call: update_address completed" },
];

async function initLogs() {
  const rows = document.getElementById("logRows");
  if (!rows) return;

  // seed with mock
  logState.rows = [];
  for (let i = 0; i < 14; i++) logState.rows.push(genLogRow(i));
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
  // Pause
  document.getElementById("pauseBtn")?.addEventListener("click", e => {
    logState.paused = !logState.paused;
    e.currentTarget.innerHTML = logState.paused
      ? `<span class="material-symbols-rounded">play_arrow</span>Resume`
      : `<span class="material-symbols-rounded">pause</span>Pause`;
  });
  // Export
  document.getElementById("exportBtn")?.addEventListener("click", () => {
    const blob = new Blob([JSON.stringify(logState.rows, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = "myai-logs.json"; a.click();
    URL.revokeObjectURL(url);
  });

  // Try websocket; fall back to interval
  try {
    const wsUrl = (location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws";
    logState.ws = new WebSocket(wsUrl);
    logState.ws.onmessage = ev => {
      if (logState.paused) return;
      try {
        const msg = JSON.parse(ev.data);
        appendLog(msg);
      } catch {
        appendLog(genLogRow(Date.now()));
      }
    };
    logState.ws.onerror = () => {/* fall back below */};
  } catch { /* ignored */ }

  // Synthetic stream as fallback (also useful in static viewing)
  if (window._myaiLogTimer) clearInterval(window._myaiLogTimer);
  window._myaiLogTimer = setInterval(() => {
    if (logState.paused) return;
    appendLog(genLogRow(Date.now()));
  }, 1800);
}

function genLogRow(seed) {
  const e = LOG_EVENTS[Math.floor(Math.random() * LOG_EVENTS.length)];
  const u = LOG_USERS[Math.floor(Math.random() * LOG_USERS.length)];
  return {
    ts: new Date(Date.now() - Math.random() * 600000).toISOString().slice(11, 23),
    type: e.type,
    user: u,
    event: e.text,
    latency: Math.floor(60 + Math.random() * 800) + "ms",
  };
}

function appendLog(row) {
  logState.rows.unshift(row);
  if (logState.rows.length > 200) logState.rows.pop();
  renderLogs(true);
}

function renderLogs(streaming = false) {
  const rows = document.getElementById("logRows");
  if (!rows) return;
  const filtered = logState.rows.filter(r => {
    if (logState.filter === "All") return true;
    if (logState.filter === "Tool Calls") return /Tool call/i.test(r.event);
    if (logState.filter === "LLM Queries") return /LLM query/i.test(r.event);
    if (logState.filter === "Errors")     return r.type === "error";
    if (logState.filter === "Auth Events")return /Auth/i.test(r.event);
    return true;
  });

  rows.innerHTML = filtered.map((r, i) => {
    const b = TYPE_BADGES[r.type];
    return `
      <div class="log-row ${streaming && i === 0 ? "fade-in" : ""}">
        <div class="ts">${r.ts}</div>
        <div><span class="pill ${b.cls}"><span class="material-symbols-rounded" style="font-size:13px">${b.icon}</span>${r.type}</span></div>
        <div class="user"><div class="avatar">${initials(r.user)}</div><span>${r.user}</span></div>
        <div class="ev">${r.event}</div>
        <div class="latency">${r.latency}</div>
        <div class="stat"><span class="material-symbols-rounded" style="font-size:18px;color:${r.type === "error" ? "var(--red)" : "var(--green)"}">${r.type === "error" ? "error" : "check_circle"}</span></div>
      </div>
    `;
  }).join("");

  // Side stats
  const ok = logState.rows.filter(r => r.type !== "error").length;
  const total = logState.rows.length || 1;
  const errs = logState.rows.filter(r => r.type === "error").length;
  document.getElementById("statEpm")?.replaceChildren(Object.assign(document.createElement("span"), { textContent: (logState.rows.length / 5).toFixed(0) }));
  document.getElementById("statSuccess") && (document.getElementById("statSuccess").textContent = Math.round((ok / total) * 100) + "%");
  document.getElementById("statErrors")  && (document.getElementById("statErrors").textContent  = String(errs));
  document.getElementById("statTopTool") && (document.getElementById("statTopTool").textContent = "send_email");

  if (logState.autoscroll) rows.scrollTop = 0;
}

function initials(name) {
  return name.split(" ").map(p => p[0]).join("").slice(0, 2).toUpperCase();
}

/* =========================================================
   Connectors / Settings (stubs)
   ========================================================= */
function initConnectors() {
  const grid = document.getElementById("connectorGrid");
  if (!grid) return;
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
