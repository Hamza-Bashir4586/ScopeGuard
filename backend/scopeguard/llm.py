"""LLM access for the three analysis steps.

* BedrockLLM - boto3 bedrock-runtime Converse API with tool use for structured output,
               validated with pydantic v2, one retry that includes the validation error.
* FakeLLM    - deterministic, rule-based outputs over the provided text (LLM_MODE=fake),
               tuned to the sample fixtures so the frontend can be built end-to-end.
"""

from __future__ import annotations

import copy
import json
import logging
import re
import threading
from typing import Any, Optional, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from . import config, prompts
from .models import (
    DocumentExtraction,
    ExtractedFact,
    FindingNarrative,
    FindingsProposal,
    ProposedCostItem,
    ProposedEvidence,
    ProposedFinding,
    ProposedQuestion,
    ProposedTimelineEntry,
)
from .validate import Fact

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


class LLM(Protocol):
    def extract_document(self, name: str, pages: list[str]) -> DocumentExtraction: ...
    def propose_findings(self, facts: list[Fact], documents: list[dict]) -> FindingsProposal: ...
    def draft_narrative(self, finding: dict, facts: list[Fact]) -> FindingNarrative: ...


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

    # -- public steps

    def extract_document(self, name: str, pages: list[str]) -> DocumentExtraction:
        return self.structured(prompts.EXTRACT_SYSTEM, prompts.extract_user(name, pages), DocumentExtraction,
                               "record_document", "Record the document classification and extracted facts.")

    def propose_findings(self, facts: list[Fact], documents: list[dict]) -> FindingsProposal:
        return self.structured(prompts.FINDINGS_SYSTEM, prompts.findings_user([f.prompt_view() for f in facts], documents),
                               FindingsProposal, "record_findings", "Record the proposed findings (potential extra work).")

    def draft_narrative(self, finding: dict, facts: list[Fact]) -> FindingNarrative:
        return self.structured(prompts.NARRATIVE_SYSTEM, prompts.narrative_user(finding, [f.prompt_view() for f in facts]),
                               FindingNarrative, "record_narrative", "Record the change-request narrative and timeline.")

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

_DATE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
_MONEY = re.compile(r"\$\s?(\d[\d,]*\.\d{2})")


def _doc_type_from_name(name: str) -> str:
    n = name.lower()
    for keys, dtype in (
        (("change_order_log", "change-order-log", "co_log", "co log"), "change_order_log"),
        (("rate",), "rate_sheet"),
        (("subcontract",), "subcontract"),
        (("timesheet", "time_sheet"), "timesheet"),
        (("daily",), "daily_report"),
        (("receipt", "invoice"), "receipt"),
        (("asi", "spec", "revis"), "spec_revision"),
        (("email", "instruction", "directive", "rfi", "letter"), "instruction"),
        (("scope",), "scope"),
    ):
        if any(k in n for k in keys):
            return dtype
    return "other"


def _topic(line: str) -> Optional[str]:
    low = line.lower()
    if "soffit" in low:
        return "L2 corridor soffit"
    if "blocking" in low:
        return "Exam room in-wall blocking"
    if "lobby" in low:
        return "L1 lobby drywall repair"
    return None


def _clean(line: str) -> str:
    return re.sub(r"^\s*[-*•]\s*", "", line).strip()


class FakeLLM:
    """Deterministic stand-in for Bedrock. Produces realistic output for the sample fixtures
    (one new change, one already recorded, one needing info) and generic output otherwise."""

    # ---- step 1

    def extract_document(self, name: str, pages: list[str]) -> DocumentExtraction:
        doc_type = _doc_type_from_name(name)
        full = "\n".join(pages)
        m = _DATE.search(full)
        doc_date = m.group(1) if m else None
        person = None
        pm = re.search(r"^From:\s*([^<\n]+)", full, re.M)
        if pm:
            person = pm.group(1).strip()
        facts: list[ExtractedFact] = []
        # The fake (like a real model sometimes does) "forgets" page breaks in the subcontract and cites page 1;
        # Python's quote check corrects the page number.
        page_iter = [(1, full)] if doc_type == "subcontract" else list(enumerate(pages, start=1))
        for page_no, text in page_iter:
            for raw in text.splitlines():
                line = raw.strip()
                if len(line) < 12:
                    continue
                facts.extend(self._facts_for_line(doc_type, line, page_no, doc_date, person))
        return DocumentExtraction(doc_type=doc_type, doc_summary=f"{doc_type.replace('_', ' ').title()}: {name}", doc_date=doc_date, facts=facts)

    def _facts_for_line(self, doc_type: str, line: str, page: int, doc_date: Optional[str], person: Optional[str]) -> list[ExtractedFact]:
        low = line.lower()
        ld = _DATE.search(line)
        date = ld.group(1) if ld else doc_date
        base = dict(page=page, quote=_clean(line), summary=_clean(line), date=date, topic=_topic(line))
        out: list[ExtractedFact] = []
        if doc_type in ("subcontract", "scope"):
            if not re.match(r"^\d+\.\d+\s", line):
                return []
            if "markup" in low:
                pct = re.search(r"(\d+(?:\.\d+)?)\s*%", line)
                out.append(ExtractedFact(kind="markup", rate=pct.group(1) if pct else None, unit="%", **base))
            elif "exclude" in low:
                out.append(ExtractedFact(kind="scope_exclusion", **base))
            elif "include" in low or "furnish" in low:
                out.append(ExtractedFact(kind="scope_inclusion", **base))
            else:
                out.append(ExtractedFact(kind="other", **base))
        elif doc_type == "rate_sheet":
            rm = re.search(r"\$(\d[\d,]*\.\d{2})\s*per\s*(hour|day)", line, re.I)
            if rm:
                label = re.split(r"\s*\.{2,}|\s{2,}", line)[0].strip()
                out.append(ExtractedFact(kind="labor_rate", rate=rm.group(1).replace(",", ""),
                                         unit="h" if rm.group(2).lower() == "hour" else "day",
                                         **{**base, "topic": label, "summary": f"{label}: ${rm.group(1)}/{rm.group(2).lower()}"}))
        elif doc_type == "instruction":
            if low.startswith("please") or "proceed" in low:
                out.append(ExtractedFact(kind="instruction", person=person, **base))
        elif doc_type == "spec_revision":
            if low.startswith(("description:", "construction:")):
                out.append(ExtractedFact(kind="spec_change", **base))
        elif doc_type == "daily_report":
            if line.startswith("-"):
                qty = None
                cm = re.search(r"crew of (\d+)[^,]*,\s*(\d+(?:\.\d+)?)\s*hrs? each", low)
                if cm:
                    from decimal import Decimal

                    qty = str(Decimal(cm.group(1)) * Decimal(cm.group(2)))
                out.append(ExtractedFact(kind="work_record", qty=qty, unit="h" if qty else None, **base))
                if "verbal" in low:
                    # Deliberately paraphrased (unverifiable) quote: exercises the quote check / greyed-out evidence.
                    out.append(ExtractedFact(kind="instruction", page=page, date=date, topic=_topic(line), person="Dave Romero",
                                             quote="GC superintendent directed Keystone in writing to repair all Level 1 lobby drywall",
                                             summary="Claimed written direction from GC superintendent to repair lobby drywall"))
        elif doc_type == "timesheet":
            tm = re.search(r"total\s+(\d+(?:\.\d+)?)", low)
            if ld and tm:
                out.append(ExtractedFact(kind="work_record", qty=tm.group(1), unit="h", **base))
        elif doc_type == "receipt":
            if low.startswith("total"):
                mm = _MONEY.search(line)
                if mm:
                    amt = mm.group(1).replace(",", "")
                    out.append(ExtractedFact(kind="material_cost", qty="1", unit="ls", rate=amt, amount=amt,
                                             **{**base, "topic": base["topic"] or "L2 corridor soffit"}))
        elif doc_type == "change_order_log":
            if re.match(r"^CO-\d+", line):
                parts = [p.strip() for p in line.split("|")]
                mm = _MONEY.search(line)
                out.append(ExtractedFact(kind="change_order", co_number=parts[0],
                                         date=parts[1] if len(parts) > 1 and _DATE.fullmatch(parts[1]) else date,
                                         amount=mm.group(1).replace(",", "") if mm else None,
                                         co_status=parts[4] if len(parts) > 4 else None,
                                         **{k: v for k, v in base.items() if k not in ("date",)} | {"summary": parts[2] if len(parts) > 2 else line}))
        return out

    # ---- step 2

    def propose_findings(self, facts: list[Fact], documents: list[dict]) -> FindingsProposal:
        def having(word: str, *, doc_types: tuple[str, ...] = (), kinds: tuple[str, ...] = ()) -> list[Fact]:
            return [f for f in facts if word in f.quote.lower()
                    and (not doc_types or f.doc_type in doc_types) and (not kinds or f.kind in kinds)]

        def first(items: list[Fact]) -> Optional[Fact]:
            return items[0] if items else None

        def ev(items: list[Fact], role: str) -> list[ProposedEvidence]:
            return [ProposedEvidence(fact_id=f.fact_id, role=role) for f in items]

        findings: list[ProposedFinding] = []
        markup = first([f for f in facts if f.kind == "markup"])

        # --- A: L2 corridor soffit (new, written instruction, costs)
        soffit_instr = having("soffit", doc_types=("instruction", "spec_revision"))
        if soffit_instr:
            carpenter = first([f for f in facts if f.kind == "labor_rate" and "carpenter" in f.quote.lower()])
            ts = having("soffit", doc_types=("timesheet",))
            receipt = first([f for f in facts if f.kind == "material_cost" and f.doc_type == "receipt"])
            evidence = (ev(soffit_instr, "instruction")
                        + ev(having("level 2 corridor", doc_types=("subcontract", "scope")), "scope_baseline")
                        + ev(having("soffit", doc_types=("subcontract", "scope")), "scope_baseline")
                        + ev(having("soffit", doc_types=("daily_report",)), "work_record")
                        + ev(ts, "work_record"))
            costs = [ProposedCostItem(category="labor", description="Carpenter labor - frame and board L2 corridor soffit (C-F)", unit="h",
                                      qty="27.5", rate="82.00", qty_fact_ids=[f.fact_id for f in ts],
                                      rate_fact_id=carpenter.fact_id if carpenter else None)]
            if receipt:
                costs.append(ProposedCostItem(category="material", description="Soffit materials - studs, track, Type X board (Coastal Building Supply)",
                                              unit="ls", qty="1", rate=receipt.rate, qty_fact_ids=[receipt.fact_id], rate_fact_id=receipt.fact_id))
            if markup:
                costs.append(ProposedCostItem(category="markup", description="Subcontract markup on labor and material", unit="%",
                                              qty=None, rate=markup.rate, rate_fact_id=markup.fact_id))
            co = first(having("soffit", kinds=("change_order",)))
            findings.append(ProposedFinding(
                change_key="l2-corridor-soffit", title="New soffit at Level 2 corridor (gridlines C-F)",
                summary="GC superintendent directed a new framed and boarded soffit to conceal the relocated duct (RFI-014 / ASI-03). Work was performed 3/12-3/13 and is not in the change-order log.",
                status="already_recorded" if co else "new", co_number=co.co_number if co else None,
                scope_baseline="Soffits only where shown on A-501 Rev 0; Level 2 corridor ceiling framing/board excluded.",
                requested_work="Frame and board a new 24 in. x 18 in. Type X soffit along the L2 corridor, gridlines C to F.",
                evidence=evidence, cost_items=costs))

        # --- B: exam room blocking (already recorded as CO-003)
        blocking_co = first(having("blocking", kinds=("change_order",)))
        blocking_work = having("blocking", doc_types=("daily_report", "timesheet"))
        if blocking_work or blocking_co:
            findings.append(ProposedFinding(
                change_key="exam-room-blocking", title="In-wall blocking for exam room equipment (Level 1)",
                summary="Blocking for owner equipment in Level 1 exam rooms 104-112 was performed at GC direction; it appears in the change-order log.",
                status="already_recorded", co_number=blocking_co.co_number if blocking_co else None,
                scope_baseline="Blocking included only where shown on A-601 Rev 0.",
                requested_work="Install in-wall blocking for exam room equipment, rooms 104-112.",
                evidence=ev(blocking_work, "work_record") + ev(having("blocking", doc_types=("subcontract", "scope")), "scope_baseline")
                + ([ProposedEvidence(fact_id=blocking_co.fact_id, role="change_order")] if blocking_co else [])))

        # --- C: L1 lobby repair (verbal only, no hours)
        lobby = having("lobby")
        if lobby:
            taper = first([f for f in facts if f.kind == "labor_rate" and "taper" in f.quote.lower()])
            lobby_claim = [f for f in facts if f.kind == "instruction" and (f.topic or "").startswith("L1 lobby")]
            findings.append(ProposedFinding(
                change_key="l1-lobby-drywall-repair", title="Drywall patch and re-tape at Level 1 lobby (damage by others)",
                summary="Crew patched and re-taped drywall in the Level 1 lobby damaged by the electrical subcontractor, reportedly on a verbal request from the GC superintendent. Hours were not tracked.",
                status="needs_info",
                scope_baseline="Repair of finished gypsum board damaged by other trades is excluded; performed only on written direction.",
                requested_work="Patch and re-tape damaged drywall at the Level 1 lobby electrical box cut-ins.",
                evidence=ev([f for f in lobby if f.kind == "work_record"], "work_record") + ev(lobby_claim, "instruction")
                + ev(having("damaged by other trades", doc_types=("subcontract", "scope")), "scope_baseline"),
                cost_items=[ProposedCostItem(category="labor", description="Drywall finisher labor - lobby patch and re-tape", unit="h",
                                             qty=None, rate="78.00", qty_fact_ids=[], rate_fact_id=taper.fact_id if taper else None)],
                questions=[ProposedQuestion(kind="unclear_scope", text="The damage was caused by the electrical subcontractor. Should this be billed to the GC as extra work or back-charged to the electrical sub?")]))

        return FindingsProposal(findings=findings)

    # ---- step 3

    def draft_narrative(self, finding: dict, facts: list[Fact]) -> FindingNarrative:
        verified = [f for f in facts if f.verified]
        dated = sorted([f for f in verified if f.date], key=lambda f: (f.date or "", f.fact_id))
        lines = [f"## {finding['title']}", "", f"**Original scope.** {finding['scope_baseline']}", "",
                 f"**Requested / performed work.** {finding['requested_work']}", ""]
        instr = [f for f in verified if f.kind in ("instruction", "spec_change")]
        if instr:
            lines.append("**Direction received.** " + " ".join(f'{f.file_name} (p.{f.page}): "{f.quote}" [{f.fact_id}]' for f in instr))
        else:
            lines.append("**Direction received.** No verified written direction was found in the project records.")
        lines.append("")
        work = [f for f in verified if f.kind == "work_record"]
        if work:
            lines.append("**Work performed.** " + " ".join(f"{f.date or ''}: {f.summary} [{f.fact_id}]".strip() for f in work))
            lines.append("")
        priced = [c for c in finding.get("ai_cost_items", []) if c.get("amount")]
        if priced:
            lines.append("**Costs.** " + "; ".join(f"{c['description']}: {c['formula']}" for c in priced) + ".")
            lines.append("")
        if finding.get("co_match"):
            lines.append(f"**Change-order log.** This work appears to be recorded as {finding['co_match']['co_number']}.")
            lines.append("")
        if finding.get("questions"):
            lines.append("**Open questions.** " + " ".join(q["text"] for q in finding["questions"]))
            lines.append("")
        lines.append("We request review of this item. This summary is not a determination of entitlement to payment.")
        timeline = [ProposedTimelineEntry(date=f.date or "", event=f"{f.file_name}: {f.summary}"[:240], fact_id=f.fact_id) for f in dated]
        return FindingNarrative(draft="\n".join(lines).strip(), timeline=timeline)


def get_llm() -> LLM:
    if config.llm_mode() == "fake":
        return FakeLLM()
    return BedrockLLM()
