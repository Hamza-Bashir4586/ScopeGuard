from decimal import Decimal


from scopeguard.validate import (
    check_quote,
    compute_cost_item,
    cost_total,
    finalize_cost_items,
    normalize_text,
    parse_decimal,
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
