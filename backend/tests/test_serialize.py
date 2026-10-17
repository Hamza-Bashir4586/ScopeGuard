import pytest

from scopeguard.serialize import BadRequest, apply_patch, default_review, serialize_finding

AI = {
    "title": "Soffit", "summary": "s", "finding_status": "new", "co_match": None, "scope_baseline": "b", "requested_work": "r",
    "evidence": [{"evidence_id": "e1", "box_file_id": "1", "box_version_id": "2", "file_name": "a.txt", "page": 1,
                  "quote": "q", "role": "instruction", "verified": True, "box_url": "https://app.box.com/file/1"}],
    "timeline": [{"date": "2026-03-12", "event": "x", "evidence_id": "e1"}],
    "questions": [{"question_id": "q1", "kind": "missing_rate", "text": "What rate?"}],
    "ai_draft": "AI draft",
    "ai_cost_items": [{"item_id": "c1", "category": "labor", "description": "d", "qty": "19.5", "unit": "h", "rate": "82.00",
                       "amount": "1599.00", "formula": "19.5 h × $82.00/h = $1,599.00", "source_evidence_id": "e1"}],
}


def item(review=None):
    return {"scan_id": "s1", "finding_id": "f1", "ai": AI, "review": review or default_review()}


def test_effective_values_default_to_ai():
    f = serialize_finding(item())
    assert f["draft"] == "AI draft"
    assert f["cost_items"] == f["ai_cost_items"]
    assert f["cost_total"] == "1599.00"
    assert f["questions"][0]["resolution"] is None
    assert f["review"]["status"] == "unreviewed" and f["packets"] == []


def test_review_overrides_and_reset_with_null():
    r = apply_patch(None, {"draft": "Edited", "cost_items": [
        {"category": "labor", "description": "d", "qty": "10", "unit": "h", "rate": "82"},
        {"category": "markup", "description": "m", "qty": None, "unit": "%", "rate": "10"},
    ], "question_resolutions": {"q1": "Use $82/h"}, "status": "confirmed"}, "f1")
    f = serialize_finding(item(r))
    assert f["draft"] == "Edited" and f["ai_draft"] == "AI draft"
    assert [c["amount"] for c in f["cost_items"]] == ["820.00", "82.00"]
    assert f["cost_items"][1]["formula"] == "10% × $820.00 = $82.00"
    assert all(c["item_id"] for c in f["cost_items"])
    assert f["cost_total"] == "902.00"
    assert f["questions"][0]["resolution"] == "Use $82/h"
    assert f["review"]["status"] == "confirmed" and f["review"]["updated_at"]

    r2 = apply_patch(r, {"draft": None, "cost_items": None}, "f1")
    f2 = serialize_finding(item(r2))
    assert f2["draft"] == "AI draft" and f2["cost_total"] == "1599.00"
    assert f2["review"]["status"] == "confirmed"  # untouched fields are kept


def test_question_resolutions_merge_and_empty_string_removes():
    r = apply_patch(None, {"question_resolutions": {"q1": "a", "q2": "b"}}, "f1")
    r = apply_patch(r, {"question_resolutions": {"q2": "", "q3": "c"}}, "f1")
    assert r["question_resolutions"] == {"q1": "a", "q3": "c"}


def test_patch_cost_item_keeps_given_item_id_and_ignores_client_amount():
    r = apply_patch(None, {"cost_items": [{"item_id": "c1", "category": "labor", "description": "d", "qty": "2",
                                           "unit": "h", "rate": "50", "amount": "9999", "formula": "bogus"}]}, "f1")
    c = r["cost_items"][0]
    assert c["item_id"] == "c1" and c["amount"] == "100.00" and c["formula"] == "2 h × $50.00/h = $100.00"


@pytest.mark.parametrize("body", [
    {"status": "approved"},
    {"cost_items": [{"category": "labor", "description": "d", "qty": 1.5, "unit": "h", "rate": "1"}]},
    {"cost_items": [{"category": "food", "description": "d", "qty": "1", "unit": "h", "rate": "1"}]},
    {"cost_items": [{"category": "labor", "description": "d", "qty": "abc", "unit": "h", "rate": "1"}]},
    {"cost_items": "nope"},
    {"question_resolutions": ["x"]},
    {"draft": 5},
])
def test_patch_validation_errors(body):
    with pytest.raises(BadRequest):
        apply_patch(None, body, "f1")
