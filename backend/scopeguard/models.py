"""Pydantic v2 models for structured LLM output (the "proposal" side).

These are what the model may propose. Python (validate.py) then verifies quotes, math,
rate sourcing and change-order matches before anything is shown to a reviewer.
Numbers proposed by the model are strings so they can be parsed exactly with Decimal.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

DocType = Literal[
    "subcontract",
    "scope",
    "spec_revision",
    "instruction",
    "daily_report",
    "timesheet",
    "receipt",
    "rate_sheet",
    "change_order_log",
    "other",
]

FactKind = Literal[
    "scope_inclusion",  # original scope includes X
    "scope_exclusion",  # original scope excludes X
    "instruction",  # someone directed / requested work
    "spec_change",  # revised spec / drawing / ASI changes requirements
    "work_record",  # work performed (daily report / timesheet entry)
    "labor_rate",  # labor or equipment rate (rate sheet / subcontract)
    "markup",  # markup / overhead / profit percentage
    "material_cost",  # material or rental cost (receipt)
    "change_order",  # change-order log entry
    "other",
]

EvidenceRole = Literal["instruction", "scope_baseline", "work_record", "cost_support", "change_order", "contradiction"]
QuestionKind = Literal["missing_authorization", "unclear_scope", "conflicting_records", "missing_rate", "missing_quantity"]
CostCategory = Literal["labor", "material", "equipment", "markup", "other"]
FindingStatus = Literal["new", "already_recorded", "needs_info"]

_STRICT = ConfigDict(extra="ignore")


# ---------------------------------------------------------------- step 1: per-document extraction


class ExtractedFact(BaseModel):
    model_config = _STRICT

    kind: FactKind
    page: int = Field(ge=1, description="1-based page number where the quote appears")
    quote: str = Field(min_length=3, description="Exact verbatim text copied from the document (one sentence or line)")
    summary: str = Field(description="One-line plain-English statement of the fact")
    date: Optional[str] = Field(default=None, description="ISO date (YYYY-MM-DD) the fact refers to, if stated")
    topic: Optional[str] = Field(default=None, description="Short label for the work item / location, e.g. 'L2 corridor soffit'")
    person: Optional[str] = Field(default=None, description="Who gave the instruction / performed the work, if stated")
    qty: Optional[str] = Field(default=None, description="Quantity stated in the quote as a decimal string, e.g. '19.5'")
    unit: Optional[str] = Field(default=None, description="Unit for qty or rate, e.g. 'h', 'ea', 'LF', '%', 'ls'")
    rate: Optional[str] = Field(default=None, description="Unit rate or price stated in the quote as a decimal string, e.g. '82.00'")
    amount: Optional[str] = Field(default=None, description="Total dollar amount stated in the quote, decimal string")
    co_number: Optional[str] = Field(default=None, description="Change-order number if this is a change-order log entry, e.g. 'CO-003'")
    co_status: Optional[str] = Field(default=None, description="Change-order status if stated (approved, pending, rejected)")


class DocumentExtraction(BaseModel):
    model_config = _STRICT

    doc_type: DocType
    doc_summary: str = Field(description="One or two sentences describing the document")
    doc_date: Optional[str] = Field(default=None, description="ISO date of the document, if stated")
    facts: list[ExtractedFact] = Field(default_factory=list)


# ---------------------------------------------------------------- step 2: grouping + findings


class ProposedEvidence(BaseModel):
    model_config = _STRICT

    fact_id: str
    role: EvidenceRole


class ProposedCostItem(BaseModel):
    model_config = _STRICT

    category: CostCategory
    description: str
    unit: str = Field(description="'h' for labor hours, 'ea', 'LF', 'ls', '%' for markup")
    qty: Optional[str] = Field(default=None, description="Decimal string; ignored for percentage markup")
    rate: Optional[str] = Field(default=None, description="Decimal string unit rate; for markup with unit '%', the percentage (amount = sum of other items x rate / 100)")
    qty_fact_ids: list[str] = Field(default_factory=list, description="Fact IDs whose qty values sum to this qty")
    rate_fact_id: Optional[str] = Field(default=None, description="Fact ID of the rate-sheet/subcontract/receipt fact supporting the rate (or markup %)")


class ProposedQuestion(BaseModel):
    model_config = _STRICT

    kind: QuestionKind
    text: str


class ProposedFinding(BaseModel):
    model_config = _STRICT

    change_key: str = Field(description="Short stable identifier for the change, e.g. 'l2-corridor-soffit'")
    title: str
    summary: str
    status: FindingStatus
    co_number: Optional[str] = Field(default=None, description="Matching change-order number from the change-order log, if already recorded")
    scope_baseline: str = Field(description="What the original scope says (short)")
    requested_work: str = Field(description="What was asked / performed beyond it (short)")
    evidence: list[ProposedEvidence] = Field(default_factory=list)
    cost_items: list[ProposedCostItem] = Field(default_factory=list)
    questions: list[ProposedQuestion] = Field(default_factory=list)


class FindingsProposal(BaseModel):
    model_config = _STRICT

    findings: list[ProposedFinding] = Field(default_factory=list)


# ---------------------------------------------------------------- step 3: narrative


class ProposedTimelineEntry(BaseModel):
    model_config = _STRICT

    date: str
    event: str
    fact_id: Optional[str] = None


class FindingNarrative(BaseModel):
    model_config = _STRICT

    draft: str = Field(description="Change-request narrative, plain text / markdown, based only on the provided verified facts")
    timeline: list[ProposedTimelineEntry] = Field(default_factory=list)
