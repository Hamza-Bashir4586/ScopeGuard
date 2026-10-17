"""Project-level reviews (revisions, conflicts), invoice draft eligibility and refresh preservation."""

from scopeguard import reviews
from scopeguard.store import BaseStore, PreconditionFailed, get_store
from tests.conftest import by_code


def setup_scan(api):
    scan = api.audit()
    return scan, by_code(scan)


def patch(api, scan, finding, **body):
    return api("PATCH", f"/findings/{finding['finding_id']}", {"scan_id": scan["scan_id"], **body})


def select(api, scan, codes, revision):
    f = by_code(scan)
    return api("PATCH", "/projects/riverside/invoice-draft",
               {"expected_revision": revision, "scan_id": scan["scan_id"], "selected_work_item_ids": [f[c]["work_item_id"] for c in codes]})


def test_finding_shape_and_review_defaults(local_env, api):
    scan, f = setup_scan(api)
    d1 = f["D-001"]
    assert set(d1) >= {"finding_id", "scan_id", "work_item_id", "code", "finding_type", "type_label", "title", "reason",
                       "explanation", "authorization_status", "approval_label", "proposal", "amount", "amount_basis",
                       "calculation", "cost_items", "financial", "evidence_groups", "source_count", "questions",
                       "invoice_eligible", "eligibility_blockers", "evidence_changed", "review", "selected"}
    assert scan["scan_id"] not in d1["work_item_id"]
    assert d1["type_label"] == "MISSING FROM INVOICE" and f["D-002"]["type_label"] == "QUANTITY CHANGE"
    assert f["D-003"]["type_label"] == "EXCLUDED WORK"
    assert d1["review"] == {"work_item_id": d1["work_item_id"], "status": "unreviewed", "internal_comment": "",
                            "customer_description": None, "authorization_acknowledged": False, "revision": 0, "updated_at": None}
    assert d1["invoice_eligible"] and f["D-002"]["invoice_eligible"]
    assert not f["D-003"]["invoice_eligible"] and f["D-003"]["eligibility_blockers"] == ["Acknowledge the unresolved authorization first"]
    assert all(e["verified"] for g in d1["evidence_groups"] for e in g["evidence"])


def test_review_patch_and_revision_conflict(local_env, api):
    scan, f = setup_scan(api)
    d1 = f["D-001"]
    status, out = patch(api, scan, d1, internal_comment="Confirm with accounting", status="reviewed", expected_revision=0)
    assert status == 200 and out["review"]["status"] == "reviewed" and out["review"]["revision"] == 1
    assert out["review"]["internal_comment"] == "Confirm with accounting" and out["review"]["updated_at"]
    status, err = patch(api, scan, d1, internal_comment="Other teammate", expected_revision=0)
    assert status == 409 and err["code"] == "revision_conflict" and err["details"]["current_revision"] == 1
    status, out = patch(api, scan, d1, customer_description="  Two dedicated conference-room circuits (CO-001)  ", expected_revision=1)
    assert status == 200 and out["review"]["customer_description"] == "Two dedicated conference-room circuits (CO-001)"
    assert out["review"]["internal_comment"] == "Confirm with accounting"
    # validation
    assert patch(api, scan, d1, status="approved")[0] == 400
    assert patch(api, scan, d1, status="needs_revalidation")[0] == 400
    assert patch(api, scan, d1, acknowledge_authorization="yes")[0] == 400
    assert patch(api, scan, d1, expected_revision="1")[0] == 400
    assert api("PATCH", "/findings/f_missing", {"scan_id": scan["scan_id"]})[0] == 404
    assert api("PATCH", f"/findings/{d1['finding_id']}", {})[0] == 400


def test_concurrent_review_write_conflict_is_409(local_env, api, monkeypatch):
    scan, f = setup_scan(api)
    store = get_store()
    original = store.put_json

    def racing(key, data, **kw):
        if "/reviews/" in key:
            raise PreconditionFailed(key)  # another writer won between our read and write
        return original(key, data, **kw)

    monkeypatch.setattr(store, "put_json", racing)
    status, err = patch(api, scan, f["D-001"], internal_comment="x")
    assert status == 409 and err["code"] == "revision_conflict"


def test_draft_eligibility_conflicts_and_subtotal(local_env, api):
    scan, f = setup_scan(api)
    status, draft = api("GET", "/projects/riverside/invoice-draft")
    assert status == 200 and draft["revision"] == 0 and draft["blockers"] == ["Select at least one item"] and not draft["can_generate"]

    status, err = select(api, scan, ["D-001", "D-003"], 0)  # D-003 needs acknowledgment first
    assert status == 422 and err["code"] == "ineligible_item" and err["details"]["work_item_id"] == f["D-003"]["work_item_id"]
    assert "Acknowledge" in err["details"]["blockers"][0]
    status, err = api("PATCH", "/projects/riverside/invoice-draft",
                      {"expected_revision": 0, "scan_id": scan["scan_id"], "selected_work_item_ids": [f["D-001"]["work_item_id"]] * 2})
    assert status == 422 and err["code"] == "duplicate_selection"
    status, err = api("PATCH", "/projects/riverside/invoice-draft",
                      {"expected_revision": 0, "scan_id": scan["scan_id"], "selected_work_item_ids": ["wi_unknown"]})
    assert status == 422 and err["details"]["work_item_id"] == "wi_unknown"
    status, err = api("PATCH", "/projects/riverside/invoice-draft",
                      {"expected_revision": 0, "scan_id": "old-scan", "selected_work_item_ids": []})
    assert status == 409 and err["code"] == "stale_selection"

    status, draft = select(api, scan, ["D-001", "D-002"], 0)
    assert status == 200 and draft["revision"] == 1 and draft["subtotal"] == "10100.00" and draft["can_generate"]
    # adding auto-marks unreviewed items reviewed (a reviewed selection, not customer approval)
    _, s = api("GET", f"/scans/{scan['scan_id']}")
    assert by_code(s)["D-001"]["review"]["status"] == "reviewed" and by_code(s)["D-001"]["selected"]
    assert by_code(s)["D-002"]["authorization_status"] == "pending"  # still a proposal
    assert select(api, scan, ["D-001"], 0)[1]["code"] == "revision_conflict"

    status, _ = patch(api, scan, f["D-003"], status="reviewed", acknowledge_authorization=True, internal_comment="PM confirmed Saturday access")
    assert status == 200
    status, draft = select(api, scan, ["D-001", "D-002", "D-003"], 1)
    assert status == 200 and draft["subtotal"] == "11000.00" and draft["revision"] == 2 and draft["blockers"] == []
    line = draft["lines"][2]
    assert line["code"] == "D-003" and line["proposal"] and line["authorization_status"] == "unclear" and line["amount"] == "900.00"
    assert set(line) == {"work_item_id", "finding_id", "code", "title", "description", "amount", "approval_label", "proposal",
                         "authorization_status", "blockers"}
    # removing one item recalculates server-side
    status, draft = select(api, scan, ["D-001", "D-003"], 2)
    assert draft["subtotal"] == "9400.00"
    _, detail = api("GET", "/projects/riverside")
    assert detail["invoice_draft"] == {"revision": 3, "item_count": 2, "subtotal": "9400.00"}
    assert detail["review_label"] == "1 finding to review" and detail["to_review_count"] == 1


def test_dismiss_removes_from_draft(local_env, api):
    scan, f = setup_scan(api)
    select(api, scan, ["D-001", "D-002"], 0)
    status, out = patch(api, scan, f["D-002"], status="dismissed", internal_comment="Customer withdrew SI-009")
    assert status == 200 and out["review"]["status"] == "dismissed" and not out["selected"]
    assert out["eligibility_blockers"] == ["Dismissed"]
    _, draft = api("GET", "/projects/riverside/invoice-draft")
    assert draft["revision"] == 2 and [l["code"] for l in draft["lines"]] == ["D-001"] and draft["subtotal"] == "8500.00"
    assert select(api, scan, ["D-001", "D-002"], 2)[0] == 422  # dismissed items cannot be re-added
    _, s = api("GET", f"/scans/{scan['scan_id']}")
    assert s["summary"]["to_review_count"] == 1 and s["summary"]["eligible_total"] == "9400.00"


def test_refresh_preserves_reviews_and_selection(local_env, api):
    scan1, f1 = setup_scan(api)
    patch(api, scan1, f1["D-001"], internal_comment="Keep this comment", status="reviewed")
    patch(api, scan1, f1["D-003"], acknowledge_authorization=True, status="reviewed")
    select(api, scan1, ["D-001", "D-003"], 0)

    scan2 = api.audit()
    f2 = by_code(scan2)
    assert {c: f2[c]["work_item_id"] for c in f2} == {c: f1[c]["work_item_id"] for c in f1}
    assert {f["finding_id"] for f in scan2["findings"]}.isdisjoint({f["finding_id"] for f in scan1["findings"]})
    assert f2["D-001"]["review"]["internal_comment"] == "Keep this comment" and f2["D-001"]["review"]["status"] == "reviewed"
    assert f2["D-001"]["selected"] and f2["D-003"]["selected"] and not f2["D-001"]["evidence_changed"]
    _, draft = api("GET", "/projects/riverside/invoice-draft")
    assert draft["subtotal"] == "9400.00" and draft["can_generate"]
    _, detail = api("GET", "/projects/riverside")
    assert detail["latest_scan"]["scan_id"] == scan2["scan_id"] and detail["new_input_available"] is False
    # the older scan's findings are no longer eligible
    _, old = api("GET", f"/scans/{scan1['scan_id']}")
    assert any("newer completed audit" in b for b in by_code(old)["D-001"]["eligibility_blockers"])


def test_changed_evidence_requires_revalidation(local_env, api):
    scan1, f1 = setup_scan(api)
    patch(api, scan1, f1["D-003"], acknowledge_authorization=True, status="reviewed", internal_comment="Saturday OK'd verbally")
    select(api, scan1, ["D-001", "D-003"], 0)
    ts = local_env / "Saturday crew timesheet.txt"
    ts.write_text(ts.read_text().replace("Nine labor hours", "Ten labor hours"))
    _, detail = api("GET", "/projects/riverside")
    assert detail["new_input_available"] is True

    scan2 = api.audit()
    d3 = by_code(scan2)["D-003"]
    assert d3["work_item_id"] == f1["D-003"]["work_item_id"] and d3["amount"] == "1000.00"
    assert d3["evidence_changed"] and d3["review"]["status"] == "needs_revalidation"
    assert d3["review"]["internal_comment"] == "Saturday OK'd verbally" and d3["selected"]  # kept
    assert not d3["invoice_eligible"] and any("Evidence changed" in b for b in d3["eligibility_blockers"])
    assert by_code(scan2)["D-001"]["review"]["status"] == "reviewed"  # unchanged evidence keeps its review
    _, draft = api("GET", "/projects/riverside/invoice-draft")
    assert not draft["can_generate"] and draft["subtotal"] == "9500.00"
    status, err = api("POST", "/projects/riverside/invoice-generations", {"request_id": "x", "expected_revision": draft["revision"]})
    assert status == 422 and err["code"] == "generation_blocked" and any("D-003" in b for b in err["details"]["blockers"])

    # status "reviewed" alone does not clear it; confirm_revalidation does
    _, out = patch(api, scan2, d3, status="reviewed")
    assert out["review"]["status"] == "needs_revalidation"
    _, out = patch(api, scan2, d3, confirm_revalidation=True)
    assert out["review"]["status"] == "reviewed" and not out["evidence_changed"] and out["invoice_eligible"]
    _, draft = api("GET", "/projects/riverside/invoice-draft")
    assert draft["can_generate"]


def test_machine_writes_never_touch_reviews(local_env, api):
    scan, f = setup_scan(api)
    patch(api, scan, f["D-002"], internal_comment="human")
    store = get_store()
    key = reviews.review_key("riverside", f["D-002"]["work_item_id"])
    before = store.get_bytes(key)
    api.audit()
    assert store.get_bytes(key) == before
    assert all(k.startswith("projects/riverside/reviews/") for k in store.list_keys("projects/riverside/reviews/"))
    assert not any("/reviews/" in k for k in store.list_keys("scans/"))
    assert BaseStore.project_key("riverside", "invoice-draft.json") not in store.list_keys("projects/riverside/")
