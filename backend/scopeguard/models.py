"""Pydantic v2 models for structured LLM output (the "proposal" side).

The model classifies records, extracts typed facts with verbatim quotes, and may propose links between facts.
Python then verifies every quote (validate.py) and decides findings, amounts and eligibility (reconcile.py).
Numbers proposed by the model are strings so they can be parsed exactly with Decimal.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

FactType = Literal[
    "agreement_value",      # original contract value (amount)
    "agreement_date",       # date the agreement was signed (date)
    "scope_inclusion",      # included work item (entity, qty/unit when stated)
    "scope_exclusion",      # excluded work (entity)
    "labor_rate",           # labor rate for additional work (rate, unit "h")
    "pricing_basis",        # other pricing method statements (materials at cost, markup %, no premium ...)
    "change_procedure",     # how changes / additional work are approved
    "payment_term",         # payment timing / billing terms
    "retainage_term",       # retainage percentage (rate = percent, unit "%")
    "instruction",          # a directive / request to perform work (reference = SI/RFI/directive number)
    "quantity_change",      # a revised required quantity (entity, qty, unit)
    "change_description",   # description of the work in a change order or proposal
    "approval",             # approval / acceptance status of a change or price (status, amount when approved)
    "work_completion",      # work performed or completed (date, related references)
    "timesheet_hours",      # labor hours recorded (qty, unit "h")
    "quote",                # priced proposal / quotation line (qty, unit, rate, amount, all_in)
    "invoice_line",         # an actual charged line on an invoice (reference = invoice number, amount)
    "invoice_total",        # an invoice's total amount
    "invoice_revision",     # this invoice revises / supersedes another (related_refs = the prior invoice)
    "billing_note",         # a statement about what an invoice does or does NOT contain
    "retainage_withheld",   # retainage withheld on an invoice (amount)
    "credit",               # credit note / deduction against an invoice (amount)
    "payment",              # payment applied to a specific invoice (amount, related_refs = [invoice])
    "other",
]

CATEGORY_IDS = ("original_agreement", "approvals", "field_records", "billing", "change_instructions",
                "pricing", "payments", "correspondence", "unclassified")

_LENIENT = ConfigDict(extra="ignore")


class ExtractedFact(BaseModel):
    model_config = _LENIENT

    fact_type: FactType
    page: int = Field(ge=1, description="1-based page number where the quote appears")
    quote: str = Field(min_length=3, description="Exact verbatim text copied from the document (one sentence or line)")
    summary: str = Field(description="One-line plain-English statement of the fact")
    date: Optional[str] = Field(default=None, description="ISO date (YYYY-MM-DD) the fact refers to, if stated")
    reference: Optional[str] = Field(default=None, description="Identity of the document/item this fact belongs to, "
                                     "e.g. 'CO-001', 'INV-014', 'SI-008', 'REM-2026-031'")
    related_refs: list[str] = Field(default_factory=list, description="Other document/item references the quote "
                                    "positively relates to, e.g. ['CO-001'] or ['INV-015']")
    entity: Optional[str] = Field(default=None, description="Short work-item noun phrase, e.g. 'LED fixtures', "
                                  "'branch circuits', 'weekend installation'")
    location: Optional[str] = Field(default=None, description="Location if stated, e.g. 'conference room'")
    qty: Optional[str] = Field(default=None, description="Quantity stated in the quote as a decimal string, e.g. '26' or '9'")
    unit: Optional[str] = Field(default=None, description="Unit for qty or rate, e.g. 'ea', 'h', '%', 'ls'")
    rate: Optional[str] = Field(default=None, description="Unit rate/price or percentage stated in the quote, decimal string")
    amount: Optional[str] = Field(default=None, description="Total dollar amount stated in the quote, decimal string")
    status: Optional[str] = Field(default=None, description="For approvals: 'approved', 'signed', 'accepted', "
                                  "'pending', 'not_recorded', 'rejected'")
    all_in: Optional[bool] = Field(default=None, description="For quotes: true when the price is all-in (supplied and installed)")


class DocumentExtraction(BaseModel):
    model_config = _LENIENT

    category_id: str = Field(description="One of: " + ", ".join(CATEGORY_IDS))
    category_label: str = Field(description="Display label, e.g. 'Original agreement', 'Approvals', 'Billing'")
    doc_reference: Optional[str] = Field(default=None, description="The document's own identifier, e.g. 'INV-014', 'CO-001'")
    doc_date: Optional[str] = Field(default=None, description="ISO date of the document, if stated")
    doc_summary: str = Field(description="One sentence describing the document")
    facts: list[ExtractedFact] = Field(default_factory=list)


LinkRelation = Literal["same_work", "prices", "performs", "instructs_excluded", "changes_quantity_of", "bills", "pays", "approves"]


class ProposedLink(BaseModel):
    model_config = _LENIENT

    from_fact_id: str
    to_fact_id: str
    relation: LinkRelation
    note: str = ""


class WorkItemLinks(BaseModel):
    model_config = _LENIENT

    links: list[ProposedLink] = Field(default_factory=list)
