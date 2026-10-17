"""LLM access for the analysis steps.

* BedrockLLM - boto3 bedrock-runtime Converse API with tool use for structured output, validated with pydantic v2,
               one bounded retry that includes the validation error [AI-01, AI-08].
* FakeLLM    - deterministic, rule-based outputs over the supplied text (LLM_MODE=fake). It reproduces the
               fictional Riverside walkthrough (backend/tests/fixtures/riverside, demo-data/riverside) with verbatim
               quotes so Python verification passes, and returns nothing invented for unknown documents.

Steps: (1) per record: classify + extract typed facts with quotes/pages; (2) across facts: propose work-item links.
"""

from __future__ import annotations

import copy
import json
import logging
import re
import threading
from datetime import datetime
from typing import Any, Optional, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from . import config, prompts
from .models import DocumentExtraction, ExtractedFact, ProposedLink, WorkItemLinks
from .reconcile import Fact

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


class LLM(Protocol):
    label: str

    def extract_document(self, name: str, pages: list[str]) -> DocumentExtraction: ...
    def propose_links(self, facts: list[Fact]) -> WorkItemLinks: ...


# ============================================================ schema helpers


def inline_json_schema(model_cls: type[BaseModel]) -> dict:
    """Pydantic JSON schema with $defs/$ref inlined (simplest shape for tool inputSchema)."""
    schema = model_cls.model_json_schema()
    defs = schema.pop("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                name = node["$ref"].split("/")[-1]
                merged = {**copy.deepcopy(defs[name]), **{k: v for k, v in node.items() if k != "$ref"}}
                return resolve(merged)
            return {k: resolve(v) for k, v in node.items() if k != "title"}
        if isinstance(node, list):
            return [resolve(v) for v in node]
        return node

    return resolve(schema)


# ============================================================ Bedrock

_bedrock_lock = threading.Lock()
_bedrock_client: Any = None
_forced_tool_choice_unsupported: set[str] = set()


def get_bedrock_client() -> Any:
    global _bedrock_client
    with _bedrock_lock:
        if _bedrock_client is None:
            import boto3
            from botocore.config import Config

            _bedrock_client = boto3.client(
                "bedrock-runtime",
                config=Config(retries={"mode": "adaptive", "max_attempts": 8}, read_timeout=600, connect_timeout=10),
            )
        return _bedrock_client


class BedrockLLM:
    def __init__(self, model_id: Optional[str] = None, client: Any = None, max_tokens: Optional[int] = None) -> None:
        self.model_id = model_id or config.bedrock_model_id()
        if not self.model_id:
            raise LLMError("BEDROCK_MODEL_ID is not set")
        self._client = client
        self.max_tokens = max_tokens or config.bedrock_max_tokens()

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = get_bedrock_client()
        return self._client

    @property
    def label(self) -> str:
        return f"bedrock:{self.model_id}"

    # -- public steps

    def extract_document(self, name: str, pages: list[str]) -> DocumentExtraction:
        return self.structured(prompts.EXTRACT_SYSTEM, prompts.extract_user(name, pages), DocumentExtraction,
                               "record_document", "Record the record classification and extracted facts.")

    def propose_links(self, facts: list[Fact]) -> WorkItemLinks:
        return self.structured(prompts.LINK_SYSTEM, prompts.links_user([f.prompt_view() for f in facts]), WorkItemLinks,
                               "record_links", "Record proposed links between facts describing the same work item.")

    # -- core

    def _converse(self, system: str, messages: list[dict], tool_spec: dict, tool_name: str) -> dict:
        from botocore.exceptions import ClientError

        forced = self.model_id not in _forced_tool_choice_unsupported
        tool_choice = {"tool": {"name": tool_name}} if forced else {"auto": {}}
        sys_blocks = [{"text": system}]
        if not forced:
            sys_blocks.append({"text": f"You must respond by calling the `{tool_name}` tool."})
        try:
            return self.client.converse(
                modelId=self.model_id,
                system=sys_blocks,
                messages=messages,
                inferenceConfig={"maxTokens": self.max_tokens},
                toolConfig={"tools": [{"toolSpec": tool_spec}], "toolChoice": tool_choice},
            )
        except ClientError as exc:
            err = exc.response.get("Error", {})
            text = (err.get("Message") or "").lower()
            if forced and err.get("Code") == "ValidationException" and ("tool_choice" in text or "toolchoice" in text or "tool choice" in text):
                # Some newer Claude models reject forced tool choice; fall back to auto + instruction.
                log.warning("Model %s rejected forced toolChoice; falling back to auto", self.model_id)
                _forced_tool_choice_unsupported.add(self.model_id)
                return self._converse(system, messages, tool_spec, tool_name)
            raise

    def structured(self, system: str, user: str, model_cls: type[T], tool_name: str, description: str) -> T:
        tool_spec = {"name": tool_name, "description": description, "inputSchema": {"json": inline_json_schema(model_cls)}}
        messages: list[dict] = [{"role": "user", "content": [{"text": user}]}]
        last_error = ""
        for attempt in range(2):
            resp = self._converse(system, messages, tool_spec, tool_name)
            msg = resp.get("output", {}).get("message", {}) or {}
            content = msg.get("content") or []
            tool_use = next((c["toolUse"] for c in content if "toolUse" in c and c["toolUse"].get("name") == tool_name), None)
            if tool_use is not None:
                try:
                    payload = tool_use.get("input")
                    if isinstance(payload, str):
                        payload = json.loads(payload)
                    return model_cls.model_validate(payload)
                except (ValidationError, json.JSONDecodeError) as exc:
                    last_error = f"The tool input failed validation: {exc}"
            elif resp.get("stopReason") == "max_tokens":
                last_error = "The response was truncated (max tokens). Call the tool again with a more concise result."
            else:
                last_error = f"You did not call the `{tool_name}` tool. Call it now."
            log.warning("Structured output attempt %s failed for %s: %s", attempt + 1, tool_name, last_error[:500])
            if attempt == 1:
                break
            if content:
                messages.append({"role": "assistant", "content": content})
                if tool_use is not None:
                    messages.append({"role": "user", "content": [
                        {"toolResult": {"toolUseId": tool_use["toolUseId"], "content": [{"text": last_error}], "status": "error"}},
                        {"text": f"Fix the problems and call `{tool_name}` again with the complete corrected input."},
                    ]})
                else:
                    messages.append({"role": "user", "content": [{"text": last_error}]})
            else:
                messages[-1]["content"].append({"text": last_error})
        raise LLMError(f"{tool_name}: structured output invalid after retry: {last_error[:800]}")


# ============================================================ Fake (deterministic)

_REF = re.compile(r"\b(?:CO|SI|INV|REM)-\d+(?:-\d+)*\b")
_NUMS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
         "eleven": 11, "twelve": 12}


def _num(word: str) -> str:
    w = word.strip().lower()
    return str(_NUMS[w]) if w in _NUMS else w.replace(",", "")


def _money(s: str) -> str:
    return s.replace(",", "")


def _date(s: str) -> Optional[str]:
    for fmt in ("%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(s.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return None


def paragraphs(page: str) -> list[str]:
    """Re-join wrapped PDF lines: a line continues the previous one unless the previous ended a sentence.
    The first line of a page is its title and stands alone."""
    lines = [" ".join(ln.split()) for ln in page.splitlines() if ln.strip()]
    out: list[str] = []
    for i, ln in enumerate(lines):
        if out and i > 1 and not re.search(r"[.!?:)]$", out[-1]):
            out[-1] += " " + ln
        else:
            out.append(ln)
    return out


def sentences(page: str) -> list[str]:
    out = []
    for para in paragraphs(page):
        out.extend(s.strip() for s in re.split(r"(?<=[.;])\s+(?=[A-Z])", para) if s.strip())
    return out


_CATEGORY_BY_TITLE = [
    (r"subcontract|agreement|exhibit [a-z]", "original_agreement", "Original agreement"),
    (r"change order", "approvals", "Approvals"),
    (r"completion record|timesheet|daily report", "field_records", "Field records"),
    (r"^invoice", "billing", "Billing"),
    (r"site instruction|directive", "change_instructions", "Change instructions"),
    (r"quotation|proposal", "pricing", "Pricing"),
    (r"remittance|payment", "payments", "Payments"),
]


class FakeLLM:
    """Deterministic stand-in for Bedrock (see module docstring)."""

    label = "fake"

    def extract_document(self, name: str, pages: list[str]) -> DocumentExtraction:
        title = paragraphs(pages[0])[0] if pages and paragraphs(pages[0]) else name
        category_id, category_label = "unclassified", "Unclassified"
        for pattern, cid, clabel in _CATEGORY_BY_TITLE:
            if re.search(pattern, title, re.I):
                category_id, category_label = cid, clabel
                break
        m = _REF.search(title)
        doc_ref = m.group(0) if m else None
        full = " ".join(" ".join(p.split()) for p in pages)
        ctx_refs = re.findall(r"[Rr]eference:? ((?:CO|SI)-\d+)", full)
        doc_date = None
        dm = re.search(r"(?:Invoice date|Date|Agreement date|Submitted): (\w+ \d{1,2}, \d{4})", full)
        if dm:
            doc_date = _date(dm.group(1))
        facts: list[ExtractedFact] = []
        ctx = {"doc_ref": doc_ref, "refs": ctx_refs, "entity": None}
        for page_no, text in enumerate(pages, start=1):
            for s in sentences(text):
                fact = self._fact(s, page_no, ctx)
                if fact is not None:
                    facts.append(fact)
        return DocumentExtraction(category_id=category_id, category_label=category_label, doc_reference=doc_ref,
                                  doc_date=doc_date, doc_summary=f"{category_label}: {title}", facts=facts)

    @staticmethod
    def _fact(s: str, page: int, ctx: dict) -> Optional[ExtractedFact]:
        def F(fact_type: str, **kw: Any) -> ExtractedFact:
            return ExtractedFact(fact_type=fact_type, page=page, quote=s, summary=kw.pop("summary", s), **kw)

        ref, refs = ctx["doc_ref"], ctx["refs"]
        rules: list[tuple[str, Any]] = [
            (r"Agreed contract value: \$([\d,]+)\..*", lambda m: F("agreement_value", amount=_money(m.group(1)))),
            (r"Agreement date: (\w+ \d{1,2}, \d{4})\.", lambda m: F("agreement_date", date=_date(m.group(1)))),
            (r"Payment terms: .+\.", lambda m: F("payment_term")),
            (r"Retainage: (\d+(?:\.\d+)?)% of .+\.", lambda m: F("retainage_term", rate=m.group(1), unit="%")),
            (r"Supply and install (\d+) (.+?)(?: at the locations .+)?\.",
             lambda m: F("scope_inclusion", qty=m.group(1), unit="ea", entity=m.group(2))),
            (r"(\w+) branch circuits included in the signed scope\.",
             lambda m: F("scope_inclusion", qty=_num(m.group(1)), unit="ea", entity="branch circuits")),
            (r"Complete testing and labeling of installed circuits\.", lambda m: F("scope_inclusion", entity="testing and labeling")),
            (r"Additional work and proposed pricing are subject to .+\.", lambda m: F("change_procedure")),
            (r"([A-Z][^.]*?) excluded\.", lambda m: F("scope_exclusion", entity=m.group(1).lower())),
            (r"Additional labor: \$([\d,]+(?:\.\d+)?) per hour.*\.", lambda m: F("labor_rate", rate=_money(m.group(1)), unit="h")),
            (r"Materials: documented cost\.", lambda m: F("pricing_basis")),
            (r"No weekend premium is specified.*\.", lambda m: F("pricing_basis")),
            (r"Provide (\w+) additional (?:dedicated )?(.+?) for (.+?) equipment\.",
             lambda m: F("change_description", reference=ref, qty=_num(m.group(1)), unit="ea", entity=m.group(2),
                         location=m.group(3).replace("-", " "))),
            (r"Approved additional amount: \$([\d,]+(?:\.\d+)?)\.",
             lambda m: F("approval", reference=ref, status="approved", amount=_money(m.group(1)))),
            (r"Signed acceptance recorded by .+ on (\w+ \d{1,2}, \d{4})\.",
             lambda m: F("approval", reference=ref, status="signed", date=_date(m.group(1)))),
            (r"(\w+) additional (.+?) installed under ((?:CO|SI)-\d+)\.",
             lambda m: F("work_completion", related_refs=[m.group(3)], qty=_num(m.group(1)), unit="ea", entity="branch circuits",
                         location="conference room" if "conference" in m.group(2) else None)),
            (r"Testing completed and work recorded by the foreman on (\w+ \d{1,2}, \d{4})\.",
             lambda m: F("work_completion", related_refs=list(refs), date=_date(m.group(1)))),
            (r"Original scope billing: \$([\d,]+(?:\.\d+)?)\.",
             lambda m: F("invoice_line", reference=ref, amount=_money(m.group(1)), entity="original scope billing")),
            (r"Retainage of \$([\d,]+(?:\.\d+)?) \((\d+)%\) is withheld .+\.",
             lambda m: F("retainage_withheld", reference=ref, amount=_money(m.group(1)), rate=m.group(2), unit="%")),
            (r"No (?:separate )?(CO-\d+) charge is listed.*\.", lambda m: F("billing_note", reference=ref)),
            (r"This previously invoiced upgrade is distinct from .+\.", lambda m: F("billing_note", reference=ref)),
            (r"(CO-\d+) (.+?): \$([\d,]+(?:\.\d+)?)\.",
             lambda m: F("invoice_line", reference=ref, related_refs=[m.group(1)], amount=_money(m.group(3)), entity=m.group(2))),
            (r"Revised installation requires (\d+) (.+?)\.",
             lambda m: F("quantity_change", reference=ref, qty=m.group(1), unit="ea", entity=m.group(2))),
            (r"Submit pricing for the additional quantity.*\.", lambda m: F("instruction", reference=ref, entity="pricing for the additional quantity")),
            (r"(\w+) additional (.+?), supplied and installed\.",
             lambda m: F("change_description", related_refs=list(refs), qty=_num(m.group(1)), unit="ea", entity=m.group(2))),
            (r"Quantity (\d+) × \$([\d,]+(?:\.\d+)?) each = \$([\d,]+(?:\.\d+)?) proposed\.",
             lambda m: F("quote", related_refs=list(refs), qty=m.group(1), unit="ea", rate=_money(m.group(2)),
                         amount=_money(m.group(3)), all_in=True, entity="LED fixtures")),
            (r"Customer acceptance has not been recorded.*\.", lambda m: F("approval", related_refs=list(refs), status="not_recorded")),
            (r"Complete the installation during (\w+) access\.",
             lambda m: F("instruction", reference=ref, entity=f"{m.group(1)} installation")),
            (r"This instruction documents .+; separate price acceptance is not included\.",
             lambda m: F("approval", reference=ref, status="not_recorded")),
            (r"Work: (.+), reference ((?:CO|SI)-\d+)\.", lambda m: F("work_completion", related_refs=[m.group(2)], entity=m.group(1))),
            (r"(\w+) labor hours recorded\.", lambda m: F("timesheet_hours", related_refs=list(refs), qty=_num(m.group(1)), unit="h")),
            (r"At the \$([\d,]+)/hour rate in Exhibit B: \$([\d,]+) labor cost\.", lambda m: F("other", amount=_money(m.group(2)))),
            (r"Payment of \$([\d,]+(?:\.\d+)?) applied to (INV-\d+)(?: \(.+\))?\.",
             lambda m: F("payment", reference=ref, related_refs=[m.group(2)], amount=_money(m.group(1)))),
        ]
        for pattern, build in rules:
            m = re.fullmatch(pattern, s)
            if m:
                return build(m)
        return None

    def propose_links(self, facts: list[Fact]) -> WorkItemLinks:
        """Same-work links a careful reader would propose (Python validates them like any model output)."""
        links: list[ProposedLink] = []
        by_type: dict[str, list[Fact]] = {}
        for f in facts:
            if f.verified:
                by_type.setdefault(f.fact_type, []).append(f)
        for ins in by_type.get("instruction", []):
            if "saturday" in (ins.entity or "").lower():
                for ex in by_type.get("scope_exclusion", []):
                    if "weekend" in (ex.entity or "").lower():
                        links.append(ProposedLink(from_fact_id=ins.fact_id, to_fact_id=ex.fact_id, relation="instructs_excluded",
                                                  note="Saturday work is weekend installation"))
                for ts in by_type.get("timesheet_hours", []):
                    if ins.reference and ins.reference in ts.related_refs:
                        links.append(ProposedLink(from_fact_id=ts.fact_id, to_fact_id=ins.fact_id, relation="performs"))
        for q in by_type.get("quote", []):
            for qc in by_type.get("quantity_change", []):
                if qc.reference and qc.reference in q.related_refs:
                    links.append(ProposedLink(from_fact_id=q.fact_id, to_fact_id=qc.fact_id, relation="prices"))
        return WorkItemLinks(links=links)


def get_llm() -> LLM:
    if config.llm_mode() == "fake":
        return FakeLLM()
    return BedrockLLM()
