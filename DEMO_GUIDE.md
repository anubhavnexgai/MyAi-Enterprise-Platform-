# MyAi — Demo Guide

A tight, ~12–15 minute walkthrough that shows every main feature, plus a prompt
cheat-sheet. Sign in as **me@nexgai.com** (your real account — it has live Gmail,
Outlook, Calendar, seeded chats, a Council project, research reports, and a watch).

> Before you start: hard-refresh once (**Ctrl+Shift+R**) so the latest UI loads.
> Use **Google Chrome or Edge** if you want voice anywhere (Arc is fine for
> everything else). The app is at your ngrok link or `localhost:8002`.

---

## The one-sentence pitch

> "MyAi is the employee's personal AI assistant — it reads your real email and
> calendar, runs a multi-agent **Council** to turn an idea into a build-ready
> plan, does deep web research, and acts only with your approval — all on free
> models, fully audited."

---

## Demo flow (in order)

### 1. Login → Dashboard  *(30s — set the frame)*
- Show the **account picker** on the login screen → "multi-account, SSO-ready."
- Land on **Dashboard**. Point out: **Live status**, **Today's Focus** stats, and
  **Active tasks** with the new **filter chips** (All / Running / Queued…). Click a
  chip to filter. Say: *"This is what MyAi is doing for me in the background."*

### 2. Copilot — the daily driver  *(3–4 min — the core)*
- Open one of the **seeded chats** in the left rail (e.g. *"Inbox triage"*) to show
  it's a real, used assistant.
- Click **New chat**. Make sure mode is **Agent** (top-right toggle).
- **Live prompt:** `What's on my calendar this week, and what should I prep for?`
  → watch the **tool chips** light up ("Checking your calendar") and the answer
  **stream** in. This proves it touches *real data*.
- Show the **model picker** (bottom-right) → "These are free OpenRouter models;
  I can switch per chat."
- Hit **⌘K / Ctrl+K** → the **command palette** → jump to any page. (Nice flourish.)
- Flip to **Chat** mode and ask something general (`Explain MyAi in 2 sentences`)
  → "Chat mode is the pure model, no tools — faster for quick questions."
- (If a long answer runs) show the **Stop** button (send button turns red).

### 3. Agents Council — the differentiator  *(3–4 min — the wow)*
- Open **Agents Council**. Walk the **pipeline graph**: Research → Business →
  Architect → Developer → Marketing → Critic. *"A whole team, in a box."*
- Pick the saved project **"AI study-buddy app for students"** from the dropdown
  (its brief loads as the goal).
- Click an agent card → show you can **assign a different model per agent**.
- Click **Run Full Council Review** → narrate the live states
  (WAITING → WORKING → READY) as each agent reports. *(A full run is a few
  minutes; if short on time, instead open the existing **reports awaiting
  approval** and talk through them.)*
- Open a **Developer** report that contains code → **Approve & apply** → "It just
  wrote that code into the project workspace — nothing happens until I approve."
- Approve another report, then show the **Undo** toast. Point out the **model
  badge** on each report ("which model wrote this").

### 4. Deep Research  *(2 min)*
- Open **Deep Research**. Show the **library** (ios 27, nvidia rtx spark) → open a
  **Visual Report** in a new tab (the polished standalone report).
- Point at **Research watches** → "recurring auto-research on a schedule" (there's
  one already: *New open-source LLM releases*).
- On a library card, click **Council** → "send this research straight into a
  Council project." (Lands you on the Agents page with the goal prefilled.)
- (Optional live) Start a quick research with **Rounds = 1** so it finishes fast.

### 5. Email  *(1–2 min)*
- Open **Email**. Show the two-pane inbox on your **real** Gmail/Outlook.
- Open a message → click **Summarize** and **AI reply** → "MyAi triages, summarizes
  and drafts." Note the bodies are clean, readable text.

### 6. Calendar  *(30s)*
- Open **Calendar** → real month grid of your connected events (~178 events).

### 7. Logs — the trust story  *(30s)*
- Open **Logs** → "Everything MyAi does is **audited** in real time —
  tool calls, logins, preference changes. Nothing is hidden."

### 8. Settings — autonomy  *(30s — close on trust)*
- Open **Settings** → the **AI Autonomy** slider (L1 Observe → L5 Autonomous).
  *"This is the safety dial. At L1 it only watches; high-risk actions like
  sending email or writing files are gated until I raise it. Every level is
  enforced server-side."*

### 9. (Optional) Admin Console
- If your account is super-admin, show **per-employee usage analytics**.

---

## Prompt cheat-sheet

**Copilot — Agent mode (touches real data, shows tools):**
- `What's on my calendar this week, and what should I prep for?`
- `Which emails still need a reply from me? Give me a prioritized one-line list.`
- `Summarize my most recent email from Sprinto and draft a reply.`
- `Who is Priti Padhy and what was our last exchange about?`
- `Find the latest stable Python version and cite your source.`  *(forces a web search)*

**Copilot — Chat mode (fast, pure model):**
- `Explain the Agents Council in 2 sentences.`
- `Give me 5 talking points for a leadership demo of MyAi.`

**Agents Council — project goals (paste into the goal box):**
- `A mobile habit-tracker app for students with streaks and reminders.`
- `A Chrome extension that summarizes long articles using a free LLM.`
- `Launch plan for a privacy-first AI notes app.`

**Deep Research — queries:**
- `Best open-source LLMs to run locally in 2026 and their tradeoffs`
- `Compare pgvector vs Pinecone vs Qdrant for a startup`

---

## If something looks slow or empty
- **Free models can lag** under load — if a chat stalls, hit **Stop** and resend, or
  switch the model in the picker.
- A **full Council run takes a few minutes** — for a short demo, talk through the
  *existing* reports instead of running live, or run a **single agent** from its card.
- **Research** can take minutes — use **Rounds = 1** for a live run, or just open a
  report from the **library**.
- **Voice** is intentionally disabled (browser cloud-speech doesn't work in Arc).
- **Ollama is paused** — that's fine; everything here runs on OpenRouter.

---

## 30-second backup pitch (if a live action is slow)
> "Under the hood, MyAi gives a small free model a set of tools — your email,
> calendar, web search, files — and a reliability layer that keeps it honest. The
> Council is the same engine fanned out into six specialists that hand work down a
> pipeline. Everything is approval-gated and audited. It's a personal AI assistant
> that's safe enough for an enterprise."
