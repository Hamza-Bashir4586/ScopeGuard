"""Convert stored items into the API contract shapes (docs/api-contract.md) and
validate PATCH input. Effective values = reviewer override if present, else AI value."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from .validate import cost_total, finalize_cost_items, parse_decimal, short_hash

REVIEW_STATUSES = ("unreviewed", "confirmed", "dismissed")
COST_CATEGORIES = ("labor", "material", "equipment", "markup", "other")


class BadRequest(ValueError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def default_review() -> dict:
    return {"status": "unreviewed", "draft": None, "cost_items": None, "question_resolutions": {}, "updated_at": None}


def _int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _str_or_none(v: Any) -> Optional[str]:
    return None if v is None else str(v)


def serialize_cost_item(it: dict) -> dict:
    return {
        "item_id": str(it.get("item_id", "")),
        "category": it.get("category", "other"),
        "description": it.get("description", ""),
        "qty": _str_or_none(it.get("qty")),
        "unit": it.get("unit", "") or "",
        "rate": _str_or_none(it.get("rate")),
        "amount": _str_or_none(it.get("amount")),
        "formula": it.get("formula", ""),
        "source_evidence_id": it.get("source_evidence_id"),
    }


def serialize_review(review: Optional[dict]) -> dict:
    r = {**default_review(), **(review or {})}
    return {
        "status": r["status"] if r["status"] in REVIEW_STATUSES else "unreviewed",
        "draft": r.get("draft"),
        "cost_items": [serialize_cost_item(c) for c in r["cost_items"]] if r.get("cost_items") is not None else None,
        "question_resolutions": {str(k): str(v) for k, v in (r.get("question_resolutions") or {}).items()},
        "updated_at": r.get("updated_at"),
    }


def serialize_packet(p: dict) -> dict:
    return {"box_file_id": str(p["box_file_id"]), "box_url": p["box_url"], "created_at": p["created_at"]}


def serialize_finding(item: dict, packets: Optional[list[dict]] = None) -> dict:
    ai = item.get("ai") or {}
    review = serialize_review(item.get("review"))
    resolutions = review["question_resolutions"]
    ai_cost_items = [serialize_cost_item(c) for c in ai.get("ai_cost_items") or []]
    ai_draft = ai.get("ai_draft") or ""
    eff_items = review["cost_items"] if review["cost_items"] is not None else ai_cost_items
    co = ai.get("co_match")
    return {
        "finding_id": item.get("finding_id") or ai.get("finding_id"),
        "scan_id": item.get("scan_id"),
        "title": ai.get("title", ""),
        "summary": ai.get("summary", ""),
        "finding_status": ai.get("finding_status", "needs_info"),
        "co_match": {"co_number": co.get("co_number", ""), "description": co.get("description", ""), "amount": _str_or_none(co.get("amount"))} if co else None,
        "scope_baseline": ai.get("scope_baseline", ""),
        "requested_work": ai.get("requested_work", ""),
        "evidence": [
            {
                "evidence_id": e["evidence_id"],
                "box_file_id": str(e["box_file_id"]),
                "box_version_id": str(e.get("box_version_id", "")),
                "file_name": e.get("file_name", ""),
                "page": _int(e.get("page"), 1),
                "quote": e.get("quote", ""),
                "role": e.get("role", "work_record"),
                "verified": bool(e.get("verified")),
                "box_url": e.get("box_url", ""),
            }
            for e in ai.get("evidence") or []
        ],
        "timeline": [{"date": t.get("date", ""), "event": t.get("event", ""), "evidence_id": t.get("evidence_id")} for t in ai.get("timeline") or []],
        "questions": [
            {"question_id": q["question_id"], "kind": q["kind"], "text": q["text"], "resolution": resolutions.get(q["question_id"])}
            for q in ai.get("questions") or []
        ],
        "ai_draft": ai_draft,
        "ai_cost_items": ai_cost_items,
        "review": review,
        "draft": review["draft"] if review["draft"] is not None else ai_draft,
        "cost_items": eff_items,
        "cost_total": cost_total(eff_items),
        "packets": [serialize_packet(p) for p in sorted(packets or [], key=lambda p: p.get("created_at", ""))],
    }


def serialize_file(f: dict) -> dict:
    return {
        "box_file_id": str(f["box_file_id"]),
        "box_version_id": str(f.get("box_version_id", "")),
        "name": f.get("name", ""),
        "doc_type": f.get("doc_type", "other"),
        "page_count": _int(f.get("page_count")),
        "box_url": f.get("box_url", ""),
    }


def serialize_scan(items: list[dict]) -> Optional[dict]:
    """Build the Scan shape from all items of one SCAN# partition (one Query)."""
    meta = None
    files, findings, packets = [], [], {}
    for it in items:
        sk = it.get("SK", "")
        if sk == "META":
            meta = it
        elif sk.startswith("FILE#"):
            files.append(it)
        elif sk.startswith("FINDING#"):
            findings.append(it)
        elif sk.startswith("PACKET#"):
            packets.setdefault(it.get("finding_id"), []).append(it)
    if meta is None:
        return None
    status_order = {"new": 0, "needs_info": 1, "already_recorded": 2}
    out_findings = [serialize_finding(f, packets.get(f.get("finding_id"))) for f in findings]
    out_findings.sort(key=lambda f: (status_order.get(f["finding_status"], 9), f["title"]))
    return {
        "scan_id": meta["scan_id"],
        "status": meta.get("status", "queued"),
        "stage": meta.get("stage", ""),
        "progress": max(0, min(100, _int(meta.get("progress")))),
        "error": meta.get("error"),
        "started_at": meta.get("started_at"),
        "finished_at": meta.get("finished_at"),
        "files": sorted((serialize_file(f) for f in files), key=lambda f: f["name"].lower()),
        "findings": out_findings,
    }


# ---------------------------------------------------------------- PATCH validation


def _decimal_field(item: dict, key: str, idx: int) -> Optional[str]:
    v = item.get(key)
    if v is None:
        return None
    if isinstance(v, float):
        raise BadRequest(f"cost_items[{idx}].{key} must be a decimal string, not a float")
    if isinstance(v, str) and v.strip() == "":
        return None
    d = parse_decimal(v) if isinstance(v, (str, int)) else None
    if d is None:
        raise BadRequest(f"cost_items[{idx}].{key} must be a decimal string or null")
    return str(d)


def normalize_patch_cost_items(raw: Any, finding_id: str) -> list[dict]:
    if not isinstance(raw, list):
        raise BadRequest("cost_items must be an array")
    out = []
    for idx, it in enumerate(raw):
        if not isinstance(it, dict):
            raise BadRequest(f"cost_items[{idx}] must be an object")
        category = it.get("category", "other")
        if category not in COST_CATEGORIES:
            raise BadRequest(f"cost_items[{idx}].category must be one of {', '.join(COST_CATEGORIES)}")
        description = it.get("description", "")
        unit = it.get("unit", "")
        if not isinstance(description, str) or not isinstance(unit, str):
            raise BadRequest(f"cost_items[{idx}].description and unit must be strings")
        src = it.get("source_evidence_id")
        if src is not None and not isinstance(src, str):
            raise BadRequest(f"cost_items[{idx}].source_evidence_id must be a string or null")
        item_id = it.get("item_id")
        if not item_id or not isinstance(item_id, str):
            item_id = "c_" + short_hash(finding_id, "review", str(idx), description, now_iso(), length=10)
        out.append({
            "item_id": item_id,
            "category": category,
            "description": description,
            "qty": _decimal_field(it, "qty", idx),
            "unit": unit,
            "rate": _decimal_field(it, "rate", idx),
            "source_evidence_id": src,
        })
    return finalize_cost_items(out)


def apply_patch(existing_review: Optional[dict], body: dict, finding_id: str) -> dict:
    """Return the new review map. Only `review` is ever written by PATCH."""
    review = {**default_review(), **(existing_review or {})}
    review["question_resolutions"] = dict(review.get("question_resolutions") or {})
    if "status" in body:
        if body["status"] not in REVIEW_STATUSES:
            raise BadRequest(f"status must be one of {', '.join(REVIEW_STATUSES)}")
        review["status"] = body["status"]
    if "draft" in body:
        if body["draft"] is not None and not isinstance(body["draft"], str):
            raise BadRequest("draft must be a string")
        review["draft"] = body["draft"]
    if "cost_items" in body:
        review["cost_items"] = None if body["cost_items"] is None else normalize_patch_cost_items(body["cost_items"], finding_id)
    if "question_resolutions" in body:
        qr = body["question_resolutions"]
        if not isinstance(qr, dict):
            raise BadRequest("question_resolutions must be an object")
        for k, v in qr.items():
            if v is None or (isinstance(v, str) and v.strip() == ""):
                review["question_resolutions"].pop(str(k), None)
            elif isinstance(v, str):
                review["question_resolutions"][str(k)] = v
            else:
                raise BadRequest("question_resolutions values must be strings")
    review["updated_at"] = now_iso()
    return review
