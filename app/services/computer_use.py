"""Computer use — let MyAi see and control the local desktop.

REAL OS control: PIL/pyautogui screen capture + pyautogui mouse/keyboard. This
module is only the low-level primitive layer — it does NO permission checking.
Every *mutating* primitive (click/type/key/scroll) is gated by the L1-L5 autonomy
dial in ``agent_loop._dispatch`` before it is ever called here. Observing
(screenshot / find_on_screen) is read-only.

Honest limits:
- Click/type targeting is only as good as the model's idea of where things are.
  ``find_on_screen`` uses OCR (pytesseract) to locate on-screen TEXT and return
  its real pixel coordinates — the most reliable way to click without a precise
  vision model. It degrades gracefully (returns None) when tesseract isn't
  installed; the agent then has to fall back to the vision description.
- The agent's planner LLM is text-only, so "seeing" the screen goes through
  ``vision.describe_image`` (Anthropic vision → Ollama vision → OCR → fallback).
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# pyautogui touches the display at import time on some platforms — guard it so a
# headless/CI import of the app never crashes just because there's no desktop.
try:  # pragma: no cover - environment dependent
    import pyautogui  # type: ignore

    pyautogui.FAILSAFE = True   # slam mouse to a corner to abort
    pyautogui.PAUSE = 0.05
    _PYAUTOGUI = True
except Exception as exc:  # noqa: BLE001
    pyautogui = None  # type: ignore
    _PYAUTOGUI = False
    logger.warning("pyautogui unavailable — computer use disabled: %s", exc)


def configure_tesseract() -> bool:
    """Point pytesseract at the tesseract binary.

    On Windows the UB-Mannheim installer drops tesseract in Program Files but
    does NOT add it to PATH, so pytesseract's default ``tesseract`` command
    fails. We resolve the real path once and set ``tesseract_cmd``.
    """
    try:
        import pytesseract  # type: ignore
    except Exception:  # noqa: BLE001
        return False
    import shutil

    cmd = getattr(pytesseract.pytesseract, "tesseract_cmd", "tesseract")
    if cmd and cmd != "tesseract" and os.path.isfile(cmd):
        return True
    env = os.environ.get("TESSERACT_CMD", "").strip()
    candidates = [env] if env else []
    candidates += [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
        os.path.expandvars(r"%USERPROFILE%\Tesseract-OCR\tesseract.exe"),
    ]
    for p in candidates:
        if p and os.path.isfile(p):
            pytesseract.pytesseract.tesseract_cmd = p
            return True
    return bool(shutil.which("tesseract"))


def is_enabled() -> bool:
    """Computer use is on only if the lib loaded AND the deploy hasn't disabled it."""
    if not _PYAUTOGUI:
        return False
    return os.environ.get("COMPUTER_USE_ENABLED", "true").strip().lower() in (
        "1", "true", "yes", "on",
    )


def unavailable_reason() -> str:
    if not _PYAUTOGUI:
        return ("Computer use isn't available on this machine (pyautogui couldn't "
                "load — the server may be running without a desktop session).")
    if not is_enabled():
        return "Computer use is disabled (COMPUTER_USE_ENABLED is off)."
    return ""


def screen_size() -> tuple[int, int]:
    if not _PYAUTOGUI:
        return (0, 0)
    w, h = pyautogui.size()
    return int(w), int(h)


def _clamp(x, y) -> tuple[int, int]:
    w, h = screen_size()
    return max(0, min(int(x), max(0, w - 1))), max(0, min(int(y), max(0, h - 1)))


# --- capture ---------------------------------------------------------------

def _capture_png() -> bytes:
    img = pyautogui.screenshot()
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


async def capture_png() -> bytes:
    return await asyncio.to_thread(_capture_png)


def downscale_png(png: bytes, max_w: int = 1280) -> bytes:
    """Shrink a screenshot so the vision payload stays small/cheap."""
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(png))
        if img.width > max_w:
            ratio = max_w / float(img.width)
            img = img.resize((max_w, int(img.height * ratio)))
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="PNG")
        return buf.getvalue()
    except Exception:  # noqa: BLE001
        return png


# --- mutating primitives (gated upstream) ----------------------------------

def _do_click(x, y, button: str = "left", clicks: int = 1) -> tuple[int, int]:
    cx, cy = _clamp(x, y)
    pyautogui.moveTo(cx, cy, duration=0.15)
    pyautogui.click(cx, cy, clicks=max(1, int(clicks)), interval=0.08,
                    button=("right" if button == "right" else "left"))
    return cx, cy


async def click(x, y, button: str = "left", clicks: int = 1) -> tuple[int, int]:
    return await asyncio.to_thread(_do_click, x, y, button, clicks)


def _do_type(text: str) -> None:
    pyautogui.typewrite(str(text), interval=0.02)


async def type_text(text: str) -> None:
    await asyncio.to_thread(_do_type, text)


def _do_key(keys: str) -> None:
    parts = [k.strip().lower() for k in str(keys).replace(" ", "").split("+") if k.strip()]
    if not parts:
        return
    if len(parts) > 1:
        pyautogui.hotkey(*parts)
    else:
        pyautogui.press(parts[0])


async def press_key(keys: str) -> None:
    await asyncio.to_thread(_do_key, keys)


def _do_scroll(amount) -> None:
    pyautogui.scroll(int(amount))


async def scroll(amount) -> None:
    await asyncio.to_thread(_do_scroll, amount)


# --- OCR element finder (best-effort, read-only) ---------------------------

def _find_on_screen(query: str, max_hits: int = 8) -> Optional[list[dict]]:
    """Return [{text,x,y,conf}] for on-screen words matching ``query``.

    Returns None when OCR isn't available so callers can distinguish
    "no matches" (``[]``) from "can't OCR".
    """
    try:
        import pytesseract  # type: ignore
        from PIL import Image
    except Exception:  # noqa: BLE001
        return None
    if not configure_tesseract():
        return None
    try:
        img = Image.open(io.BytesIO(_capture_png()))
        data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
    except Exception as exc:  # noqa: BLE001
        logger.warning("find_on_screen OCR failed: %s", exc)
        return None

    q = (query or "").lower().strip()
    hits: list[dict] = []
    n = len(data.get("text", []))
    confs = data.get("conf", [])
    for i in range(n):
        word = (data["text"][i] or "").strip()
        if not word or (q and q not in word.lower()):
            continue
        cx = int(data["left"][i] + data["width"][i] / 2)
        cy = int(data["top"][i] + data["height"][i] / 2)
        try:
            conf = int(float(confs[i])) if i < len(confs) else -1
        except (ValueError, TypeError):
            conf = -1
        hits.append({"text": word, "x": cx, "y": cy, "conf": conf})
    hits.sort(key=lambda h: h["conf"], reverse=True)
    return hits[:max_hits]


async def find_on_screen(query: str, max_hits: int = 8) -> Optional[list[dict]]:
    return await asyncio.to_thread(_find_on_screen, query, max_hits)
