"""Deterministic verification primitives. Pure Python, no I/O.

The LLM proposes; Python verifies [AI-05, FIN-10]:
  * quotes exist in the cited source text (tolerant normalisation, wrong page numbers corrected),
  * all money/quantity math uses Decimal (ROUND_HALF_UP to cents) and formulas are generated here.
Reconciliation rules built on these live in reconcile.py.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from difflib import SequenceMatcher
from typing import Any, Iterable, Optional

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
