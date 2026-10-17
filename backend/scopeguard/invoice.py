"""Supplemental invoice draft generations [INV-03..12].

POST generation revalidates the current draft against the latest audit, snapshots it immutably
(projects/<id>/invoice-generations/<gid>/manifest.json) and renders one PDF (draft.pdf, temporary in S3).
The generation ID is a hash of project + request_id + draft revision, so the same request returns the same
generation. Save-to-Box uploads that exact PDF once to the output folder, reconciling by the unique expected
filename before any upload, and stores the Box result on the manifest.

The PDF is labeled "Draft - for customer review and sign-off" and "Not sent", discloses approvals vs proposals,
embeds verified evidence excerpts per line, includes a customer acceptance section and never includes internal
comments. Core PDF fonts are Latin-1 only, so all text is transliterated.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Optional

from fpdf import FPDF
from fpdf.enums import XPos, YPos

from . import box_client, config, projects, records, reviews
from .box_client import SourceError, SourceUnavailable
from .errors import ApiError, bad_request, conflict, not_found, unprocessable
from .packet import latin1
from .reconcile import short_hash
from .serialize import now_iso, parse_iso
from .store import BaseStore, get_store
from .validate import fmt_money, parse_decimal

APPENDIX_EXPLANATIONS = {
    "Original agreement": "Shows what the signed agreement includes or excludes for this work.",
    "Approved change and completed work": "Shows the signed change approval and that the work was completed.",
    "Billing comparison": "Shows the supplied invoice contains no matching charge for this work.",
    "Changed requirement": "Shows the revised requirement that increases the quantity beyond the signed scope.",
    "Proposed pricing": "Supports the proposed quantity and unit price.",
    "New instruction": "Shows the instruction to perform the work.",
    "Work and cost record": "Supports the recorded hours and the agreement rate used for pricing.",
    "Approval": "Shows the recorded approval.",
}


def _gen_key(project_id: str, gid: str, name: str) -> str:
    return BaseStore.project_key(project_id, f"invoice-generations/{gid}/{name}")


def _public(m: dict) -> dict:
    keys = ("generation_id", "project_id", "scan_id", "draft_revision", "created_at", "file_name", "content_hash", "lines",
            "subtotal", "appendix", "content_path", "box")
    return {k: m.get(k) for k in keys}


def get_generation(project_id: str, gid: str) -> dict:
    projects.get_project(project_id)
    m = get_store().get_json(_gen_key(project_id, gid, "manifest.json"))
    if m is None:
        raise not_found(f"Invoice generation {gid} not found", "generation_not_found")
    return m


FACT_EXPLANATIONS = {
    "timesheet_hours": "Recorded labor hours used for pricing.",
    "labor_rate": "Agreement labor rate used for pricing.",
    "work_completion": "Shows the work was performed.",
    "quote": "Supports the proposed quantity and unit price.",
    "billing_note": "Shows the supplied invoice contains no matching charge for this work.",
    "invoice_line": "Supplied invoice line compared against this work; it is not a matching charge.",
}


def _explain(group_label: str, e: dict) -> str:
    if e.get("fact_type") == "approval":
        return ("Shows the recorded approval and approved amount." if e.get("approved")
                else "Shows that customer acceptance is not recorded; this line remains a proposal.")
    if group_label in ("Original agreement", "Changed requirement", "New instruction"):
        return APPENDIX_EXPLANATIONS[group_label]
    return FACT_EXPLANATIONS.get(e.get("fact_type") or "") or APPENDIX_EXPLANATIONS.get(group_label, group_label)


def _appendix(finding: dict) -> list[dict]:
    entries = []
    for g in finding.get("evidence_groups", []):
        for e in g["evidence"]:
            if not e.get("verified"):
                continue  # only verified excerpts reach customer documents [AI-05]
            entries.append({"label": g["label"], "file_name": e["file_name"], "version_id": e["version_id"],
                            "page": int(e["page"]), "quote": e["quote"],
                            "explanation": _explain(g["label"], e)})
    return entries


def _file_name(project: dict, revision: int, gid: str) -> str:
    raw = f"Supplemental-Invoice-Draft_{project['code']}_r{revision}_{gid[2:10]}.pdf"
    return re.sub(r"[^A-Za-z0-9._-]+", "-", raw)


def create_generation(project_id: str, body: dict) -> dict:
    from . import scans

    project = projects.get_project(project_id)
    request_id = body.get("request_id")
    if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 200:
        raise bad_request("'request_id' is required (string, at most 200 characters)")
    expected = body.get("expected_revision")
    if isinstance(expected, bool) or not isinstance(expected, int):
        raise bad_request("'expected_revision' must be an integer")
    store = get_store()
    gid = "g_" + short_hash(project_id, request_id, str(expected), length=20)
    existing = store.get_json(_gen_key(project_id, gid, "manifest.json"))
    if existing:
        return existing  # idempotent: same request + revision -> same generation [INV-10]

    draft = reviews.draft_view(project_id, store)
    if draft["revision"] != expected:
        raise conflict("The invoice draft changed. Reload and try again.", "revision_conflict",
                       {"current_revision": draft["revision"]})
    # Revalidate at generation time, including whether the signed agreement changed since the audit [INV-03].
    latest = scans.latest_completed_scan(store, project_id)
    blockers = list(draft["blockers"])
    if latest:
        refs = records.inventory()
        if latest.get("baseline_version") and projects.baseline_version(project, refs) != latest["baseline_version"]:
            blockers.append("The signed agreement changed after this audit; refresh the audit")
    if blockers or not latest:
        detail = blockers + [f"{l['code']}: {b}" for l in draft["lines"] for b in l["blockers"]]
        raise unprocessable("The invoice draft cannot be generated yet.", "generation_blocked",
                            {"blockers": detail or ["Run an audit first"]})
    findings = scans.findings_by_work_item(store, latest["scan_id"])
    revs = reviews.load_reviews(store, project_id)
    lines = [dict(l) for l in draft["lines"]]
    for l in lines:
        l.pop("blockers", None)
        l["blockers"] = []
    appendix = [{"line_index": i, "title": f"{l['code']} {l['title']}", "entries": _appendix(findings[l["work_item_id"]])}
                for i, l in enumerate(lines, start=1)]
    created_at = now_iso()
    manifest = {
        "generation_id": gid, "project_id": project_id, "scan_id": latest["scan_id"], "draft_revision": draft["revision"],
        "request_id": request_id, "created_at": created_at, "file_name": _file_name(project, draft["revision"], gid),
        "content_hash": "", "lines": lines, "subtotal": draft["subtotal"], "appendix": appendix,
        "content_path": f"/projects/{project_id}/invoice-generations/{gid}/content", "box": None,
        "review_revisions": {l["work_item_id"]: int((revs.get(l["work_item_id"]) or {}).get("revision", 0)) for l in lines},
        "source_versions": sorted({(e["file_name"], e["version_id"]) for a in appendix for e in a["entries"]}),
        "project": {k: project[k] for k in ("code", "name", "contractor", "customer", "contract_label", "currency")},
    }
    pdf = render_invoice(manifest)
    if len(pdf) > config.MAX_FILE_BYTES:
        raise ApiError(413, "The generated invoice is larger than the 4 MiB download limit.", "file_too_large")
    manifest["content_hash"] = hashlib.sha256(pdf).hexdigest()
    store.put_bytes(_gen_key(project_id, gid, "draft.pdf"), pdf, "application/pdf", temporary=True)
    if not store.create_json(_gen_key(project_id, gid, "manifest.json"), manifest):
        return store.get_json(_gen_key(project_id, gid, "manifest.json"))  # concurrent identical request won
    return manifest


def generation_pdf(project_id: str, gid: str) -> tuple[bytes, dict]:
    """The exact generated PDF: S3 copy, else a deterministic re-render verified by hash, else the Box copy."""
    m = get_generation(project_id, gid)
    store = get_store()
    got = store.get_bytes(_gen_key(project_id, gid, "draft.pdf"))
    data = got[0] if got else None
    if data is None:
        rendered = render_invoice(m)
        if hashlib.sha256(rendered).hexdigest() == m["content_hash"]:
            data = rendered
        elif m.get("box"):
            try:
                data = box_client.get_source().download_output(m["box"]["file_id"])
            except (SourceUnavailable, SourceError):
                data = None
    if data is None:
        raise ApiError(410, "This generated invoice is no longer available; generate it again.", "generation_expired")
    return data, m


def save_to_box(project_id: str, gid: str) -> dict:
    m = get_generation(project_id, gid)
    if m.get("box"):
        return m  # repeat saves return the stored Box result [INV-12]
    data, _ = generation_pdf(project_id, gid)
    source = box_client.get_source()
    try:
        out = source.find_output(m["file_name"]) or source.upload_output(m["file_name"], data)
    except SourceError as exc:
        raise ApiError(502, str(exc), "box_unavailable") from None
    box = {"file_id": out.file_id, "version_id": out.version_id, "url": out.url, "saved_at": now_iso()}

    def mutate(cur: Optional[dict]) -> Optional[dict]:
        if cur is None or cur.get("box"):
            return None
        cur["box"] = box
        return cur

    store = get_store()
    store.update_json(_gen_key(project_id, gid, "manifest.json"), mutate)
    return store.get_json(_gen_key(project_id, gid, "manifest.json"))


# ---------------------------------------------------------------- PDF


class _InvoicePDF(FPDF):
    def footer(self) -> None:
        self.set_y(-12)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(110)
        self.cell(0, 6, latin1(f"Supplemental invoice draft - Not sent - for customer review and sign-off - page {self.page_no()}/{{nb}}"),
                  align="C")


def _money(v: Optional[str]) -> str:
    d = parse_decimal(v)
    return fmt_money(d) if d is not None else "Unknown"


def _disclosure(line: dict) -> str:
    if line.get("authorization_status") == "approved" and not line.get("proposal"):
        return f"{line['approval_label']}."
    if line.get("authorization_status") == "unclear":
        return ("Proposal - authorization unresolved. Provided for customer review; requires customer acceptance "
                "before it is payable.")
    return "Proposal - requires customer acceptance; not yet approved."


def render_invoice(m: dict) -> bytes:
    created = parse_iso(m["created_at"]) or datetime.now(timezone.utc)
    proj = m.get("project") or {}
    pdf = _InvoicePDF(format="Letter")
    pdf.set_creation_date(created)  # deterministic bytes for the same snapshot
    pdf.set_title(latin1(f"Supplemental invoice draft {proj.get('code', '')}"))
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.set_margins(16, 16, 16)
    pdf.alias_nb_pages()
    pdf.add_page()
    w = pdf.epw

    def text(t: str, size: float = 10, style: str = "", color: int = 30, h: float = 5) -> None:
        pdf.set_font("Helvetica", style, size)
        pdf.set_text_color(color)
        pdf.multi_cell(w, h, latin1(t), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    def heading(t: str) -> None:
        pdf.ln(3)
        pdf.set_font("Helvetica", "B", 12)
        pdf.set_text_color(20)
        pdf.cell(w, 7, latin1(t), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_draw_color(180)
        pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + w, pdf.get_y())
        pdf.ln(1.5)

    text("SUPPLEMENTAL INVOICE · DRAFT", 18, "B", 20, 9)
    pdf.set_fill_color(255, 246, 220)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(90, 60, 0)
    pdf.multi_cell(w, 6, latin1("Draft — for customer review and sign-off   |   Status: Not sent"), fill=True,
                   new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(2)
    text(f"Draft reference: {m['generation_id']} (draft revision {m['draft_revision']})   |   Date: {created.strftime('%Y-%m-%d')}"
         f"   |   Currency: {proj.get('currency', 'USD')}", 8.5, color=90)
    text(f"Audit: {m['scan_id']}", 8.5, color=90)
    pdf.ln(2)
    text(f"From: {proj.get('contractor', '')}", 10, "B")
    text(f"To: {proj.get('customer', '')}", 10, "B")
    text(f"Project: {proj.get('name', '')} ({proj.get('code', '')}) - {proj.get('contract_label', '')}", 10)

    heading("Invoice lines")
    cols = [("#", 0.06), ("Description", 0.5), ("Approval / disclosure", 0.28), ("Amount", 0.16)]
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(235)
    pdf.set_text_color(20)
    for name, frac in cols:
        pdf.cell(w * frac, 6, name, border=1, fill=True)
    pdf.ln()
    for i, line in enumerate(m["lines"], start=1):
        desc = f"{line['code']} - {line['description']}"
        disc = _disclosure(line)
        pdf.set_font("Helvetica", "", 9)
        h_desc = len(pdf.multi_cell(w * cols[1][1], 4.5, latin1(desc), dry_run=True, output="LINES"))
        h_disc = len(pdf.multi_cell(w * cols[2][1], 4.5, latin1(disc), dry_run=True, output="LINES"))
        row_h = max(6, 4.5 * max(h_desc, h_disc))
        if pdf.get_y() + row_h > pdf.page_break_trigger:
            pdf.add_page()
        y0, x0 = pdf.get_y(), pdf.l_margin
        pdf.set_xy(x0, y0)
        pdf.cell(w * cols[0][1], row_h, str(i), border=1)
        x = x0 + w * cols[0][1]
        for val, frac in ((desc, cols[1][1]), (disc, cols[2][1])):
            pdf.set_xy(x, y0)
            pdf.multi_cell(w * frac, 4.5, latin1(val), border=0)
            pdf.rect(x, y0, w * frac, row_h)
            x += w * frac
        pdf.set_xy(x, y0)
        pdf.cell(w * cols[3][1], row_h, latin1(_money(line["amount"])), border=1, align="R")
        pdf.set_xy(x0, y0 + row_h)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(w, 8, latin1(f"Subtotal (selected items): {_money(m['subtotal'])}"), align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    text("Taxes, payment terms and retainage are not applied in this draft. Complete them per the agreement before issuing. "
         "Existing invoice balances and retainage are tracked separately and are not included as new charges. "
         "Proposed items are offered for customer acceptance; this draft does not by itself establish entitlement to payment.",
         8.5, "I", 70, 4.4)

    heading("Customer acceptance")
    text("The customer accepts the lines marked below. Proposed items become payable only once accepted.", 9.5)
    pdf.ln(1)
    for i, line in enumerate(m["lines"], start=1):
        text(f"[   ] Accept line {i}: {line['code']} - {line['description']} ({_money(line['amount'])})", 9.5)
    pdf.ln(3)
    for label in ("Accepted by (name, title)", "Signature", "Date"):
        text(f"{label}: ________________________________________", 10, h=8)

    heading("Evidence appendix")
    text("Verified excerpts from the project records, organized by invoice line. Each excerpt was matched to the text of "
         "the pinned record version listed.", 8.5, "I", 70, 4.4)
    for block in m["appendix"]:
        pdf.ln(2)
        text(f"Line {block['line_index']}: {block['title']}", 10.5, "B", 20)
        for e in block["entries"]:
            text(f"{e['label']} - {e['file_name']}, version {e['version_id']}, page {e['page']}", 9, "B", 40)
            text(f'"{e["quote"]}"', 9, "I", 30)
            text(e["explanation"], 8.5, "", 80, 4.4)
            pdf.ln(1)
    return bytes(pdf.output())
