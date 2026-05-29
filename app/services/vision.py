"""Vision client — lets the copilot actually see images.

Provider priority:
  1. Anthropic Claude (ANTHROPIC_API_KEY)  — best quality, requires API key
  2. Ollama vision model (llava etc.)      — works locally if user pulled it
  3. Tesseract OCR (pytesseract + binary)  — text extraction only

`describe_image(...)` returns a text description that the calling LLM can use
as if it had read the image itself. The frontend never has to know which path
ran.
"""

from __future__ import annotations

import base64
import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


def _anthropic_key() -> Optional[str]:
    return os.environ.get("ANTHROPIC_API_KEY", "").strip() or None


def _ollama_url() -> str:
    return os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")


_OLLAMA_MODEL_CACHE: Optional[list[str]] = None


def _list_ollama_models() -> list[str]:
    """Cached list of installed Ollama model tags."""
    global _OLLAMA_MODEL_CACHE
    if _OLLAMA_MODEL_CACHE is not None:
        return _OLLAMA_MODEL_CACHE
    try:
        with httpx.Client(timeout=3.0) as c:
            r = c.get(f"{_ollama_url()}/api/tags")
            if r.status_code < 400:
                _OLLAMA_MODEL_CACHE = [m.get("name", "") for m in r.json().get("models", [])]
                return _OLLAMA_MODEL_CACHE
    except Exception:
        pass
    _OLLAMA_MODEL_CACHE = []
    return _OLLAMA_MODEL_CACHE


def _ollama_vision_model() -> Optional[str]:
    # Explicit override always wins
    cfg = os.environ.get("OLLAMA_VISION_MODEL", "").strip()
    if cfg:
        return cfg
    # Auto-detect a known vision model from the installed models list
    installed = _list_ollama_models()
    for candidate in ["llava:13b", "llava:7b", "llava", "llava-llama3", "bakllava", "moondream"]:
        for m in installed:
            if m == candidate or m.startswith(candidate.split(":")[0] + ":"):
                return m
    return None


async def _describe_via_anthropic(
    image_bytes: bytes, media_type: str, prompt: str
) -> Optional[str]:
    key = _anthropic_key()
    if not key:
        return None
    b64 = base64.standard_b64encode(image_bytes).decode()
    model = os.environ.get("ANTHROPIC_VISION_MODEL", "claude-3-5-sonnet-20241022")
    headers = {
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body = {
        "model": model,
        "max_tokens": 1024,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": b64,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    }
    try:
        async with httpx.AsyncClient(timeout=90.0) as c:
            r = await c.post(
                "https://api.anthropic.com/v1/messages", headers=headers, json=body
            )
            if r.status_code >= 400:
                logger.warning("Anthropic vision failed: %s %s", r.status_code, r.text[:200])
                return None
            data = r.json()
            parts = data.get("content") or []
            text = "".join(p.get("text", "") for p in parts if p.get("type") == "text")
            return text.strip() or None
    except Exception as exc:
        logger.warning("Anthropic vision call failed: %s", exc)
        return None


async def _describe_via_ollama(image_bytes: bytes, prompt: str) -> Optional[str]:
    model = _ollama_vision_model()
    if not model:
        return None
    b64 = base64.b64encode(image_bytes).decode()
    body = {
        "model": model,
        "prompt": prompt,
        "images": [b64],
        "stream": False,
    }
    try:
        async with httpx.AsyncClient(timeout=180.0) as c:
            r = await c.post(f"{_ollama_url()}/api/generate", json=body)
            if r.status_code >= 400:
                logger.warning("Ollama vision failed: %s", r.text[:200])
                return None
            return (r.json() or {}).get("response", "").strip() or None
    except Exception as exc:
        logger.warning("Ollama vision call failed: %s", exc)
        return None


def _describe_via_ocr(image_bytes: bytes) -> Optional[str]:
    """Tesseract OCR (text extraction only). Best-effort."""
    try:
        from PIL import Image  # type: ignore
        import pytesseract  # type: ignore
        import io as _io

        img = Image.open(_io.BytesIO(image_bytes))
        text = pytesseract.image_to_string(img)
        text = (text or "").strip()
        if not text:
            return None
        return f"[Text extracted via OCR from image]\n{text}"
    except Exception:
        return None


async def describe_image(
    image_bytes: bytes,
    media_type: str = "image/png",
    prompt: Optional[str] = None,
) -> tuple[str, str]:
    """Return (description, provider_used).

    Never raises — if no provider works, returns a polite fallback that the
    LLM can still pass through to the user (no "install X" instructions).
    """
    prompt = prompt or (
        "Describe this image in detail. If it contains UI, list the visible "
        "buttons, headings, text content and any errors. If it shows a "
        "document or screenshot of text, transcribe the text. Be specific "
        "enough that someone who hasn't seen the image can act on it."
    )

    # 1. Anthropic Claude vision (best)
    desc = await _describe_via_anthropic(image_bytes, media_type, prompt)
    if desc:
        return desc, "anthropic"

    # 2. Ollama vision model (works fully local)
    desc = await _describe_via_ollama(image_bytes, prompt)
    if desc:
        return desc, "ollama"

    # 3. Tesseract OCR fallback
    desc = _describe_via_ocr(image_bytes)
    if desc:
        return desc, "ocr"

    # 4. Friendly fallback — never tell the user to install things
    return (
        "[Image attached. The assistant could not analyse the image content "
        "in this configuration. Please describe what's in it or paste any "
        "important text and the assistant will act on that.]",
        "none",
    )


def _try_ocr() -> bool:
    try:
        import pytesseract  # type: ignore

        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def vision_status() -> dict:
    """Quick capability probe for the settings page."""
    return {
        "anthropic": bool(_anthropic_key()),
        "ollama_vision_model": _ollama_vision_model(),
        "ocr_available": _try_ocr(),
    }
