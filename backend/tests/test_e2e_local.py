"""End-to-end through api_handler with memory store, local Box and fake LLM."""

import json
import time

import api_handler


def call(method, path, body=None):
    event = {"version": "2.0", "rawPath": path, "requestContext": {"http": {"method": method, "path": path}},
             "body": json.dumps(body) if body is not None else None, "isBase64Encoded": False}
    resp = api_handler.handler(event, None)
    return resp["statusCode"], (json.loads(resp["body"]) if resp["body"] else None)


def wait_for_scan(scan_id, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status, scan = call("GET", f"/scans/{scan_id}")
        assert status == 200
        if scan["status"] in ("completed", "failed"):
            return scan
        time.sleep(0.1)
    raise AssertionError("scan did not finish")


def test_full_flow(local_env):
    status, body = call("POST", "/scans", {})
    assert status == 202 and body["status"] == "queued" and body["scan_id"]
    scan_id = body["scan_id"]

    scan = wait_for_scan(scan_id)
    assert scan["status"] == "completed", scan["error"]
    assert scan["progress"] == 100 and scan["finished_at"] and scan["error"] is None
    assert len(scan["files"]) == 10
    assert {f["doc_type"] for f in scan["files"]} >= {"subcontract", "rate_sheet", "instruction", "daily_report", "timesheet",
                                                      "receipt", "change_order_log", "spec_revision"}
    assert all(f["box_url"] == f"https://app.box.com/file/{f['box_file_id']}" for f in scan["files"])

    by_status = {f["finding_status"]: f for f in scan["findings"]}
    assert set(by_status) == {"new", "already_recorded", "needs_info"}

    new = by_status["new"]
    assert new["cost_total"] == "3297.51"
    assert [c["amount"] for c in new["cost_items"]] == ["2255.00", "612.40", "430.11"]
    assert all(e["verified"] for e in new["evidence"])
    excl = next(e for e in new["evidence"] if e["quote"].startswith("2.2 Framing"))
    assert excl["page"] == 2  # page number corrected by the quote check

    rec = by_status["already_recorded"]
    assert rec["co_match"]["co_number"] == "CO-003" and rec["co_match"]["amount"] == "1840.00"

    info = by_status["needs_info"]
    kinds = {q["kind"] for q in info["questions"]}
    assert {"missing_authorization", "missing_quantity", "unclear_scope"} <= kinds
    assert any(not e["verified"] for e in info["evidence"])

    fid = new["finding_id"]
    # packet before confirmation -> 409
    status, err = call("POST", "/packets", {"scan_id": scan_id, "finding_id": fid})
    assert status == 409 and "error" in err

    # PATCH: confirm + edit draft + edit a cost item -> total recomputed server-side
    items = [dict(c) for c in new["cost_items"]]
    items[0]["qty"] = "30"  # 30 h × $82.00 = 2460.00
    status, f = call("PATCH", f"/findings/{fid}", {"scan_id": scan_id, "status": "confirmed",
                                                    "draft": "Reviewed narrative.", "cost_items": items,
                                                    "question_resolutions": {}})
    assert status == 200
    assert f["review"]["status"] == "confirmed" and f["draft"] == "Reviewed narrative."
    assert f["cost_items"][0]["amount"] == "2460.00"
    assert f["cost_items"][0]["formula"] == "30 h × $82.00/h = $2,460.00"
    # markup = 15% × (2460.00 + 612.40) = 460.86
    assert f["cost_items"][2]["amount"] == "460.86"
    assert f["cost_total"] == "3533.26"
    assert f["ai_cost_items"][0]["amount"] == "2255.00"  # AI values untouched

    # POST /packets -> PDF in "<dir>/ScopeGuard Packets/"
    status, packet = call("POST", "/packets", {"scan_id": scan_id, "finding_id": fid})
    assert status == 201
    assert packet["box_url"] == f"https://app.box.com/file/{packet['box_file_id']}" and packet["created_at"].endswith("Z")
    pdfs = list((local_env / "ScopeGuard Packets").glob("*.pdf"))
    assert len(pdfs) == 1 and pdfs[0].read_bytes().startswith(b"%PDF")
    from pypdf import PdfReader

    text = "\n".join(p.extract_text() for p in PdfReader(str(pdfs[0])).pages)
    assert "not a determination of entitlement" in text
    assert "Reviewed narrative." in text and "$3,533.26" in text

    _, scan = call("GET", f"/scans/{scan_id}")
    again = next(x for x in scan["findings"] if x["finding_id"] == fid)
    assert again["packets"] == [packet]

    # a rescan folder listing must skip the packets subfolder
    from scopeguard.box_client import get_source

    assert all(not r.name.endswith(".pdf") for r in get_source().list_files())


def test_errors(local_env):
    assert call("GET", "/scans/nope") == (404, {"error": "Scan nope not found"})
    status, body = call("PATCH", "/findings/f_x", {})
    assert status == 400 and "scan_id" in body["error"]
    status, body = call("PATCH", "/findings/f_x", {"scan_id": "s"})
    assert status == 404
    status, body = call("POST", "/packets", {"scan_id": "s"})
    assert status == 400
    event = {"rawPath": "/scans", "requestContext": {"http": {"method": "POST"}}, "body": "{bad"}
    resp = api_handler.handler(event)
    assert resp["statusCode"] == 400 and "error" in json.loads(resp["body"])
    assert call("GET", "/nothing")[0] == 404
    assert api_handler.handler({"rawPath": "/scans", "requestContext": {"http": {"method": "OPTIONS"}}})["statusCode"] == 204


def test_list_saved_scans(local_env):
    status, body = call("GET", "/scans")
    assert status == 200 and body == {"scans": []}

    first = call("POST", "/scans", {})[1]["scan_id"]
    wait_for_scan(first)
    time.sleep(0.01)  # distinct started_at
    second = call("POST", "/scans", {})[1]["scan_id"]
    wait_for_scan(second)

    status, body = call("GET", "/scans")
    assert status == 200
    scans = body["scans"]
    assert [s["scan_id"] for s in scans] == [second, first]  # newest first
    assert scans[0]["status"] == "completed" and scans[0]["finished_at"]
    assert scans[0]["summary"] == {
        "file_count": 10, "finding_count": 3, "new_count": 1, "needs_info_count": 1,
        "already_recorded_count": 1, "potential_total": "3297.51",
    }
    assert "findings" not in scans[0]  # summaries only; GET /scans/{id} has the details

    event = {"rawPath": "/scans", "queryStringParameters": {"limit": "1"}, "requestContext": {"http": {"method": "GET"}}}
    assert [s["scan_id"] for s in json.loads(api_handler.handler(event)["body"])["scans"]] == [second]


def test_list_backfills_summary_for_old_scans(local_env):
    from scopeguard.store import get_store

    scan_id = call("POST", "/scans", {})[1]["scan_id"]
    wait_for_scan(scan_id)
    get_store().update_scan(scan_id, summary=None)  # as if completed before summaries existed
    scans = call("GET", "/scans")[1]["scans"]
    assert scans[0]["summary"]["finding_count"] == 3
    meta = next(i for i in get_store().get_scan_items(scan_id) if i["SK"] == "META")
    assert meta["summary"]["finding_count"] == 3
