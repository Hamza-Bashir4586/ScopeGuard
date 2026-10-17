"""Deterministic reconciliation: verified facts -> findings + existing items. Pure Python, no I/O.

The model extracts typed facts (quotes verified by validate.check_quote) and may propose links between facts.
This module decides [FIN-01..12, AI-04..06, REV-06/07]:

* approved_unbilled        - a change with a verified approval + amount, a completion record, and no invoice line
                             referencing it in the supplied invoices ("not found in supplied records").
* scope_change_proposed    - a revised quantity above the signed-scope quantity, priced from a supported quote
                             (delta x quoted rate; no markup on all-in prices). A proposal until accepted.
* authorization_unresolved - an instruction for work the agreement excludes, priced from recorded hours x the
                             agreement labor rate, with no price acceptance. Needs acknowledgment to invoice.
* existing items           - invoice balance = billed - allocated payments - credits (never a new charge);
                             retainage identified as (part of) that balance; already-billed changes.

Amounts come only from verified facts. Unknown quantities/rates/payments stay None (never 0) and raise questions.
Work-item IDs are a hash of project ID + normalized work identity (change ref / instruction ref / entity) and
never include a scan ID, so reviews carry across scans.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any, Iterable, Optional

from .validate import fmt_money, money_str, normalize_text, parse_decimal, qty_str

# ============================================================ facts


@dataclass
class Fact:
    """One extracted fact after quote verification, pinned to a file version and page."""

    fact_id: str
    file_id: str
    version_id: str
    file_name: str
    category_id: str
    fact_type: str
    page: int
    quote: str
    summary: str
    verified: bool
    doc_reference: Optional[str] = None
    doc_date: Optional[str] = None
    date: Optional[str] = None
    reference: Optional[str] = None
    related_refs: list[str] = field(default_factory=list)
    entity: Optional[str] = None
    location: Optional[str] = None
    qty: Optional[str] = None
    unit: Optional[str] = None
    rate: Optional[str] = None
    amount: Optional[str] = None
    status: Optional[str] = None
    all_in: Optional[bool] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Fact":
        known = {k: d.get(k) for k in cls.__dataclass_fields__ if k in d}
        known["related_refs"] = list(known.get("related_refs") or [])
        return cls(**known)

    def prompt_view(self) -> dict:
        skip = {"file_id", "version_id", "verified"}
        out = {k: v for k, v in asdict(self).items() if v not in (None, "", []) and k not in skip}
        out["verified"] = self.verified
        return out

    def source_ref(self) -> dict:
        return {"file_id": self.file_id, "version_id": self.version_id, "file_name": self.file_name,
                "page": int(self.page), "quote": self.quote if self.verified else None, "available": True}


def short_hash(*parts: str, length: int = 16) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:length]


def evidence_id(f: Fact) -> str:
    return "e_" + short_hash(f.file_id, f.version_id, str(f.page), normalize_text(f.quote), length=12)


def evidence_from_fact(f: Fact) -> dict:
    return {"evidence_id": evidence_id(f), "file_id": f.file_id, "version_id": f.version_id, "file_name": f.file_name,
            "page": int(f.page), "quote": f.quote, "verified": bool(f.verified), "fact_type": f.fact_type,
            "approved": is_approved(f) if f.fact_type == "approval" else None}


def evidence_fingerprint(evidence: Iterable[dict]) -> str:
    """Hash of the cited file/version/quote set [REV-08]; changes when any cited record version or quote changes."""
    items = sorted({(e["file_id"], e["version_id"], normalize_text(e["quote"])) for e in evidence})
    return "fp_" + short_hash(*["\x1f".join(i) for i in items], length=20)


# ============================================================ references and entities

_REF_RE = re.compile(r"\b(CO|PCO|CCO|SI|RFI|ASI|INV|REM|CN|DIR)[ \t#-]*(\d+(?:-\d+)*)\b", re.I)
CHANGE_PREFIXES = {"CO", "PCO", "CCO"}
INSTRUCTION_PREFIXES = {"SI", "RFI", "ASI", "DIR"}
INVOICE_PREFIXES = {"INV"}
_NEGATION = re.compile(r"\b(no|not|without|distinct from|other than|excluding|excludes|except)\b[^.;:]*$", re.I)
_APPROVED = {"approved", "signed", "accepted", "executed", "approved_signed", "fully_executed"}


def norm_ref(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    text = re.sub(r"change\s*order\s*(no\.?|number|#)?\s*", "CO-", str(value), flags=re.I)
    m = _REF_RE.search(text)
    if not m:
        return None
    prefix, num = m.group(1).upper(), m.group(2)
    if "-" in num:
        return f"{prefix}-{num}"
    return f"{prefix}-{int(num):03d}"


def ref_prefix(ref: Optional[str]) -> str:
    return (ref or "").split("-", 1)[0]


def own_ref(f: Fact) -> Optional[str]:
    return norm_ref(f.reference) or norm_ref(f.doc_reference)


def quote_refs(f: Fact, *, positive_only: bool = True) -> set[str]:
    out = set()
    for m in _REF_RE.finditer(f.quote or ""):
        if positive_only and _NEGATION.search(f.quote[: m.start()]):
            continue
        n = norm_ref(m.group(0))
        if n:
            out.add(n)
    return out


def negated_refs(f: Fact) -> set[str]:
    return quote_refs(f, positive_only=False) - quote_refs(f, positive_only=True)


def all_refs(f: Fact) -> set[str]:
    """Own ref + positively related refs (model-supplied related refs minus refs the quote itself negates)."""
    out = {r for r in (norm_ref(x) for x in f.related_refs) if r} | quote_refs(f)
    out -= negated_refs(f)
    o = own_ref(f)
    if o:
        out.add(o)
    return out


_STOP = {
    "the", "a", "an", "of", "to", "and", "or", "in", "on", "at", "for", "during", "with", "under", "per", "by", "is",
    "are", "be", "all", "any", "this", "that", "complete", "completed", "install", "installation", "installed",
    "supply", "supplied", "provide", "work", "excluded", "exclude", "included", "include", "scope", "signed",
    "existing", "additional", "new", "requested", "request", "requires", "require", "required", "revised", "record",
    "recorded", "each", "item", "items", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "location", "locations", "shown", "rev", "project", "customer", "contract", "agreement", "change", "approved",
    "amount", "quantity", "total", "per", "from", "its", "into", "has", "have", "been", "not", "no",
}
_SYN = {"saturday": "weekend", "sunday": "weekend", "weekends": "weekend", "luminaire": "fixture", "luminaires": "fixture",
        "conference-room": "conference"}


def tokens(text: Optional[str]) -> set[str]:
    out = set()
    for w in re.findall(r"[a-z]+", (text or "").lower()):
        w = _SYN.get(w, w)
        if len(w) > 4 and w.endswith("ies"):
            w = w[:-3] + "y"
        elif len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        w = _SYN.get(w, w)
        if len(w) < 3 or w in _STOP:
            continue
        out.add(w)
    return out


def entity_tokens(f: Fact) -> set[str]:
    return tokens(f.entity) | tokens(f.quote) if f.entity else tokens(f.quote)


def overlap(a: Fact, b: Fact) -> int:
    ta = tokens(a.entity) if a.entity else tokens(a.quote)
    tb = tokens(b.entity) if b.entity else tokens(b.quote)
    best = len(ta & tb)
    # fall back to quote tokens (entities are short and may be phrased differently)
    return max(best, len(tokens(a.quote) & tokens(b.quote)))


_NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
              "ten": 10, "eleven": 11, "twelve": 12}
_WORDS = {v: k for k, v in _NUM_WORDS.items()}


def number_word(d: Decimal) -> str:
    return _WORDS.get(int(d), qty_str(d)) if d == d.to_integral_value() else qty_str(d)


def dec(value: Any) -> Optional[Decimal]:
    d = parse_decimal(value)
    if d is None and isinstance(value, str) and value.strip().lower() in _NUM_WORDS:
        d = Decimal(_NUM_WORDS[value.strip().lower()])
    return d


def is_approved(f: Fact) -> bool:
    st = (f.status or "").strip().lower().replace(" ", "_")
    if st:
        return st in _APPROVED
    q = f.quote.lower()
    if re.search(r"\b(not|no|pending|without|unsigned|awaiting)\b", q):
        return False
    return bool(re.search(r"\b(approved|signed acceptance|accepted|executed)\b", q))


def short_doc_label(f: Fact) -> str:
    ref = norm_ref(f.doc_reference)
    if ref:
        return ref
    stem = f.file_name.rsplit(".", 1)[0]
    return stem.split(" — ")[0].split(" - ")[0].strip()


def source_label(facts: list[Fact]) -> str:
    labels: list[str] = []
    for f in facts:
        lab = short_doc_label(f)
        if lab not in labels:
            labels.append(lab)
    pages = sorted({f.page for f in facts})
    joiner = " / " if all(ref_prefix(norm_ref(l)) in INVOICE_PREFIXES for l in labels) else " + "
    page_part = f", p. {pages[0]}" if len(pages) == 1 else ""
    return joiner.join(labels) + page_part


def cite(f: Fact) -> str:
    return f'"{f.quote}" ({f.file_name}, p. {f.page})'


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s


# ============================================================ result types

TYPE_LABELS = {
    "approved_unbilled": "MISSING FROM INVOICE",
    "scope_change_proposed": "QUANTITY CHANGE",
    "authorization_unresolved": "EXCLUDED WORK",
    "invoice_balance_unresolved": "UNRESOLVED BALANCE",
    "needs_information": "NEEDS INFORMATION",
}
TYPE_ORDER = {"approved_unbilled": 0, "scope_change_proposed": 1, "authorization_unresolved": 2,
              "invoice_balance_unresolved": 3, "needs_information": 4}
BLOCKING_QUESTION_KINDS = {"possible_duplicate", "conflicting_records", "missing_completion", "missing_billing_records"}
EXISTING_ORDER = {"invoice_balance": 0, "retainage": 1, "approved_billed": 2, "credit": 3, "reconciled": 4}


@dataclass
class Check:
    message: str
    source: Optional[dict]


@dataclass
class ReconcileResult:
    findings: list[dict]
    existing_items: list[dict]
    checks: list[Check]
    warnings: list[str]


def work_item_id(project_id: str, identity: str) -> str:
    return "wi_" + short_hash(project_id, identity, length=16)


def _question(identity: str, kind: str, text: str) -> dict:
    return {"question_id": "q_" + short_hash(identity, kind, text, length=10), "kind": kind, "text": text}


def _group(label: str, facts: list[Fact], summary_quote: str) -> Optional[dict]:
    facts = [f for f in facts if f is not None]
    if not facts:
        return None
    seen, ev = set(), []
    for f in facts:
        e = evidence_from_fact(f)
        if e["evidence_id"] not in seen:
            seen.add(e["evidence_id"])
            ev.append(e)
    return {"label": label, "source_label": source_label(facts), "summary_quote": summary_quote, "evidence": ev}


def _cost_item(description: str, qty: Optional[Decimal], unit: str, rate: Optional[Decimal], source: Optional[Fact],
               formula_override: Optional[str] = None) -> dict:
    amount = None
    if qty is not None and rate is not None:
        amount = qty * rate
    if formula_override:
        formula = formula_override
    elif amount is None:
        missing = [n for n, v in (("quantity", qty), ("rate", rate)) if v is None]
        formula = f"Missing {' and '.join(missing)} - amount not computed"
    elif unit == "h":
        formula = f"{qty_str(qty)} h × {fmt_money(rate)}/h = {fmt_money(amount)}"
    else:
        formula = f"{qty_str(qty)} × {fmt_money(rate)} = {fmt_money(amount)}"
    return {"description": description, "qty": qty_str(qty) if qty is not None else None, "unit": unit,
            "rate": money_str(rate) if rate is not None else None,
            "amount": money_str(amount) if amount is not None else None, "formula": formula,
            "source": source.source_ref() if source else None}


def _financial(approved: Optional[Decimal] = None) -> dict:
    # Invoiced/paid/credited/retained for a NEW item are not asserted from silence: unknown -> None [FIN-06].
    return {"approved_amount": money_str(approved) if approved is not None else None, "invoiced_amount": None,
            "credited_amount": None, "paid_amount": None, "retained_amount": None, "unresolved_balance": None}


def _finding(project_id: str, identity: str, finding_type: str, *, title: str, reason: str, explanation: str,
             authorization_status: str, approval_label: str, proposal: bool, amount: Optional[Decimal],
             amount_basis: str, cost_items: list[dict], financial: dict, groups: list[Optional[dict]],
             questions: list[dict], refs: list[str]) -> dict:
    groups_ = [g for g in groups if g]
    evidence = [e for g in groups_ for e in g["evidence"]]
    calc = "; ".join(c["formula"] for c in cost_items if c.get("amount")) or None
    return {
        "work_item_id": work_item_id(project_id, identity),
        "identity": identity,
        "refs": refs,
        "finding_type": finding_type,
        "type_label": TYPE_LABELS[finding_type],
        "title": title,
        "reason": reason,
        "explanation": explanation,
        "authorization_status": authorization_status,
        "approval_label": approval_label,
        "proposal": proposal,
        "amount": money_str(amount) if amount is not None else None,
        "amount_basis": amount_basis if amount is not None else "unknown",
        "calculation": calc if amount is not None else None,
        "cost_items": cost_items,
        "financial": financial,
        "evidence_groups": groups_,
        "source_count": len({(e["file_id"]) for e in evidence}),
        "questions": questions,
        "fingerprint": evidence_fingerprint(evidence),
    }


# ============================================================ link validation


_LINK_TYPES = {
    "prices": ({"quote"}, {"quantity_change", "instruction", "change_description"}),
    "performs": ({"timesheet_hours", "work_completion"}, {"instruction", "approval", "change_description"}),
    "instructs_excluded": ({"instruction"}, {"scope_exclusion"}),
    "changes_quantity_of": ({"quantity_change"}, {"scope_inclusion"}),
    "bills": ({"invoice_line"}, {"approval", "change_description", "instruction", "quantity_change", "quote"}),
}


def accepted_links(links: Iterable[Any], facts: dict[str, Fact]) -> set[tuple[str, str, str]]:
    """Keep only model links between verified facts whose types fit the relation (either direction)."""
    out: set[tuple[str, str, str]] = set()
    for link in links or []:
        rel = getattr(link, "relation", None) or (link.get("relation") if isinstance(link, dict) else None)
        a = getattr(link, "from_fact_id", None) or (link.get("from_fact_id") if isinstance(link, dict) else None)
        b = getattr(link, "to_fact_id", None) or (link.get("to_fact_id") if isinstance(link, dict) else None)
        fa, fb = facts.get(a or ""), facts.get(b or "")
        if not fa or not fb or not fa.verified or not fb.verified:
            continue
        candidates = [rel] if rel in _LINK_TYPES else (list(_LINK_TYPES) if rel == "same_work" else [])
        for r in candidates:
            src, dst = _LINK_TYPES[r]
            if fa.fact_type in src and fb.fact_type in dst:
                out.add((r, fa.fact_id, fb.fact_id))
            elif fb.fact_type in src and fa.fact_type in dst:
                out.add((r, fb.fact_id, fa.fact_id))
    return out


# ============================================================ main entry


def reconcile(project_id: str, facts: list[Fact], links: Iterable[Any] = ()) -> ReconcileResult:
    V = [f for f in facts if f.verified]
    by_id = {f.fact_id: f for f in V}
    linkset = accepted_links(links, {f.fact_id: f for f in facts})

    def of(*types: str) -> list[Fact]:
        return [f for f in V if f.fact_type in types]

    def linked(rel: str, to_id: str) -> list[Fact]:
        return [by_id[a] for (r, a, b) in linkset if r == rel and b == to_id and a in by_id]

    checks: list[Check] = []
    warnings: list[str] = []
    findings: list[dict] = []
    existing: list[dict] = []

    # ---------------- shared context
    invoice_facts = of("invoice_line", "invoice_total", "billing_note", "retainage_withheld", "invoice_revision")
    supplied_invoices = sorted({r for r in (own_ref(f) for f in invoice_facts) if ref_prefix(r) in INVOICE_PREFIXES})
    superseded = {r for f in of("invoice_revision") for r in all_refs(f) - {own_ref(f)} if ref_prefix(r) in INVOICE_PREFIXES}
    def from_invoice_doc(f: Fact) -> bool:
        # Invoice amounts count only from the invoice itself, never from a remittance restating them [FIN-04].
        return f.category_id == "billing" or (norm_ref(f.doc_reference) is not None and norm_ref(f.doc_reference) == own_ref(f))

    invoice_lines = _dedupe([f for f in of("invoice_line") if parse_decimal(f.amount) is not None and from_invoice_doc(f)])
    inclusions = of("scope_inclusion")
    exclusions = of("scope_exclusion")
    agreement_docs = {f.file_id for f in V if f.category_id == "original_agreement"}
    labor_rates = [f for f in of("labor_rate") if (f.file_id in agreement_docs or re.search(
        r"exhibit|agreement|contract|rate sheet", f.file_name, re.I)) and parse_decimal(f.rate) is not None]
    markup_terms = [f for f in of("pricing_basis") if "markup" in f.quote.lower() and parse_decimal(f.rate) is not None
                    and not re.search(r"\bno\b", f.quote.lower())]
    # An approval may be phrased as a change description ("Approved additional amount: $8,500.").
    approvals = of("approval") + [f for f in of("change_description", "other") if parse_decimal(f.amount) is not None
                                  and re.search(r"\bapproved\b", f.quote, re.I) and not re.search(r"\b(not|pending)\b", f.quote, re.I)]

    def billing_lines_for(ref: str) -> list[Fact]:
        return [f for f in invoice_lines if ref in all_refs(f) and own_ref(f) != ref and own_ref(f) not in superseded]

    def billing_evidence(ref: str) -> list[Fact]:
        out = []
        for inv in supplied_invoices:
            notes = [f for f in of("billing_note") if own_ref(f) == inv and ref in quote_refs(f, positive_only=False)]
            lines = [f for f in invoice_lines if own_ref(f) == inv]
            out.extend(notes[:1] or lines[:1])
        return out

    def accepted_for(ref: Optional[str], target: Fact) -> list[Fact]:
        """Verified approvals/acceptances that positively reference the instruction (never inferred from silence)."""
        if not ref:
            return []
        return [a for a in approvals if is_approved(a) and ref in all_refs(a)]

    def acceptance_notes(ref: Optional[str]) -> list[Fact]:
        return [a for a in approvals if not is_approved(a) and ref and ref in all_refs(a)]

    def uncertain_billing(identity: str, targets: Iterable[Optional[Fact]]) -> list[dict]:
        """Model-proposed billing links without an explicit reference stay questions, never silent matches [AI-04]."""
        ids = {t.fact_id for t in targets if t is not None}
        return [_question(identity, "possible_duplicate",
                          f"{short_doc_label(by_id[a])} may already bill this work ({cite(by_id[a])}). Confirm it is not a duplicate.")
                for (r, a, b) in sorted(linkset) if r == "bills" and b in ids and a in by_id]

    used_identities: set[str] = set()
    covered_instruction_refs: set[str] = set()

    # ---------------- 1. changes with approvals (CO refs)
    change_refs = sorted({r for f in approvals + of("change_description") for r in all_refs(f)
                          if ref_prefix(r) in CHANGE_PREFIXES})
    billed_change_refs: set[str] = set()
    for ref in change_refs:
        ref_approvals = [a for a in approvals if ref in all_refs(a)]
        approved_facts = [a for a in ref_approvals if is_approved(a)]
        amounts = sorted({parse_decimal(a.amount) for a in approved_facts if parse_decimal(a.amount) is not None})
        desc = next((f for f in of("change_description") if ref in all_refs(f)), None) or next(
            (a for a in ref_approvals if a.entity), ref_approvals[0] if ref_approvals else None)
        amount_fact = next((a for a in approved_facts if parse_decimal(a.amount) is not None), None)
        completions = [f for f in of("work_completion") if ref in all_refs(f)]
        lines = billing_lines_for(ref)
        for f in V:  # instructions covered by this change order are not separate work items
            if ref in all_refs(f) and f.fact_type in ("instruction",):
                covered_instruction_refs.add(own_ref(f) or "")
        if lines:
            billed_change_refs.add(ref)
            continue  # handled as an existing "approved_billed" item below
        if not approved_facts or not amounts:
            continue  # unapproved change orders are not new charges; nothing to assert
        identity = f"change:{ref}"
        used_identities.add(identity)
        amount = amounts[0] if len(amounts) == 1 else None
        questions: list[dict] = uncertain_billing(identity, [desc, amount_fact, *approved_facts])
        if len(amounts) > 1:
            questions.append(_question(identity, "conflicting_records",
                                       f"{ref} states different approved amounts ({', '.join(fmt_money(a) for a in amounts)}). Which amount applies?"))
        base = max(inclusions, key=lambda i: overlap(i, desc or amount_fact), default=None) if (desc or amount_fact) else None
        if base is not None and overlap(base, desc or amount_fact) == 0:
            base = None
        entity = (desc.entity if desc and desc.entity else None) or (amount_fact.entity if amount_fact else None) or "approved work"
        location = next((f.location for f in [desc, amount_fact, *completions] if f is not None and f.location), None)
        title = _cap(f"additional {location} {entity}" if location else f"additional {entity}")
        bill_ev = billing_evidence(ref)
        inv_list = ", ".join(supplied_invoices)
        checks.append(Check(f"Comparing {ref} with {' and '.join(supplied_invoices) if supplied_invoices else 'the supplied invoices'}",
                            (amount_fact or desc).source_ref() if (amount_fact or desc) else None))
        groups = [
            _group("Original agreement", [base] if base else [], base.quote if base else ""),
            _group("Approved change and completed work", [x for x in [desc, amount_fact] if x] + completions[:2],
                   f"{ref} approved at {fmt_money(amount) if amount is not None else 'an unconfirmed amount'}"
                   + (" and recorded as completed." if completions else "; no completion record found.")),
            _group("Billing comparison", bill_ev,
                   (f"No supplied invoice ({inv_list}) contains a matching {ref} charge." if supplied_invoices
                    else "No invoices were supplied for comparison.")),
        ]
        cost = [_cost_item(f"{ref} approved lump sum", Decimal(1) if amount is not None else None, "ls", amount, amount_fact,
                           formula_override=f"Approved lump sum ({ref}) = {fmt_money(amount)}" if amount is not None else None)]
        if not supplied_invoices:
            questions.append(_question(identity, "missing_billing_records",
                                       "No invoices were found in the supplied records, so billing coverage cannot be compared."))
            ftype = "needs_information"
        elif not completions:
            questions.append(_question(identity, "missing_completion",
                                       f"No completion record for {ref} was found. Has the approved work been performed?"))
            ftype = "needs_information"
        else:
            ftype = "approved_unbilled"
            questions.append(_question(identity, "billing_coverage",
                                       f"Confirm {ref} was not billed on an invoice outside the supplied records before issuing a new charge."))
        explanation = " ".join(x for x in [
            f"What differs: {ref} approves {cite(desc) if desc else 'additional work'}"
            + (f" for {fmt_money(amount)} ({amount_fact.file_name}, p. {amount_fact.page})" if amount is not None and amount_fact else "") + ".",
            (f"Completion: {cite(completions[0])}." if completions else "No completion record was found."),
            (f"Original scope: {cite(base)}; the change is in addition to it." if base else ""),
            (f"What is missing: no invoice line referencing {ref} was found in the supplied invoices ({inv_list}). "
             "This means it was not found in the supplied records, not that it was never billed." if supplied_invoices
             else "What is missing: no invoices were supplied, so billing could not be compared."),
            (f"How it was priced: the approved lump sum of {fmt_money(amount)} stated in {ref}; no markup added." if amount is not None
             else "How it was priced: the approved amount is unclear, so no amount is proposed."),
        ] if x)
        reason = (f"The signed change {ref} and the completion record describe the same work. No matching charge was "
                  f"identified in the supplied invoices ({inv_list}). Confirm billing coverage before issuing a new charge."
                  if ftype == "approved_unbilled" else f"{ref} is approved but cannot be compared end to end: "
                  + "; ".join(q["text"] for q in questions if q["kind"] in BLOCKING_QUESTION_KINDS))
        findings.append(_finding(
            project_id, identity, ftype, title=title, reason=reason, explanation=explanation,
            authorization_status="approved", approval_label=f"Previously approved · {ref}", proposal=False,
            amount=amount, amount_basis="approved_lump_sum", cost_items=cost, financial=_financial(amount),
            groups=groups, questions=questions, refs=[ref]))

    # ---------------- 2. quantity increases over the signed scope
    qty_candidates = of("quantity_change") + [f for f in of("instruction") if dec(f.qty) is not None]
    for qc in qty_candidates:
        new_qty = dec(qc.qty)
        candidates = [i for i in inclusions if dec(i.qty) is not None]
        linked_bases = [by_id[b] for (r, a, b) in linkset if r == "changes_quantity_of" and a == qc.fact_id and b in by_id]
        base = linked_bases[0] if linked_bases else max(candidates, key=lambda i: overlap(qc, i), default=None)
        if base is None or overlap(qc, base) == 0 and not linked_bases or new_qty is None:
            continue
        base_qty = dec(base.qty)
        if base_qty is None or new_qty <= base_qty:
            continue
        delta = new_qty - base_qty
        ref = own_ref(qc) if ref_prefix(own_ref(qc)) in INSTRUCTION_PREFIXES else next(
            (r for r in all_refs(qc) if ref_prefix(r) in INSTRUCTION_PREFIXES), None)
        ent_key = " ".join(sorted(tokens(qc.entity) if qc.entity else tokens(base.quote)))
        identity = f"instruction:{ref}" if ref else f"quantity:{ent_key}"
        if identity in used_identities or (ref and ref in covered_instruction_refs):
            continue
        used_identities.add(identity)
        quotes = [q for q in of("quote") if (ref and ref in all_refs(q)) or q.fact_id in {a for (r, a, b) in linkset if r == "prices" and b == qc.fact_id}]
        if not quotes:
            quotes = [q for q in of("quote") if overlap(q, qc) > 0 and not own_ref(q)]
        priced = next((q for q in quotes if dec(q.rate) is not None), None)
        rate = dec(priced.rate) if priced else None
        quote_qty = dec(priced.qty) if priced else None
        questions = []
        if priced is None:
            questions.append(_question(identity, "missing_rate", "No supported quotation or unit price was found for the additional quantity. What price applies?"))
        elif quote_qty is not None and quote_qty != delta:
            questions.append(_question(identity, "conflicting_records",
                                       f"The quotation covers {qty_str(quote_qty)} units but the scope increase is {qty_str(delta)}. Which quantity applies?"))
        same_doc = [f for f in V if priced and f.file_id == priced.file_id]
        all_in = bool(priced and (priced.all_in or re.search(r"supplied and installed|all[- ]in", " ".join(q.quote for q in same_doc), re.I)))
        accepted = accepted_for(ref, qc)
        lines = billing_lines_for(ref) if ref else []
        if lines:
            existing.append(_approved_billed_item(project_id, ref or ent_key, lines))
            continue
        questions.extend(uncertain_billing(identity, [qc, *quotes]))
        unit = (priced.unit if priced and priced.unit else base.unit) or "ea"
        unit = "ea" if unit.lower() in ("each", "ea", "unit", "units", "fixture", "fixtures") else unit
        entity = qc.entity or base.entity or "items"
        desc = f"Additional {entity}" + (", supplied and installed (all-in)" if all_in else "")
        cost = [_cost_item(desc, delta, unit, rate, priced)]
        markup_note = "No markup applied: the quoted price is all-in." if all_in else "No markup applied: none is supported by the agreement."
        if markup_terms and not all_in and rate is not None:
            mk = markup_terms[0]
            base_amt = delta * rate
            pct = parse_decimal(mk.rate)
            cost.append(_cost_item(f"Markup per agreement ({qty_str(pct)}%)", None, "%", None, mk,
                                   formula_override=f"{qty_str(pct)}% × {fmt_money(base_amt)} = {fmt_money(base_amt * pct / 100)}"))
            cost[-1]["amount"] = money_str(base_amt * pct / 100)
            cost[-1]["rate"] = qty_str(pct)
            markup_note = f"Markup of {qty_str(pct)}% applied once, as stated in the agreement."
        amount = sum((Decimal(c["amount"]) for c in cost if c["amount"]), Decimal(0)) if rate is not None else None
        notes = acceptance_notes(ref) + [q for q in quotes if q is not priced and not dec(q.rate)]
        checks.append(Check(f"Checking {ref or 'the revised quantity'}: {entity} {qty_str(base_qty)} in the signed scope vs {qty_str(new_qty)} required",
                            qc.source_ref()))
        groups = [
            _group("Original agreement", [base], base.quote),
            _group("Changed requirement", [qc], qc.quote),
            _group("Proposed pricing", ([priced] if priced else []) + notes[:2],
                   (f"{_cap(number_word(delta))} additional {entity}: {cost[0]['formula']}" if rate is not None
                    else "No supported price found.")),
        ]
        if accepted:
            ftype, auth, label, proposal = "approved_unbilled", "approved", f"Previously approved · {short_doc_label(accepted[0])}", False
            groups.append(_group("Approval", accepted[:1], accepted[0].quote))
        else:
            ftype, auth, label, proposal = "scope_change_proposed", "pending", "Proposed addition · Customer sign-off required", True
            questions.append(_question(identity, "acceptance_pending",
                                       f"Customer acceptance of the {ref or 'additional quantity'} pricing was not found in the supplied records; the line stays a proposal."))
        explanation = " ".join(x for x in [
            f"What differs: the signed scope states {cite(base)}; {ref or 'a later record'} states {cite(qc)} - an increase of {qty_str(delta)}.",
            (f"Pricing: {cite(priced)}. {markup_note}" if priced else "Pricing: no supported quotation was found, so no amount is proposed."),
            f"Billing: no invoice line for {ref or 'this quantity'} was found in the supplied invoices"
            + (f" ({', '.join(supplied_invoices)})" if supplied_invoices else "") + ".",
            ("Authorization: customer acceptance is not recorded in the supplied records, so this is a proposal for customer sign-off."
             if not accepted else f"Authorization: {cite(accepted[0])}."),
            (f"How it was priced: {cost[0]['formula']}." if rate is not None else ""),
        ] if x)
        reason = (f"The revised quantity exceeds the original scope by {number_word(delta)} {entity}. "
                  + (f"The quoted {'all-in ' if all_in else ''}rate is {fmt_money(rate)} each, producing a {fmt_money(amount)} proposal. " if rate is not None and amount is not None else "No supported price was found. ")
                  + ("No prior billing or customer acceptance was found in the supplied records." if not accepted else "Acceptance is recorded."))
        findings.append(_finding(
            project_id, identity, ftype, title=_cap(f"{number_word(delta)} additional {entity}"), reason=reason,
            explanation=explanation, authorization_status=auth, approval_label=label, proposal=proposal, amount=amount,
            amount_basis="quantity_x_rate", cost_items=cost, financial=_financial(amount if accepted else None), groups=groups,
            questions=questions, refs=[r for r in [ref] if r]))

    # ---------------- 3. instructions for excluded work
    for ins in of("instruction"):
        if any(ref_prefix(r) in CHANGE_PREFIXES for r in all_refs(ins)):
            continue  # direction tied to a change order is handled with that change order
        linked_excl = [by_id[b] for (r, a, b) in linkset if r == "instructs_excluded" and a == ins.fact_id and b in by_id]
        scored = []
        for e in exclusions:
            et = tokens(e.entity) if e.entity else tokens(e.quote)
            hit = len(et & entity_tokens(ins))
            if hit and hit * 2 >= len(et):  # most of the exclusion's distinctive words must match
                scored.append((hit, e))
        excl = linked_excl[0] if linked_excl else (max(scored, key=lambda s: s[0])[1] if scored else None)
        if excl is None:
            continue
        ref = own_ref(ins) if ref_prefix(own_ref(ins)) in INSTRUCTION_PREFIXES else next(
            (r for r in all_refs(ins) if ref_prefix(r) in INSTRUCTION_PREFIXES), None)
        identity = f"instruction:{ref}" if ref else "excluded:" + " ".join(sorted(tokens(excl.entity or excl.quote)))
        if identity in used_identities or (ref and ref in covered_instruction_refs):
            continue
        used_identities.add(identity)
        hours_facts = [h for h in of("timesheet_hours") if (ref and ref in all_refs(h))]
        hours_facts += [h for h in linked("performs", ins.fact_id) if h.fact_type == "timesheet_hours" and h not in hours_facts]
        # Repeated quantities can be different workers. A printed total is a rollup,
        # not another time entry to add to those workers' hours.
        hours_by_file: dict[str, list[Fact]] = {}
        for h in _dedupe(hours_facts):
            hours_by_file.setdefault(h.file_id, []).append(h)
        hours_facts = []
        hours_conflict = False
        for entries in hours_by_file.values():
            totals = [h for h in entries if re.search(r"\btotal\b", h.quote, re.I)]
            if totals:
                if len({dec(h.qty) for h in totals}) != 1:
                    hours_conflict = True
                hours_facts.append(totals[0])
            else:
                hours_facts.extend(entries)
        hour_vals = [dec(h.qty) for h in hours_facts]
        hours = sum(hour_vals, Decimal(0)) if not hours_conflict and hours_facts and all(v is not None for v in hour_vals) else None
        rates = sorted({parse_decimal(r.rate) for r in labor_rates})
        rate_fact = labor_rates[0] if len(rates) == 1 else None
        rate = rates[0] if len(rates) == 1 else None
        work_facts = [w for w in of("work_completion") if ref and ref in all_refs(w)]
        questions = uncertain_billing(identity, [ins, *hours_facts])
        if hours_conflict:
            questions.append(_question(identity, "conflicting_records", "The timesheet contains conflicting totals. Confirm the labor hours before invoicing."))
        if hours is None:
            questions.append(_question(identity, "missing_quantity", f"No recorded labor hours were found for {ref or 'this instruction'}. How many hours were worked?"))
        if rate is None:
            questions.append(_question(identity, "missing_rate", "No single agreement labor rate supports this work. Which rate applies?"
                                       if len(rates) > 1 else "No agreement labor rate was found. Which rate applies?"))
        accepted = accepted_for(ref, ins)
        lines = billing_lines_for(ref) if ref else []
        if lines:
            existing.append(_approved_billed_item(project_id, ref or identity, lines))
            continue
        premium = next((p for p in of("pricing_basis") if "premium" in p.quote.lower()), None)
        entity = excl.entity or "excluded work"
        cost = [_cost_item(f"Labor - {entity} ({ref})" if ref else f"Labor - {entity}", hours, "h", rate, rate_fact)]
        amount = parse_decimal(cost[0]["amount"])
        checks.append(Check(f"Checking {ref or 'the instruction'} against the agreement exclusions and labor rates", ins.source_ref()))
        groups = [
            _group("Original agreement", [excl], excl.quote),
            _group("New instruction", [ins] + acceptance_notes(ref)[:1], ins.quote),
            _group("Work and cost record", hours_facts[:2] + ([rate_fact] if rate_fact else []) + work_facts[:1],
                   (f"{_cap(number_word(hours))} hours recorded at {fmt_money(rate)}/hour = {fmt_money(amount)}."
                    + (" No weekend premium assumed." if "weekend" in tokens(entity) or premium else "")) if amount is not None
                   else "Hours or rate not supported; no amount proposed."),
        ]
        if accepted:
            ftype, auth, label, proposal = "approved_unbilled", "approved", f"Previously approved · {short_doc_label(accepted[0])}", False
        else:
            ftype, auth, label, proposal = ("authorization_unresolved", "unclear",
                                            "Proposed change · Authorization unresolved; customer sign-off required", True)
            questions.append(_question(identity, "missing_authorization",
                                       f"No price acceptance for {ref or 'this instruction'} was found. Acknowledge the unresolved authorization before invoicing."))
        explanation = " ".join(x for x in [
            f"What differs: the agreement states {cite(excl)}, but {ref or 'an instruction'} states {cite(ins)}.",
            (f"Work and cost: {cite(hours_facts[0])}; the agreement labor rate is {cite(rate_fact)}." if hours_facts and rate_fact
             else "Work and cost: hours or rate could not be verified."),
            ("No premium rate is assumed because none is stated." if premium or "weekend" in tokens(entity) else ""),
            ("What is missing: no price acceptance or approval for this work was found in the supplied records; "
             "the cost alone does not establish payment entitlement." if not accepted else f"Approval: {cite(accepted[0])}."),
            (f"How it was priced: {cost[0]['formula']}." if amount is not None else ""),
        ] if x)
        reason = ("A field instruction requests work excluded from the original agreement. "
                  + (f"The timesheet records {qty_str(hours)} hours at the {fmt_money(rate)}/hour rate, supporting {fmt_money(amount)} in labor cost. "
                     if amount is not None else "The supporting hours or rate are missing. ")
                  + "That cost alone does not establish payment entitlement.")
        findings.append(_finding(
            project_id, identity, ftype, title=_cap(entity), reason=reason, explanation=explanation,
            authorization_status=auth, approval_label=label, proposal=proposal, amount=amount,
            amount_basis="quantity_x_rate", cost_items=cost, financial=_financial(amount if accepted else None),
            groups=groups, questions=questions, refs=[r for r in [ref] if r]))

    # Additional work without a numbered instruction must not silently disappear.
    # This is a review question, not a priced charge or a claim that the work is out of scope.
    covered_evidence = {e["evidence_id"] for item in findings for group in item["evidence_groups"]
                        if group["label"] not in ("Original agreement", "Billing comparison") for e in group["evidence"]}
    for work in of("work_completion"):
        if evidence_id(work) in covered_evidence or not re.search(r"\b(additional|extra)\b", work.quote, re.I):
            continue
        if any(ref in billed_change_refs or f"change:{ref}" in used_identities or f"instruction:{ref}" in used_identities for ref in all_refs(work)):
            continue
        identity = f"unmatched-work:{work.file_id}:" + " ".join(sorted(tokens(work.entity or work.quote) | tokens(work.location)))
        if identity in used_identities:
            continue
        used_identities.add(identity)
        candidates = [q for q in of("quote") if overlap(q, work) > 0
                      and (dec(q.qty) is None or dec(work.qty) is None or dec(q.qty) == dec(work.qty))]
        procedure = of("change_procedure")[:1]
        questions = [
            _question(identity, "missing_scope_confirmation", "Confirm whether this recorded additional work exceeds the signed scope and which instruction or change it belongs to."),
            _question(identity, "missing_authorization", "Confirm written customer approval, pricing, and whether this work has already been billed before adding a new charge."),
        ]
        findings.append(_finding(
            project_id, identity, "needs_information", title=_cap(f"recorded additional {work.entity or 'work'}"),
            reason="A field record describes additional work without a matched change instruction. Scope, authorization, and billing need review.",
            explanation=f"Recorded work: {cite(work)}. " +
                ("Potential pricing to check: " + "; ".join(cite(q) for q in candidates[:2]) + ". " if candidates else "No matching price was established. ") +
                "The record does not establish that the work is outside the agreement, approved, or unbilled. No invoice amount is assigned until these are confirmed.",
            authorization_status="unclear", approval_label="Needs scope and authorization review", proposal=True,
            amount=None, amount_basis="unknown", cost_items=[], financial=_financial(),
            groups=[_group("Recorded additional work", [work], work.quote),
                    _group("Potential pricing to verify", candidates[:2], "Possible quotation; confirm it covers this work."),
                    _group("Agreement change procedure", procedure, procedure[0].quote if procedure else "")],
            questions=questions, refs=sorted(all_refs(work))))

    # ---------------- 4. already-billed changes
    for ref in sorted({r for f in invoice_lines for r in all_refs(f) if ref_prefix(r) in CHANGE_PREFIXES}):
        lines = billing_lines_for(ref)
        if lines and not any(x["reference"] == ref for x in existing):
            existing.append(_approved_billed_item(project_id, ref, lines))

    # ---------------- 5. invoice balances, payments, credits, retainage
    retainage_terms = [f for f in of("retainage_term") if parse_decimal(f.rate) is not None]
    payments = _dedupe(of("payment"))
    allocated_payment_ids: set[str] = set()
    for inv in supplied_invoices:
        if inv in superseded:
            warnings.append(f"{inv} is superseded by a revised invoice and is not counted separately.")
            continue
        totals = [f for f in of("invoice_total") if own_ref(f) == inv and parse_decimal(f.amount) is not None and from_invoice_doc(f)]
        lines = [f for f in invoice_lines if own_ref(f) == inv]
        billed = parse_decimal(totals[0].amount) if totals else (sum((parse_decimal(f.amount) for f in lines), Decimal(0)) if lines else None)
        pays = []
        for p in payments:
            inv_refs = {r for r in all_refs(p) if ref_prefix(r) in INVOICE_PREFIXES}
            if inv_refs == {inv} and parse_decimal(p.amount) is not None:
                pays.append(p)
                allocated_payment_ids.add(p.fact_id)
        credits = _dedupe([c for c in of("credit") if inv in all_refs(c) and parse_decimal(c.amount) is not None])
        ret = [r for r in of("retainage_withheld") if (own_ref(r) == inv or inv in all_refs(r)) and parse_decimal(r.amount) is not None]
        if billed is None:
            continue
        paid = sum((abs(parse_decimal(p.amount)) for p in pays), Decimal(0)) if pays else None
        # A net amount due can already include deposit/credit deductions printed on the invoice.
        # Prove that with the invoice's own line arithmetic before subtracting any credit again.
        embedded_credits: list[Fact] = []
        if totals and credits and lines:
            invoice_credits = [c for c in credits if c.file_id == totals[0].file_id]
            gross_lines = sum((parse_decimal(f.amount) for f in lines), Decimal(0))
            local_deductions = sum((abs(parse_decimal(c.amount)) for c in invoice_credits), Decimal(0))
            if local_deductions > 0 and gross_lines - local_deductions == billed:
                embedded_credits = invoice_credits
        credited = sum((abs(parse_decimal(c.amount)) for c in credits if c not in embedded_credits), Decimal(0))
        retained = parse_decimal(ret[0].amount) if ret else None
        sources = [f.source_ref() for f in (totals[:1] or lines) + pays + credits + ret]
        if pays:
            checks.append(Check(f"Allocating {short_doc_label(pays[0])} payments to {inv}", pays[0].source_ref()))
        label_line = lines[0] if lines else None
        what = (label_line.entity or re.sub(r":.*$", "", label_line.quote)).strip() if label_line else "invoice"
        if paid is None:
            existing.append(_existing(project_id, "invoice_balance", inv, f"{inv} balance unknown - no allocated payment found", None,
                                      f"{fmt_money(billed)} billed on {inv}. No payment allocated to {inv} was found in the supplied "
                                      "records, so the balance is unknown (not assumed unpaid or overdue).", sources))
            continue
        balance = billed - paid - credited
        basis_label = "net invoice amount (printed deductions already included)" if embedded_credits else "billed"
        formula = f"{fmt_money(billed)} {basis_label} − {fmt_money(paid)} allocated payment" + (f" − {fmt_money(credited)} credits" if credited else "") + f" = {fmt_money(balance)}"
        ret_note = ""
        if retained is not None and retainage_terms:
            pct = parse_decimal(retainage_terms[0].rate)
            expected = billed * pct / 100
            ret_note = (f" Consistent with the {qty_str(pct)}% retainage in the agreement payment terms." if money_str(expected) == money_str(retained)
                        else f" Note: {qty_str(pct)}% retainage would be {fmt_money(expected)}; the invoice states {fmt_money(retained)}.")
        if retained is not None and balance == retained and balance > 0:
            existing.append(_existing(project_id, "retainage", inv, f"{inv} retainage withheld", retained,
                                      f"{formula}, identified as retainage withheld per {cite(ret[0])}.{ret_note} Tracked as an existing "
                                      "receivable, not a new charge.", sources))
        elif balance == 0:
            existing.append(_existing(project_id, "reconciled", inv, f"{inv} fully paid", balance, f"{formula}.", sources))
        else:
            extra = (f" Of this, {fmt_money(retained)} is retainage withheld.{ret_note}" if retained is not None else "")
            existing.append(_existing(project_id, "invoice_balance", inv, f"{inv} {what} - unresolved balance", balance,
                                      f"{formula} unresolved.{extra} Unpaid does not mean overdue; tracked as an existing balance, "
                                      "not a new charge.", sources))
        for c in credits:
            existing.append(_existing(project_id, "credit", own_ref(c) or inv, f"Credit against {inv}", abs(parse_decimal(c.amount)),
                                      f"Credit {cite(c)} " + (f"already included in the net amount due on {inv}; not deducted a second time." if c in embedded_credits else f"applied to {inv}."), [c.source_ref()]))
    for p in payments:
        if p.fact_id not in allocated_payment_ids and parse_decimal(p.amount) is not None:
            warnings.append(f"A payment of {fmt_money(parse_decimal(p.amount))} in {p.file_name} could not be allocated to a single "
                            "supplied invoice; it was not applied to any balance.")

    # ---------------- 6. duplicate / overlap protection [FIN-12]
    findings.sort(key=lambda f: (TYPE_ORDER.get(f["finding_type"], 9), f["identity"]))
    seen_evidence: dict[str, str] = {}
    for f in findings:
        core = {e["evidence_id"] for g in f["evidence_groups"] if g["label"] not in ("Original agreement", "Billing comparison")
                for e in g["evidence"] if e["file_id"] not in agreement_docs}
        for eid in core:
            other = seen_evidence.get(eid)
            if other and other != f["work_item_id"]:
                f["questions"].append(_question(f["identity"], "possible_duplicate",
                                                "This finding cites the same work record as another finding. Confirm they are different work before invoicing."))
                break
        for eid in core:
            seen_evidence.setdefault(eid, f["work_item_id"])
    existing.sort(key=lambda x: (EXISTING_ORDER.get(x["kind"], 9), x["reference"]))
    return ReconcileResult(findings=findings, existing_items=existing, checks=checks, warnings=warnings)


def _dedupe(facts: list[Fact]) -> list[Fact]:
    seen, out = set(), []
    for f in facts:
        key = (f.file_id, normalize_text(f.quote), f.amount)
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


def _existing(project_id: str, kind: str, reference: str, label: str, amount: Optional[Decimal], explanation: str,
              sources: list[dict]) -> dict:
    return {"item_id": "x_" + short_hash(project_id, kind, reference, length=12), "kind": kind, "reference": reference,
            "label": label, "amount": money_str(amount) if amount is not None else None, "explanation": explanation,
            "sources": sources}


def _approved_billed_item(project_id: str, ref: str, lines: list[Fact]) -> dict:
    total = sum((parse_decimal(f.amount) for f in lines), Decimal(0))
    invs = ", ".join(sorted({own_ref(f) or f.file_name for f in lines}))
    what = (lines[0].entity or re.sub(r"^\s*" + re.escape(ref) + r"\s*", "", re.sub(r":.*$", "", lines[0].quote))).strip()
    label = f"{ref} {what} already invoiced".replace("  ", " ")
    return _existing(project_id, "approved_billed", ref, label, total,
                     f"Billed on {invs}: {'; '.join(cite(f) for f in lines)}. Already invoiced, so it is excluded from new charges.",
                     [f.source_ref() for f in lines])


# ============================================================ agreement refresh [PRJ-02..04]


def agreement_from_facts(facts: list[Fact]) -> dict:
    """Refreshed agreement fields from verified facts in original-agreement records (None/[] when not found)."""
    V = [f for f in facts if f.verified and f.category_id == "original_agreement"]

    def terms(*types: str) -> list[dict]:
        out, seen = [], set()
        for f in V:
            if f.fact_type in types and normalize_text(f.quote) not in seen:
                seen.add(normalize_text(f.quote))
                out.append({"text": f.quote, "source": f.source_ref()})
        return out

    value = next((parse_decimal(f.amount) for f in V if f.fact_type == "agreement_value" and parse_decimal(f.amount) is not None), None)
    signed = next((f.date for f in V if f.fact_type == "agreement_date" and f.date), None)
    return {
        "contract_value": money_str(value) if value is not None else None,
        "signed_date": signed,
        "inclusions": terms("scope_inclusion"),
        "exclusions": terms("scope_exclusion"),
        "pricing_basis": terms("labor_rate", "pricing_basis"),
        "change_terms": terms("change_procedure"),
        "payment_terms": terms("payment_term", "retainage_term"),
    }
