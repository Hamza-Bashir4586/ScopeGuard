"""Reconciliation rules on verified facts: walkthrough amounts, no double counting, unknown != 0, stable IDs."""

from decimal import Decimal

import pytest

from scopeguard.extract import extract_pages
from scopeguard.llm import FakeLLM
from scopeguard.models import ProposedLink
from scopeguard.reconcile import Fact, accepted_links, norm_ref, reconcile, tokens, work_item_id
from scopeguard.validate import check_quote
from tests.conftest import FIXTURES


def load_facts(skip=(), replace=None):
    replace = replace or {}
    llm = FakeLLM()
    facts = []
    for idx, path in enumerate(sorted(FIXTURES.glob("*.txt")), start=1):
        if any(path.name.startswith(s) for s in skip):
            continue
        text = replace.get(path.name[:7], path.read_text(encoding="utf-8"))
        pages = extract_pages(path.name, text.encode())
        ext = llm.extract_document(path.name, pages)
        for n, ef in enumerate(ext.facts, start=1):
            qc = check_quote(ef.quote, pages, ef.page)
            facts.append(Fact(fact_id=f"F{idx:02d}-{n:03d}", file_id=f"file{idx}", version_id=f"v{idx}-{hash(text) & 0xffff}",
                              file_name=path.name, category_id=ext.category_id, fact_type=ef.fact_type, page=qc.page,
                              quote=ef.quote, summary=ef.summary, verified=qc.verified, doc_reference=ext.doc_reference,
                              doc_date=ext.doc_date, date=ef.date, reference=ef.reference, related_refs=ef.related_refs,
                              entity=ef.entity, location=ef.location, qty=ef.qty, unit=ef.unit, rate=ef.rate,
                              amount=ef.amount, status=ef.status, all_in=ef.all_in))
    return facts


def by_type(result):
    return {f["finding_type"]: f for f in result.findings}


def test_all_fake_quotes_verify():
    facts = load_facts()
    assert facts and all(f.verified for f in facts)


def test_walkthrough_amounts():
    result = reconcile("riverside", load_facts())
    f = by_type(result)
    assert set(f) == {"approved_unbilled", "scope_change_proposed", "authorization_unresolved"}

    d1 = f["approved_unbilled"]
    assert d1["amount"] == "8500.00" and d1["amount_basis"] == "approved_lump_sum" and not d1["proposal"]
    assert d1["authorization_status"] == "approved" and d1["approval_label"] == "Previously approved · CO-001"
    assert d1["financial"]["approved_amount"] == "8500.00" and d1["financial"]["invoiced_amount"] is None
    assert "not found in the supplied records" in d1["explanation"]
    assert [g["label"] for g in d1["evidence_groups"]] == ["Original agreement", "Approved change and completed work", "Billing comparison"]
    assert d1["evidence_groups"][0]["summary_quote"] == "Six branch circuits included in the signed scope."
    assert d1["evidence_groups"][2]["source_label"] == "INV-014 / INV-015, p. 1"
    assert d1["source_count"] == 5

    d2 = f["scope_change_proposed"]
    assert d2["amount"] == "1600.00" and d2["amount_basis"] == "quantity_x_rate" and d2["proposal"]
    assert d2["calculation"] == "2 × $800.00 = $1,600.00" and d2["title"] == "Two additional LED fixtures"
    assert d2["authorization_status"] == "pending" and len(d2["cost_items"]) == 1  # all-in: no markup line
    assert d2["cost_items"][0]["qty"] == "2" and d2["cost_items"][0]["rate"] == "800.00"

    d3 = f["authorization_unresolved"]
    assert d3["amount"] == "900.00" and d3["calculation"] == "9 h × $100.00/h = $900.00" and d3["proposal"]
    assert d3["authorization_status"] == "unclear" and d3["title"] == "Weekend installation"
    rate_src = d3["cost_items"][0]["source"]
    assert rate_src["file_name"].startswith("Exhibit B")  # rate from the agreement exhibit, not the timesheet
    assert "premium" in d3["explanation"]

    total = sum(Decimal(x["amount"]) for x in result.findings)
    assert total == Decimal("11000.00")


def test_existing_items_are_never_new_charges():
    result = reconcile("riverside", load_facts())
    items = {(x["kind"], x["reference"]): x for x in result.existing_items}
    assert set(items) == {("invoice_balance", "INV-015"), ("retainage", "INV-014"), ("approved_billed", "CO-002")}
    assert items[("invoice_balance", "INV-015")]["amount"] == "500.00"
    assert "$1,500.00 billed − $1,000.00 allocated payment = $500.00" in items[("invoice_balance", "INV-015")]["explanation"]
    assert items[("retainage", "INV-014")]["amount"] == "6250.00"
    assert "Consistent with the 5% retainage" in items[("retainage", "INV-014")]["explanation"]
    assert items[("approved_billed", "CO-002")]["amount"] == "1500.00"
    # nothing double counted: no finding references CO-002, INV-014 or INV-015 as its own work
    assert all(not set(f["refs"]) & {"CO-002", "INV-014", "INV-015"} for f in result.findings)
    assert [x["kind"] for x in result.existing_items] == ["invoice_balance", "retainage", "approved_billed"]


def test_work_item_ids_are_stable_and_scan_independent():
    a = reconcile("riverside", load_facts())
    b = reconcile("riverside", load_facts())
    assert [f["work_item_id"] for f in a.findings] == [f["work_item_id"] for f in b.findings]
    assert {f["identity"] for f in a.findings} == {"change:CO-001", "instruction:SI-009", "instruction:SI-008"}
    assert a.findings[0]["work_item_id"] == work_item_id("riverside", "change:CO-001")
    assert [f["fingerprint"] for f in a.findings] == [f["fingerprint"] for f in b.findings]


def test_changed_evidence_changes_fingerprint_only_for_that_item():
    base = reconcile("riverside", load_facts())
    ts = (FIXTURES / "Saturday crew timesheet.txt").read_text().replace("Nine labor hours", "Ten labor hours")
    changed = reconcile("riverside", load_facts(replace={"Saturda": ts}))
    fb = {f["identity"]: f for f in base.findings}
    fc = {f["identity"]: f for f in changed.findings}
    assert fc["instruction:SI-008"]["amount"] == "1000.00"
    assert fc["instruction:SI-008"]["work_item_id"] == fb["instruction:SI-008"]["work_item_id"]
    assert fc["instruction:SI-008"]["fingerprint"] != fb["instruction:SI-008"]["fingerprint"]
    assert fc["change:CO-001"]["fingerprint"] == fb["change:CO-001"]["fingerprint"]


def test_unknown_is_not_zero():
    # no timesheet -> hours unknown -> amount None with a question, never $0.00
    r = reconcile("riverside", load_facts(skip=("Saturday crew",)))
    d3 = by_type(r)["authorization_unresolved"]
    assert d3["amount"] is None and d3["amount_basis"] == "unknown" and d3["calculation"] is None
    assert any(q["kind"] == "missing_quantity" for q in d3["questions"])
    # no quotation -> price unknown
    r = reconcile("riverside", load_facts(skip=("Additional fixture",)))
    d2 = by_type(r)["scope_change_proposed"]
    assert d2["amount"] is None and any(q["kind"] == "missing_rate" for q in d2["questions"])
    # no remittance -> payments unknown -> balance unknown (not "unpaid")
    r = reconcile("riverside", load_facts(skip=("REM-",)))
    bal = [x for x in r.existing_items if x["kind"] == "invoice_balance"]
    assert {x["reference"] for x in bal} == {"INV-014", "INV-015"} and all(x["amount"] is None for x in bal)
    assert not any(x["kind"] == "retainage" for x in r.existing_items)


def test_approved_change_without_completion_needs_information():
    r = reconcile("riverside", load_facts(skip=("Conference-room",)))
    f = {x["identity"]: x for x in r.findings}["change:CO-001"]
    assert f["finding_type"] == "needs_information" and any(q["kind"] == "missing_completion" for q in f["questions"])


def test_billed_change_order_is_not_a_finding():
    inv = (FIXTURES / "INV-015 — Lighting upgrade invoice.txt").read_text() + "\fInvoice INV-015\nCO-001 conference circuits: $8,500.\n"
    r = reconcile("riverside", load_facts(replace={"INV-015": inv}))
    assert "change:CO-001" not in {f["identity"] for f in r.findings}
    assert {x["reference"] for x in r.existing_items if x["kind"] == "approved_billed"} == {"CO-001", "CO-002"}


def test_unverified_facts_are_ignored():
    facts = load_facts()
    for f in facts:
        if f.fact_type == "timesheet_hours":
            f.verified = False
    d3 = by_type(reconcile("riverside", facts))["authorization_unresolved"]
    assert d3["amount"] is None


def test_link_validation_and_uncertain_billing_links():
    facts = load_facts()
    ids = {f.fact_type + ":" + (f.reference or ""): f.fact_id for f in facts}
    ts = next(f for f in facts if f.fact_type == "timesheet_hours")
    inv_line = next(f for f in facts if f.fact_type == "invoice_line" and "CO-002" in f.related_refs)
    quote = next(f for f in facts if f.fact_type == "quote")
    links = [
        ProposedLink(from_fact_id=ts.fact_id, to_fact_id=inv_line.fact_id, relation="prices"),  # wrong types -> rejected
        ProposedLink(from_fact_id="F99-999", to_fact_id=quote.fact_id, relation="prices"),  # unknown fact -> rejected
        ProposedLink(from_fact_id=inv_line.fact_id, to_fact_id=quote.fact_id, relation="bills"),  # uncertain -> question
    ]
    accepted = accepted_links(links, {f.fact_id: f for f in facts})
    assert accepted == {("bills", inv_line.fact_id, quote.fact_id)}
    d2 = by_type(reconcile("riverside", facts, links))["scope_change_proposed"]
    assert any(q["kind"] == "possible_duplicate" for q in d2["questions"])  # blocks invoicing until resolved
    assert ids  # silence unused


@pytest.mark.parametrize("raw,expected", [("CO-001", "CO-001"), ("co 1", "CO-001"), ("Change Order #3", "CO-003"),
                                          ("INV-014", "INV-014"), ("REM-2026-031", "REM-2026-031"), ("SI-9", "SI-009"),
                                          ("conference room", None)])
def test_norm_ref(raw, expected):
    assert norm_ref(raw) == expected


def test_tokens_synonyms():
    assert "weekend" in tokens("Complete the installation during Saturday access.")
    assert tokens("Supply and install 24 LED fixtures.") == {"led", "fixture"}


def test_tolerates_plausible_model_labeling_variance():
    """Real models label facts differently; amounts must still come out right and never double count."""
    import dataclasses

    facts = load_facts()
    out = []
    for f in facts:
        if f.quote.startswith("Approved additional amount"):
            f = dataclasses.replace(f, fact_type="change_description", status=None)  # approval phrased as description
        if f.quote.startswith("Revised installation requires"):
            f = dataclasses.replace(f, fact_type="instruction")  # quantity revision labeled as an instruction
        if f.fact_type == "quote":
            f = dataclasses.replace(f, all_in=None, related_refs=[])  # no explicit link or all-in flag
        out.append(f)
        if f.fact_type == "timesheet_hours":  # the same nine hours restated in the same record
            out.append(dataclasses.replace(f, fact_id=f.fact_id + "b", quote="At the $100/hour rate in Exhibit B: $900 labor cost."))
        if f.fact_type == "payment" and "INV-014" in f.related_refs:  # remittance restating the invoice amount
            out.append(dataclasses.replace(f, fact_id=f.fact_id + "x", fact_type="invoice_line", reference="INV-014",
                                           related_refs=[], amount="125000.00"))
    r = reconcile("riverside", out)
    amounts = {f["finding_type"]: f["amount"] for f in r.findings}
    assert amounts == {"approved_unbilled": "8500.00", "scope_change_proposed": "1600.00", "authorization_unresolved": "900.00"}
    items = {(x["kind"], x["reference"]): x["amount"] for x in r.existing_items}
    assert items[("retainage", "INV-014")] == "6250.00" and items[("invoice_balance", "INV-015")] == "500.00"
