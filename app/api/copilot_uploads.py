"""File / screenshot upload endpoint for the copilot.

Extracts text from the upload so the LLM (which may not be multimodal) can
reason about it. Supported extractors:
- text/* and code/*  → raw read
- application/pdf    → pdfplumber (falls back to pypdf)
- application/msword and *.docx → python-docx
- image/*            → OCR via pytesseract when available; otherwise we just
                       report the dimensions + filename so the user knows the
                       upload landed (and a vision-capable model in cloud mode
                       can take over later).

The returned text is *not* automatically sent to the LLM. The frontend
attaches it as a system message on the next /api/copilot/chat call.
"""

from __future__ import annotations

import io
import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile

from app.auth.jwt import PlatformTokenClaims
from app.auth.middleware import get_current_user
from app.services.vision import describe_image

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/copilot", tags=["copilot"])

MAX_BYTES = 12 * 1024 * 1024  # 12 MB cap
TEXT_MIMES = ("text/", "application/json", "application/xml")


def _extract_pdf(data: bytes) -> str:
    text = ""
    try:
        import pdfplumber  # type: ignore

        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for page in pdf.pages[:30]:
                text += (page.extract_text() or "") + "\n\n"
    except Exception as exc:
        logger.debug("pdfplumber failed, trying pypdf: %s", exc)
        try:
            import pypdf  # type: ignore

            reader = pypdf.PdfReader(io.BytesIO(data))
            for page in reader.pages[:30]:
                text += (page.extract_text() or "") + "\n\n"
        except Exception as exc2:
            return f"[PDF extraction failed: {exc2}]"
    return text.strip()


def _extract_docx(data: bytes) -> str:
    try:
        import docx  # type: ignore

        d = docx.Document(io.BytesIO(data))
        return "\n".join(p.text for p in d.paragraphs if p.text).strip()
    except Exception as exc:
        return f"[DOCX extraction failed: {exc}]"


# Images now go through app.services.vision.describe_image which tries
# Anthropic Claude → Ollama vision model → tesseract OCR → friendly fallback.


@router.post("/upload", response_model=Dict[str, Any])
async def upload_attachment(
    request: Request,
    file: UploadFile = File(...),
    user: PlatformTokenClaims = Depends(get_current_user),
) -> Dict[str, Any]:
    raw = await file.read()
    if len(raw) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="File too large (max 12 MB)")
    mime = file.content_type or ""
    name = file.filename or "upload"

    text = ""
    if mime.startswith(TEXT_MIMES) or name.endswith((".md", ".txt", ".log", ".py", ".js", ".csv", ".json", ".yml", ".yaml")):
        try:
            text = raw.decode("utf-8", errors="replace")
        except Exception as exc:
            text = f"[Text decode failed: {exc}]"
    elif mime == "application/pdf" or name.endswith(".pdf"):
        text = _extract_pdf(raw)
    elif "wordprocessingml" in mime or name.endswith(".docx"):
        text = _extract_docx(raw)
    elif mime.startswith("image/") or name.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
        mt = mime if mime.startswith("image/") else "image/png"
        text, provider = await describe_image(raw, media_type=mt)
        logger.info("Image description via %s for %s (%d bytes)", provider, name, len(raw))
    else:
        text = (
            f"[{name} ({mime or 'unknown type'}, {len(raw)} bytes) attached. "
            "MyAi can read text, code, PDFs, Word docs, and screenshots — "
            "this format isn't one of those. If it's important, paste the "
            "key content as text and MyAi will work with that.]"
        )

    return {
        "filename": name,
        "content_type": mime,
        "size_bytes": len(raw),
        "extracted_text": text[:20000],
        "truncated": len(text) > 20000,
    }
