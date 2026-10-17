"""v1 single-finding packet PDF (fpdf2), kept for POST /packets compatibility; the v2 combined invoice is in
invoice.py. Uses only verified evidence and never internal comments. Core PDF fonts are Latin-1, so text is
transliterated where needed."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from typing import Optional

from fpdf import FPDF
from fpdf.enums import XPos, YPos

from .validate import fmt_money, parse_decimal

DISCLAIMER = (
    "This packet was assembled with AI assistance from project records and reviewed by the project manager. "
    "Flagging work as potential extra work is not a determination of entitlement to payment or of contractual "
    "responsibility. Open questions, including any missing authorization, remain unresolved unless answered below. "
    "Only evidence whose quoted text was verified against the source document is listed."
)

_TRANSLIT = {
    "‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", "−": "-",
    "…": "...", "•": "*", " ": " ", "→": "->", "≤": "<=", "≥": ">=",
}


def latin1(text: Optional[str]) -> str:
    if not text:
        return ""
    t = "".join(_TRANSLIT.get(ch, ch) for ch in str(text))
    t = unicodedata.normalize("NFKC", t)
    return t.encode("latin-1", errors="replace").decode("latin-1")


def packet_filename(title: str, when: datetime) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-")[:60] or "finding"
    return f"ScopeGuard-CR_{slug}_{when.strftime('%Y%m%d-%H%M%S')}Z.pdf"


class _PDF(FPDF):
    def footer(self) -> None:
        self.set_y(-12)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(110)
        self.cell(0, 6, latin1(f"ScopeGuard change-request packet - not a determination of entitlement - page {self.page_no()}/{{nb}}"),
                  align="C")


def render_packet(finding: dict, files: list[dict], generated_at: Optional[datetime] = None) -> bytes:
    """Render one v2 Finding (docs/api-contract-v2.md) as a change-request packet."""
    generated_at = generated_at or datetime.now(timezone.utc)
    pdf = _PDF(format="Letter")
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.set_margins(16, 16, 16)
    pdf.alias_nb_pages()
    pdf.add_page()
    w = pdf.epw

    def h1(text: str) -> None:
        pdf.set_font("Helvetica", "B", 16)
        pdf.set_text_color(20)
        pdf.multi_cell(w, 8, latin1(text), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    def h2(text: str) -> None:
        pdf.ln(3)
        pdf.set_font("Helvetica", "B", 12)
        pdf.set_text_color(20)
        pdf.cell(w, 7, latin1(text), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_draw_color(180)
        pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + w, pdf.get_y())
        pdf.ln(1.5)

    def para(text: str, size: float = 10, style: str = "", color: int = 30) -> None:
        pdf.set_font("Helvetica", style, size)
        pdf.set_text_color(color)
        pdf.multi_cell(w, 5, latin1(text), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    h1(f"Change Request: {finding.get('code', '')} {finding.get('title', '')}")
    para(f"Finding ID: {finding.get('finding_id')}   |   Scan: {finding.get('scan_id')}   |   "
         f"{finding.get('type_label', '')}   |   Generated: {generated_at.strftime('%Y-%m-%d %H:%M UTC')}", 8.5, color=90)
    pdf.ln(2)
    pdf.set_fill_color(255, 246, 220)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(90, 60, 0)
    pdf.multi_cell(w, 4.6, latin1("Disclaimer: " + DISCLAIMER), fill=True, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    h2("Summary")
    para(finding.get("approval_label", ""), 9.5, "B")
    para(finding.get("review", {}).get("customer_description") or finding.get("reason", ""))
    h2("Why this was flagged")
    para(finding.get("explanation", ""))
    h2("Cost")
    for it in finding.get("cost_items", []):
        para(f"{it.get('description', '')}: {it.get('formula', '')}", 9.5)
    amount = parse_decimal(finding.get("amount"))
    para(f"Amount: {fmt_money(amount) if amount is not None else 'Unknown'}", 10, "B")
    h2("Evidence references (verified)")
    for g in finding.get("evidence_groups", []):
        para(f"{g.get('label')} - {g.get('source_label')}", 9.5, "B")
        for e in g.get("evidence", []):
            if not e.get("verified"):
                continue
            para(f'"{e.get("quote", "")}"', 9.5, "I")
            para(f"{e.get('file_name')} - page {e.get('page')} - file {e.get('file_id')}, version {e.get('version_id')}", 8, color=90)
        pdf.ln(1)
    h2("Open questions")
    questions = finding.get("questions", [])
    if not questions:
        para("None.", 9.5, "I")
    for q in questions:
        para(f"[{q.get('kind')}] {q.get('text')}", 9.5)
    return bytes(pdf.output())
