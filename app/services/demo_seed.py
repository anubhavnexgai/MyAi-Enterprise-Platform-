"""Employee-centric demo dataset.

When DEMO_MODE is on, the harvester seeds the inbox/calendar/contacts with
realistic internal NexgAI content instead of crawling the host's personal Gmail.
This lets a tester exercise MyAi end-to-end with relatable work data (HR, Finance,
Sales, Eng, Ops) and no IT/OAuth permissions. Flip DEMO_MODE=false to go back to
real connected accounts — the seed writes to the same cache tables, so switching
is clean.
"""
from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete

from app.storage.models import (
    ContactMemory,
    HarvestedEvent,
    HarvestedMessage,
    InboxTask,
    MessageEnrichment,
)
from app.tenants.router import get_tenant_router

logger = logging.getLogger(__name__)


def demo_mode_enabled() -> bool:
    return os.environ.get("DEMO_MODE", "false").strip().lower() in ("1", "true", "yes", "on")


# Only these users (the built-in demo accounts — see app/api/auth_routes.py
# DEMO_ACCOUNTS) receive the synthetic dataset. Everyone else — real SSO logins
# and the personal "My Real Account" — sees ONLY their own connected data.
DEMO_USER_IDS = {"demo.superadmin", "demo.user"}


def is_demo_user(user_id: str) -> bool:
    return (user_id or "") in DEMO_USER_IDS


# (sender_name, sender_addr, subject, snippet, priority, suggestion, action, hours_ago, unread)
_EMAILS = [
    ("Priti Padhy", "priti.padhy@nexgai.com",
     "Q3 OKRs & AI Workforce roadmap — your input by Thu",
     "Hi {name}, ahead of the leadership review I need MyAi's Q3 objectives and how it plugs into the AI Workforce. Can you send a one-pager and a few slides? Let's align before the all-hands.",
     "high",
     "Priti (leadership) is asking for MyAi's Q3 OKRs + a one-pager before Thursday's leadership review. This is a direct ask from the top — draft the one-pager and reply with a timeline.",
     "reply", 3, True),
    ("Mariya Varghese", "mariya.varghese@nexgai.com",
     "Action required: Submit your Q3 PTO plan",
     "Please log your planned time off for Jul–Sep in the HR portal by June 6 so we can plan coverage. Reply here if you have questions about carry-over or the new policy.",
     "high",
     "HR needs your Q3 PTO plan submitted by June 6. Quick action: confirm your dates and reply, or note you have none planned.",
     "reply", 6, True),
    ("Manikanta Bavirisetti", "manikanta.b@nexgai.com",
     "MyAi end-to-end test — first-pass feedback",
     "Did a first pass. The flow is smooth but the seeded data felt personal (flights, promos). Now that it's employee-centric I'll run the full HR→Finance→Sales scenarios and share notes. Couple of edge cases to discuss.",
     "high",
     "Manikanta (your tester) is ready to run the full end-to-end test now that data is employee-centric. Reply to thank him and ask him to flag any edge cases in this thread.",
     "reply", 1, True),
    ("Shekhar Kumar", "shekhar.kumar@nexgai.com",
     "Sprint 14 planning — notes & your action items",
     "Recap from planning: you own the agent-orchestration spike and the calendar-edit fix. Story points logged. Can you confirm the two items and update the board before standup tomorrow?",
     "medium",
     "Shekhar logged your Sprint 14 items (orchestration spike + calendar fix). Action: confirm the two items and update the board before tomorrow's standup.",
     "reply", 20, True),
    ("IT Security", "security@nexgai.com",
     "Mandatory: complete Security Awareness training by June 5",
     "Annual security awareness training is due June 5. It takes ~20 minutes. Accounts that miss the deadline will be temporarily restricted. Click through to start.",
     "high",
     "Mandatory security training is due June 5 — missing it restricts your account. Block 20 minutes on your calendar to finish it.",
     "schedule", 28, True),
    ("Jigar Patel", "jigar.patel@nexgai.com",
     "Demo prep: NexgAI AI Workforce for Acme Corp (Fri)",
     "We're demoing the AI Workforce + MyAi to Acme on Friday. Can you prep the MyAi portion — inbox triage, autonomy dial, calendar actions? Let's do a dry run Thursday afternoon.",
     "high",
     "Jigar wants you to prep the MyAi portion of Friday's Acme demo and do a Thursday dry run. Action: reply to confirm and schedule the dry run.",
     "schedule", 5, True),
    ("Riya Agarwal", "riya.agarwal@nexgai.com",
     "Interview scheduled: Senior ML Engineer candidate",
     "You're on the panel for the Senior ML Engineer loop. Slot booked Wed 4:00 PM. Candidate resume and the scorecard are attached. Please add your focus area so we don't overlap.",
     "medium",
     "You're interviewing a Senior ML Engineer candidate Wed 4 PM. Action: review the resume and reply with your focus area to avoid panel overlap.",
     "reply", 22, True),
    ("Nikhil Malshette", "nikhil.malshette@nexgai.com",
     "Code review needed: agent orchestration PR #214",
     "Opened PR #214 for the decompose→route→synthesize loop. ~400 lines. Could you review before EOD? A couple of design choices I'd like a second opinion on (parallel fan-out vs sequential).",
     "medium",
     "Nikhil needs PR #214 (agent orchestration) reviewed before EOD, with input on fan-out vs sequential. Action: reply with a review time or your take.",
     "reply", 9, True),
    ("Sarah Chen", "sarah.chen@acmecorp.com",
     "Follow-up: NexgAI pilot proposal & pricing",
     "Thanks for the walkthrough last week. Our team is keen to move forward with a 60-day pilot. Could you share the proposal with pricing tiers and a rough onboarding timeline?",
     "high",
     "Acme Corp (external client) wants to move forward with a 60-day pilot and is asking for the proposal + pricing. High-value — reply promptly with the proposal and timeline.",
     "reply", 4, True),
    ("Priti Shivani", "priti.shivani@nexgai.com",
     "Design handoff: new inbox UI is ready in Figma",
     "The cleaned-up inbox (compact autonomy panel, account switcher, suggestion card) is ready for handoff. Specs and tokens are in Figma. Ping me if anything's unclear during build.",
     "medium",
     "Priti Shivani handed off the new inbox UI in Figma. Action: reply to confirm receipt and flag any build questions.",
     "reply", 26, False),
    ("Finance", "finance@nexgai.com",
     "Reminder: June expense reports due Friday",
     "A friendly reminder that June expense reports are due this Friday. Submit receipts via the portal. Unsubmitted expenses roll to next month's cycle.",
     "medium",
     "Expense reports are due Friday. If you have receipts pending, submit them this week so they don't roll over.",
     "reply", 30, True),
    ("Finance", "finance@nexgai.com",
     "Your reimbursement of ₹12,400 has been approved",
     "Your reimbursement claim #RB-3391 for ₹12,400 (conference travel) has been approved and will be credited with the next payroll run. No action needed.",
     "low",
     "A reimbursement of ₹12,400 was approved — credited next payroll. No action needed; you can archive this.",
     "archive", 34, False),
    ("People Team", "people@nexgai.com",
     "All-Hands June 3 — Product & Hiring updates",
     "Join the company all-hands on June 3 at 2:00 PM. Agenda: Q3 product roadmap (incl. AI Workforce + MyAi), hiring plan, and an open Q&A with leadership. Calendar invite attached.",
     "medium",
     "Company all-hands June 3, 2 PM — covers the roadmap your work is part of. Action: accept the invite so it's on your calendar.",
     "schedule", 31, True),
    ("Benefits", "benefits@nexgai.com",
     "Benefits enrollment window closes June 10",
     "Open enrollment ends June 10. Review your health, dental, and retirement elections in the portal. If you make no changes, last year's elections roll over.",
     "medium",
     "Benefits open enrollment closes June 10. If you want changes, act before then; otherwise last year's choices roll over.",
     "ignore", 40, True),
    ("CloudVMs Billing", "billing@cloudvms.io",
     "Invoice #INV-2042 due — NexgAI compute (May)",
     "Invoice #INV-2042 for May GPU/compute usage is now due. Amount: $1,280. Please forward to your finance team for processing within 14 days.",
     "medium",
     "A vendor invoice (CloudVMs, $1,280 May compute) is due in 14 days. Action: forward to finance for payment.",
     "pay", 27, True),
    ("Saurin Mehta", "saurin.mehta@nexgai.com",
     "Re: NexgAI Workspace access for MyAi — permissions",
     "Looped in IT on the Workspace OAuth scopes you need (mail read/modify, calendar). They'll need admin consent. Send me the exact scopes and the redirect URI and I'll push it through.",
     "medium",
     "Saurin can push through the NexgAI Workspace OAuth access you need — he's asking for the exact scopes + redirect URI. Action: reply with gmail.modify, calendar, and your callback URL.",
     "reply", 2, True),
    ("Keshab Roy", "keshab.roy@nexgai.com",
     "Re: Connecting NexgAI Workspace to MyAi",
     "Happy to help wire the NexgAI mailbox once Saurin clears admin consent. Ping me when the app is ready and I'll test the connect flow from my account too.",
     "medium",
     "Keshab will help connect the NexgAI mailbox after admin consent and can test the connect flow. Action: reply to coordinate timing.",
     "reply", 7, False),
    ("The AI Workforce Weekly", "news@aiworkforce.email",
     "This week: agentic orchestration, eval harnesses & more",
     "Top reads on multi-agent orchestration, evaluation flywheels, and small-model reliability. Plus: 5 patterns for grounding LLM answers in enterprise data.",
     "low",
     "An industry newsletter — relevant but not urgent. Skim later or unsubscribe if the inbox is noisy.",
     "unsubscribe", 44, False),
    ("Sarah Chen", "sarah.chen@acmecorp.com",
     "Re: Pilot proposal — two questions before we sign",
     "Thanks for the proposal! Two things before we sign: (1) can the 60-day pilot include SSO with our Okta? (2) what's the data residency for our EU users? Happy to hop on a quick call this week.",
     "high",
     "Acme (client) replied with two pre-signature questions — Okta SSO and EU data residency — and wants a call. High-value: answer both and propose call times today.",
     "reply", 2, True),
    ("Customer Success", "success@nexgai.com",
     "NPS dipped for 2 pilot accounts — quick sync?",
     "Heads up: two pilot accounts' NPS dropped this week (slow responses + the calendar bug). Can we grab 15 minutes to align on fixes before the QBR on Monday?",
     "high",
     "CS flags an NPS dip for 2 pilots (slow responses + calendar bug) and wants a 15-min sync before Monday's QBR. Action: reply with a slot and loop in engineering.",
     "schedule", 1, True),
    ("GitHub", "notifications@github.com",
     "[nexgai/myai] PR #218 merged · 2 issues assigned to you",
     "Your pull request 'OpenRouter free-model fallback' was merged into main. You were also assigned two new issues: #221 'Calendar timezone off by one' and #222 'Docs editor: add diff view'.",
     "medium",
     "GitHub: your PR merged, and 2 issues were assigned (calendar TZ bug, docs diff view). Action: triage both and add them to the sprint board.",
     "reply", 8, True),
    ("AWS Billing", "no-reply@aws.amazon.com",
     "Your May AWS bill is ready — $2,140 (up 12%)",
     "Your AWS usage for May totals $2,140, up 12% month-over-month, driven mainly by EC2 GPU and S3 storage. Review the full cost breakdown in Cost Explorer.",
     "medium",
     "AWS bill is up 12% to $2,140 (GPU + S3). Action: review the breakdown, forward to finance, and consider right-sizing the GPU instances.",
     "pay", 18, True),
    ("Payroll", "payroll@nexgai.com",
     "Your May payslip is now available",
     "Your payslip for May has been generated and is available in the HR portal. Net pay was credited on May 31. No action needed — this is for your records.",
     "low",
     "Payroll: May payslip is available and already credited. No action needed — safe to archive.",
     "archive", 50, False),
    ("Anita Desai", "anita.desai@nexgai.com",
     "Lunch & Learn: prompt patterns that actually work (Thu 1 PM)",
     "I'm hosting a 30-minute Lunch & Learn on Thursday at 1 PM about prompt patterns for enterprise agents — grounding, tool routing, and eval loops. Bring your trickiest cases and we'll work through them.",
     "low",
     "Optional Lunch & Learn Thu 1 PM on prompt patterns. Action: add it to your calendar if you want to attend.",
     "schedule", 12, True),
    ("Zoom", "no-reply@zoom.us",
     "Cloud recording ready: Demo dry run (internal)",
     "The cloud recording for 'Demo dry run (internal)' is ready and will be available for 30 days. You can view, download, or share it from your Zoom account.",
     "low",
     "Zoom: the internal dry-run recording is ready. Skim it or share with the team if useful; otherwise archive.",
     "archive", 14, False),
    ("LinkedIn", "notifications@linkedin.com",
     "You appeared in 9 searches this week",
     "Recruiters and peers found your profile 9 times this week. See who's been viewing your profile and what they searched for.",
     "low",
     "LinkedIn engagement digest — not urgent. Skim or unsubscribe to keep the inbox quiet.",
     "unsubscribe", 60, False),
]


# (title, summary, priority, status, due_in_hours, source). source is agent|rule|
# manual (NOT "email" — that would make them appear in the email list).
_TASKS = [
    ("Draft MyAi Q3 OKR one-pager for Priti", "Leadership review Thursday — objectives + how MyAi plugs into the AI Workforce. One page + a few slides.", "high", "open", 30, "agent"),
    ("Reply to Acme: Okta SSO + EU data residency", "Two pre-signature questions from Sarah Chen; propose a call this week.", "high", "open", 6, "agent"),
    ("Sync with CS on pilot NPS dip", "Two pilots down (slow responses + calendar bug) — align on fixes before Monday's QBR.", "high", "open", 5, "agent"),
    ("Complete Security Awareness training", "~20 minutes; due June 5 or the account gets restricted.", "high", "in_progress", 10, "rule"),
    ("Prep Acme demo — MyAi portion", "Inbox triage, autonomy dial, calendar actions. Dry run Thursday afternoon.", "high", "open", 48, "agent"),
    ("Review PR #214 — agent orchestration", "~400 lines; weigh parallel fan-out vs sequential. Before EOD.", "medium", "open", 8, "agent"),
    ("Submit Q3 PTO plan", "Log Jul–Sep time off in the HR portal by June 6.", "medium", "open", 24, "rule"),
    ("Forward CloudVMs invoice ($1,280) to finance", "Invoice #INV-2042 for May compute, due in 14 days.", "medium", "open", 72, "agent"),
    ("Confirm Sprint 14 items + update board", "Orchestration spike + calendar fix, before tomorrow's standup.", "medium", "open", 16, "agent"),
    ("Triage 2 new GitHub issues", "#221 calendar timezone bug, #222 docs editor diff view.", "low", "open", 36, "agent"),
    ("Review AWS May cost spike", "Up 12% to $2,140 (GPU + S3) — consider right-sizing GPU instances.", "low", "open", 40, "agent"),
]


def _iso(hours_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


def _h(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


async def seed_demo_data(user_id: str, tenant_id: str, recipient_name: str = "there") -> dict:
    """Replace the user's cached inbox/calendar/contacts/tasks with employee-centric
    demo data. ``recipient_name`` personalises greetings (e.g. the demo account's
    first name)."""
    first = (recipient_name or "there").split()[0]
    router_db = get_tenant_router()
    async with router_db.session_for(tenant_id) as session:
        # Reset everything we own for this user (clean slate, no leftovers).
        for model in (HarvestedMessage, HarvestedEvent, MessageEnrichment, ContactMemory, InboxTask):
            await session.execute(
                delete(model)
                .where(model.tenant_id == tenant_id)
                .where(model.creator_id == user_id)
            )

        # --- Emails + pre-computed AI suggestions ---
        for (name, addr, subject, snippet, prio, suggestion, action, hrs, unread) in _EMAILS:
            ext = "demo-" + _h(addr, subject)
            session.add(HarvestedMessage(
                tenant_id=tenant_id, creator_id=user_id, account="gmail",
                external_id=ext, thread_id=ext,
                subject=subject, from_addr=addr, from_name=name,
                date=_iso(hrs), snippet=snippet.replace("{name}", first),
                label_ids=["INBOX"] + (["UNREAD"] if unread else []),
                priority=prio, unread=1 if unread else 0,
            ))
            session.add(MessageEnrichment(
                tenant_id=tenant_id, creator_id=user_id, external_id=ext,
                content_hash=_h(subject, snippet), suggestion=suggestion, action=action,
            ))

        # --- Calendar (next several days; weekday standups + key meetings) ---
        events = _build_events()
        for ev in events:
            session.add(HarvestedEvent(
                tenant_id=tenant_id, creator_id=user_id, account="google_calendar",
                external_id="demo-" + _h(ev["title"], ev["start"]),
                title=ev["title"], start=ev["start"], end=ev["end"],
                location=ev.get("location", ""), html_link="",
                attendee_count=ev.get("attendees", 0), all_day=1 if ev.get("all_day") else 0,
            ))

        # --- Action items / tasks (drive the Tasks page + dashboard) ---
        now = datetime.now(timezone.utc)
        for (title, summary, prio, status_, due_h, src) in _TASKS:
            session.add(InboxTask(
                tenant_id=tenant_id, creator_id=user_id,
                title=title, summary=summary, source=src, priority=prio, status=status_,
                assignee_id="me", due_at=now + timedelta(hours=due_h),
                sla_minutes=int(due_h * 60),
            ))

        # --- Per-sender memory for a few key colleagues/clients ---
        for (name, addr, count, summary) in _CONTACTS:
            session.add(ContactMemory(
                tenant_id=tenant_id, creator_id=user_id,
                sender_key=name.lower(), sender_name=name, sender_addr=addr,
                message_count=count, last_date=_iso(2),
                content_hash=_h(name, summary), summary=summary,
            ))

        await session.commit()

    return {"emails": len(_EMAILS), "events": len(events),
            "contacts": len(_CONTACTS), "tasks": len(_TASKS)}


def _build_events() -> list[dict]:
    """Weekday 9:30 standups for the next 7 days + a handful of key meetings."""
    now = datetime.now(timezone.utc)
    today = now.date()
    out: list[dict] = []

    def at(day_offset: int, hour: int, minute: int = 0) -> datetime:
        d = today + timedelta(days=day_offset)
        return datetime(d.year, d.month, d.day, hour, minute, tzinfo=timezone.utc)

    # Weekday daily standup for the next 7 days (skip Sat=5, Sun=6).
    for off in range(0, 8):
        d = today + timedelta(days=off)
        if d.weekday() < 5:
            s = at(off, 4, 0)  # 9:30 IST ~= 04:00 UTC
            out.append({"title": "Daily standup", "start": s.isoformat(),
                        "end": (s + timedelta(minutes=15)).isoformat(), "attendees": 6})

    keyed = [
        (0, 11, 30, 30, "1:1 with Priti Padhy", "Leadership sync", 2),
        (0, 9, 0, 60, "Sprint 14 planning", "Engineering", 6),
        (2, 8, 30, 60, "Company All-Hands", "Main / Zoom", 80),
        (2, 10, 30, 30, "Interview: Senior ML Engineer", "Panel", 4),
        (4, 5, 30, 45, "Acme Corp demo — AI Workforce + MyAi", "Client / Zoom", 7),
        (3, 9, 0, 30, "Demo dry run (internal)", "Eng", 5),
    ]
    for (off, hh, mm, dur, title, loc, att) in keyed:
        s = at(off, hh, mm)
        out.append({"title": title, "start": s.isoformat(),
                    "end": (s + timedelta(minutes=dur)).isoformat(),
                    "location": loc, "attendees": att})
    return out


# (full_name, email, roles, dept, chat_count, action_count, last_login_hours_ago)
_DEMO_EMPLOYEES = [
    ("Priti Padhy",          "priti.padhy@nexgai.com",       "admin,agent",  "Leadership", 42, 18, 2),
    ("Manikanta Bavirisetti","manikanta.b@nexgai.com",       "agent",        "Engineering", 31, 12, 1),
    ("Shekhar Kumar",        "shekhar.kumar@nexgai.com",     "agent",        "Engineering", 17, 6,  20),
    ("Riya Agarwal",         "riya.agarwal@nexgai.com",      "agent",        "People",      9,  3,  26),
    ("Nikhil Malshette",     "nikhil.malshette@nexgai.com",  "agent",        "Engineering", 24, 9,  5),
    ("Mariya Varghese",      "mariya.varghese@nexgai.com",   "agent",        "HR",          6,  2,  30),
    ("Jigar Patel",          "jigar.patel@nexgai.com",       "admin,agent",  "Sales",       38, 15, 4),
]

_DEMO_TOOLS = ["web_search", "search_email", "list_calendar", "deep_research",
               "send_email", "recall_contact", "create_calendar_event"]


async def seed_demo_org(
    tenant_id: str, dev_user_id: str, dev_email: str, dev_name: str, dev_roles: str
) -> dict:
    """Seed the employee directory + varied per-employee activity for the admin
    dashboard. Idempotent: upserts employees and only adds synthetic AuditLog
    rows for a colleague who has none yet (so the dev user's real activity and
    re-runs aren't double-counted).
    """
    from sqlalchemy import func as _func, select

    from app.services.employees import upsert_employee
    from app.storage.models import AuditLog

    # The signed-in dev user (real activity flows in as they use the app).
    await upsert_employee(tenant_id, dev_user_id, dev_email, dev_name, dev_roles,
                          touch_login=True)

    router_db = get_tenant_router()
    seeded = 0
    for (name, email, roles, dept, chats, actions, login_hrs) in _DEMO_EMPLOYEES:
        uid = email.split("@", 1)[0]
        await upsert_employee(tenant_id, uid, email, name, roles,
                              department=dept, touch_login=False)
        async with router_db.session_for(tenant_id) as session:
            # Stamp a realistic last_login without an actual SSO round-trip.
            from app.storage.models import Employee
            emp = (await session.execute(
                select(Employee).where(Employee.tenant_id == tenant_id)
                .where(Employee.user_id == uid))).scalars().first()
            if emp is not None:
                emp.last_login_at = datetime.now(timezone.utc) - timedelta(hours=login_hrs)
            existing = int((await session.execute(
                select(_func.count(AuditLog.id))
                .where(AuditLog.tenant_id == tenant_id)
                .where(AuditLog.creator_id == uid))).scalar() or 0)
            if existing == 0:
                rows = []
                for i in range(chats):
                    rows.append(AuditLog(
                        tenant_id=tenant_id, creator_id=uid, event_type="copilot.chat",
                        severity="info", message="demo chat turn",
                        created_at=datetime.now(timezone.utc) - timedelta(hours=(i * 6) % 168),
                    ))
                for i in range(actions):
                    tool = _DEMO_TOOLS[i % len(_DEMO_TOOLS)]
                    rows.append(AuditLog(
                        tenant_id=tenant_id, creator_id=uid, event_type=f"tool.{tool}",
                        severity="info", message=f"demo {tool}",
                        payload={"tool": tool},
                        created_at=datetime.now(timezone.utc) - timedelta(hours=(i * 9) % 168),
                    ))
                session.add_all(rows)
                seeded += 1
            await session.commit()
    return {"employees": len(_DEMO_EMPLOYEES) + 1, "activity_seeded_for": seeded}


_CONTACTS = [
    ("Priti Padhy", "priti.padhy@nexgai.com", 5,
     "NexgAI leadership. Recent threads: Q3 OKRs for MyAi, AI Workforce roadmap alignment, "
     "and prep for the leadership review. Currently waiting on your one-pager + slides by Thursday."),
    ("Manikanta Bavirisetti", "manikanta.b@nexgai.com", 4,
     "Your colleague QA-testing MyAi end-to-end. Flagged that earlier demo data looked personal; "
     "now running full HR→Finance→Sales scenarios and will share edge cases. Awaiting your reply."),
    ("Shekhar Kumar", "shekhar.kumar@nexgai.com", 3,
     "Engineering teammate / scrum. Owns sprint board. You have two Sprint 14 items to confirm "
     "(orchestration spike, calendar fix) before standup."),
    ("Sarah Chen", "sarah.chen@acmecorp.com", 2,
     "External — Acme Corp. Wants a 60-day NexgAI pilot; asked for proposal + pricing tiers and an "
     "onboarding timeline. Warm, high-intent lead."),
]
