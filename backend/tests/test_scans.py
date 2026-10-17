"""Audit lifecycle: lease/409, request_id dedup, stale lease, duplicate worker delivery, stages, coverage, failures."""

import threading

import pytest

from scopeguard import analyze, scans
from scopeguard.llm import FakeLLM, LLMError
from scopeguard.store import BaseStore, get_store
from tests.conftest import by_code


@pytest.fixture
def manual_worker(monkeypatch):
    """POST /scans queues the scan but does not start the worker (tests run it explicitly)."""
    started = []
    monkeypatch.setattr(scans, "_start_worker", lambda sid: started.append(sid))
    return started


def test_one_active_scan_409_and_request_dedup(local_env, api, manual_worker):
    status, first = api("POST", "/scans", {"project_id": "riverside", "cutoff_date": "2026-10-16", "request_id": "demo-1"})
    assert status == 202 and first["status"] == "queued" and first["project_id"] == "riverside"
    status, again = api("POST", "/scans", {"project_id": "riverside", "request_id": "demo-1"})
    assert status == 202 and again["scan_id"] == first["scan_id"]
    status, err = api("POST", "/scans", {})  # {} = configured demo project
    assert status == 409 and err["code"] == "scan_active" and err["details"] == {"scan_id": first["scan_id"]}
    assert manual_worker == [first["scan_id"]]

    _, detail = api("GET", "/projects/riverside")
    assert detail["active_scan"]["scan_id"] == first["scan_id"] and detail["audit_action"] == "run"

    manifest = get_store().get_json(BaseStore.scan_key(first["scan_id"], "manifest.json"))
    assert manifest["cutoff_date"] == "2026-10-16" and manifest["prompt_version"] and manifest["analysis_version"]
    assert manifest["baseline_version"] == detail["agreement"]["baseline_version"]
    assert len(manifest["files"]) == 11 and all(f["file_id"] and f["version_id"] and f["included"] for f in manifest["files"])

    analyze.run_scan(first["scan_id"])
    assert api.wait(first["scan_id"])["status"] == "completed"
    status, second = api("POST", "/scans", {"project_id": "riverside"})
    assert status == 202 and second["scan_id"] != first["scan_id"]
    # same request_id after completion still returns the original scan
    assert api("POST", "/scans", {"project_id": "riverside", "request_id": "demo-1"})[1]["scan_id"] == first["scan_id"]


def test_scan_request_validation(local_env, api, manual_worker):
    assert api("POST", "/scans", {"project_id": "nope"})[0] == 404
    assert api("POST", "/scans", {"project_id": "cedar-street"})[0] == 404
    assert api("POST", "/scans", {"cutoff_date": "16/10/2026"})[0] == 400
    assert api("POST", "/scans", {"request_id": ""})[0] == 400


def test_stale_lease_is_failed_and_new_scan_allowed(local_env, api, manual_worker):
    _, first = api("POST", "/scans", {})
    get_store().update_scan(first["scan_id"], deadline_at="2026-01-01T00:00:00.000Z")  # worker died long ago
    status, second = api("POST", "/scans", {})
    assert status == 202 and second["scan_id"] != first["scan_id"]
    _, old = api("GET", f"/scans/{first['scan_id']}")
    assert old["status"] == "failed" and old["error_code"] == "worker_timeout" and old["error"]
    # the late worker for the stale scan must not run it
    analyze.run_scan(first["scan_id"])
    assert api("GET", f"/scans/{first['scan_id']}")[1]["status"] == "failed"


def test_worker_start_failure_releases_lease(local_env, api, monkeypatch):
    def boom(sid):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(scans, "_start_worker", boom)
    status, err = api("POST", "/scans", {})
    assert status == 502 and err["code"] == "worker_start_failed" and "secret" not in err["error"]
    monkeypatch.setattr(scans, "_start_worker", lambda sid: None)
    assert api("POST", "/scans", {})[0] == 202


class CountingLLM(FakeLLM):
    def __init__(self):
        self.calls = 0
        self.lock = threading.Lock()

    def extract_document(self, name, pages):
        with self.lock:
            self.calls += 1
        return super().extract_document(name, pages)


def test_duplicate_worker_delivery_runs_once(local_env, api, manual_worker):
    _, s = api("POST", "/scans", {})
    llm = CountingLLM()
    threads = [threading.Thread(target=analyze.run_scan, args=(s["scan_id"],), kwargs={"llm": llm}) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    analyze.run_scan(s["scan_id"], llm=llm)  # a later redelivery
    scan = api.wait(s["scan_id"])
    assert scan["status"] == "completed" and len(scan["findings"]) == 3
    assert llm.calls == 11  # one extraction per record, never repeated


def test_stages_events_and_coverage(local_env, api, manual_worker, monkeypatch):
    seen = []
    original = BaseStore.update_scan

    def spy(self, scan_id, **fields):
        seen.append(fields)
        return original(self, scan_id, **fields)

    monkeypatch.setattr(BaseStore, "update_scan", spy)
    _, s = api("POST", "/scans", {})
    analyze.run_scan(s["scan_id"])
    stages = [f["stage_id"] for f in seen if "stage_id" in f]
    order = ["reading_box_records", "connecting_evidence", "checking_billing", "findings_ready"]
    assert [st for i, st in enumerate(stages) if i == 0 or st != stages[i - 1]] == order
    progress = [f["progress"] for f in seen if "progress" in f]
    assert progress == sorted(progress) and progress[-1] == 100
    events = [f["current_event"] for f in seen if f.get("current_event")]
    reading = next(e for e in events if e["message"] == "Reading Signed scope and agreement.txt")
    assert reading["source"]["file_id"] and reading["source"]["version_id"]
    comparing = next(e for e in events if e["message"] == "Comparing CO-001 with INV-014 and INV-015")
    assert comparing["source"]["file_name"].startswith("CO-001") and comparing["source"]["quote"]
    assert any(e["message"].startswith("Allocating REM-2026-031 payments to INV-015") for e in events)

    scan = api("GET", f"/scans/{s['scan_id']}")[1]
    assert scan["files_processed"] == scan["files_total"] == 11
    assert scan["coverage"] == {"complete": True, "warnings": []}
    assert scan["stage_id"] == "findings_ready" and scan["stage"] == "3 findings ready to review"
    assert scan["summary"]["file_count"] == 11 and scan["summary"]["eligible_total"] == "11000.00"
    assert all(f["ingestion_status"] == "ingested" for f in scan["files"])

    # refresh reuses the extraction cache (file + version + prompt version) and says so
    seen.clear()
    _, s2 = api("POST", "/scans", {})
    analyze.run_scan(s2["scan_id"])
    assert any(f.get("current_event", {}) and f["current_event"]["message"].startswith("Reused cached extraction") for f in seen)


def test_unreadable_record_makes_coverage_incomplete_and_blocks_invoice(local_env, api):
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()  # no text: like a scanned PDF
    (local_env / "Scanned delivery ticket.pdf").write_bytes(bytes(pdf.output()))
    scan = api.audit()
    assert scan["status"] == "completed"  # execution status and coverage are separate [AUD-05]
    assert scan["coverage"]["complete"] is False
    assert any("Scanned delivery ticket.pdf" in w and "No extractable text" in w for w in scan["coverage"]["warnings"])
    rec = next(f for f in scan["files"] if f["name"] == "Scanned delivery ticket.pdf")
    assert rec["ingestion_status"] == "failed" and rec["warnings"]
    d001 = by_code(scan)["D-001"]
    assert not d001["invoice_eligible"] and any("could not be read" in b for b in d001["eligibility_blockers"])


def test_model_failure_on_one_record_is_incomplete(local_env, api, monkeypatch):
    original = FakeLLM.extract_document

    def flaky(self, name, pages):
        if name.startswith("REM-2026-031"):
            raise LLMError("record_document: structured output invalid after retry")
        return original(self, name, pages)

    monkeypatch.setattr(FakeLLM, "extract_document", flaky)
    scan = api.audit()
    assert scan["status"] == "completed" and scan["coverage"]["complete"] is False
    rem = next(f for f in scan["files"] if f["name"].startswith("REM"))
    assert rem["ingestion_status"] == "failed"
    # without the remittance, payments are unknown: the balances are unknown, never assumed unpaid (unknown != 0)
    balances = {x["reference"]: x for x in scan["existing_items"] if x["kind"] == "invoice_balance"}
    assert balances["INV-015"]["amount"] is None and balances["INV-014"]["amount"] is None


def test_baseline_model_failure_fails_scan(local_env, api, monkeypatch):
    original = FakeLLM.extract_document

    def flaky(self, name, pages):
        if name.startswith("Signed scope"):
            raise LLMError("bad")
        return original(self, name, pages)

    monkeypatch.setattr(FakeLLM, "extract_document", flaky)
    scan = api.audit()
    assert scan["status"] == "failed" and scan["error_code"] == "model_failure"
    assert "bad" not in scan["error"]


def test_failed_refresh_keeps_previous_results_and_reviews(local_env, api):
    first = api.audit()
    d001 = by_code(first)["D-001"]
    api("PATCH", f"/findings/{d001['finding_id']}", {"scan_id": first["scan_id"], "internal_comment": "Keep me"})
    (local_env / "Signed scope and agreement.txt").rename(local_env / "moved-away.bak")
    failed = api.audit()
    assert failed["status"] == "failed" and failed["error_code"] == "baseline_missing" and failed["summary"] is None
    _, detail = api("GET", "/projects/riverside")
    assert detail["latest_scan"]["scan_id"] == first["scan_id"] and detail["audit_action"] == "refresh"
    assert detail["active_scan"] is None
    again = api("GET", f"/scans/{first['scan_id']}")[1]
    assert by_code(again)["D-001"]["review"]["internal_comment"] == "Keep me"
    scans_list = api("GET", "/scans", query={"project_id": "riverside"})[1]["scans"]
    assert [s["scan_id"] for s in scans_list] == [failed["scan_id"], first["scan_id"]]
    assert scans_list[1]["summary"]["to_review_count"] == 3 and scans_list[0]["summary"] is None


def test_list_scans_shape(local_env, api):
    assert api("GET", "/scans")[1] == {"scans": []}
    scan = api.audit()
    _, body = api("GET", "/scans", query={"project_id": "riverside", "limit": "5"})
    s = body["scans"][0]
    assert set(s) == {"scan_id", "project_id", "status", "stage_id", "stage", "progress", "error", "error_code", "cutoff_date",
                      "started_at", "updated_at", "finished_at", "summary"}
    assert s["summary"] == {"file_count": 11, "finding_count": 3, "to_review_count": 3, "eligible_total": "11000.00",
                            "by_type": {"approved_unbilled": 1, "scope_change_proposed": 1, "authorization_unresolved": 1,
                                        "invoice_balance_unresolved": 0, "needs_information": 0}}
    assert s["scan_id"] == scan["scan_id"]
    assert api("GET", "/scans", query={"limit": "x"})[0] == 400
    assert api("GET", "/scans/nope")[0] == 404
