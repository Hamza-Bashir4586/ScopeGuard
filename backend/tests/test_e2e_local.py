"""End-to-end through api_handler against the generated demo PDFs (memory store, local folder, fake LLM)."""

import json

import api_handler
from tests.conftest import by_code


def test_full_demo_flow_on_pdfs(pdf_env, api):
    _, projects = api("GET", "/projects")
    assert projects["projects"][0]["review_label"] == "Not audited yet"
    _, detail = api("GET", "/projects/riverside")
    assert detail["audit_action"] == "run" and detail["agreement"]["source"]["file_name"] == "Signed scope and agreement.pdf"

    scan = api.audit(cutoff_date="2026-10-16", request_id="e2e")
    assert scan["status"] == "completed" and scan["coverage"]["complete"], scan["coverage"]
    f = by_code(scan)
    assert [(c, f[c]["finding_type"], f[c]["amount"]) for c in sorted(f)] == [
        ("D-001", "approved_unbilled", "8500.00"), ("D-002", "scope_change_proposed", "1600.00"),
        ("D-003", "authorization_unresolved", "900.00")]
    existing = {(x["kind"], x["reference"]): x["amount"] for x in scan["existing_items"]}
    assert existing == {("invoice_balance", "INV-015"): "500.00", ("retainage", "INV-014"): "6250.00",
                        ("approved_billed", "CO-002"): "1500.00"}
    assert all(e["verified"] for x in scan["findings"] for g in x["evidence_groups"] for e in g["evidence"])

    _, detail = api("GET", "/projects/riverside")
    assert detail["audit_action"] == "refresh" and detail["agreement"]["status"] == "refreshed"
    assert detail["review_label"] == "3 findings to review"
    assert detail["agreement"]["contract_value"] == "125000.00" and detail["agreement"]["signed_date"] == "2026-09-28"

    # review, acknowledge, select all three
    sid = scan["scan_id"]
    api("PATCH", f"/findings/{f['D-001']['finding_id']}", {"scan_id": sid, "status": "reviewed", "internal_comment": "ok"})
    api("PATCH", f"/findings/{f['D-002']['finding_id']}", {"scan_id": sid, "status": "reviewed"})
    api("PATCH", f"/findings/{f['D-003']['finding_id']}", {"scan_id": sid, "status": "reviewed", "acknowledge_authorization": True})
    _, draft = api("PATCH", "/projects/riverside/invoice-draft",
                   {"expected_revision": 0, "scan_id": sid, "selected_work_item_ids": [f[c]["work_item_id"] for c in ("D-001", "D-002", "D-003")]})
    assert draft["subtotal"] == "11000.00" and draft["can_generate"]
    status, gen = api("POST", "/projects/riverside/invoice-generations", {"request_id": "g1", "expected_revision": draft["revision"]})
    assert status == 201 and gen["subtotal"] == "11000.00"
    status, pdf, _ = api("GET", gen["content_path"])
    assert status == 200 and pdf.startswith(b"%PDF")
    status, saved = api("POST", f"/projects/riverside/invoice-generations/{gen['generation_id']}/save-to-box", {})
    assert status == 200 and saved["box"]
    assert (pdf_env / "ScopeGuard Packets" / gen["file_name"]).read_bytes() == pdf

    # v1 POST /packets still works for a reviewed finding
    status, packet = api("POST", "/packets", {"scan_id": sid, "finding_id": f["D-001"]["finding_id"]})
    assert status == 201 and packet["box_url"].endswith(packet["box_file_id"]) and packet["created_at"].endswith("Z")
    _, d = api("GET", f"/scans/{sid}")
    assert d["summary"]["to_review_count"] == 0
    _, cards = api("GET", "/projects")
    assert cards["projects"][0]["review_label"] == "All reviewed"


def test_packets_require_review_and_errors(local_env, api):
    scan = api.audit()
    fid = by_code(scan)["D-002"]["finding_id"]
    status, err = api("POST", "/packets", {"scan_id": scan["scan_id"], "finding_id": fid})
    assert status == 409 and err["code"] == "not_reviewed"
    assert api("POST", "/packets", {"scan_id": "s"})[0] == 400
    assert api("POST", "/packets", {"scan_id": "nope", "finding_id": "x"})[0] == 404


def test_router_errors_and_cors(local_env, api):
    assert api("GET", "/nothing")[0] == 404
    status, err = api("DELETE", "/projects")
    assert status == 405 and err["code"] == "method_not_allowed"
    event = {"rawPath": "/scans", "requestContext": {"http": {"method": "POST"}}, "body": "{bad"}
    resp = api_handler.handler(event)
    assert resp["statusCode"] == 400 and json.loads(resp["body"])["error"]
    assert resp["headers"]["Access-Control-Allow-Origin"] == "*"
    opt = api_handler.handler({"rawPath": "/projects/riverside/records", "requestContext": {"http": {"method": "OPTIONS"}}})
    assert opt["statusCode"] == 204 and "PATCH" in opt["headers"]["Access-Control-Allow-Methods"]
    # rawQueryString is honoured when queryStringParameters is absent (local server / some proxies)
    resp = api_handler.handler({"rawPath": "/projects/riverside/records", "rawQueryString": "category_id=billing",
                                "requestContext": {"http": {"method": "GET"}}})
    assert len(json.loads(resp["body"])["records"]) == 2
