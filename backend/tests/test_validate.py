from decimal import Decimal

import pytest

from scopeguard.models import ProposedCostItem, ProposedEvidence, ProposedFinding
from scopeguard.validate import (
    Fact,
    check_quote,
    compute_cost_item,
    cost_total,
    finalize_cost_items,
    make_finding_id,
    normalize_co_number,
    normalize_text,
    parse_decimal,
    validate_finding,
)

# ------------------------------------------------------------------ quotes


def test_normalize_handles_ligatures_quotes_hyphenation_whitespace():
    raw = "The ﬁre-rated “soffit” shall be exten-\nded   along\tthe  corridor—C to F."
    assert normalize_text(raw) == 'the fire-rated "soffit" shall be extended along the corridor-c to f.'


def test_exact_quote_on_cited_page():
    pages = ["Intro text.", "Please frame and board a new soffit along the Level 2 corridor."]
    qc = check_quote("Please frame and board a new soffit along the Level 2 corridor.", pages, 2)
    assert qc.verified and qc.page == 2 and qc.score == 1.0


def test_quote_with_pdf_artifacts_still_matches():
    page = "Please frame and board a new sof-\nfit along the Level 2\ncorridor from grid­line C to F."
    qc = check_quote("Please frame and board a new soffit along the Level 2 corridor from gridline C to F.", [page], 1)
    assert qc.verified and qc.page == 1


def test_curly_quotes_and_ligatures_match():
    page = "Owner’s ﬁnal oﬀice layout"
    assert check_quote("Owner's final office layout", [page], 1).verified


def test_fuzzy_match_above_threshold():
    page = "Started L2 corridor soffit framing gridlines C-F per Dave Romero email of 3/10 and ASI-03. Crew of 3 carpenters."
    # one character typo in a long quote -> ratio >= 0.95
    qc = check_quote("Started L2 corridor soffit framing gridlines C-F per Dave Romero emial of 3/10 and ASI-03.", [page], 1)
    assert qc.verified and 0.95 <= qc.score < 1.0


def test_paraphrase_is_not_verified():
    page = "Per verbal request from GC super (Dave), patched and re-taped damaged drywall in the Level 1 lobby."
    qc = check_quote("GC superintendent directed Keystone in writing to repair all Level 1 lobby drywall", [page], 1)
    assert not qc.verified and qc.page == 1


def test_wrong_page_is_corrected():
    pages = ["1.1 Scope inclusions here.", "2.2 Framing or board at the Level 2 corridor ceiling is excluded."]
    qc = check_quote("2.2 Framing or board at the Level 2 corridor ceiling is excluded.", pages, 1)
    assert qc.verified and qc.page == 2


def test_out_of_range_page_is_corrected():
    qc = check_quote("hello world quote", ["nothing", "a hello world quote here"], 7)
    assert qc.verified and qc.page == 2


# ------------------------------------------------------------------ decimal math


def test_parse_decimal_rejects_floats_and_garbage():
    assert parse_decimal("$1,599.00") == Decimal("1599.00")
    assert parse_decimal("6.5") == Decimal("6.5")
    assert parse_decimal(6.5) is None
    assert parse_decimal("six") is None
    assert parse_decimal(None) is None


def test_cost_item_formula_and_half_up_rounding():
    amount, formula = compute_cost_item(Decimal("19.5"), "h", Decimal("82.00"))
    assert amount == "1599.00"
    assert formula == "19.5 h × $82.00/h = $1,599.00"
    # 0.125 * 1 -> 0.13 with ROUND_HALF_UP (banker's rounding would give 0.12)
    assert compute_cost_item(Decimal("1"), "ea", Decimal("0.125"))[0] == "0.13"
    assert compute_cost_item(Decimal("3"), "ea", Decimal("0.335"))[0] == "1.01"  # 1.005 -> 1.01


def test_missing_qty_or_rate_gives_null_amount():
    amount, formula = compute_cost_item(None, "h", Decimal("78"))
    assert amount is None and "Missing quantity" in formula


def test_markup_percentage_uses_sum_of_non_markup_amounts_and_ignores_qty():
    items = finalize_cost_items([
        {"item_id": "a", "category": "labor", "description": "x", "qty": "20", "unit": "h", "rate": "100"},
        {"item_id": "b", "category": "material", "description": "y", "qty": "1", "unit": "ls", "rate": "450"},
        {"item_id": "c", "category": "markup", "description": "m", "qty": "999", "unit": "%", "rate": "10"},
        {"item_id": "d", "category": "other", "description": "z", "qty": None, "unit": "ea", "rate": "5"},
    ])
    m = items[2]
    assert m["amount"] == "245.00"
    assert m["formula"] == "10% × $2,450.00 = $245.00"
    assert items[3]["amount"] is None
    assert cost_total(items) == "2695.00"


def test_markup_half_up():
    items = finalize_cost_items([
        {"category": "labor", "qty": "27.5", "unit": "h", "rate": "82.00"},
        {"category": "material", "qty": "1", "unit": "ls", "rate": "612.40"},
        {"category": "markup", "qty": None, "unit": "%", "rate": "15"},
    ])
    assert items[2]["amount"] == "430.11"  # 2867.40 * 0.15 = 430.11
    assert cost_total(items) == "3297.51"


# ------------------------------------------------------------------ finding validation


def _fact(fid, doc_type, kind, quote="some quote", verified=True, **kw):
    return Fact(fact_id=fid, box_file_id="1" + fid[-3:], box_version_id="v" + fid, file_name=f"{doc_type}.txt",
                doc_type=doc_type, kind=kind, page=1, quote=quote, summary=quote, verified=verified, **kw)


@pytest.fixture
def facts():
    fs = [
        _fact("F01-001", "instruction", "instruction", "Please frame the soffit"),
        _fact("F02-001", "rate_sheet", "labor_rate", "Carpenter $82.00 per hour", rate="82.00", unit="h"),
        _fact("F03-001", "timesheet", "work_record", "Total 19.5", qty="19.5", unit="h"),
        _fact("F03-002", "timesheet", "work_record", "Total 8.0", qty="8.0", unit="h"),
        _fact("F04-001", "receipt", "material_cost", "Total soffit materials: $612.40", qty="1", rate="612.40", amount="612.40"),
        _fact("F05-001", "daily_report", "work_record", "Mentions $95.00 per hour foreman", rate="95.00"),
        _fact("F06-001", "change_order_log", "change_order", "CO-003 | blocking | $1,840.00", co_number="CO-003", amount="1840.00"),
        _fact("F07-001", "subcontract", "markup", "markup is 15%", rate="15", unit="%"),
        _fact("F08-001", "instruction", "instruction", "paraphrased claim", verified=False),
    ]
    return {f.fact_id: f for f in fs}


def _finding(**kw):
    base = dict(change_key="L2 Corridor Soffit", title="t", summary="s", status="new", scope_baseline="b", requested_work="r")
    base.update(kw)
    return ProposedFinding(**base)


def test_finding_id_is_stable_and_normalized():
    assert make_finding_id("scan1", "L2 Corridor  Soffit!") == make_finding_id("scan1", "l2-corridor-soffit")
    assert make_finding_id("scan1", "a") != make_finding_id("scan2", "a")


def test_rates_from_rate_sheet_and_receipt_and_python_math(facts):
    pf = _finding(
        evidence=[ProposedEvidence(fact_id="F01-001", role="instruction")],
        cost_items=[
            ProposedCostItem(category="labor", description="Carpenters", unit="h", qty="99", rate="90.00",
                             qty_fact_ids=["F03-001", "F03-002"], rate_fact_id="F02-001"),
            ProposedCostItem(category="material", description="Materials", unit="ls", qty="1", rate="700",
                             qty_fact_ids=["F04-001"], rate_fact_id="F04-001"),
            ProposedCostItem(category="markup", description="Markup", unit="%", rate="20", rate_fact_id="F07-001"),
        ],
    )
    ai = validate_finding("s1", pf, facts)
    labor, material, markup = ai["ai_cost_items"]
    # LLM-proposed numbers are replaced by verified source values
    assert (labor["qty"], labor["rate"], labor["amount"]) == ("27.5", "82.00", "2255.00")
    assert labor["formula"] == "27.5 h × $82.00/h = $2,255.00"
    assert (material["rate"], material["amount"]) == ("612.40", "612.40")
    assert (markup["rate"], markup["amount"]) == ("15", "430.11")
    assert labor["source_evidence_id"] in {e["evidence_id"] for e in ai["evidence"]}
    assert ai["finding_status"] == "new"
    assert not ai["questions"]


def test_rate_not_from_rate_sheet_is_rejected(facts):
    pf = _finding(
        evidence=[ProposedEvidence(fact_id="F01-001", role="instruction")],
        cost_items=[
            # labor rate cited from a daily report -> not allowed
            ProposedCostItem(category="labor", description="Foreman", unit="h", qty="8", rate="95.00",
                             qty_fact_ids=["F03-002"], rate_fact_id="F05-001"),
            # material price cited from the rate sheet -> not allowed (receipts only)
            ProposedCostItem(category="material", description="Board", unit="ea", qty="3", rate="82.00",
                             qty_fact_ids=[], rate_fact_id="F02-001"),
        ],
    )
    ai = validate_finding("s1", pf, facts)
    foreman, board = ai["ai_cost_items"]
    assert foreman["rate"] is None and foreman["amount"] is None and foreman["qty"] == "8"
    assert board["rate"] is None and board["qty"] is None and board["amount"] is None
    kinds = [q["kind"] for q in ai["questions"]]
    assert kinds.count("missing_rate") == 2 and kinds.count("missing_quantity") == 1


def test_co_match_must_exist_in_log(facts):
    ok = validate_finding("s1", _finding(change_key="blocking", status="already_recorded", co_number="co 3"), facts)
    assert ok["finding_status"] == "already_recorded"
    assert ok["co_match"] == {"co_number": "CO-003", "description": "CO-003 | blocking | $1,840.00", "amount": "1840.00"}
    assert not any(q["kind"] == "missing_authorization" for q in ok["questions"])

    bad = validate_finding("s1", _finding(change_key="x", status="already_recorded", co_number="CO-009",
                                          evidence=[ProposedEvidence(fact_id="F01-001", role="instruction")]), facts)
    assert bad["finding_status"] == "needs_info" and bad["co_match"] is None
    assert any(q["kind"] == "conflicting_records" for q in bad["questions"])

    no_number = validate_finding("s1", _finding(change_key="y", status="already_recorded",
                                                evidence=[ProposedEvidence(fact_id="F01-001", role="instruction")]), facts)
    assert no_number["finding_status"] == "needs_info"


def test_missing_authorization_question(facts):
    # only an unverified instruction -> missing_authorization + needs_info
    ai = validate_finding("s1", _finding(evidence=[ProposedEvidence(fact_id="F08-001", role="instruction")]), facts)
    assert ai["finding_status"] == "needs_info"
    assert [q["kind"] for q in ai["questions"]] == ["missing_authorization"]
    assert ai["evidence"][0]["verified"] is False  # unverified evidence is kept (greyed out)
    # question ids are stable across runs (so resolutions survive re-scans)
    again = validate_finding("s1", _finding(evidence=[ProposedEvidence(fact_id="F08-001", role="instruction")]), facts)
    assert again["questions"][0]["question_id"] == ai["questions"][0]["question_id"]


def test_unknown_fact_ids_are_dropped(facts):
    ai = validate_finding("s1", _finding(evidence=[ProposedEvidence(fact_id="F99-999", role="instruction"),
                                                   ProposedEvidence(fact_id="F01-001", role="instruction")]), facts)
    assert len(ai["evidence"]) == 1


def test_normalize_co_number():
    assert normalize_co_number("CO-003") == normalize_co_number("co 3") == normalize_co_number("Change Order #3") == "CO3"
