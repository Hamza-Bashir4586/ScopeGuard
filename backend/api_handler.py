"""API Lambda (API Gateway HTTP API, payload v2). Implements docs/api-contract-v2.md:

  GET   /projects                                                  -> {"projects": ProjectCard[]}
  GET   /projects/{project_id}                                     -> ProjectDetail
  GET   /projects/{project_id}/records?category_id=                -> {"records", "categories", "total"}
  POST  /projects/{project_id}/records                             -> 201 SourceRecord (multipart, field "file")
  GET   /projects/{project_id}/records/{file_id}/content           -> PDF / text bytes (base64 proxy response)
  GET   /projects/{project_id}/records/{file_id}/pages/{page}      -> RecordPage
  POST  /scans                                                     -> 202 {"scan_id", "project_id", "status"}
  GET   /scans?project_id=&limit=                                  -> {"scans": ScanSummary[]}
  GET   /scans/{scan_id}                                           -> Scan
  PATCH /findings/{finding_id}                                     -> Finding (project-level review)
  GET   /projects/{project_id}/invoice-draft                       -> InvoiceDraft
  PATCH /projects/{project_id}/invoice-draft                       -> InvoiceDraft
  POST  /projects/{project_id}/invoice-generations                 -> 201 InvoiceGeneration
  GET   /projects/{project_id}/invoice-generations/{gid}           -> InvoiceGeneration
  GET   /projects/{project_id}/invoice-generations/{gid}/content   -> PDF attachment
  POST  /projects/{project_id}/invoice-generations/{gid}/save-to-box -> InvoiceGeneration (box set)
  POST  /packets                                                   -> 201 Packet (v1 compatibility)

Errors: {"error", "code"?, "details"?}; never raw SDK text.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from datetime import datetime, timezone
from urllib.parse import parse_qs

from scopeguard import box_client, config, demo, invoice, projects, records, reviews, scans
from scopeguard.box_client import SourceError
from scopeguard.errors import ApiError, bad_request, conflict, not_found
from scopeguard.packet import packet_filename, render_packet
from scopeguard.store import BaseStore, get_store

log = logging.getLogger()
log.setLevel(logging.INFO)

HttpError = ApiError  # backwards-compatible name


def _cors_headers() -> dict:
    return {
        "Access-Control-Allow-Origin": config.allowed_origin(),
        "Access-Control-Allow-Methods": "GET,POST,PATCH,OPTIONS",
        "Access-Control-Allow-Headers": "content-type",
        "Access-Control-Expose-Headers": "content-disposition",
    }


def _response(status: int, body: object | None) -> dict:
    headers = {"Content-Type": "application/json", **_cors_headers()}
    return {"statusCode": status, "headers": headers, "body": "" if body is None else json.dumps(body)}


def _binary(status: int, data: bytes, headers: dict) -> dict:
    return {"statusCode": status, "headers": {**headers, **_cors_headers()}, "isBase64Encoded": True,
            "body": base64.b64encode(data).decode("ascii")}


def _raw_body(event: dict) -> bytes:
    raw = event.get("body")
    if raw in (None, ""):
        return b""
    if event.get("isBase64Encoded"):
        return base64.b64decode(raw)
    return raw.encode("utf-8") if isinstance(raw, str) else raw


def _json_body(event: dict) -> dict:
    raw = _raw_body(event)
    if not raw:
        return {}
    try:
        body = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise bad_request("Request body must be valid JSON", "malformed_json")
    if not isinstance(body, dict):
        raise bad_request("Request body must be a JSON object", "malformed_json")
    return body


def _params(event: dict) -> dict:
    params = dict(event.get("queryStringParameters") or {})
    if not params and event.get("rawQueryString"):
        params = {k: v[0] for k, v in parse_qs(event["rawQueryString"], keep_blank_values=True).items()}
    return params


def _header(event: dict, name: str) -> str:
    for k, v in (event.get("headers") or {}).items():
        if k.lower() == name:
            return v or ""
    return ""


def _require_str(body: dict, key: str) -> str:
    v = body.get(key)
    if not isinstance(v, str) or not v.strip():
        raise bad_request(f"'{key}' is required")
    return v.strip()


# ---------------------------------------------------------------- handlers


def get_projects(event: dict) -> dict:
    return _response(200, scans.project_cards())


def get_project(pid: str) -> dict:
    return _response(200, scans.project_detail(pid))


def get_records(event: dict, pid: str) -> dict:
    project = projects.get_project(pid)
    category = _params(event).get("category_id") or None
    return _response(200, records.list_records(project, category))


def post_record(event: dict, pid: str) -> dict:
    project = projects.get_project(pid)
    body = _raw_body(event)
    if len(body) > config.MAX_FILE_BYTES + 64 * 1024:
        raise ApiError(413, "The file is larger than the 4 MiB upload limit.", "file_too_large")
    return _response(201, records.upload_record(project, body, _header(event, "content-type")))


def get_record_content(event: dict, pid: str, file_id: str) -> dict:
    project = projects.get_project(pid)
    data, headers = records.get_content(project, file_id, _params(event).get("version_id") or None)
    return _binary(200, data, headers)


def get_record_page(event: dict, pid: str, file_id: str, page: str) -> dict:
    project = projects.get_project(pid)
    try:
        page_no = int(page)
    except ValueError:
        raise bad_request("page must be a positive integer")
    if page_no < 1:
        raise bad_request("page must be a positive integer")
    p = _params(event)
    return _response(200, records.get_page(project, file_id, page_no, p.get("version_id") or None, p.get("scan_id") or None,
                                           p.get("finding_id") or None))


def post_scans(event: dict) -> dict:
    return _response(202, scans.start_scan(_json_body(event)))


def list_scans(event: dict) -> dict:
    params = _params(event)
    try:
        limit = max(1, min(100, int(params.get("limit") or 20)))
    except ValueError:
        raise bad_request("limit must be an integer")
    return _response(200, scans.list_scans(params.get("project_id") or None, limit))


def get_scan(scan_id: str) -> dict:
    return _response(200, scans.scan_view(scan_id))


def patch_finding(event: dict, finding_id: str) -> dict:
    return _response(200, reviews.patch_finding(finding_id, _json_body(event)))


def get_draft(pid: str) -> dict:
    projects.get_project(pid)
    return _response(200, reviews.draft_view(pid))


def patch_draft(event: dict, pid: str) -> dict:
    projects.get_project(pid)
    return _response(200, reviews.patch_draft(pid, _json_body(event)))


def post_generation(event: dict, pid: str) -> dict:
    return _response(201, invoice._public(invoice.create_generation(pid, _json_body(event))))


def get_generation(pid: str, gid: str) -> dict:
    return _response(200, invoice._public(invoice.get_generation(pid, gid)))


def get_generation_content(pid: str, gid: str) -> dict:
    data, m = invoice.generation_pdf(pid, gid)
    return _binary(200, data, {"Content-Type": "application/pdf",
                               "Content-Disposition": records.content_disposition("attachment", m["file_name"]),
                               "X-Content-Type-Options": "nosniff"})


def post_save_to_box(event: dict, pid: str, gid: str) -> dict:
    _json_body(event)
    return _response(200, invoice._public(invoice.save_to_box(pid, gid)))


def post_packets(event: dict) -> dict:
    """v1 compatibility: one reviewed finding -> change-request packet PDF in the Box output folder."""
    body = _json_body(event)
    scan_id = _require_str(body, "scan_id")
    finding_id = _require_str(body, "finding_id")
    store = get_store()
    if store.get_scan_meta(scan_id) is None:
        raise not_found(f"Scan {scan_id} not found", "scan_not_found")
    scan = scans.scan_view(scan_id)
    finding = next((f for f in scan["findings"] if f["finding_id"] == finding_id), None)
    if finding is None:
        raise not_found(f"Finding {finding_id} not found in scan {scan_id}", "finding_not_found")
    if finding["review"]["status"] != "reviewed":
        raise conflict("Finding must be reviewed before a packet can be generated", "not_reviewed")
    now = datetime.now(timezone.utc)
    pdf = render_packet(finding, scan["files"], now)
    try:
        uploaded = box_client.get_source().upload_packet(packet_filename(finding["title"], now), pdf)
    except SourceError as exc:
        raise ApiError(502, str(exc), "box_unavailable") from None
    created_at = now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    packet = {"box_file_id": uploaded.file_id, "box_url": uploaded.url, "created_at": created_at}
    store.put_json(BaseStore.scan_key(scan_id, f"packets/{finding_id}/{created_at}.json"), packet)
    return _response(201, packet)


# ---------------------------------------------------------------- router

_P = r"([A-Za-z0-9_-]+)"
_ROUTES = [
    ("GET", re.compile(r"^/projects$"), lambda e, m: get_projects(e)),
    ("GET", re.compile(rf"^/projects/{_P}$"), lambda e, m: get_project(m.group(1))),
    ("POST", re.compile(rf"^/projects/{_P}/demo-reset$"), lambda e, m: _response(200, demo.reset(m.group(1), _json_body(e)))),
    ("GET", re.compile(rf"^/projects/{_P}/records$"), lambda e, m: get_records(e, m.group(1))),
    ("POST", re.compile(rf"^/projects/{_P}/records$"), lambda e, m: post_record(e, m.group(1))),
    ("GET", re.compile(rf"^/projects/{_P}/records/{_P}/content$"), lambda e, m: get_record_content(e, m.group(1), m.group(2))),
    ("GET", re.compile(rf"^/projects/{_P}/records/{_P}/pages/([^/]+)$"),
     lambda e, m: get_record_page(e, m.group(1), m.group(2), m.group(3))),
    ("GET", re.compile(rf"^/projects/{_P}/invoice-draft$"), lambda e, m: get_draft(m.group(1))),
    ("PATCH", re.compile(rf"^/projects/{_P}/invoice-draft$"), lambda e, m: patch_draft(e, m.group(1))),
    ("POST", re.compile(rf"^/projects/{_P}/invoice-generations$"), lambda e, m: post_generation(e, m.group(1))),
    ("GET", re.compile(rf"^/projects/{_P}/invoice-generations/{_P}$"), lambda e, m: get_generation(m.group(1), m.group(2))),
    ("GET", re.compile(rf"^/projects/{_P}/invoice-generations/{_P}/content$"),
     lambda e, m: get_generation_content(m.group(1), m.group(2))),
    ("POST", re.compile(rf"^/projects/{_P}/invoice-generations/{_P}/save-to-box$"),
     lambda e, m: post_save_to_box(e, m.group(1), m.group(2))),
    ("POST", re.compile(r"^/scans$"), lambda e, m: post_scans(e)),
    ("GET", re.compile(r"^/scans$"), lambda e, m: list_scans(e)),
    ("GET", re.compile(rf"^/scans/{_P}$"), lambda e, m: get_scan(m.group(1))),
    ("PATCH", re.compile(rf"^/findings/{_P}$"), lambda e, m: patch_finding(e, m.group(1))),
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
            raise ApiError(405, f"Method {method} not allowed for {path}", "method_not_allowed")
        raise ApiError(404, f"Route not found: {method} {path}", "route_not_found")
    except ApiError as exc:
        return _response(exc.status, exc.body())
    except Exception:  # pragma: no cover - last-resort error shape, no internals leaked
        log.exception("Unhandled error")
        return _response(500, {"error": "Internal error. Try again.", "code": "internal_error"})
