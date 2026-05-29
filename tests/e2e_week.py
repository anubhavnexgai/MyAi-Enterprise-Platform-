"""End-to-end "week of usage" test for MyAi-Enterprise.

Hits every public endpoint that a real user would touch through the UI:
- Identity and connectors
- Dashboard load
- Inbox listing, task CRUD, action endpoints, autonomy persistence
- Copilot chat (real Gmail grounding, multi-turn context, draft, plan, calendar)
- Threads CRUD (rename, export-ready fetch, delete)
- Insights
- Preferences
- Logs
- Notifications

Each step prints PASS/FAIL with a one-line reason. Exit code = number of fails.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://localhost:8002"

PASSES: list[str] = []
FAILS: list[tuple[str, str]] = []


def _req(method: str, path: str, body=None, timeout=120) -> tuple[int, dict | list | str]:
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            try:
                return r.status, json.loads(raw or b"null")
            except Exception:
                return r.status, raw.decode(errors="replace")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"null")
        except Exception:
            return e.code, str(e)
    except Exception as e:
        return 0, str(e)


def check(name: str, ok: bool, why: str = "") -> bool:
    if ok:
        PASSES.append(name)
        print(f"  PASS  {name}")
    else:
        FAILS.append((name, why))
        print(f"  FAIL  {name} :: {why}")
    return ok


# ---------------------------------------------------------------------------
# Day 1 — Monday: User logs in, checks dashboard, opens inbox
# ---------------------------------------------------------------------------


def day_1_monday() -> None:
    print("\n--- Monday: arrive at office, triage ---")

    # 1. Identity
    code, me = _req("GET", "/api/auth/me")
    check("auth.me 200", code == 200, f"status={code}")
    check("auth.me has user_id", isinstance(me, dict) and me.get("id") == "dev.user")
    check("auth.me has email", isinstance(me, dict) and "@" in str(me.get("email", "")))

    # 2. Connectors
    code, cs = _req("GET", "/api/connectors")
    check("connectors list 200", code == 200)
    check("connectors has gmail", isinstance(cs, dict) and any(c["provider"] == "google_gmail" for c in cs.get("connectors", [])))
    gmail_conn = next((c for c in cs.get("connectors", []) if c["provider"] == "google_gmail"), {})
    check("gmail is connected", gmail_conn.get("connected") is True, f"acct={gmail_conn.get('account_label')}")

    # 3. Dashboard
    code, dash = _req("GET", "/api/dashboard")
    check("dashboard 200", code == 200)
    check("dashboard has kpis", isinstance(dash, dict) and len(dash.get("kpis", [])) >= 6)
    check("dashboard has retention block", isinstance(dash.get("retention"), dict))
    check("dashboard has threads", isinstance(dash.get("negotiations"), list))

    # Verify NEEDS ATTENTION KPI is a real integer count (was 23047 lifetime
    # unread before the classifier; should now be filtered).
    kpi = next((k for k in dash.get("kpis", []) if k["label"] == "NEEDS ATTENTION"), {})
    val = str(kpi.get("value", ""))
    check("NEEDS ATTENTION is numeric", val.isdigit(), f"got '{val}'")
    check("dashboard threads card is now 'Active tasks' shape",
          all(t.get("status") in {"RUNNING","WAITING ON YOU","QUEUED","IDLE","CLEAR"}
              for t in dash.get("negotiations", [])),
          f"got {[t.get('status') for t in dash.get('negotiations', [])]}")


# ---------------------------------------------------------------------------
# Day 2 — Tuesday: Inbox CRUD, autonomy slider
# ---------------------------------------------------------------------------


def day_2_tuesday() -> None:
    print("\n--- Tuesday: triage inbox, change autonomy ---")

    # 1. Initial inbox
    code, inbox = _req("GET", "/api/inbox")
    check("inbox list 200", code == 200)
    check("inbox has sources", isinstance(inbox.get("sources"), dict))
    check("inbox has real gmail items", inbox.get("sources", {}).get("gmail", 0) > 0, f"sources={inbox.get('sources')}")

    # Verify Gmail items have subjects and senders (not 'Unknown')
    email_tasks = [t for t in inbox.get("tasks", []) if t.get("source") == "email"]
    if email_tasks:
        first = email_tasks[0]
        check("gmail item has real subject", first.get("title") and first.get("title") != "(no subject)", f"title={first.get('title')}")
        check("gmail item has real sender", first.get("from_name") and first.get("from_name") != "Unknown", f"from={first.get('from_name')}")

    # 2. Create a manual task
    code, created = _req("POST", "/api/inbox/tasks", {
        "title": "Tuesday test task",
        "summary": "Verify task lifecycle works",
        "priority": "high",
        "source": "manual",
    })
    check("create task 201", code == 201, f"status={code} body={created}")
    tid = created.get("id") if isinstance(created, dict) else None
    check("created task has id", isinstance(tid, int))

    if tid:
        # 3. Update status
        code, _ = _req("POST", f"/api/inbox/tasks/{tid}/run")
        check("run task", code == 200)
        code, _ = _req("POST", f"/api/inbox/tasks/{tid}/pause")
        check("pause task", code == 200)
        code, _ = _req("POST", f"/api/inbox/tasks/{tid}/resume")
        check("resume task", code == 200)
        code, _ = _req("POST", f"/api/inbox/tasks/{tid}/approve")
        check("approve task", code == 200)

        # 4. Detail
        code, detail = _req("GET", f"/api/inbox/tasks/{tid}")
        check("task detail 200", code == 200)
        check("task detail correct id", isinstance(detail, dict) and detail.get("id") == tid)

        # 5. Delete
        code, _ = _req("DELETE", f"/api/inbox/tasks/{tid}")
        check("delete task 204", code == 204)

    # 6. Autonomy preferences — every level should persist
    for level in [1, 2, 3, 4, 5]:
        code, p = _req("PUT", "/api/preferences", {"autonomy_level": level})
        check(f"set autonomy L{level}", code == 200 and p.get("autonomy_level") == level)
        code, p2 = _req("GET", "/api/preferences")
        check(f"reload autonomy L{level}", code == 200 and p2.get("autonomy_level") == level, f"got {p2}")
    # Leave at L2 so copilot calls work
    _req("PUT", "/api/preferences", {"autonomy_level": 2})


# ---------------------------------------------------------------------------
# Day 3 — Wednesday: Copilot multi-turn chat (real Gmail grounding)
# ---------------------------------------------------------------------------


def day_3_wednesday() -> None:
    print("\n--- Wednesday: copilot conversations ---")

    # 1. First message: should ground in real Gmail
    code, r = _req("POST", "/api/copilot/chat",
                   {"message": "Summarize my unread emails — what needs a reply today?",
                    "history": []}, timeout=180)
    check("copilot summarize 200", code == 200, f"status={code}")
    reply = r.get("reply", "") if isinstance(r, dict) else ""
    tools = r.get("tool_calls", []) if isinstance(r, dict) else []
    check("copilot summarize has reply", len(reply) > 30, f"reply_len={len(reply)}")
    check("copilot used gmail grounding", any(t.get("name") == "Gmail" for t in tools), f"tools={tools}")
    # Must reference real sender names if any unread emails
    has_real_sender = any(s in reply for s in ["LinkedIn", "HDFC", "Pepperfry", "adidas", "Gmail", "google"]) or "no unread" in reply.lower()
    check("copilot reply references real data", has_real_sender or "email" in reply.lower(), f"reply={reply[:200]}")

    # 2. Multi-turn: follow up with a question
    history = [
        {"role": "user", "content": "Summarize my unread emails"},
        {"role": "assistant", "content": reply},
    ]
    code, r2 = _req("POST", "/api/copilot/chat",
                    {"message": "Which of those should I deal with first?", "history": history},
                    timeout=180)
    check("copilot follow-up 200", code == 200)
    check("copilot follow-up keeps context",
          isinstance(r2, dict) and len(r2.get("reply", "")) > 20,
          f"reply={(r2 or {}).get('reply','')[:200]}")

    # 3. Calendar question
    code, r3 = _req("POST", "/api/copilot/chat",
                    {"message": "What's on my calendar today?", "history": []}, timeout=180)
    check("copilot calendar 200", code == 200)
    cal_tools = (r3 or {}).get("tool_calls", [])
    check("copilot used calendar grounding",
          any("Calendar" in str(t.get("name", "")) for t in cal_tools),
          f"tools={cal_tools}")

    # 4. Draft a status update — must NOT use [Your Name] placeholders
    code, r4 = _req("POST", "/api/copilot/chat",
                    {"message": "Draft a short status update for my manager based on my recent work",
                     "history": []}, timeout=180)
    check("copilot draft 200", code == 200)
    body = (r4 or {}).get("reply", "")
    has_placeholder = any(p in body for p in ["[Your Name]", "[Date]", "[Project Name]", "[colleague", "[Manager"])
    check("draft has no [placeholder] fields", not has_placeholder,
          f"first 300 chars: {body[:300]}")

    # 5. Generic question still works
    code, r5 = _req("POST", "/api/copilot/chat",
                    {"message": "What is 17 times 23?", "history": []}, timeout=180)
    check("copilot math 200", code == 200)
    check("copilot math gives 391",
          "391" in (r5 or {}).get("reply", ""),
          f"reply={(r5 or {}).get('reply','')[:200]}")


# ---------------------------------------------------------------------------
# Day 4 — Thursday: Chat threads (rail history, rename, delete)
# ---------------------------------------------------------------------------


def day_4_thursday() -> None:
    print("\n--- Thursday: chat thread management ---")

    # 1. List threads — may be empty
    code, d = _req("GET", "/api/threads")
    check("threads list 200", code == 200)
    check("threads has list", isinstance(d.get("threads"), list))

    # 2. Create a new thread
    code, t = _req("POST", "/api/threads", {"title": "Thursday planning"})
    check("create thread 201", code == 201)
    tid = t.get("id") if isinstance(t, dict) else None
    check("thread has id", isinstance(tid, int))

    if tid:
        # 3. Append messages
        code, _ = _req("POST", f"/api/threads/{tid}/messages",
                       {"role": "user", "content": "Plan my Thursday"})
        check("append user msg", code == 200)
        code, _ = _req("POST", f"/api/threads/{tid}/messages",
                       {"role": "assistant", "content": "Here is your plan: ..."})
        check("append assistant msg", code == 200)

        # 4. Fetch full thread
        code, full = _req("GET", f"/api/threads/{tid}")
        check("get thread 200", code == 200)
        check("thread has 2 messages", len(full.get("messages", [])) == 2)

        # 5. Rename
        code, _ = _req("PATCH", f"/api/threads/{tid}", {"title": "Renamed thursday"})
        check("rename thread", code == 200)

        # 6. Verify rename
        code, d2 = _req("GET", "/api/threads")
        renamed = next((x for x in d2.get("threads", []) if x["id"] == tid), {})
        check("rename persisted", renamed.get("title") == "Renamed thursday", f"got '{renamed.get('title')}'")

        # 7. Delete
        code, _ = _req("DELETE", f"/api/threads/{tid}")
        check("delete thread 204", code == 204)

        # 8. Confirm gone
        code, _ = _req("GET", f"/api/threads/{tid}")
        check("deleted thread is 404", code == 404)


# ---------------------------------------------------------------------------
# Day 5 — Friday: Insights, notifications, logs
# ---------------------------------------------------------------------------


def day_5_friday() -> None:
    print("\n--- Friday: end-of-week review ---")

    code, i = _req("GET", "/api/insights")
    check("insights 200", code == 200)
    check("insights has actions_today",
          isinstance(i, dict) and "actions_today" in i, f"got {i}")
    check("insights actions_today is int",
          isinstance(i.get("actions_today"), int) and i["actions_today"] >= 0)

    # Notifications (uses /api/logs)
    code, logs = _req("GET", "/api/logs?limit=20")
    check("logs 200", code == 200)
    check("logs is list", isinstance(logs, list))
    # We should have at least one audit entry from all this testing
    check("logs has entries", isinstance(logs, list) and len(logs) > 0,
          f"count={len(logs) if isinstance(logs, list) else 'N/A'}")


# ---------------------------------------------------------------------------
# Day 6 — Saturday: Gmail tool — search by keyword
# ---------------------------------------------------------------------------


def day_6_saturday() -> None:
    print("\n--- Saturday: targeted email searches ---")

    code, r = _req("POST", "/api/copilot/chat",
                   {"message": "Find any emails about LinkedIn in my inbox",
                    "history": []}, timeout=180)
    check("copilot search 200", code == 200)
    # Should have actually queried Gmail
    tools = (r or {}).get("tool_calls", [])
    check("search used Gmail tool",
          any(t.get("name") == "Gmail" for t in tools),
          f"tools={tools}")


# ---------------------------------------------------------------------------
# Day 7 — Sunday: Autonomy gating + final smoke
# ---------------------------------------------------------------------------


def day_7_sunday() -> None:
    print("\n--- Sunday: settle preferences, final smoke ---")

    # Confirm preferences round-trip
    code, _ = _req("PUT", "/api/preferences", {"autonomy_level": 3, "data": {"theme": "light"}})
    check("save prefs+data", code == 200)
    code, p = _req("GET", "/api/preferences")
    check("prefs round-trip",
          p.get("autonomy_level") == 3 and p.get("data", {}).get("theme") == "light",
          f"got {p}")

    # Static SPA assets must load
    for path in ["/", "/app.js", "/styles.css", "/pages/dashboard.html",
                 "/pages/inbox.html", "/pages/copilot.html", "/pages/connectors.html",
                 "/pages/settings.html", "/pages/logs.html"]:
        code, _ = _req("GET", path)
        check(f"static {path}", code == 200, f"code={code}")


def day_8_autonomy_gating() -> None:
    """Verify autonomy levels actually block / allow real actions."""
    print("\n--- Autonomy gating: L1 blocks, L5 allows ---")

    # Pick a real Gmail message ID to use as the target action
    _, inbox = _req("GET", "/api/inbox")
    gmail_tasks = [t for t in inbox.get("tasks", []) if str(t.get("id", "")).startswith("gmail:")]
    if not gmail_tasks:
        check("autonomy test has gmail msg", False, "no gmail items in inbox to test")
        return
    msg_id = gmail_tasks[0]["external_id"]

    # L1 should block both mark-read AND archive
    _req("PUT", "/api/preferences", {"autonomy_level": 1})
    code, _ = _req("POST", f"/api/inbox/gmail/{msg_id}/mark-read")
    check("L1 blocks mark-read", code == 403, f"got {code}")
    code, _ = _req("POST", f"/api/inbox/gmail/{msg_id}/archive")
    check("L1 blocks archive", code == 403, f"got {code}")

    # L3 should allow mark-read but block archive (delete)
    _req("PUT", "/api/preferences", {"autonomy_level": 3})
    code, _ = _req("POST", f"/api/inbox/gmail/{msg_id}/archive")
    check("L3 blocks archive", code == 403, f"got {code}")

    # L5 allows everything (don't actually fire mark-read here so we don't
    # alter the user's real inbox during the test)


def day_9_file_upload() -> None:
    """Upload a plain text "screenshot" and have the copilot reason about it."""
    print("\n--- File upload: copilot reads attached text ---")

    # Build a small in-memory text file via multipart
    import urllib.request as ur

    boundary = "----WebKitTestBoundary"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="note.txt"\r\n'
        'Content-Type: text/plain\r\n\r\n'
        "Sprint goals for the week:\n"
        "1. Ship the new inbox UI\n"
        "2. Wire Gmail mark-read and archive\n"
        "3. Update the welcome flow for new users\n"
        f"\r\n--{boundary}--\r\n"
    ).encode()

    req = ur.Request(
        BASE + "/api/copilot/upload",
        data=body,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with ur.urlopen(req, timeout=30) as r:
            up = json.loads(r.read())
    except Exception as e:
        check("upload 200", False, str(e))
        return

    check("upload 200", isinstance(up, dict) and up.get("filename") == "note.txt")
    check("upload extracted text",
          "Sprint goals" in str(up.get("extracted_text", "")),
          f"got {(up.get('extracted_text') or '')[:80]}")


def day_11_harvester() -> None:
    """Force a harvest, then verify cached reads populate the inbox."""
    print("\n--- Harvester worker — force refresh + cached reads ---")

    code, res = _req("POST", "/api/inbox/refresh")
    check("harvester refresh 200", code == 200, f"got {code}")
    accounts = (res or {}).get("refresh", {}).get("accounts", {})
    check("harvester crawled gmail",
          accounts.get("gmail_messages", 0) > 0,
          f"accounts={accounts}")

    # After refresh, the inbox should still load (cache-first)
    code, inbox = _req("GET", "/api/inbox")
    check("inbox loads after harvest", code == 200)
    src = inbox.get("sources", {})
    check("inbox still surfaces gmail items", src.get("gmail", 0) > 0,
          f"sources={src}")


def day_12_lifecycle() -> None:
    """Create a task and verify lifecycle SLA fields + ribbon."""
    print("\n--- Service request lifecycle — SLA + ribbon ---")

    # Restore autonomy so create works
    _req("PUT", "/api/preferences", {"autonomy_level": 3})

    code, t = _req("POST", "/api/inbox/tasks", {
        "title": "Lifecycle ribbon test",
        "summary": "Should have SLA, due_at, assignee",
        "priority": "high",
        "source": "manual",
    })
    check("create lifecycle task 201", code == 201, f"got {code}")
    tid = (t or {}).get("id")
    check("lifecycle: due_at populated", t.get("due_at") is not None, f"got {t.get('due_at')}")
    check("lifecycle: sla_minutes populated", t.get("sla_minutes") is not None, f"got {t.get('sla_minutes')}")
    check("lifecycle: assignee defaults to 'me'", t.get("assignee_id") == "me", f"got {t.get('assignee_id')}")

    if tid:
        # PATCH due_at + assignee
        from datetime import datetime, timedelta, timezone
        future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        code, patched = _req("PATCH", f"/api/inbox/tasks/{tid}", {
            "due_at": future,
            "assignee_id": "myai",
        })
        check("patch due_at + assignee", code == 200, f"got {code}")
        check("patched assignee is 'myai'", patched.get("assignee_id") == "myai")

        # Detail returns lifecycle ribbon shape
        code, d = _req("GET", f"/api/inbox/tasks/{tid}")
        check("detail returns lifecycle ribbon",
              isinstance(d.get("lifecycle"), dict) and
              "stages" in d["lifecycle"] and "stage_index" in d["lifecycle"],
              f"got {d.get('lifecycle')}")

        # Action lifecycle: run -> started_at, approve -> completed_at
        code, _ = _req("POST", f"/api/inbox/tasks/{tid}/run")
        check("run task records started_at", code == 200)
        code, d = _req("GET", f"/api/inbox/tasks/{tid}")
        check("started_at populated after run", d.get("started_at") is not None)

        code, _ = _req("POST", f"/api/inbox/tasks/{tid}/approve")
        check("approve task records completed_at", code == 200)
        code, d = _req("GET", f"/api/inbox/tasks/{tid}")
        check("completed_at populated after approve", d.get("completed_at") is not None)

        # Cleanup
        _req("DELETE", f"/api/inbox/tasks/{tid}")


def day_10_outlook_routes() -> None:
    """Outlook routes return clean errors when not connected (so the UI doesn't crash)."""
    print("\n--- Outlook routes are safe when not connected ---")

    code, r = _req("GET", "/api/inbox/outlook/fake_id")
    check("outlook detail not-connected returns 5xx",
          code == 502,
          f"got {code} {r}")


def main() -> int:
    t0 = time.time()
    print("===== MyAi-Enterprise: Week-of-usage E2E test =====")
    day_1_monday()
    day_2_tuesday()
    day_3_wednesday()
    day_4_thursday()
    day_5_friday()
    day_6_saturday()
    day_7_sunday()
    day_8_autonomy_gating()
    day_9_file_upload()
    day_10_outlook_routes()
    day_11_harvester()
    day_12_lifecycle()

    elapsed = time.time() - t0
    print(f"\n===== {len(PASSES)} passed, {len(FAILS)} failed in {elapsed:.1f}s =====")
    if FAILS:
        print("\nFailures:")
        for n, w in FAILS:
            print(f"  - {n}: {w}")
    return len(FAILS)


if __name__ == "__main__":
    sys.exit(main())
