# How MyAi Works — Features, Internals & Build Story

A plain-English guide to what every feature does, **what happens behind the
scenes**, and **how MyAi was built**. Written to be readable by anyone — share it,
present from it, or use it to onboard a teammate.

---

## 1. What MyAi is

MyAi is a **personal AI assistant for the workplace**. It connects to your real
email, calendar and files, can research the live web, runs a **multi-agent
Council** to turn an idea into a build-ready plan, and **only acts with your
approval**. It runs on **free models** and is built to be safe enough for an
enterprise: every action is gated by an autonomy level and recorded in an audit
log, and each customer (tenant) is isolated.

It's the employee-facing "front door" to the broader **NexgAI AI Workforce** — one
friendly assistant, with specialist agents working behind it.

---

## 2. The big picture (architecture)

```
   Browser (vanilla-JS single-page app)
        │  cookie-authenticated calls
        ▼
   FastAPI backend  ──►  OpenRouter (free LLMs)   ← the "brain"
        │              ─►  Gmail / Outlook / Google Calendar (your data)
        │              ─►  Odysseus engine (deep research, per-tenant subprocess)
        ▼
   SQLite per tenant  (threads, tasks, council reports, preferences, audit log)
```

- **Frontend:** a hand-built single-page app — no React, no framework. Plain HTML
  fragments + one `app.js` router + `styles.css`. It's fast, tiny, and easy to
  change. Each page "self-initializes" when the router swaps it in.
- **Backend:** **FastAPI** (Python). It exposes a clean REST API (`/api/...`),
  authenticates every request, talks to the LLM and your connectors, and streams
  responses back token-by-token.
- **The brain:** an **LLM client** that is provider-agnostic but points at
  **OpenRouter**, using only **free** models (ids ending `:free`). A central
  allow-list keeps non-chat models (safety classifiers, embedders, image/audio
  models) out of the picture.
- **Storage:** **SQLite**, one database per tenant, with lightweight automatic
  schema migrations on boot. Chat threads, tasks, Council reports, preferences and
  the audit log all live here.
- **Isolation:** every request carries a tenant + user identity; data reads and
  writes are always scoped to that user. The Odysseus research engine runs as a
  **separate subprocess per tenant** so customers never share memory.

---

## 3. Feature by feature — what it does and what happens underneath

### Copilot (Chat) — the daily driver
**What you see:** a chat with an **Agent / Chat** toggle, a model picker, a stop
button, friendly "tool" chips, and a Sources footer.

**Under the hood — the agent loop.** When you send a message in **Agent** mode, the
backend gives a small model a **set of tools** (search your email, read calendar,
search Drive, recall a contact, web search, fetch a page, deep research, send
email, create calendar events, write a file). The model decides which tools to
call; the backend runs them and feeds the results back, round after round, until
the model has enough to answer. Then the final answer is **streamed** to you
token-by-token.

A small **reliability layer ("Life-Harness")** wraps this loop so a *small* model
behaves: a per-turn round + time budget, automatic correction of slightly-wrong
tool names, a one-shot nudge if the model says "I'll search" but forgets to, and a
forced "use what you have and answer" step if it loops. This is what makes a free
model feel dependable.

**Chat** mode skips all tools — it's the pure model, faster, for quick questions.

**Persistence:** every message is saved to a thread in the database, so your
history survives refreshes and shows up in the left rail.

### Agents Council — a team in a box
**What you see:** a node graph of six specialists — **Research → Business →
Architect → Developer → Marketing → Critic** — that light up live as they work,
producing **reports** that wait for your approval.

**Under the hood — the orchestrator.** You give it a project goal. A **planner**
LLM breaks the goal into sub-tasks and a **dependency graph**: independent steps
run in **parallel**, dependent ones run in order (e.g. Developer waits for
Architect). Each specialist is the *same* agent loop as chat, but with a focused
role prompt and a restricted toolset, and **strict context isolation** — it only
sees the outputs of the steps it depends on. A final **synthesis** step merges
everything into a step-by-step *"what we could do together"* action plan.

**Safety model:** agents **only produce reports** — nothing touches the world until
you approve. The Developer's report can be **Approved & Applied**, which extracts
the code blocks and writes them into a sandboxed project workspace
(`data/council_workspace/<tenant>/<you>/`). Approvals have an **Undo**, and each
report records **which model** produced it.

**Live + resumable:** progress streams over a reconnect-safe event stream, so if
you refresh mid-run it picks back up.

### Deep Research — multi-step web research
**What you see:** ask a question, watch it plan → search → read → write, then open a
rich **visual report**. Plus **watches** (recurring auto-research) and a **"send to
Council"** handoff.

**Under the hood.** This is powered by a vendored **Odysseus** research engine
running as a per-tenant subprocess, reached through a proxy (`/api/oui/...`). An
"LLM-in-the-loop" researcher runs several **rounds**: it generates search queries,
fetches and extracts the best sources, synthesizes findings, and decides whether to
go deeper — then writes a final cited report. Reports are saved to disk so the
**visual report** can be regenerated any time. **Watches** are scheduled topics that
re-run on an interval and drop fresh reports into your library.

### Email & Calendar — your real data
**What you see:** a clean two-pane inbox over your **real** Gmail + Outlook, with
search, folders, AI **Summarize** and **AI reply**; and a month-grid calendar of
your connected events.

**Under the hood.** MyAi connects via **OAuth** (Google + Microsoft). A background
**harvester** periodically caches your recent messages, calendar events and
contact memory, so the assistant can answer instantly without hitting the
providers every time. Email bodies (which arrive as messy HTML — Teams
notifications, marketing mail) are cleaned to readable text before display. AI
summary/reply are grounded **only** in the actual message — no fabrication.

### Dashboard — at a glance
A live overview: a status line, "Today's Focus" stats, and **Active tasks** — what
MyAi is running or waiting on you for — now with **filter chips** (All / Running /
Queued / Waiting on you) that filter the list. Numbers animate up on load.

### Autonomy & gating — the safety dial
A single **L1–L5** slider (in Settings) controls how much MyAi may do on its own:

| Level | Name | Behaviour |
|------|------|-----------|
| L1 | Observe | Watches and surfaces insight; **zero** actions. |
| L2 | Draft Assist | Drafts replies for you to send. |
| L3 | Augmented | Suggests actions; you approve each step. |
| L4 | Guarded Auto | Auto-resolves *low-risk*; high-risk goes to you. |
| L5 | Autonomous | End-to-end with an audit trail. |

This is **enforced server-side**: high-risk actions (sending email, deleting,
controlling the screen, writing files) pass through a `decide_write_gate` check
before they run — the UI can't bypass it.

### Logs — full audit trail
Every meaningful action — tool calls, logins, preference changes, Council runs — is
written to an **audit log** and streamed to the Logs page in real time. Nothing the
assistant does is hidden.

### Workspace extras
**Notes** (a side drawer with to-dos and drawings), **Tasks & Routines** (scheduled
recurring jobs), **Cookbook** (model catalog), **Documents** (an editor the agent
can write into), and **Connectors** (manage your OAuth links).

---

## 4. How MyAi was made

**Stack.** Python **FastAPI** backend; a **vanilla-JavaScript** single-page app
(no framework — deliberately, for speed and zero build tooling); **SQLite** per
tenant; **OpenRouter** for LLMs (free models only); **OAuth** for Gmail/Outlook/
Calendar; and a vendored **Odysseus** engine for deep research, run as an isolated
subprocess per tenant behind a proxy.

**Key design decisions.**
- **Tools over hard-coding.** Instead of scripting "if the user asks about email,
  do X," the model is handed tools and decides. This keeps the assistant general
  and lets it combine abilities (read email *and* calendar *and* the web in one
  turn).
- **Small models, made reliable.** Free models are weaker, so a reliability harness
  (budgets, tool-name correction, anti-loop nudges, forced synthesis) sits around
  the loop. The result behaves far better than the raw model would.
- **Multi-agent = one engine, fanned out.** The Council didn't need a new system —
  it reuses the chat agent loop as specialists, with a planner and a
  dependency-aware executor on top. Context isolation between agents is what makes
  the hand-offs clean.
- **Approval-gated by default.** The product's core promise is trust: agents draft,
  humans decide. File writes and sends are gated; everything is audited.
- **Cost ≈ zero.** Free OpenRouter models plus parallel task scheduling keep it
  responsive without a bill. (When two tasks both need a *local* model they run
  serially to avoid GPU contention; remote/API tasks run several at once.)
- **Provider-agnostic brain.** The LLM client is written against an OpenAI-style
  interface, so the model or provider can change without touching feature code.

**How it evolved.** It started as a single-agent assistant (chat + connectors +
autonomy gating), then grew the reliability harness, then the multi-agent
orchestrator, then the Council UI and the research integration — each layer built
on the one below rather than replacing it. Recent work added per-agent models,
"Approve & apply," scheduled research, a command palette, cleaner email, and the
parallel scheduler.

---

## 5. Safety & trust, in one paragraph

MyAi never acts without permission you've granted: a server-enforced autonomy level
decides what's allowed, high-risk actions are explicitly gated, the multi-agent
Council only *proposes* until you approve, every action is written to an audit log,
and each tenant's data is fully isolated. It's an assistant you can hand real
access to — because you stay in control of what it does with it.
