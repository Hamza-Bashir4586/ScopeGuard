"""Change-request packet PDF (fpdf2). Uses effective (reviewer-edited) values and only
verified evidence. Core PDF fonts are Latin-1, so text is transliterated where needed."""

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


def _strip_md(text: str) -> str:
    t = re.sub(r"^#{1,6}\s*", "", text, flags=re.M)
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", t)
    t = re.sub(r"__(.+?)__", r"\1", t)
    return t


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

    verified = [e for e in finding.get("evidence", []) if e.get("verified")]
    ev_index = {e["evidence_id"]: i for i, e in enumerate(verified, start=1)}

    h1(f"Change Request: {finding.get('title', '')}")
    para(f"Finding ID: {finding.get('finding_id')}   |   Scan: {finding.get('scan_id')}   |   "
         f"Status: {finding.get('finding_status')}   |   Generated: {generated_at.strftime('%Y-%m-%d %H:%M UTC')}", 8.5, color=90)
    pdf.ln(2)
    pdf.set_fill_color(255, 246, 220)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(90, 60, 0)
    pdf.multi_cell(w, 4.6, latin1("Disclaimer: " + DISCLAIMER), fill=True, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    h2("Summary")
    para(finding.get("summary", ""))
    pdf.ln(1)
    para("Original scope: " + finding.get("scope_baseline", ""), 9.5)
    para("Requested / performed work: " + finding.get("requested_work", ""), 9.5)
    if finding.get("co_match"):
        co = finding["co_match"]
        amt = f" ({fmt_money(parse_decimal(co['amount']))})" if co.get("amount") and parse_decimal(co["amount"]) is not None else ""
        para(f"Change-order log: appears as {co['co_number']} - {co.get('description', '')}{amt}", 9.5, "B")

    h2("Narrative")
    para(_strip_md(finding.get("draft", "")))

    h2("Timeline")
    timeline = finding.get("timeline", [])
    if not timeline:
        para("No dated events.", 9.5, "I")
    for t in timeline:
        ref = f"  [E{ev_index[t['evidence_id']]}]" if t.get("evidence_id") in ev_index else ""
        para(f"{t.get('date', '')}   {t.get('event', '')}{ref}", 9.5)

    h2("Cost worksheet")
    items = finding.get("cost_items", [])
    cols = [("Category", 0.11), ("Description", 0.33), ("Qty", 0.08), ("Unit", 0.07), ("Rate", 0.12), ("Amount", 0.13), ("Src", 0.16)]
    pdf.set_font("Helvetica", "B", 8.5)
    pdf.set_fill_color(235)
    pdf.set_text_color(20)
    for name, frac in cols:
        pdf.cell(w * frac, 6, name, border=1, fill=True)
    pdf.ln()
    pdf.set_font("Helvetica", "", 8.5)
    for it in items:
        rate = parse_decimal(it.get("rate"))
        amount = parse_decimal(it.get("amount"))
        src = it.get("source_evidence_id")
        values = [it.get("category", ""), it.get("description", ""), it.get("qty") or "?", it.get("unit", ""),
                  fmt_money(rate) if rate is not None else "?", fmt_money(amount) if amount is not None else "-",
                  f"E{ev_index[src]}" if src in ev_index else ""]
        # row height from the description wrap
        desc_lines = pdf.multi_cell(w * cols[1][1], 4.5, latin1(values[1]), dry_run=True, output="LINES")
        row_h = max(6, 4.5 * len(desc_lines))
        if pdf.get_y() + row_h + 10 > pdf.page_break_trigger:
            pdf.add_page()
        y0, x0 = pdf.get_y(), pdf.get_x()
        x = x0
        for (name, frac), val in zip(cols, values):
            pdf.set_xy(x, y0)
            if name == "Description":
                pdf.multi_cell(w * frac, 4.5, latin1(val), border=0)
                pdf.rect(x, y0, w * frac, row_h)
            else:
                pdf.cell(w * frac, row_h, latin1(str(val)), border=1, align="R" if name in ("Qty", "Rate", "Amount") else "L")
            x += w * frac
        pdf.set_xy(x0, y0 + row_h)
        pdf.set_font("Helvetica", "I", 7.5)
        pdf.set_text_color(90)
        pdf.multi_cell(w, 4, latin1("    " + it.get("formula", "")), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_font("Helvetica", "", 8.5)
        pdf.set_text_color(20)
    pdf.set_font("Helvetica", "B", 10)
    total = parse_decimal(finding.get("cost_total"))
    pdf.cell(w, 7, latin1(f"Total (items with known qty and rate): {fmt_money(total) if total is not None else '$0.00'}"),
             align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    if any(it.get("amount") is None for it in items):
        para("Items marked '?' have an unknown quantity or rate and are excluded from the total.", 8.5, "I", 90)

    h2("Evidence references (verified)")
    if not verified:
        para("No verified evidence.", 9.5, "I")
    names = {f["box_file_id"]: f.get("name") for f in files}
    for e in verified:
        idx = ev_index[e["evidence_id"]]
        para(f"E{idx}. {e.get('file_name') or names.get(e['box_file_id'], '')} - page {e.get('page')} - role: {e.get('role')}", 9.5, "B")
        para(f'"{e.get("quote", "")}"', 9.5, "I")
        para(f"Box file ID {e.get('box_file_id')}, version ID {e.get('box_version_id')} - {e.get('box_url')}", 8, color=90)
        pdf.ln(1)

    h2("Open questions")
    questions = finding.get("questions", [])
    if not questions:
        para("None.", 9.5, "I")
    for q in questions:
        para(f"[{q.get('kind')}] {q.get('text')}", 9.5)
        para("Resolution: " + (q.get("resolution") or "UNRESOLVED"), 9, "B" if q.get("resolution") else "BI",
             40 if q.get("resolution") else 150)
        pdf.ln(1)

    out = pdf.output()
    return bytes(out)
