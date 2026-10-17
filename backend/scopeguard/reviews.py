"""Project-level reviews, eligibility and the invoice draft [REV-01..09, INV-01/02/11].

Reviews live at projects/<id>/reviews/<work_item_id>.json and the draft at projects/<id>/invoice-draft.json.
Both carry a revision and are written with S3 conditional writes; a stale expected_revision or a lost race returns
409 ``revision_conflict``. Machine (worker) writes never touch these objects: "needs_revalidation" is derived by
comparing the review's confirmed evidence fingerprint with the current finding's fingerprint [REV-08].
"""

from __future__ import annotations

import copy
from decimal import Decimal
from typing import Optional

from .errors import bad_request, conflict, not_found, unprocessable
from .reconcile import BLOCKING_QUESTION_KINDS
from .serialize import now_iso
from .store import BaseStore, PreconditionFailed, get_store
from .validate import money_str, parse_decimal

REVIEW_STATUSES = ("unreviewed", "reviewed", "dismissed")
MAX_COMMENT = 10_000
MAX_DESCRIPTION = 2_000


def default_review(work_item_id: str) -> dict:
    return {"work_item_id": work_item_id, "status": "unreviewed", "internal_comment": "", "customer_description": None,
            "authorization_acknowledged": False, "evidence_fingerprint": None, "revision": 0, "updated_at": None}


def review_key(project_id: str, work_item_id: str) -> str:
    return BaseStore.project_key(project_id, f"reviews/{work_item_id}.json")


def draft_key(project_id: str) -> str:
    return BaseStore.project_key(project_id, "invoice-draft.json")


def load_reviews(store: BaseStore, project_id: str) -> dict[str, dict]:
    return {r["work_item_id"]: r for r in store.list_json_under(BaseStore.project_key(project_id, "reviews/")) if r.get("work_item_id")}


def empty_draft(project_id: str) -> dict:
    return {"project_id": project_id, "revision": 0, "scan_id": None, "items": [], "updated_at": None}


def load_draft(store: BaseStore, project_id: str) -> tuple[dict, Optional[str]]:
    draft, etag = store.get_json_etag(draft_key(project_id))
    return (draft or empty_draft(project_id)), etag


def selected_ids(draft: dict) -> list[str]:
    return [i["work_item_id"] for i in draft.get("items", [])]


# ---------------------------------------------------------------- derived review state


def effective_status(review: dict, ai: dict, selected: bool) -> tuple[str, bool]:
    """(status, evidence_changed). Changed evidence on a reviewed or selected item -> needs_revalidation."""
    fp = review.get("evidence_fingerprint")
    changed = bool(fp) and fp != ai.get("fingerprint")
    stored = review.get("status", "unreviewed")
    if changed and stored != "dismissed" and (stored == "reviewed" or selected):
        return "needs_revalidation", True
    return stored, changed


def review_view(review: dict, status: str) -> dict:
    return {"work_item_id": review["work_item_id"], "status": status, "internal_comment": review.get("internal_comment") or "",
            "customer_description": review.get("customer_description"),
            "authorization_acknowledged": bool(review.get("authorization_acknowledged")),
            "revision": int(review.get("revision", 0)), "updated_at": review.get("updated_at")}


def blockers_for(ai: dict, review: dict, status: str, ctx: dict) -> list[str]:
    """Why this finding cannot enter a priced invoice draft right now [REV-05, FIN-08/09/12, INV-11]."""
    out: list[str] = []
    if ai.get("amount") is None:
        out.append("The amount is unknown; a supported quantity and rate are required before invoicing")
    if status == "dismissed":
        out.append("Dismissed")
    if status == "needs_revalidation":
        out.append("Evidence changed since it was reviewed; re-check the sources and confirm")
    if ai.get("finding_type") == "authorization_unresolved" and not review.get("authorization_acknowledged"):
        out.append("Acknowledge the unresolved authorization first")
    if ai.get("finding_type") in ("needs_information", "invoice_balance_unresolved"):
        out.append("Needs information before it can be invoiced")
    for q in ai.get("questions") or []:
        if q.get("kind") in BLOCKING_QUESTION_KINDS:
            out.append(q["text"])
    if ctx.get("required_failed"):
        out.append("Some records could not be read in this audit; fix them and refresh before invoicing")
    if ctx.get("baseline_changed"):
        out.append("The signed agreement changed after this audit; refresh the audit")
    if ctx.get("not_latest"):
        out.append("A newer completed audit exists; review the latest audit")
    return out


def finding_view(ai: dict, review: Optional[dict], selected: bool, ctx: dict) -> dict:
    review = review or default_review(ai["work_item_id"])
    status, changed = effective_status(review, ai, selected)
    blockers = blockers_for(ai, review, status, ctx)
    keys = ("finding_id", "scan_id", "work_item_id", "code", "finding_type", "type_label", "title", "reason", "explanation",
            "authorization_status", "approval_label", "proposal", "amount", "amount_basis", "calculation", "cost_items",
            "financial", "evidence_groups", "source_count", "questions")
    out = {k: copy.deepcopy(ai.get(k)) for k in keys}
    out.update({"invoice_eligible": not blockers, "eligibility_blockers": blockers, "evidence_changed": changed,
                "review": review_view(review, status), "selected": selected})
    return out


# ---------------------------------------------------------------- scan context helpers


def _scans():
    from . import scans

    return scans


def finding_ai(store: BaseStore, scan_id: str, finding_id: str) -> Optional[dict]:
    return store.get_json(BaseStore.scan_key(scan_id, f"findings/{finding_id}/ai.json"))


def _require_int(body: dict, key: str, required: bool = True) -> Optional[int]:
    v = body.get(key)
    if v is None and not required:
        return None
    if isinstance(v, bool) or not isinstance(v, int):
        raise bad_request(f"'{key}' must be an integer")
    return v


def _require_str(body: dict, key: str) -> str:
    v = body.get(key)
    if not isinstance(v, str) or not v.strip():
        raise bad_request(f"'{key}' is required")
    return v.strip()


# ---------------------------------------------------------------- PATCH /findings/{finding_id}


def patch_finding(finding_id: str, body: dict) -> dict:
    scans = _scans()
    store = get_store()
    scan_id = _require_str(body, "scan_id")
    meta = store.get_scan_meta(scan_id)
    ai = finding_ai(store, scan_id, finding_id) if meta else None
    if ai is None:
        raise not_found(f"Finding {finding_id} not found in scan {scan_id}", "finding_not_found")
    pid = meta["project_id"]
    wid = ai["work_item_id"]
    expected = _require_int(body, "expected_revision", required=False)
    status = body.get("status")
    if status is not None and status not in REVIEW_STATUSES:
        raise bad_request("status must be one of unreviewed, reviewed, dismissed")
    comment = body.get("internal_comment")
    if comment is not None and (not isinstance(comment, str) or len(comment) > MAX_COMMENT):
        raise bad_request(f"internal_comment must be a string of at most {MAX_COMMENT} characters")
    if "customer_description" in body:
        cd = body["customer_description"]
        if cd is not None and (not isinstance(cd, str) or len(cd) > MAX_DESCRIPTION):
            raise bad_request(f"customer_description must be a string of at most {MAX_DESCRIPTION} characters or null")
    for flag in ("acknowledge_authorization", "confirm_revalidation"):
        if flag in body and not isinstance(body[flag], bool):
            raise bad_request(f"{flag} must be a boolean")

    key = review_key(pid, wid)
    current, etag = store.get_json_etag(key)
    review = current or default_review(wid)
    if expected is not None and expected != int(review.get("revision", 0)):
        raise conflict("This finding was updated by someone else. Reload and try again.", "revision_conflict",
                       {"current_revision": int(review.get("revision", 0)), "work_item_id": wid})
    draft, _ = load_draft(store, pid)
    selected = wid in selected_ids(draft)
    _, changed = effective_status(review, ai, selected)
    new = dict(review)
    if comment is not None:
        new["internal_comment"] = comment
    if "customer_description" in body:
        cd = body["customer_description"]
        new["customer_description"] = cd.strip() if isinstance(cd, str) and cd.strip() else None
    if "acknowledge_authorization" in body:
        new["authorization_acknowledged"] = body["acknowledge_authorization"]
    if status is not None:
        new["status"] = status
        if status == "reviewed" and (not review.get("evidence_fingerprint") or not changed):
            new["evidence_fingerprint"] = ai.get("fingerprint")
    if body.get("confirm_revalidation"):
        new["evidence_fingerprint"] = ai.get("fingerprint")
        if new["status"] == "unreviewed":
            new["status"] = "reviewed"
    new["revision"] = int(review.get("revision", 0)) + 1
    new["updated_at"] = now_iso()
    try:
        if etag is None:
            store.put_json(key, new, if_none_match=True)
        else:
            store.put_json(key, new, if_match=etag)
    except PreconditionFailed:
        latest = store.get_json(key) or review
        raise conflict("This finding was updated by someone else. Reload and try again.", "revision_conflict",
                       {"current_revision": int(latest.get("revision", 0)), "work_item_id": wid}) from None
    if new["status"] == "dismissed" and selected:
        _remove_from_draft(store, pid, wid)
        draft, _ = load_draft(store, pid)
    return finding_view(ai, new, wid in selected_ids(draft), scans.finding_context(store, meta))


def _remove_from_draft(store: BaseStore, project_id: str, wid: str) -> None:
    def mutate(cur: Optional[dict]) -> Optional[dict]:
        if not cur or wid not in selected_ids(cur):
            return None
        cur["items"] = [i for i in cur["items"] if i["work_item_id"] != wid]
        cur["revision"] = int(cur.get("revision", 0)) + 1
        cur["updated_at"] = now_iso()
        return cur

    store.update_json(draft_key(project_id), mutate)


# ---------------------------------------------------------------- invoice draft


def draft_view(project_id: str, store: Optional[BaseStore] = None) -> dict:
    scans = _scans()
    store = store or get_store()
    draft, _ = load_draft(store, project_id)
    latest = scans.latest_completed_scan(store, project_id)
    findings = scans.findings_by_work_item(store, latest["scan_id"]) if latest else {}
    ctx = scans.finding_context(store, latest) if latest else {}
    reviews = load_reviews(store, project_id)
    lines = []
    for item in draft.get("items", []):
        ai = findings.get(item["work_item_id"])
        if ai is None:
            snap = item.get("snapshot") or {}
            lines.append({"work_item_id": item["work_item_id"], "finding_id": item.get("finding_id", ""),
                          "code": snap.get("code", ""), "title": snap.get("title", ""), "description": snap.get("title", ""),
                          "amount": snap.get("amount"), "approval_label": snap.get("approval_label", ""),
                          "proposal": bool(snap.get("proposal")), "authorization_status": snap.get("authorization_status", "unclear"),
                          "blockers": ["No longer found in the latest audit; remove it from the draft"]})
            continue
        review = reviews.get(item["work_item_id"]) or default_review(item["work_item_id"])
        status, _ = effective_status(review, ai, True)
        lines.append({
            "work_item_id": ai["work_item_id"], "finding_id": ai["finding_id"], "code": ai["code"], "title": ai["title"],
            "description": review.get("customer_description") or ai["title"], "amount": ai.get("amount"),
            "approval_label": ai["approval_label"], "proposal": bool(ai["proposal"]),
            "authorization_status": ai["authorization_status"], "blockers": blockers_for(ai, review, status, ctx),
        })
    amounts = [parse_decimal(l["amount"]) for l in lines]
    subtotal = money_str(sum(amounts, Decimal(0))) if all(a is not None for a in amounts) else None
    blockers = []
    if not lines:
        blockers.append("Select at least one item")
    blocked = sum(1 for l in lines if l["blockers"])
    if blocked:
        blockers.append(f"Resolve the blockers on {blocked} line{'s' if blocked != 1 else ''}")
    wids = [l["work_item_id"] for l in lines]
    if len(set(wids)) != len(wids):
        blockers.append("The same work item is selected twice")
    return {"project_id": project_id, "revision": int(draft.get("revision", 0)), "scan_id": draft.get("scan_id"),
            "lines": lines, "subtotal": subtotal, "blockers": blockers, "can_generate": not blockers,
            "updated_at": draft.get("updated_at")}


def patch_draft(project_id: str, body: dict) -> dict:
    scans = _scans()
    store = get_store()
    expected = _require_int(body, "expected_revision")
    scan_id = _require_str(body, "scan_id")
    ids = body.get("selected_work_item_ids")
    if not isinstance(ids, list) or not all(isinstance(i, str) and i for i in ids):
        raise bad_request("'selected_work_item_ids' must be an array of work item IDs")
    draft, etag = load_draft(store, project_id)
    if expected != int(draft.get("revision", 0)):
        raise conflict("The invoice draft changed. Reload and try again.", "revision_conflict",
                       {"current_revision": int(draft.get("revision", 0))})
    latest = scans.latest_completed_scan(store, project_id)
    if latest is None or latest["scan_id"] != scan_id:
        raise conflict("Selections must come from the latest completed audit. Reload and try again.", "stale_selection",
                       {"latest_scan_id": latest["scan_id"] if latest else None})
    seen: set[str] = set()
    for wid in ids:
        if wid in seen:
            raise unprocessable("The same work item cannot be selected twice.", "duplicate_selection", {"work_item_id": wid})
        seen.add(wid)
    findings = scans.findings_by_work_item(store, scan_id)
    ctx = scans.finding_context(store, latest)
    reviews = load_reviews(store, project_id)
    current_ids = selected_ids(draft)
    added = [w for w in ids if w not in current_ids]
    for wid in ids:
        if wid not in findings and wid not in current_ids:
            raise unprocessable("This item is not part of the latest audit.", "unknown_work_item", {"work_item_id": wid})
    for wid in added:
        ai = findings[wid]
        review = reviews.get(wid) or default_review(wid)
        status, _ = effective_status(review, ai, False)
        blockers = blockers_for(ai, review, status, ctx)
        if blockers:
            raise unprocessable(f"{ai['code']} cannot be added to the invoice: {blockers[0]}", "ineligible_item",
                                {"work_item_id": wid, "blockers": blockers})
    # Adding records a reviewed selection (not customer approval) [REV-05]: unreviewed items become reviewed.
    for wid in added:
        key = review_key(project_id, wid)
        cur, retag = store.get_json_etag(key)
        review = cur or default_review(wid)
        if review.get("status") == "unreviewed":
            new = {**review, "status": "reviewed", "evidence_fingerprint": findings[wid].get("fingerprint"),
                   "revision": int(review.get("revision", 0)) + 1, "updated_at": now_iso()}
            try:
                store.put_json(key, new, if_none_match=True) if retag is None else store.put_json(key, new, if_match=retag)
            except PreconditionFailed:
                raise conflict("A finding was updated concurrently. Reload and try again.", "revision_conflict",
                               {"work_item_id": wid}) from None
    old_items = {i["work_item_id"]: i for i in draft.get("items", [])}
    now = now_iso()
    items = []
    for wid in ids:
        ai = findings.get(wid)
        if ai is None:
            items.append(old_items[wid])
            continue
        prev = old_items.get(wid, {})
        items.append({"work_item_id": wid, "finding_id": ai["finding_id"], "scan_id": scan_id, "fingerprint": ai.get("fingerprint"),
                      "added_at": prev.get("added_at", now),
                      "snapshot": {k: ai.get(k) for k in ("code", "title", "amount", "approval_label", "proposal", "authorization_status")}})
    new_draft = {"project_id": project_id, "revision": int(draft.get("revision", 0)) + 1, "scan_id": scan_id, "items": items,
                 "updated_at": now}
    try:
        if etag is None:
            store.put_json(draft_key(project_id), new_draft, if_none_match=True)
        else:
            store.put_json(draft_key(project_id), new_draft, if_match=etag)
    except PreconditionFailed:
        latest_draft, _ = load_draft(store, project_id)
        raise conflict("The invoice draft changed. Reload and try again.", "revision_conflict",
                       {"current_revision": int(latest_draft.get("revision", 0))}) from None
    return draft_view(project_id, store)


def to_review_count(work_item_ids: list[str], reviews: dict[str, dict], selected: set[str]) -> int:
    """Findings not dismissed and not selected."""
    return sum(1 for w in work_item_ids if w not in selected and (reviews.get(w) or {}).get("status") != "dismissed")


def summary_counts(base: Optional[dict], reviews: dict[str, dict], selected: set[str]) -> Optional[dict]:
    if not base:
        return None
    items = base.get("items", [])
    wids = [i["work_item_id"] for i in items]
    # Selected items still need review when their source versions changed.
    # Older scan summaries lack fingerprints and retain their legacy count until refreshed.
    pending = (sum(1 for item in items if effective_status(
        reviews.get(item["work_item_id"]) or default_review(item["work_item_id"]), item,
        item["work_item_id"] in selected)[0] in ("unreviewed", "needs_revalidation"))
        if all(item.get("fingerprint") for item in items) else to_review_count(wids, reviews, selected))
    eligible = [parse_decimal(i.get("amount")) for i in items
                if i.get("amount") is not None and (reviews.get(i["work_item_id"]) or {}).get("status") != "dismissed"
                and i.get("finding_type") in ("approved_unbilled", "scope_change_proposed", "authorization_unresolved")]
    return {"file_count": int(base.get("file_count", 0)), "finding_count": len(items),
            "to_review_count": pending,
            "eligible_total": money_str(sum((e for e in eligible if e is not None), Decimal(0))),
            "by_type": dict(base.get("by_type", {}))}
