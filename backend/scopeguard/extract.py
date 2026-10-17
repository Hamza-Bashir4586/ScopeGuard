"""Per-page text extraction: PDF via pypdf; .txt/.md as one page, or split on form feeds."""

from __future__ import annotations

import io
import logging

log = logging.getLogger(__name__)


def extract_pages(name: str, data: bytes) -> list[str]:
    lower = name.lower()
    if lower.endswith(".pdf"):
        return _pdf_pages(data)
    if lower.endswith((".txt", ".md")):
        return _text_pages(data)
    raise ValueError(f"Unsupported file type: {name}")


def _text_pages(data: bytes) -> list[str]:
    text = data.decode("utf-8-sig", errors="replace").replace("\r\n", "\n")
    pages = text.split("\f")
    while len(pages) > 1 and not pages[-1].strip():
        pages.pop()
    return pages or [""]


def _pdf_pages(data: bytes) -> list[str]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages = []
    for i, page in enumerate(reader.pages):
        try:
            pages.append(page.extract_text() or "")
        except Exception as exc:  # a single bad page should not fail the scan
            log.warning("Text extraction failed on page %s: %s", i + 1, exc)
            pages.append("")
    return pages or [""]
