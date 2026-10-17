"""Configured projects and the seeded, reviewed agreement baseline [PRJ-01..05].

Projects are server configuration: Box folders are never derived from user input. Riverside is the one
functional project (mapped to BOX_FOLDER_ID); the other cards are explicit layout placeholders (preview_only).
The baseline manifest's source references name records by filename and are resolved against the live record
inventory to real file/version IDs, so the overview opens the actual signed agreement before any audit.
"""

from __future__ import annotations

import re
from typing import Optional

from . import config
from .box_client import SourceFileRef
from .errors import not_found

AGREEMENT_FILE = "Signed scope and agreement"
EXHIBIT_FILE = "Exhibit B — Labor and pricing"

PROJECTS: list[dict] = [
    {
        "project_id": "riverside", "code": "SG-024", "name": "Riverside Office Renovation",
        "contractor": "Summit Electrical", "customer": "Northline Builders", "contract_label": "Electrical contract",
        "start_date": "2026-10-05", "end_date": "2026-11-20", "status": "active", "preview_only": False,
        "currency": "USD",
        "baseline": {
            "summary": ("Summit Electrical provides the electrical fit-out for the Riverside Office Renovation under drawing "
                        "E-101 Rev A: 48 outlets, 24 LED fixtures, six branch circuits, and testing and labeling of installed "
                        "circuits. Weekend installation and modifications to existing service equipment are excluded. "
                        "Additional work is priced under Exhibit B ($100 per labor hour) and follows the contract change and "
                        "approval procedure and requires written customer approval. A 20% deposit ($25,000) is due on signing; "
                        "the contract balance is invoiced on completion, with invoices payable net 30 days."),
            "signed_date": "2026-09-28",
            "contract_value": "125000.00",
            "key_terms": ["Weekend installation excluded", "Written approval required",
                          "20% deposit · $25,000", "Completion billing · Net 30"],
            "agreement_file": AGREEMENT_FILE,
            "inclusions": [
                (AGREEMENT_FILE, 2, "Supply and install 48 outlets at the locations shown on E-101 Rev A."),
                (AGREEMENT_FILE, 2, "Supply and install 24 LED fixtures."),
                (AGREEMENT_FILE, 2, "Six branch circuits included in the signed scope."),
                (AGREEMENT_FILE, 2, "Complete testing and labeling of installed circuits."),
            ],
            "exclusions": [
                (AGREEMENT_FILE, 3, "Weekend installation excluded."),
                (AGREEMENT_FILE, 3, "Modifications to existing service equipment excluded."),
            ],
            "pricing_basis": [
                (EXHIBIT_FILE, 1, "Additional labor: $100 per hour, subject to the contract change procedure."),
                (EXHIBIT_FILE, 1, "Materials: documented cost."),
                (EXHIBIT_FILE, 1, "Additional LED fixture, supplied and installed: $800 each."),
                (EXHIBIT_FILE, 1, "No weekend premium is specified in this exhibit."),
            ],
            "change_terms": [
                (AGREEMENT_FILE, 3, "No change is billable until it has been approved in writing by Northline Builders."),
            ],
            "payment_terms": [
                (AGREEMENT_FILE, 1, "A deposit of 20% of the contract value ($25,000) is due on signing."),
                (AGREEMENT_FILE, 1, "Invoices are payable net 30 days."),
            ],
        },
    },
    {
        "project_id": "harbor-view", "code": "SG-025", "name": "Harbor View Apartments", "contractor": "Summit Electrical",
        "customer": "Beacon Construction", "contract_label": "Electrical contract", "start_date": "2026-09-14",
        "end_date": "2026-12-18", "status": "active", "preview_only": True, "review_label": "1 change log to review",
        "to_review_count": 1,
    },
    {
        "project_id": "cedar-street", "code": "SG-019", "name": "Cedar Street Retail Fit-Out", "contractor": "Summit Electrical",
        "customer": "Alder General Contractors", "contract_label": "Electrical contract", "start_date": "2026-07-06",
        "end_date": "2026-09-25", "status": "completed", "preview_only": True, "review_label": "All reviewed",
        "to_review_count": 0,
    },
]

TERM_LISTS = ("inclusions", "exclusions", "pricing_basis", "change_terms", "payment_terms")


def get_project(project_id: Optional[str]) -> dict:
    """An openable configured project; unknown and preview-only projects -> 404."""
    pid = project_id or config.DEMO_PROJECT_ID
    project = next((p for p in PROJECTS if p["project_id"] == pid), None)
    if project is None or project["preview_only"]:
        raise not_found(f"Project {pid} not found", "project_not_found")
    return project


def folder_id(project: dict) -> str:
    return config.box_folder_id() if project["project_id"] == config.DEMO_PROJECT_ID else ""


def _stem(name: str) -> str:
    # Filename typography must not disconnect the agreement's pricing exhibit.
    stem = re.sub(r"[\u2010-\u2015\u2212]", "-", name.rsplit(".", 1)[0]).lower()
    return " ".join(stem.split())


def find_record(refs: list[SourceFileRef], stem: str) -> Optional[SourceFileRef]:
    return next((r for r in refs if r.supported and _stem(r.name) == _stem(stem)), None)


def agreement_record(project: dict, refs: list[SourceFileRef]) -> Optional[SourceFileRef]:
    baseline = project.get("baseline") or {}
    return find_record(refs, baseline.get("agreement_file", "")) if baseline else None


def seeded_record_ids(project: dict, refs: list[SourceFileRef]) -> set[str]:
    baseline = project.get("baseline") or {}
    stems = {t[0].lower() for lst in TERM_LISTS for t in baseline.get(lst, [])} | {baseline.get("agreement_file", "").lower()}
    return {r.box_file_id for r in refs if _stem(r.name) in stems}


def baseline_version(project: dict, refs: list[SourceFileRef]) -> str:
    rec = agreement_record(project, refs)
    return rec.box_version_id if rec else "missing"


def _source(refs: list[SourceFileRef], stem: str, page: Optional[int], quote: Optional[str]) -> Optional[dict]:
    rec = find_record(refs, stem)
    if rec is None:
        return None
    return {"file_id": rec.box_file_id, "version_id": rec.box_version_id, "file_name": rec.name, "page": page,
            "quote": quote, "available": True}


def agreement(project: dict, refs: list[SourceFileRef], refreshed: Optional[dict]) -> dict:
    """Seeded reviewed baseline resolved to real records; fields refreshed from the latest audit when that audit
    analyzed the same agreement version [PRJ-04/05]."""
    b = project["baseline"]
    version = baseline_version(project, refs)
    out = {
        "summary": b["summary"], "signed_date": b["signed_date"], "contract_value": b["contract_value"],
        "key_terms": list(b["key_terms"]),
        **{lst: [{"text": q, "source": _source(refs, stem, page, q)} for stem, page, q in b[lst]] for lst in TERM_LISTS},
        "source": _source(refs, b["agreement_file"], 1, None),
        "baseline_version": version,
        "status": "seeded",
    }
    if refreshed and refreshed.get("baseline_version") == version and version != "missing":
        if refreshed.get("contract_value"):
            out["contract_value"] = refreshed["contract_value"]
        if refreshed.get("signed_date"):
            out["signed_date"] = refreshed["signed_date"]
        for lst in TERM_LISTS:
            if refreshed.get(lst):
                out[lst] = refreshed[lst]
        out["status"] = "refreshed"
    return out


def baseline_quotes(project: dict, refs: list[SourceFileRef], refreshed: Optional[dict]) -> list[dict]:
    """(file_id, version_id, page, quote) of every agreement term, for source-viewer highlights."""
    ag = agreement(project, refs, refreshed)
    out = []
    for lst in TERM_LISTS:
        for term in ag[lst]:
            src = term.get("source")
            if src and src.get("quote"):
                out.append(src)
    return out


def card(project: dict) -> dict:
    keys = ("project_id", "code", "name", "contractor", "customer", "contract_label", "start_date", "end_date", "status",
            "preview_only")
    out = {k: project[k] for k in keys}
    if project["preview_only"]:
        out["review_label"] = project["review_label"]
        out["to_review_count"] = project["to_review_count"]
    return out


def review_label(to_review: Optional[int]) -> str:
    if to_review is None:
        return "Not audited yet"
    if to_review == 0:
        return "All reviewed"
    return f"{to_review} finding{'s' if to_review != 1 else ''} to review"
