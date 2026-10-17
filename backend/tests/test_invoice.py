"""Invoice generations: idempotency, blockers, PDF content, download, save-to-Box idempotency."""

import hashlib
import io

from pypdf import PdfReader

from scopeguard.store import BaseStore, get_store
from tests.conftest import by_code


def ready_draft(api, comment="INTERNAL: do not show the customer"):
    scan = api.audit()
    f = by_code(scan)
    api("PATCH", f"/findings/{f['D-001']['finding_id']}",
        {"scan_id": scan["scan_id"], "status": "reviewed", "internal_comment": comment,
         "customer_description": "Two dedicated conference-room circuits per CO-001"})
    api("PATCH", f"/findings/{f['D-003']['finding_id']}",
        {"scan_id": scan["scan_id"], "status": "reviewed", "acknowledge_authorization": True, "internal_comment": comment})
    status, draft = api("PATCH", "/projects/riverside/invoice-draft",
                        {"expected_revision": 0, "scan_id": scan["scan_id"],
                         "selected_work_item_ids": [f[c]["work_item_id"] for c in ("D-001", "D-002", "D-003")]})
    assert status == 200 and draft["subtotal"] == "11000.00"
    return scan, f, draft


def pdf_text(data: bytes) -> str:
    return "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(data)).pages)


def test_generation_blockers(local_env, api):
    scan = api.audit()
    status, err = api("POST", "/projects/riverside/invoice-generations", {"request_id": "g", "expected_revision": 0})
    assert status == 422 and err["code"] == "generation_blocked" and "Select at least one item" in err["details"]["blockers"]
    assert api("POST", "/projects/riverside/invoice-generations", {"expected_revision": 0})[0] == 400
    assert api("POST", "/projects/riverside/invoice-generations", {"request_id": "g", "expected_revision": 5})[0] in (409, 422)
    assert scan["status"] == "completed"


def test_generation_idempotent_snapshot_and_pdf(local_env, api):
    scan, f, draft = ready_draft(api)
    status, gen = api("POST", "/projects/riverside/invoice-generations", {"request_id": "req-1", "expected_revision": draft["revision"]})
    assert status == 201
    assert set(gen) == {"generation_id", "project_id", "scan_id", "draft_revision", "created_at", "file_name", "content_hash",
                        "lines", "subtotal", "appendix", "content_path", "box"}
    assert gen["subtotal"] == "11000.00" and gen["scan_id"] == scan["scan_id"] and gen["box"] is None
    assert [l["code"] for l in gen["lines"]] == ["D-001", "D-002", "D-003"]
    assert gen["lines"][0]["description"] == "Two dedicated conference-room circuits per CO-001"
    assert gen["content_path"] == f"/projects/riverside/invoice-generations/{gen['generation_id']}/content"
    assert [a["line_index"] for a in gen["appendix"]] == [1, 2, 3]
    entry = gen["appendix"][1]["entries"][2]
    assert entry == {"label": "Proposed pricing", "file_name": "Additional fixture quotation.txt", "version_id": entry["version_id"],
                     "page": 1, "quote": "Quantity 2 × $800 each = $1,600 proposed.",
                     "explanation": "Supports the proposed quantity and unit price."}
    assert all(e["quote"] and e["version_id"] for a in gen["appendix"] for e in a["entries"])

    # same request + revision -> same generation; GET returns the stored snapshot
    status, again = api("POST", "/projects/riverside/invoice-generations", {"request_id": "req-1", "expected_revision": draft["revision"]})
    assert status == 201 and again == gen
    assert api("GET", f"/projects/riverside/invoice-generations/{gen['generation_id']}")[1] == gen
    assert api("GET", "/projects/riverside/invoice-generations/g_nope")[0] == 404

    status, data, headers = api("GET", gen["content_path"])
    assert status == 200 and headers["Content-Type"] == "application/pdf" and data.startswith(b"%PDF")
    assert headers["Content-Disposition"].startswith(f'attachment; filename="{gen["file_name"]}"')
    assert hashlib.sha256(data).hexdigest() == gen["content_hash"]
    text = pdf_text(data)
    for needle in ("SUPPLEMENTAL INVOICE", "DRAFT", "Draft - for customer review and sign-off", "Not sent",
                   "From: Summit Electrical", "To: Northline Builders", "Riverside Office Renovation (SG-024)",
                   "Two dedicated conference-room circuits per CO-001", "D-002 - Two additional LED fixtures",
                   "Proposal - requires customer", "authorization", "$8,500.00", "$1,600.00", "$900.00",
                   "Subtotal (selected items): $11,000.00", "Taxes, payment terms and retainage are not applied",
                   "Customer acceptance", "Signature", "Evidence appendix", "Revised installation requires 26 LED fixtures.",
                   "Nine labor hours recorded.", "version"):
        assert needle in text, needle
    assert "INTERNAL" not in text and "do not show" not in text  # internal comments never in customer documents
    assert "$6,250" not in text and "$500.00" not in text  # retainage / existing balances are not charges

    # a new draft revision requires a new generation and filename
    f2 = by_code(scan)
    status, d2 = api("PATCH", "/projects/riverside/invoice-draft",
                     {"expected_revision": draft["revision"], "scan_id": scan["scan_id"],
                      "selected_work_item_ids": [f2["D-001"]["work_item_id"]]})
    status, gen2 = api("POST", "/projects/riverside/invoice-generations", {"request_id": "req-1", "expected_revision": d2["revision"]})
    assert status == 201 and gen2["generation_id"] != gen["generation_id"] and gen2["file_name"] != gen["file_name"]
    assert gen2["subtotal"] == "8500.00"
    # the earlier generation is immutable
    assert api("GET", f"/projects/riverside/invoice-generations/{gen['generation_id']}")[1]["subtotal"] == "11000.00"
    status, err = api("POST", "/projects/riverside/invoice-generations", {"request_id": "req-2", "expected_revision": 1})
    assert status == 409 and err["code"] == "revision_conflict"


def test_download_after_temporary_pdf_expired(local_env, api):
    _, _, draft = ready_draft(api)
    _, gen = api("POST", "/projects/riverside/invoice-generations", {"request_id": "r", "expected_revision": draft["revision"]})
    get_store().delete(BaseStore.project_key("riverside", f"invoice-generations/{gen['generation_id']}/draft.pdf"))
    status, data, _ = api("GET", gen["content_path"])  # deterministic re-render, verified against the stored hash
    assert status == 200 and hashlib.sha256(data).hexdigest() == gen["content_hash"]


def test_save_to_box_once(local_env, api):
    _, _, draft = ready_draft(api)
    _, gen = api("POST", "/projects/riverside/invoice-generations", {"request_id": "r", "expected_revision": draft["revision"]})
    status, saved = api("POST", f"/projects/riverside/invoice-generations/{gen['generation_id']}/save-to-box", {})
    assert status == 200 and saved["box"]["file_id"] and saved["box"]["url"].endswith(saved["box"]["file_id"])
    assert saved["box"]["saved_at"] and saved["box"]["version_id"]
    out_dir = local_env / "ScopeGuard Packets"
    files = list(out_dir.iterdir())
    assert [p.name for p in files] == [gen["file_name"]]
    assert hashlib.sha256(files[0].read_bytes()).hexdigest() == gen["content_hash"]  # the exact generated PDF
    status, again = api("POST", f"/projects/riverside/invoice-generations/{gen['generation_id']}/save-to-box", {})
    assert status == 200 and again["box"] == saved["box"] and len(list(out_dir.iterdir())) == 1
    # an uncertain earlier upload (file exists, result not stored) is reconciled by filename, not re-uploaded
    key = BaseStore.project_key("riverside", f"invoice-generations/{gen['generation_id']}/manifest.json")
    m = get_store().get_json(key)
    m["box"] = None
    get_store().put_json(key, m)
    status, third = api("POST", f"/projects/riverside/invoice-generations/{gen['generation_id']}/save-to-box", {})
    assert third["box"]["file_id"] == saved["box"]["file_id"] and len(list(out_dir.iterdir())) == 1
    # outputs never become audit inputs
    _, recs = api("GET", "/projects/riverside/records")
    assert gen["file_name"] not in [r["name"] for r in recs["records"]]
