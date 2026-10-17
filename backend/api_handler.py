"""API Lambda (API Gateway HTTP API, payload v2). Implements docs/api-contract.md:

  POST  /scans                    -> 202 {"scan_id", "status": "queued"}
  GET   /scans/{scan_id}          -> 200 Scan
  PATCH /findings/{finding_id}    -> 200 Finding   (writes only `review`)
  POST  /packets                  -> 201 Packet    (requires review.status == "confirmed", else 409)
"""

from __future__ import annotations

import base64
import json
import logging
import re
import threading
import uuid
from datetime import datetime, timezone

from scopeguard import analyze, box_client, config
from scopeguard.packet import packet_filename, render_packet
from scopeguard.serialize import (
    BadRequest, apply_patch, now_iso, scan_summary, serialize_finding, serialize_scan, serialize_scan_summary,
)
from scopeguard.store import NotFound, get_store

log = logging.getLogger()
log.setLevel(logging.INFO)

_lambda_client = None


class HttpError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _cors_headers() -> dict:
    return {
        "Access-Control-Allow-Origin": config.allowed_origin(),
        "Access-Control-Allow-Methods": "GET,POST,PATCH,OPTIONS",
        "Access-Control-Allow-Headers": "content-type",
    }


def _response(status: int, body: object | None) -> dict:
    headers = {"Content-Type": "application/json", **_cors_headers()}
    return {"statusCode": status, "headers": headers, "body": "" if body is None else json.dumps(body)}


def _json_body(event: dict) -> dict:
    raw = event.get("body")
    if raw in (None, ""):
        return {}
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8")
    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        raise HttpError(400, "Request body must be valid JSON")
    if not isinstance(body, dict):
        raise HttpError(400, "Request body must be a JSON object")
    return body


def _require_str(body: dict, key: str) -> str:
    v = body.get(key)
    if not isinstance(v, str) or not v.strip():
        raise HttpError(400, f"'{key}' is required")
    return v.strip()


# ---------------------------------------------------------------- handlers


def _start_worker(scan_id: str) -> None:
    global _lambda_client
    fn = config.worker_function_name()
    if fn:
        if _lambda_client is None:
            import boto3

            _lambda_client = boto3.client("lambda")
        _lambda_client.invoke(FunctionName=fn, InvocationType="Event", Payload=json.dumps({"scan_id": scan_id}).encode())
    else:
        # Local mode: run the worker in a background thread sharing this process's store.
        threading.Thread(target=analyze.run_scan, args=(scan_id,), name=f"scan-{scan_id}", daemon=True).start()


def post_scans(event: dict) -> dict:
    _json_body(event)  # body is {} by contract; validate it parses
    store = get_store()
    scan_id = uuid.uuid4().hex
    store.put_scan_meta(scan_id, {
        "status": "queued", "stage": "Queued", "progress": 0, "error": None,
        "started_at": now_iso(), "finished_at": None, "box_folder_id": config.box_folder_id(),
    })
    try:
        _start_worker(scan_id)
    except Exception as exc:
        log.exception("Failed to start worker")
        store.update_scan(scan_id, status="failed", error=f"Could not start worker: {exc}", finished_at=now_iso())
        raise HttpError(500, f"Could not start scan worker: {exc}")
    return _response(202, {"scan_id": scan_id, "status": "queued"})


def list_scans(event: dict) -> dict:
    """Saved scans, newest first, so the frontend can reopen results without re-ingesting."""
    params = event.get("queryStringParameters") or {}
    try:
        limit = max(1, min(100, int(params.get("limit") or 20)))
    except ValueError:
        raise HttpError(400, "limit must be an integer")
    store = get_store()
    metas = sorted(store.list_scan_metas(), key=lambda m: m.get("started_at") or "", reverse=True)[:limit]
    for meta in metas:
        # Scans completed before summaries existed: compute once and backfill.
        if meta.get("status") == "completed" and not meta.get("summary"):
            scan = serialize_scan(store.get_scan_items(meta["scan_id"]))
            if scan is not None:
                meta["summary"] = scan_summary(scan)
                store.update_scan(meta["scan_id"], summary=meta["summary"])
    return _response(200, {"scans": [serialize_scan_summary(m) for m in metas]})


def get_scan(scan_id: str) -> dict:
    scan = serialize_scan(get_store().get_scan_items(scan_id))
    if scan is None:
        raise HttpError(404, f"Scan {scan_id} not found")
    return _response(200, scan)


def patch_finding(event: dict, finding_id: str) -> dict:
    body = _json_body(event)
    scan_id = _require_str(body, "scan_id")
    store = get_store()
    item = store.get_finding(scan_id, finding_id)
    if item is None:
        raise HttpError(404, f"Finding {finding_id} not found in scan {scan_id}")
    try:
        review = apply_patch(item.get("review"), body, finding_id)
    except BadRequest as exc:
        raise HttpError(400, str(exc))
    try:
        store.update_finding_review(scan_id, finding_id, review)
    except NotFound:
        raise HttpError(404, f"Finding {finding_id} not found in scan {scan_id}")
    item["review"] = review
    return _response(200, serialize_finding(item, store.list_packets(scan_id, finding_id)))


def post_packets(event: dict) -> dict:
    body = _json_body(event)
    scan_id = _require_str(body, "scan_id")
    finding_id = _require_str(body, "finding_id")
    store = get_store()
    items = store.get_scan_items(scan_id)
    scan = serialize_scan(items)
    if scan is None:
        raise HttpError(404, f"Scan {scan_id} not found")
    finding = next((f for f in scan["findings"] if f["finding_id"] == finding_id), None)
    if finding is None:
        raise HttpError(404, f"Finding {finding_id} not found in scan {scan_id}")
    if finding["review"]["status"] != "confirmed":
        raise HttpError(409, "Finding must be confirmed by a reviewer before a packet can be generated")
    now = datetime.now(timezone.utc)
    pdf = render_packet(finding, scan["files"], now)
    uploaded = box_client.get_source().upload_packet(packet_filename(finding["title"], now), pdf)
    created_at = now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    packet = {"box_file_id": uploaded.box_file_id, "box_url": uploaded.box_url, "created_at": created_at}
    store.put_packet(scan_id, finding_id, packet)
    return _response(201, packet)


# ---------------------------------------------------------------- router

_ROUTES = [
    ("POST", re.compile(r"^/scans$"), lambda e, m: post_scans(e)),
    ("GET", re.compile(r"^/scans$"), lambda e, m: list_scans(e)),
    ("GET", re.compile(r"^/scans/([^/]+)$"), lambda e, m: get_scan(m.group(1))),
    ("PATCH", re.compile(r"^/findings/([^/]+)$"), lambda e, m: patch_finding(e, m.group(1))),
    ("POST", re.compile(r"^/packets$"), lambda e, m: post_packets(e)),
]


def handler(event: dict, context: object = None) -> dict:
    http = (event.get("requestContext") or {}).get("http") or {}
    method = (http.get("method") or event.get("httpMethod") or "GET").upper()
    path = event.get("rawPath") or http.get("path") or "/"
    path = path.rstrip("/") or "/"
    if method == "OPTIONS":
        return {"statusCode": 204, "headers": _cors_headers(), "body": ""}
    try:
        path_matched = False
        for route_method, pattern, fn in _ROUTES:
            m = pattern.match(path)
            if not m:
                continue
            path_matched = True
            if route_method == method:
                return fn(event, m)
        if path_matched:
            raise HttpError(405, f"Method {method} not allowed for {path}")
        raise HttpError(404, f"Route not found: {method} {path}")
    except HttpError as exc:
        return _response(exc.status, {"error": exc.message})
    except Exception as exc:  # pragma: no cover - last-resort error shape
        log.exception("Unhandled error")
        return _response(500, {"error": f"Internal error: {type(exc).__name__}: {exc}"})
