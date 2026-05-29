"""Playwright UI smoke test — drives the real browser, clicks real buttons,
verifies real renders. This catches the gap that the API-only e2e misses.

Run while the dev server is up at http://localhost:8002:
    python tests/ui_smoke.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, expect, TimeoutError as PWTimeout

BASE = "http://localhost:8002"
PASSES: list[str] = []
FAILS: list[tuple[str, str]] = []


def check(name: str, ok: bool, why: str = "") -> bool:
    if ok:
        PASSES.append(name)
        print(f"  PASS  {name}")
    else:
        FAILS.append((name, why))
        print(f"  FAIL  {name} :: {why}")
    return ok


def safe(label: str, fn, *args, **kwargs) -> bool:
    try:
        fn(*args, **kwargs)
        return check(label, True)
    except Exception as e:
        return check(label, False, str(e)[:200])


def _make_test_png(path: Path) -> None:
    """Generate a small PNG with readable text for the vision test."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (480, 160), "white")
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 479, 159], outline="black", width=2)
    d.text((30, 30), "MEETING NOTES", fill="black")
    d.text((30, 60), "Project: MyAi rollout", fill="black")
    d.text((30, 90), "Deadline: Friday", fill="black")
    d.text((30, 120), "Owner: Anubhav", fill="black")
    img.save(path)


def run() -> int:
    print("===== UI smoke test (Playwright) =====")

    test_png = Path("tests/_test_screenshot.png").resolve()
    _make_test_png(test_png)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1400, "height": 900})
        page = ctx.new_page()

        # ---- Sidebar identity loads ---------------------------------------
        page.goto(BASE, wait_until="networkidle")
        page.wait_for_selector("#userName", timeout=10_000)

        name = page.locator("#userName").inner_text(timeout=5_000)
        check("sidebar shows Dev User (not Jigar)", name.strip() == "Dev User", f"got '{name}'")

        role = page.locator("#userRole").inner_text(timeout=5_000)
        check("sidebar shows connected gmail",
              "@gmail.com" in role.lower() or "@nexgai.com" in role.lower(),
              f"got '{role}'")

        # ---- Dashboard ------------------------------------------------------
        page.locator("a[data-route='dashboard']").click()
        page.wait_for_selector("#kpiRow .kpi", timeout=10_000)
        kpi_labels = page.locator(".kpi-label").all_inner_texts()
        check("dashboard KPI tiles render", len(kpi_labels) >= 6, f"got {len(kpi_labels)}")
        check("dashboard does NOT show old 'UNREAD EMAILS' label",
              "UNREAD EMAILS" not in kpi_labels,
              f"labels: {kpi_labels}")
        # Verify 'Active threads' was renamed
        page_text = page.content()
        check("dashboard card titled 'Active tasks'",
              "Active tasks" in page_text and "Active threads" not in page_text)

        # ---- Inbox + account switcher --------------------------------------
        page.locator("a[data-route='inbox']").click()
        page.wait_for_selector("#taskList", timeout=10_000)

        acc_buttons = page.locator("#accountSwitcher button").all_inner_texts()
        check("account switcher shows All / Gmail / Outlook",
              any("Gmail" in t for t in acc_buttons) and any("Outlook" in t for t in acc_buttons),
              f"got {acc_buttons}")

        # Check: no obvious promotional email is classified High/Critical.
        # Walks the first 5 items; if any clearly-promo title is marked
        # High/Critical, that's a regression in the classifier.
        items = page.locator(".task").all()[:5]
        promo_hits = []
        for it in items:
            title = it.locator(".task-title").inner_text()
            pill = it.locator(".pill").first.inner_text() if it.locator(".pill").count() else ""
            looks_promo = any(s in title.lower() for s in [
                "% off", " off!", "sale", "deal", "save ", "exclusive", "coupon",
                "ends on", "ends in", "limited time", "newsletter",
            ])
            if looks_promo and pill in {"Critical", "High"}:
                promo_hits.append(f"{pill}: {title}")
        check("no promo email is marked High/Critical",
              len(promo_hits) == 0,
              f"promos miscategorised: {promo_hits}")

        # Source filter buttons exist
        check("source filters present (Email, Calendar, Tasks)",
              page.locator("button[data-src='email']").count() == 1 and
              page.locator("button[data-src='calendar']").count() == 1 and
              page.locator("button[data-src='manual']").count() == 1)

        # ---- Autonomy slider clicks and saves ------------------------------
        before = page.locator("#levelBadge").inner_text()
        # Click L3 node
        page.locator(".level[data-level='2']").click()
        page.wait_for_timeout(400)
        after = page.locator("#levelBadge").inner_text()
        check("autonomy slider visually updates", "L3" in after, f"badge='{after}' before='{before}'")
        # reload page; level should persist
        page.reload(wait_until="networkidle")
        page.locator("a[data-route='inbox']").click()
        page.wait_for_selector("#levelBadge", timeout=10_000)
        persisted = page.locator("#levelBadge").inner_text()
        check("autonomy persists after reload", "L3" in persisted, f"got '{persisted}'")
        # Put it back to L1
        page.locator(".level[data-level='0']").click()
        page.wait_for_timeout(400)

        # ---- Copilot -------------------------------------------------------
        page.locator("a[data-route='copilot']").click()
        page.wait_for_selector("#copilotInput", timeout=10_000)

        # Header should say MyAi, not Max
        chat_head = page.locator(".copilot-head").inner_text()
        check("copilot header says MyAi", "MyAi" in chat_head and "Max" not in chat_head, f"head='{chat_head[:120]}'")

        # Send a quick math question and wait for reply
        page.locator("#copilotInput").fill("What is 12 times 11?")
        page.locator("#copilotSend").click()
        try:
            page.wait_for_selector(".msg.ai:not(.typing-bubble)", timeout=120_000)
            ai_text = page.locator(".msg.ai").last.inner_text()
            check("copilot reply mentions 132", "132" in ai_text, f"got '{ai_text[:120]}'")
        except PWTimeout:
            check("copilot reply within 2 min", False, "timeout waiting for ai bubble")

        # ---- Attachment upload via input + send ----------------------------
        # New chat to clear state
        page.locator("#newChatBtn").click()
        page.wait_for_timeout(500)

        # Upload the test screenshot via the hidden file input
        page.set_input_files("#copilotFile", str(test_png))

        # Wait for upload to COMPLETE — the chip transitions from
        # "Uploading…" to showing the file size. Vision (llava) can take ~15s.
        try:
            page.wait_for_selector(f"text=_test_screenshot.png", timeout=10_000)
            page.wait_for_function(
                """() => {
                    const row = document.getElementById('attachmentRow');
                    if (!row) return false;
                    // chip is fully uploaded when it no longer shows 'Uploading…'
                    return row.innerHTML.includes('KB') && !row.innerHTML.includes('Uploading');
                }""",
                timeout=90_000,  # llava on 7B can be slow
            )
            check("attachment chip shows filename before send", True)
        except PWTimeout as e:
            check("attachment chip shows filename before send", False, f"upload didn't complete: {e}")

        # Send with no text — must be allowed
        page.locator("#copilotSend").click()
        try:
            page.wait_for_selector(".msg.user", timeout=10_000)
            user_bubble = page.locator(".msg.user").last.inner_text()
            check("attachment-only send produces user bubble",
                  "Sent an attachment" in user_bubble or "_test_screenshot" in user_bubble,
                  f"got '{user_bubble[:120]}'")
            # Image preview should be present in the bubble
            img_in_bubble = page.locator(".msg.user img").count()
            check("user bubble has inline image preview", img_in_bubble >= 1, f"count={img_in_bubble}")
        except PWTimeout:
            check("attachment-only send produces user bubble", False, "no user bubble")

        # ---- Sign out / profile menu (we don't actually sign out, just check it opens) ---
        page.locator("a[data-route='dashboard']").click()
        page.wait_for_selector("#userBtn", timeout=5_000)
        page.locator("#userBtn").click()
        page.wait_for_timeout(200)
        menu_open = page.locator("#userMenu.open").count()
        check("profile menu opens on click", menu_open == 1, f"count={menu_open}")
        # close it
        page.mouse.click(10, 10)

        # ---- Logs page (no synthetic Jigar Patel rows) ---------------------
        page.locator("a[data-route='logs']").click()
        page.wait_for_timeout(2000)  # let the fetch + render happen
        log_html = page.locator("#logRows").inner_html()
        check("logs page has no 'Jigar Patel' fake rows",
              "Jigar Patel" not in log_html and "Sarah M." not in log_html and "retention_offer" not in log_html,
              "found bank-themed mock content")

        # ---- Settings page (no bank policies, IST in tz) -------------------
        page.locator("a[data-route='settings']").click()
        page.wait_for_selector("#settingsTz", timeout=5_000)
        tz_options = page.locator("#settingsTz option").all_inner_texts()
        check("settings tz includes IST", any("Kolkata" in o for o in tz_options), f"got {tz_options}")
        settings_html = page.content()
        check("settings does not mention '30% discount' or 'retention offers'",
              "30% discount" not in settings_html and "retention offers" not in settings_html)

        browser.close()

    # Cleanup
    try:
        test_png.unlink()
    except Exception:
        pass

    elapsed_summary = f"{len(PASSES)} passed, {len(FAILS)} failed"
    print(f"\n===== {elapsed_summary} =====")
    if FAILS:
        print("\nFailures:")
        for n, w in FAILS:
            print(f"  - {n}: {w}")
    return len(FAILS)


if __name__ == "__main__":
    sys.exit(run())
