"""Audit (scan) lifecycle and read models [AUD-01..11, PRJ-01].

* POST /scans: validates the project, deduplicates request_id, acquires the one-active-scan lease
  (projects/<id>/active-scan.json, S3 conditional writes), writes an immutable input manifest, then invokes the
  worker with the scan ID only.
* Stale detection: a queued/running scan past its deadline is surfaced as failed and its lease released, so a
  new audit can start without a scheduler [AUD-10].
* Read models: project cards/detail, scan summaries and the full Scan with findings, reviews and selections.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import box_client, config, projects, records, reviews
from .box_client import SourceFileRef
from .errors import ApiError, bad_request, conflict, not_found
from .serialize import FINDING_TYPES, STAGE_LABELS, now_iso, parse_iso, scan_summary
from .store import BaseStore, PreconditionFailed, get_store
from .validate import check_quote

log = logging.getLogger(__name__)
_lambda_client = None

TERMINAL = ("completed", "failed")


# ---------------------------------------------------------------- small readers


def project_state(store: BaseStore, project_id: str) -> dict:
    return store.get_json(BaseStore.project_key(project_id, "project.json")) or {}


def latest_completed_scan(store: BaseStore, project_id: str) -> Optional[dict]:
    sid = project_state(store, project_id).get("latest_completed_scan_id")
    return store.get_scan_meta(sid) if sid else None


def file_outcomes(store: BaseStore, scan_id: str) -> dict[str, dict]:
    return {o["file_id"]: o for o in store.list_json_under(BaseStore.scan_key(scan_id, "files/"))}


def results(store: BaseStore, scan_id: str) -> dict:
    return store.get_json(BaseStore.scan_key(scan_id, "results.json")) or {}


def scan_findings(store: BaseStore, scan_id: str) -> list[dict]:
    order = results(store, scan_id).get("finding_ids", [])
    got = store.get_many_json([BaseStore.scan_key(scan_id, f"findings/{fid}/ai.json") for fid in order])
    return [got[k] for k in (BaseStore.scan_key(scan_id, f"findings/{fid}/ai.json") for fid in order) if k in got]


def findings_by_work_item(store: BaseStore, scan_id: str) -> dict[str, dict]:
    return {f["work_item_id"]: f for f in scan_findings(store, scan_id)}


def finding_context(store: BaseStore, meta: Optional[dict], current_baseline: Optional[str] = None) -> dict:
    if not meta:
        return {}
    latest = project_state(store, meta.get("project_id", "")).get("latest_completed_scan_id")
    ctx = {"required_failed": bool((meta.get("coverage") or {}).get("required_failed")),
           "not_latest": bool(latest and latest != meta["scan_id"])}
    if current_baseline is not None and meta.get("baseline_version") and current_baseline != meta["baseline_version"]:
        ctx["baseline_changed"] = True
    return ctx


# ---------------------------------------------------------------- leases


def _lease_key(project_id: str) -> str:
    return BaseStore.project_key(project_id, "active-scan.json")


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def check_stale(store: BaseStore, meta: Optional[dict]) -> Optional[dict]:
    """Mark a queued/running scan past its deadline as failed and release its lease [AUD-10]."""
    if not meta or meta.get("status") in TERMINAL:
        return meta
    deadline = parse_iso(meta.get("deadline_at"))
    if deadline is None or datetime.now(timezone.utc) <= deadline:
        return meta
    now = now_iso()
    meta = store.update_scan(meta["scan_id"], status="failed", error_code="worker_timeout",
                             error="The audit did not finish before its deadline. Run the audit again.",
                             finished_at=now, updated_at=now, current_event=None)
    release_lease(store, meta["project_id"], meta["scan_id"])
    return meta


def acquire_lease(store: BaseStore, project_id: str, scan_id: str, deadline: str) -> None:
    key = _lease_key(project_id)
    for _ in range(4):
        lease, etag = store.get_json_etag(key)
        if lease and not lease.get("released"):
            holder = lease.get("scan_id")
            meta = check_stale(store, store.get_scan_meta(holder)) if holder else None
            if meta is not None and meta.get("status") not in TERMINAL:
                raise conflict("An audit is already running for this project.", "scan_active", {"scan_id": holder})
            if meta is None:
                acquired = parse_iso(lease.get("acquired_at"))
                if acquired and datetime.now(timezone.utc) - acquired < timedelta(seconds=60):
                    raise conflict("An audit is already starting for this project.", "scan_active", {"scan_id": holder})
        new = {"scan_id": scan_id, "project_id": project_id, "acquired_at": now_iso(), "expires_at": deadline,
               "revision": int((lease or {}).get("revision", 0)) + 1, "released": False}
        try:
            if etag is None:
                store.put_json(key, new, if_none_match=True)
            else:
                store.put_json(key, new, if_match=etag)
            return
        except PreconditionFailed:
            continue
    lease = store.get_json(key) or {}
    raise conflict("An audit is already running for this project.", "scan_active", {"scan_id": lease.get("scan_id")})


def release_lease(store: BaseStore, project_id: str, scan_id: str) -> None:
    def mutate(cur: Optional[dict]) -> Optional[dict]:
        if not cur or cur.get("scan_id") != scan_id or cur.get("released"):
            return None
        return {**cur, "released": True, "released_at": now_iso(), "revision": int(cur.get("revision", 0)) + 1}

    try:
        store.update_json(_lease_key(project_id), mutate)
    except PreconditionFailed:  # pragma: no cover - lease changed hands; nothing to release
        pass


def active_scan(store: BaseStore, project_id: str) -> Optional[dict]:
    lease = store.get_json(_lease_key(project_id))
    if not lease or lease.get("released"):
        return None
    meta = check_stale(store, store.get_scan_meta(lease["scan_id"]))
    return meta if meta and meta.get("status") not in TERMINAL else None


# ---------------------------------------------------------------- POST /scans


def _start_worker(scan_id: str) -> None:
    global _lambda_client
    fn = config.worker_function_name()
    if fn:
        if _lambda_client is None:
            import boto3

            _lambda_client = boto3.client("lambda")
        _lambda_client.invoke(FunctionName=fn, InvocationType="Event", Payload=json.dumps({"scan_id": scan_id}).encode())
    else:
        from . import analyze

        # Local mode: run the worker in a background thread sharing this process's store.
        threading.Thread(target=analyze.run_scan, args=(scan_id,), name=f"scan-{scan_id}", daemon=True).start()


def build_manifest_files(project: dict, refs: list[SourceFileRef]) -> list[dict]:
    agreement = projects.agreement_record(project, refs)
    ordered = sorted(refs, key=lambda r: (0 if agreement and r.box_file_id == agreement.box_file_id else 1, r.name.lower()))
    files, included, total_bytes = [], 0, 0
    for r in ordered:
        entry = {"file_id": r.box_file_id, "version_id": r.box_version_id, "name": r.name, "extension": r.extension,
                 "mime_type": r.mime_type, "size_bytes": int(r.size_bytes), "modified_at": r.modified_at,
                 "included": False, "status": None, "reason": None}
        if not r.supported:
            entry.update(status="unsupported", reason="Unsupported file type; only PDF, TXT and Markdown records are analyzed.")
        elif r.size_bytes > config.MAX_FILE_BYTES:
            entry.update(status="excluded", reason="Larger than the 4 MiB per-record limit; not analyzed.")
        elif included >= config.MAX_SCAN_FILES:
            entry.update(status="excluded", reason=f"Over the {config.MAX_SCAN_FILES}-record limit for one audit; not analyzed.")
        elif total_bytes + r.size_bytes > config.MAX_DATASET_BYTES:
            entry.update(status="excluded", reason="Over the 40 MiB dataset limit for one audit; not analyzed.")
        else:
            entry["included"] = True
            included += 1
            total_bytes += r.size_bytes
        files.append(entry)
    return sorted(files, key=lambda f: f["name"].lower())


def start_scan(body: dict) -> dict:
    project = projects.get_project(body.get("project_id"))
    pid = project["project_id"]
    cutoff = body.get("cutoff_date")
    if cutoff is not None:
        if not isinstance(cutoff, str):
            raise bad_request("cutoff_date must be YYYY-MM-DD")
        try:
            datetime.strptime(cutoff, "%Y-%m-%d")
        except ValueError:
            raise bad_request("cutoff_date must be YYYY-MM-DD") from None
    request_id = body.get("request_id")
    if request_id is not None and (not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 200):
        raise bad_request("request_id must be a non-empty string of at most 200 characters")
    store = get_store()
    req_key = BaseStore.project_key(pid, f"scan-requests/{BaseStore.hashed(request_id)}.json") if request_id else None
    if req_key:
        prior = store.get_json(req_key)
        if prior:
            meta = check_stale(store, store.get_scan_meta(prior["scan_id"])) or {}
            return {"scan_id": prior["scan_id"], "project_id": pid, "status": meta.get("status", "queued")}

    refs = records.inventory()
    scan_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    deadline = _iso(now + timedelta(seconds=config.scan_deadline_seconds()))
    acquire_lease(store, pid, scan_id, deadline)
    if req_key and not store.create_json(req_key, {"request_id": request_id, "scan_id": scan_id, "created_at": now_iso()}):
        release_lease(store, pid, scan_id)
        prior = store.get_json(req_key) or {}
        meta = store.get_scan_meta(prior.get("scan_id", "")) or {}
        return {"scan_id": prior.get("scan_id"), "project_id": pid, "status": meta.get("status", "queued")}

    files = build_manifest_files(project, refs)
    agreement = projects.agreement_record(project, refs)
    baseline = projects.baseline_version(project, refs)
    requested_at = now_iso()
    manifest = {
        "scan_id": scan_id, "project_id": pid, "box_folder_id": projects.folder_id(project), "cutoff_date": cutoff,
        "request_id": request_id, "requested_at": requested_at, "prompt_version": config.PROMPT_VERSION,
        "analysis_version": config.ANALYSIS_VERSION,
        "model": "fake" if config.llm_mode() == "fake" else f"bedrock:{config.bedrock_model_id()}",
        "baseline_version": baseline, "baseline_file_id": agreement.box_file_id if agreement else None,
        "files": files,
    }
    store.put_json(BaseStore.scan_key(scan_id, "manifest.json"), manifest, if_none_match=True)
    for f in files:  # remember analyzed versions so their pinned content stays viewable [DOC-06]
        records.merge_metadata(store, pid, f["file_id"], f["version_id"], name=f["name"])
    store.put_json(BaseStore.scan_key(scan_id, "meta.json"), {
        "scan_id": scan_id, "project_id": pid, "status": "queued", "stage_id": "queued", "stage": STAGE_LABELS["queued"],
        "progress": 0, "error": None, "error_code": None, "cutoff_date": cutoff, "started_at": requested_at,
        "updated_at": requested_at, "finished_at": None, "deadline_at": deadline, "baseline_version": baseline,
        "files_processed": 0, "files_total": sum(1 for f in files if f["included"]), "current_event": None,
        "coverage": {"complete": False, "warnings": []},
    }, if_none_match=True)
    try:
        _start_worker(scan_id)
    except Exception as exc:
        log.error("Failed to start worker: %s", type(exc).__name__)
        store.update_scan(scan_id, status="failed", error_code="worker_start_failed",
                          error="The audit worker could not be started. Try again.", finished_at=now_iso())
        release_lease(store, pid, scan_id)
        raise ApiError(502, "The audit worker could not be started. Try again.", "worker_start_failed") from None
    return {"scan_id": scan_id, "project_id": pid, "status": "queued"}


# ---------------------------------------------------------------- read models


def _counts(store: BaseStore, meta: dict, cache: dict) -> Optional[dict]:
    pid = meta.get("project_id", "")
    if pid not in cache:
        draft, _ = reviews.load_draft(store, pid)
        cache[pid] = (reviews.load_reviews(store, pid), set(reviews.selected_ids(draft)))
    revs, selected = cache[pid]
    return reviews.summary_counts(meta.get("summary_base"), revs, selected)


def summary_view(store: BaseStore, meta: Optional[dict], cache: Optional[dict] = None) -> Optional[dict]:
    if not meta:
        return None
    return scan_summary(meta, _counts(store, meta, cache if cache is not None else {}))


def list_scans(project_id: Optional[str], limit: int) -> dict:
    store = get_store()
    if project_id:
        projects.get_project(project_id)
    metas = [m for m in store.list_scan_metas() if not project_id or m.get("project_id") == project_id]
    metas = sorted(metas, key=lambda m: m.get("started_at") or "", reverse=True)[:limit]
    cache: dict = {}
    return {"scans": [summary_view(store, check_stale(store, m), cache) for m in metas]}


def _pending_record(entry: dict) -> dict:
    ref = SourceFileRef(entry["file_id"], entry["version_id"], entry["name"], entry.get("size_bytes", 0), entry.get("modified_at"))
    rec = records.source_record(ref, meta=None, outcome=None, seeded_ids=set())
    if entry.get("status") in ("unsupported", "excluded"):
        rec["ingestion_status"] = entry["status"]
        rec["warnings"] = [entry["reason"]] if entry.get("reason") else []
    return rec


def scan_view(scan_id: str) -> dict:
    store = get_store()
    meta = check_stale(store, store.get_scan_meta(scan_id))
    if meta is None:
        raise not_found(f"Scan {scan_id} not found", "scan_not_found")
    manifest = store.get_json(BaseStore.scan_key(scan_id, "manifest.json")) or {"files": []}
    outcomes = file_outcomes(store, scan_id)
    files = [outcomes.get(f["file_id"]) or _pending_record(f) for f in manifest.get("files", [])]
    res = results(store, scan_id)
    pid = meta["project_id"]
    draft, _ = reviews.load_draft(store, pid)
    selected = set(reviews.selected_ids(draft))
    revs = reviews.load_reviews(store, pid)
    current_baseline = None
    if meta.get("status") == "completed":
        try:
            current_baseline = projects.baseline_version(projects.get_project(pid), box_client.get_source().list_files())
        except Exception:  # Box unavailable: skip the agreement-change check rather than fail the read
            current_baseline = None
    ctx = finding_context(store, meta, current_baseline)
    findings = [reviews.finding_view(ai, revs.get(ai["work_item_id"]), ai["work_item_id"] in selected, ctx)
                for ai in scan_findings(store, scan_id)]
    coverage = meta.get("coverage") or {"complete": False, "warnings": []}
    out = scan_summary(meta, reviews.summary_counts(meta.get("summary_base"), revs, selected))
    out.update({
        "files_processed": int(meta.get("files_processed", 0)),
        "files_total": int(meta.get("files_total", 0)),
        "current_event": meta.get("current_event"),
        "coverage": {"complete": bool(coverage.get("complete")), "warnings": list(coverage.get("warnings") or [])},
        "files": files,
        "findings": findings,
        "existing_items": res.get("existing_items", []),
    })
    return out


def highlights_for(store: BaseStore, project: dict, file_id: str, version_id: str, page: int, text: str,
                   scan_id: Optional[str], finding_id: Optional[str], refs: list[SourceFileRef]) -> list[str]:
    out: list[str] = []
    pid = project["project_id"]
    if finding_id:
        sid = scan_id or (latest_completed_scan(store, pid) or {}).get("scan_id")
        meta = store.get_scan_meta(sid) if sid else None
        ai = reviews.finding_ai(store, sid, finding_id) if meta and meta.get("project_id") == pid else None
        if ai is None:
            raise not_found(f"Finding {finding_id} not found", "finding_not_found")
        for g in ai.get("evidence_groups", []):
            for e in g["evidence"]:
                if e["verified"] and e["file_id"] == file_id and e["version_id"] == version_id and int(e["page"]) == page:
                    out.append(e["quote"])
    else:
        latest = latest_completed_scan(store, pid)
        refreshed = results(store, latest["scan_id"]).get("agreement") if latest else None
        for src in projects.baseline_quotes(project, refs, refreshed):
            if src["file_id"] == file_id and src["version_id"] == version_id and src.get("page") == page:
                if check_quote(src["quote"], [text], 1).verified:  # only highlight text that is really there
                    out.append(src["quote"])
    return list(dict.fromkeys(out))


# ---------------------------------------------------------------- projects


def project_cards() -> dict:
    store = get_store()
    cards = []
    cache: dict = {}
    for p in projects.PROJECTS:
        card = projects.card(p)
        if not p["preview_only"]:
            latest = latest_completed_scan(store, p["project_id"])
            counts = _counts(store, latest, cache) if latest else None
            n = counts["to_review_count"] if counts else None
            card.update(review_label=projects.review_label(n), to_review_count=n)
        cards.append(card)
    return {"projects": cards}


def project_detail(project_id: str) -> dict:
    project = projects.get_project(project_id)
    store = get_store()
    refs = records.inventory()
    latest = latest_completed_scan(store, project_id)
    res = results(store, latest["scan_id"]) if latest else {}
    cache: dict = {}
    latest_summary = summary_view(store, latest, cache)
    n = latest_summary["summary"]["to_review_count"] if latest_summary and latest_summary["summary"] else None
    new_input = False
    if latest:
        manifest = store.get_json(BaseStore.scan_key(latest["scan_id"], "manifest.json")) or {}
        analyzed = {(f["file_id"], f["version_id"]) for f in manifest.get("files", [])}
        current = {(r.box_file_id, r.box_version_id) for r in refs}
        new_input = analyzed != current
    draft = reviews.draft_view(project_id, store)
    return {
        **projects.card(project),
        "review_label": projects.review_label(n), "to_review_count": n,
        "currency": project.get("currency", "USD"),
        "agreement": projects.agreement(project, refs, res.get("agreement")),
        "audit_action": "refresh" if latest else "run",
        "latest_scan": latest_summary,
        "active_scan": summary_view(store, active_scan(store, project_id), cache),
        "records_count": len(refs),
        "new_input_available": new_input,
        "invoice_draft": {"revision": draft["revision"], "item_count": len(draft["lines"]), "subtotal": draft["subtotal"]},
    }


def by_type_counts(findings: list[dict]) -> dict[str, int]:
    out = {t: 0 for t in FINDING_TYPES}
    for f in findings:
        out[f["finding_type"]] = out.get(f["finding_type"], 0) + 1
    return out
