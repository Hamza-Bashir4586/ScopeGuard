"""Deterministic verification of LLM proposals. Pure Python, no I/O.

The LLM proposes; this module verifies:
  * quotes exist in the cited source text (with tolerant normalisation),
  * cost math is done with Decimal (ROUND_HALF_UP to cents) and formulas are generated here,
  * labor/equipment rates come only from rate-sheet / subcontract facts and material prices
    only from receipt facts (otherwise rate/qty become null and a question is raised),
  * change-order matches reference a CO number that exists in the extracted change-order log,
  * findings without verified instruction evidence get a missing_authorization question.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from difflib import SequenceMatcher
from typing import Any, Iterable, Optional

from .models import ProposedCostItem, ProposedFinding

FUZZY_THRESHOLD = 0.95
CENTS = Decimal("0.01")


def short_hash(*parts: str, length: int = 12) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:length]


# ============================================================ quote verification

_CHAR_MAP = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"', "″": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-", "−": "-",
    " ": " ", " ": " ", " ": " ", "…": "...", "­": "",
    "×": "x",
}
_CHAR_TABLE = str.maketrans(_CHAR_MAP)


def normalize_text(text: str) -> str:
    """Normalise for quote matching: ligatures (NFKC), curly quotes, dashes, hyphenated
    line breaks, all whitespace collapsed, case-folded."""
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", text)  # ﬁ -> fi, ﬂ -> fl, full-width forms, etc.
    t = t.translate(_CHAR_TABLE)
    t = t.replace("\r\n", "\n").replace("\r", "\n").replace("\f", "\n")
    t = re.sub(r"(\w)-[ \t]*\n[ \t]*(\w)", r"\1\2", t)  # de-hyphenate "exten-\nsion" -> "extension"
    t = re.sub(r"\s+", " ", t)
    return t.strip().casefold()


def _best_window_ratio(needle: str, haystack: str) -> float:
    """Best SequenceMatcher ratio between needle and any window of haystack of similar length."""
    if not needle or not haystack:
        return 0.0
    n = len(needle)
    if n > len(haystack):
        return SequenceMatcher(None, needle, haystack, autojunk=False).ratio()
    matcher = SequenceMatcher(None, haystack, needle, autojunk=False)
    candidates: set[int] = set()
    for block in matcher.get_matching_blocks():
        if block.size == 0:
            continue
        start = block.a - block.b
        for offset in (-2, -1, 0, 1, 2):
            s = start + offset
            if 0 <= s <= len(haystack) - n:
                candidates.add(s)
            elif s < 0:
                candidates.add(0)
            else:
                candidates.add(max(0, len(haystack) - n))
    best = 0.0
    for s in candidates:
        for width in (n - 1, n, n + 1):
            if width <= 0:
                continue
            window = haystack[s : s + width]
            r = SequenceMatcher(None, needle, window, autojunk=False).ratio()
            if r > best:
                best = r
                if best >= 0.999:
                    return best
    return best


def _match_on_page(norm_quote: str, page_text: str) -> float:
    norm_page = normalize_text(page_text)
    if norm_quote in norm_page:
        return 1.0
    return _best_window_ratio(norm_quote, norm_page)


@dataclass
class QuoteCheck:
    verified: bool
    page: int  # corrected page (1-based); the cited page if not found
    score: float


def check_quote(quote: str, pages: list[str], cited_page: int) -> QuoteCheck:
    """Exact (normalised) substring on the cited page, else fuzzy best-window >= 0.95;
    else search other pages (exact first, then fuzzy) and correct the page number."""
    norm_quote = normalize_text(quote)
    if len(norm_quote) < 3 or not pages:
        return QuoteCheck(False, cited_page, 0.0)
    idx = cited_page - 1
    if 0 <= idx < len(pages):
        score = _match_on_page(norm_quote, pages[idx])
        if score >= FUZZY_THRESHOLD:
            return QuoteCheck(True, cited_page, score)
    others = [i for i in range(len(pages)) if i != idx]
    normalized = {i: normalize_text(pages[i]) for i in others}
    for i in others:
        if norm_quote in normalized[i]:
            return QuoteCheck(True, i + 1, 1.0)
    best_i, best_score = None, 0.0
    for i in others:
        s = _best_window_ratio(norm_quote, normalized[i])
        if s > best_score:
            best_i, best_score = i, s
    if best_i is not None and best_score >= FUZZY_THRESHOLD:
        return QuoteCheck(True, best_i + 1, best_score)
    return QuoteCheck(False, cited_page, best_score)


# ============================================================ decimal math


def parse_decimal(value: Any) -> Optional[Decimal]:
    """Parse '1,599.00', '$82.00', '6.5', Decimal or int into Decimal. Floats are rejected."""
    if value is None or isinstance(value, bool) or isinstance(value, float):
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, int):
        return Decimal(value)
    if not isinstance(value, str):
        return None
    s = value.strip().replace(",", "").replace("$", "").replace("%", "").strip()
    if not s or not re.fullmatch(r"-?\d+(\.\d+)?|-?\.\d+", s):
        return None
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None
    return d if d.is_finite() else None


def to_cents(d: Decimal) -> Decimal:
    return d.quantize(CENTS, rounding=ROUND_HALF_UP)


def money_str(d: Decimal) -> str:
    """Contract decimal string for money: '1599.00'."""
    return str(to_cents(d))


def qty_str(d: Decimal) -> str:
    """Contract decimal string for quantities without exponent or trailing zeros: '6.5', '20'."""
    s = format(d.normalize(), "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def fmt_money(d: Decimal) -> str:
    """Human formatting for formulas: '$1,599.00'."""
    c = to_cents(d)
    sign = "-" if c < 0 else ""
    return f"{sign}${abs(c):,.2f}"


def is_pct_markup(item: dict) -> bool:
    return item.get("category") == "markup" and (item.get("unit") or "").strip() == "%"


def compute_cost_item(qty: Optional[Decimal], unit: str, rate: Optional[Decimal]) -> tuple[Optional[str], str]:
    """amount = qty × rate (cents, ROUND_HALF_UP). Returns (amount decimal string or None, formula)."""
    unit = (unit or "").strip()
    if qty is None or rate is None:
        missing = [n for n, v in (("quantity", qty), ("rate", rate)) if v is None]
        return None, f"Missing {' and '.join(missing)} - amount not computed"
    amount = to_cents(qty * rate)
    unit_part = f" {unit}" if unit else ""
    per = f"/{unit}" if unit else ""
    return money_str(amount), f"{qty_str(qty)}{unit_part} × {fmt_money(rate)}{per} = {fmt_money(amount)}"


def compute_markup(pct: Optional[Decimal], base: Decimal) -> tuple[Optional[str], str]:
    """Percentage markup: amount = base × pct / 100, base = sum of all non-markup amounts."""
    if pct is None:
        return None, "Missing rate (markup %) - amount not computed"
    amount = to_cents(base * pct / Decimal(100))
    return money_str(amount), f"{qty_str(pct)}% × {fmt_money(base)} = {fmt_money(amount)}"


def finalize_cost_items(items: Iterable[dict]) -> list[dict]:
    """Normalise qty/rate strings and (re)compute amount + formula for a whole list
    (percentage markups need the sum of the other items). Returns new dicts."""
    out: list[dict] = []
    for item in items:
        it = dict(item)
        qty = parse_decimal(it.get("qty"))
        rate = parse_decimal(it.get("rate"))
        it["qty"] = qty_str(qty) if qty is not None else None
        if is_pct_markup(it):
            it["rate"] = qty_str(rate) if rate is not None else None
            it["amount"], it["formula"] = None, ""
        else:
            it["rate"] = money_str(rate) if rate is not None else None
            it["amount"], it["formula"] = compute_cost_item(qty, it.get("unit", ""), rate)
        out.append(it)
    base = sum((Decimal(i["amount"]) for i in out if not is_pct_markup(i) and i["amount"] is not None), Decimal("0"))
    for it in out:
        if is_pct_markup(it):
            it["amount"], it["formula"] = compute_markup(parse_decimal(it["rate"]), base)
    return out


def cost_total(items: Iterable[dict]) -> str:
    total = Decimal("0")
    for it in items:
        a = parse_decimal(it.get("amount"))
        if a is not None:
            total += a
    return money_str(total)


# ============================================================ facts + findings


@dataclass
class Fact:
    """A fact extracted by the LLM from one document page, after quote verification."""

    fact_id: str
    box_file_id: str
    box_version_id: str
    file_name: str
    doc_type: str
    kind: str
    page: int
    quote: str
    summary: str
    verified: bool
    date: Optional[str] = None
    topic: Optional[str] = None
    person: Optional[str] = None
    qty: Optional[str] = None
    unit: Optional[str] = None
    rate: Optional[str] = None
    amount: Optional[str] = None
    co_number: Optional[str] = None
    co_status: Optional[str] = None

    def prompt_view(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if v not in (None, "") and k not in ("box_file_id", "box_version_id")}
        return d


RATE_DOC_TYPES = {"rate_sheet", "subcontract"}
MATERIAL_DOC_TYPES = {"receipt"}
QTY_DOC_TYPES = {"daily_report", "timesheet", "receipt"}
INSTRUCTION_DOC_TYPES = {"instruction", "spec_revision"}


def normalize_change_key(key: str) -> str:
    k = unicodedata.normalize("NFKC", key or "").casefold()
    k = re.sub(r"[^a-z0-9]+", "-", k).strip("-")
    return k or "unnamed"


def make_finding_id(scan_id: str, change_key: str) -> str:
    return "f_" + short_hash(scan_id, normalize_change_key(change_key), length=16)


def normalize_co_number(co: Optional[str]) -> str:
    """'CO-003', 'co 3', 'Change Order #3' -> 'CO3'."""
    if not co:
        return ""
    s = co.upper()
    s = re.sub(r"CHANGE\s*ORDER", "CO", s)
    s = re.sub(r"[^A-Z0-9]", "", s)
    s = re.sub(r"(\D)0+(\d)", r"\1\2", s)
    return s


def evidence_id_for(fact: Fact) -> str:
    return "e_" + short_hash(fact.fact_id, fact.box_file_id, str(fact.page), fact.quote, length=10)


def question_id_for(finding_id: str, kind: str, text: str) -> str:
    return "q_" + short_hash(finding_id, kind, " ".join(text.split()).casefold(), length=10)


def box_url(file_id: str) -> str:
    return f"https://app.box.com/file/{file_id}"


def evidence_from_fact(fact: Fact, role: str) -> dict:
    return {
        "evidence_id": evidence_id_for(fact),
        "box_file_id": fact.box_file_id,
        "box_version_id": fact.box_version_id,
        "file_name": fact.file_name,
        "page": int(fact.page),
        "quote": fact.quote,
        "role": role,
        "verified": bool(fact.verified),
        "box_url": box_url(fact.box_file_id),
    }


@dataclass
class _Builder:
    finding_id: str
    facts: dict[str, Fact]
    evidence: list[dict] = field(default_factory=list)
    questions: list[dict] = field(default_factory=list)

    def add_evidence(self, fact: Fact, role: str) -> str:
        eid = evidence_id_for(fact)
        if not any(e["evidence_id"] == eid for e in self.evidence):
            self.evidence.append(evidence_from_fact(fact, role))
        return eid

    def add_question(self, kind: str, text: str) -> None:
        qid = question_id_for(self.finding_id, kind, text)
        if not any(q["question_id"] == qid for q in self.questions):
            self.questions.append({"question_id": qid, "kind": kind, "text": text})

    def has_question(self, kind: str) -> bool:
        return any(q["kind"] == kind for q in self.questions)


def _rate_fact_allowed(category: str, fact: Fact) -> bool:
    if not fact.verified:
        return False
    if category in ("labor", "equipment"):
        return fact.doc_type in RATE_DOC_TYPES or (category == "equipment" and fact.doc_type in MATERIAL_DOC_TYPES)
    if category == "material":
        return fact.doc_type in MATERIAL_DOC_TYPES
    if category == "markup":
        return fact.doc_type in RATE_DOC_TYPES
    return fact.doc_type in RATE_DOC_TYPES | MATERIAL_DOC_TYPES


def _validate_costs(b: _Builder, proposed: list[ProposedCostItem]) -> list[dict]:
    items: list[dict] = []
    for idx, pc in enumerate(proposed):
        item_id = "c_" + short_hash(b.finding_id, str(idx), pc.category, pc.description, length=10)
        rate_fact = b.facts.get(pc.rate_fact_id) if pc.rate_fact_id else None
        source_eid: Optional[str] = None

        if pc.category == "markup" and (pc.unit or "%").strip() == "%":
            # Percentage markup: rate = markup %, sourced only from rate sheet / subcontract; qty ignored.
            pct: Optional[Decimal] = None
            if rate_fact and _rate_fact_allowed("markup", rate_fact):
                pct = parse_decimal(rate_fact.rate) or parse_decimal(rate_fact.qty)
                if pct is not None:
                    source_eid = b.add_evidence(rate_fact, "cost_support")
            if pct is None:
                b.add_question("missing_rate", f"Markup percentage for '{pc.description}' is not supported by the subcontract or rate sheet. What markup applies?")
            items.append({"item_id": item_id, "category": "markup", "description": pc.description, "qty": None,
                          "unit": "%", "rate": qty_str(pct) if pct is not None else None, "source_evidence_id": source_eid})
            continue

        # ---- rate: only from an allowed, verified source fact; the fact's stated value wins
        rate: Optional[Decimal] = None
        if rate_fact and _rate_fact_allowed(pc.category, rate_fact):
            rate = parse_decimal(rate_fact.rate) or parse_decimal(rate_fact.amount if pc.category == "material" else None)
            if rate is not None:
                source_eid = b.add_evidence(rate_fact, "cost_support")
        if rate is None:
            source_kind = "receipt" if pc.category == "material" else "rate sheet or subcontract"
            b.add_question("missing_rate", f"No verified {source_kind} rate found for '{pc.description}'. What rate applies?")

        # ---- qty: sum of verified quantity facts from daily reports / timesheets / receipts
        qty: Optional[Decimal] = None
        qty_facts = [b.facts[f] for f in pc.qty_fact_ids if f in b.facts]
        usable = [f for f in qty_facts if f.verified and f.doc_type in QTY_DOC_TYPES and parse_decimal(f.qty) is not None]
        if usable:
            qty = sum((parse_decimal(f.qty) for f in usable), Decimal("0"))  # type: ignore[misc]
            for f in usable:
                b.add_evidence(f, "cost_support" if f.doc_type == "receipt" else "work_record")
        if qty is None:
            b.add_question("missing_quantity", f"No verified quantity found in daily reports, timesheets or receipts for '{pc.description}'. What quantity was performed/used?")

        items.append({"item_id": item_id, "category": pc.category, "description": pc.description,
                      "qty": qty_str(qty) if qty is not None else None, "unit": pc.unit or "",
                      "rate": money_str(rate) if rate is not None else None, "source_evidence_id": source_eid})

    return finalize_cost_items(items)


def validate_finding(scan_id: str, proposed: ProposedFinding, facts: dict[str, Fact]) -> dict:
    """Turn an LLM ProposedFinding into the verified `ai` map stored on the finding item."""
    finding_id = make_finding_id(scan_id, proposed.change_key)
    b = _Builder(finding_id=finding_id, facts=facts)

    for pe in proposed.evidence:
        fact = facts.get(pe.fact_id)
        if fact is not None:
            b.add_evidence(fact, pe.role)

    for q in proposed.questions:
        b.add_question(q.kind, q.text)

    cost_items = _validate_costs(b, proposed.cost_items)

    # ---- change-order match must reference a CO number present in the extracted CO log
    status = proposed.status
    co_match = None
    co_facts = [f for f in facts.values() if f.kind == "change_order" or f.doc_type == "change_order_log"]
    if proposed.co_number:
        wanted = normalize_co_number(proposed.co_number)
        matches = [f for f in co_facts if f.co_number and normalize_co_number(f.co_number) == wanted]
        verified_matches = [f for f in matches if f.verified]
        if verified_matches:
            m = verified_matches[0]
            amount = parse_decimal(m.amount)
            co_match = {"co_number": m.co_number, "description": m.summary or m.quote, "amount": money_str(amount) if amount is not None else None}
            b.add_evidence(m, "change_order")
            status = "already_recorded"
        else:
            status = "needs_info"
            b.add_question("conflicting_records", f"The analysis matched this work to {proposed.co_number}, but that change order was not found (verified) in the change-order log. Is it already recorded?")
    elif status == "already_recorded":
        status = "needs_info"
        b.add_question("conflicting_records", "This work appears to be already recorded, but no matching change-order number was identified in the change-order log. Which CO covers it?")

    # ---- authorization: needs verified instruction evidence unless already recorded
    if status != "already_recorded":
        has_instruction = any(e["role"] == "instruction" and e["verified"] for e in b.evidence)
        if not has_instruction:
            b.add_question("missing_authorization", "No verified written instruction or directive for this work was found. Who authorized it, and is there written direction (email, ASI, field directive)?")
            status = "needs_info"

    return {
        "finding_id": finding_id,
        "change_key": normalize_change_key(proposed.change_key),
        "title": proposed.title,
        "summary": proposed.summary,
        "finding_status": status,
        "co_match": co_match,
        "scope_baseline": proposed.scope_baseline,
        "requested_work": proposed.requested_work,
        "evidence": b.evidence,
        "timeline": [],
        "questions": b.questions,
        "ai_draft": "",
        "ai_cost_items": cost_items,
    }


def validate_timeline(entries: Iterable[Any], facts: dict[str, Fact], ai: dict) -> list[dict]:
    """Keep timeline entries; map fact_id -> evidence_id only for verified facts in the finding."""
    by_fact_eid = {}
    for fid, f in facts.items():
        eid = evidence_id_for(f)
        if f.verified and any(e["evidence_id"] == eid for e in ai["evidence"]):
            by_fact_eid[fid] = eid
    out = []
    for e in entries:
        fact_id = getattr(e, "fact_id", None)
        out.append({"date": str(getattr(e, "date", "") or ""), "event": str(getattr(e, "event", "") or ""),
                    "evidence_id": by_fact_eid.get(fact_id) if fact_id else None})
    out.sort(key=lambda x: x["date"])
    return out
