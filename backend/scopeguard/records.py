"""Source records of a configured project: inventory, categories, upload, pinned content and page text.

Only records inventoried from the project's configured folder (or versions pinned by one of its scans) are
ever served; arbitrary file IDs, folders or URLs are rejected [VIEW-02, DOC-01].
"""

from __future__ import annotations

import base64
import hashlib
import io
import re
import unicodedata
from email import policy
from email.parser import BytesParser
from typing import Any, Optional

from . import box_client, config
from .box_client import SourceError, SourceFileRef, SourceUnavailable
from .errors import ApiError, bad_request, not_found
from .extract import extract_pages
from .store import BaseStore, get_store

CATEGORY_LABELS = {
    "original_agreement": "Original agreement",
    "approvals": "Approvals",
    "field_records": "Field records",
    "billing": "Billing",
    "change_instructions": "Change instructions",
    "pricing": "Pricing",
    "payments": "Payments",
    "correspondence": "Correspondence",
    "unclassified": "Unclassified",
}
CATEGORY_ORDER = list(CATEGORY_LABELS)

# Equivalent labels the model may produce -> stable category IDs [DOC-08].
_CATEGORY_ALIASES = {
    "agreement": "original_agreement", "contract": "original_agreement", "original_contract": "original_agreement",
    "scope": "original_agreement", "subcontract": "original_agreement", "exhibit": "original_agreement",
    "approval": "approvals", "change_order": "approvals", "change_orders": "approvals",
    "field_record": "field_records", "field": "field_records", "timesheet": "field_records", "timesheets": "field_records",
    "daily_report": "field_records", "completion": "field_records",
    "invoice": "billing", "invoices": "billing",
    "instruction": "change_instructions", "instructions": "change_instructions", "site_instruction": "change_instructions",
    "directive": "change_instructions", "change_instruction": "change_instructions",
    "quote": "pricing", "quotation": "pricing", "quotations": "pricing", "proposal": "pricing",
    "payment": "payments", "remittance": "payments", "remittances": "payments",
    "email": "correspondence", "letter": "correspondence", "other": "unclassified", "unknown": "unclassified",
}

_FILENAME_RULES = [
    (r"agreement|contract|subcontract|exhibit", "original_agreement"),
    (r"\bco[- ]?\d+|change approval|change order", "approvals"),
    (r"remittance|payment|\brem[- ]?\d+", "payments"),
    (r"\binv[- ]?\d+|invoice", "billing"),
    (r"\bsi[- ]?\d+|instruction|directive|\brfi\b", "change_instructions"),
    (r"quot|pricing|proposal|rate sheet", "pricing"),
    (r"completion|timesheet|daily|field|log", "field_records"),
]


def normalize_category(category_id: Optional[str], label: Optional[str] = None) -> tuple[str, str]:
    raw = re.sub(r"[^a-z0-9]+", "_", (category_id or label or "").lower()).strip("_")
    cid = raw if raw in CATEGORY_LABELS else _CATEGORY_ALIASES.get(raw)
    if cid is None and label:
        lab = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
        cid = lab if lab in CATEGORY_LABELS else _CATEGORY_ALIASES.get(lab)
    if cid is None:
        if raw and len(raw) <= 40:  # keep a generated category, but give it a stable slug ID
            return raw, (label or raw.replace("_", " ")).strip().capitalize()
        return "unclassified", CATEGORY_LABELS["unclassified"]
    return cid, CATEGORY_LABELS[cid]


def category_from_filename(name: str) -> Optional[str]:
    stem = name.rsplit(".", 1)[0].lower()
    for pattern, cid in _FILENAME_RULES:
        if re.search(pattern, stem):
            return cid
    return None


# ---------------------------------------------------------------- inventory


def inventory(source=None) -> list[SourceFileRef]:
    source = source or box_client.get_source()
    try:
        return source.list_files()
    except SourceError as exc:
        raise ApiError(502, str(exc), "box_unavailable") from None


def metadata_key(project_id: str, file_id: str, version_id: str) -> str:
    return BaseStore.record_key(project_id, file_id, version_id, "metadata.json")


def pages_key(project_id: str, file_id: str, version_id: str) -> str:
    return BaseStore.record_key(project_id, file_id, version_id, "pages.json")


def merge_metadata(store: BaseStore, project_id: str, file_id: str, version_id: str, **fields: Any) -> dict:
    def mutate(cur: Optional[dict]) -> dict:
        cur = cur or {"file_id": file_id, "version_id": version_id}
        cur.update({k: v for k, v in fields.items() if v is not None})
        return cur

    return store.update_json(metadata_key(project_id, file_id, version_id), mutate)  # type: ignore[return-value]


def classify(ref: SourceFileRef, meta: Optional[dict], seeded_ids: set[str]) -> tuple[str, str, str]:
    """(category_id, label, basis): AI category from an audit, else seeded baseline, else filename rules."""
    if meta and meta.get("category_id") and meta.get("classification_basis") == "ai":
        cid, label = normalize_category(meta["category_id"], meta.get("category_label"))
        return cid, label, "ai"
    if ref.box_file_id in seeded_ids:
        return "original_agreement", CATEGORY_LABELS["original_agreement"], "seeded"
    if not ref.supported:
        return "unclassified", CATEGORY_LABELS["unclassified"], "unclassified"
    cid = category_from_filename(ref.name)
    if cid:
        return cid, CATEGORY_LABELS[cid], "filename"
    return "unclassified", CATEGORY_LABELS["unclassified"], "unclassified"


def source_record(ref: SourceFileRef, *, meta: Optional[dict], outcome: Optional[dict], seeded_ids: set[str]) -> dict:
    cid, label, basis = classify(ref, meta, seeded_ids)
    if not ref.supported:
        status, warnings = "unsupported", ["Unsupported file type; only PDF, TXT and Markdown records are analyzed."]
    elif outcome:
        status, warnings = outcome.get("ingestion_status", "ingested"), list(outcome.get("warnings") or [])
    else:
        status, warnings = "pending", []
    page_count = (meta or {}).get("page_count")
    return {
        "file_id": ref.box_file_id,
        "version_id": ref.box_version_id,
        "name": ref.name,
        "extension": ref.extension,
        "mime_type": ref.mime_type,
        "size_bytes": int(ref.size_bytes),
        "modified_at": ref.modified_at,
        "document_date": (meta or {}).get("document_date"),
        "page_count": int(page_count) if page_count is not None else None,
        "category_id": cid,
        "category_label": label,
        "classification_basis": basis,
        "ingestion_status": status,
        "warnings": warnings,
        "box_url": box_client.box_url(ref.box_file_id),
    }


def list_records(project: dict, category_id: Optional[str] = None, refs: Optional[list[SourceFileRef]] = None) -> dict:
    from . import projects, scans

    store = get_store()
    refs = refs if refs is not None else inventory()
    seeded = projects.seeded_record_ids(project, refs)
    metas = store.get_many_json([metadata_key(project["project_id"], r.box_file_id, r.box_version_id) for r in refs])
    latest = scans.latest_completed_scan(store, project["project_id"])
    outcomes = scans.file_outcomes(store, latest["scan_id"]) if latest else {}
    records = []
    for r in refs:
        meta = metas.get(metadata_key(project["project_id"], r.box_file_id, r.box_version_id))
        outcome = outcomes.get(r.box_file_id)
        if outcome and outcome.get("version_id") != r.box_version_id:
            outcome = None  # a newer version was uploaded after the audit: pending again
        records.append(source_record(r, meta=meta, outcome=outcome, seeded_ids=seeded))
    counts: dict[str, dict] = {}
    for rec in records:
        c = counts.setdefault(rec["category_id"], {"category_id": rec["category_id"], "label": rec["category_label"], "count": 0})
        c["count"] += 1
    categories = sorted(counts.values(), key=lambda c: (CATEGORY_ORDER.index(c["category_id"]) if c["category_id"] in CATEGORY_ORDER else 50, c["label"]))
    filtered = [r for r in records if not category_id or r["category_id"] == category_id]
    return {"records": filtered, "categories": categories, "total": len(records)}


# ---------------------------------------------------------------- resolve / pages / content


def resolve(project: dict, file_id: str, version_id: Optional[str], refs: Optional[list[SourceFileRef]] = None) -> dict:
    """Resolve an allowlisted record version. Allowed: the current version of an inventoried record, or a
    version pinned by one of this project's scans (metadata exists). Anything else -> 404."""
    store = get_store()
    refs = refs if refs is not None else inventory()
    current = next((r for r in refs if r.box_file_id == file_id), None)
    vid = version_id or (current.box_version_id if current else None)
    if not vid:
        raise not_found("Unknown record for this project", "record_not_found")
    meta = store.get_json(metadata_key(project["project_id"], file_id, vid))
    if current is not None and vid == current.box_version_id:
        return {"ref": current, "version_id": vid, "name": current.name, "meta": meta, "is_current": True, "in_folder": True}
    if meta is None:
        raise not_found("Unknown record version for this project", "record_not_found")
    return {"ref": current, "version_id": vid, "name": meta.get("name") or (current.name if current else file_id),
            "meta": meta, "is_current": False, "in_folder": current is not None}


def load_pages(store: BaseStore, project_id: str, file_id: str, version_id: str, name: str, source=None) -> list[str]:
    """Extracted page text for a pinned version, cached at projects/<id>/records/<file>/<version>/pages.json."""
    key = pages_key(project_id, file_id, version_id)
    cached = store.get_json(key)
    if cached is not None:
        return list(cached["pages"])
    source = source or box_client.get_source()
    data = source.download(file_id, version_id)
    if len(data) > config.MAX_FILE_BYTES:
        raise ValueError("file_too_large")
    pages = extract_pages(name, data)
    store.put_json(key, {"file_id": file_id, "version_id": version_id, "name": name, "pages": pages,
                         "content_sha256": hashlib.sha256(data).hexdigest()})
    merge_metadata(store, project_id, file_id, version_id, name=name, page_count=len(pages),
                   content_sha256=hashlib.sha256(data).hexdigest())
    return pages


def get_page(project: dict, file_id: str, page: int, version_id: Optional[str], scan_id: Optional[str],
             finding_id: Optional[str]) -> dict:
    from . import projects, scans

    store = get_store()
    refs = inventory()
    info = resolve(project, file_id, version_id, refs)
    vid = info["version_id"]
    pid = project["project_id"]
    available, warning = True, None
    try:
        pages = load_pages(store, pid, file_id, vid, info["name"])
    except SourceUnavailable:
        available = False
        cached = store.get_json(pages_key(pid, file_id, vid))
        pages = list(cached["pages"]) if cached else []
        warning = ("This record version can no longer be opened in Box; showing the text captured during the audit."
                   if pages else "This record version can no longer be opened in Box.")
    except SourceError as exc:
        raise ApiError(502, str(exc), "box_unavailable") from None
    except ValueError:
        raise ApiError(413, "This record is larger than the 4 MiB viewing limit.", "file_too_large") from None
    if available and not info["is_current"]:
        try:
            still_open = info["in_folder"] and box_client.get_source().version_available(file_id, vid)
        except SourceError:
            still_open = True  # unknown: do not claim the source is gone
        if still_open:
            warning = "Viewing the version analyzed by this audit; a newer version exists in Box."
        else:
            available = False
            warning = ("This record version can no longer be opened in Box; showing the text captured during the audit."
                       if info["in_folder"] else "The record was removed from the project folder; showing the text captured during the audit.")
    page_count = len(pages)
    if page_count and not 1 <= page <= page_count:
        raise not_found(f"Page {page} does not exist (1-{page_count})", "page_not_found")
    text = pages[page - 1] if page_count else ""
    meta = info["meta"] or store.get_json(metadata_key(pid, file_id, vid)) or {}
    ref = info["ref"] or SourceFileRef(file_id, vid, info["name"])
    seeded = projects.seeded_record_ids(project, refs)
    cid, label, _basis = classify(ref, meta, seeded)
    highlights = scans.highlights_for(store, project, file_id, vid, page, text, scan_id, finding_id, refs)
    return {
        "file_id": file_id, "version_id": vid, "name": info["name"], "category_label": label,
        "document_date": meta.get("document_date"), "page": page, "page_count": max(page_count, 1) if available else page_count,
        "text": text, "highlights": highlights, "available": available, "warning": warning,
    }


def content_disposition(kind: str, name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    ascii_name = re.sub(r'[^A-Za-z0-9._ ()-]+', "-", ascii_name).strip() or "document"
    from urllib.parse import quote

    return f"{kind}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name, safe='')}"


def get_content(project: dict, file_id: str, version_id: Optional[str]) -> tuple[bytes, dict]:
    info = resolve(project, file_id, version_id)
    try:
        data = box_client.get_source().download(file_id, info["version_id"])
    except SourceUnavailable as exc:
        raise ApiError(404, str(exc), "source_unavailable") from None
    except SourceError as exc:
        raise ApiError(502, str(exc), "box_unavailable") from None
    if len(data) > config.MAX_FILE_BYTES:
        raise ApiError(413, "This record is larger than the 4 MiB viewing limit.", "file_too_large")
    name = info["name"]
    if name.lower().endswith(".pdf"):
        ctype = "application/pdf"
    elif name.lower().endswith((".txt", ".md")):
        ctype = "text/plain; charset=utf-8"
    else:
        raise ApiError(415, "Only PDF, TXT and Markdown records can be viewed.", "unsupported_media_type")
    return data, {"Content-Type": ctype, "Content-Disposition": content_disposition("inline", name),
                  "Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"}


# ---------------------------------------------------------------- upload [DOC-04, DOC-05]


def sanitize_filename(name: str) -> str:
    name = (name or "").replace("\\", "/").split("/")[-1]
    name = unicodedata.normalize("NFC", name)
    name = "".join(ch for ch in name if ch.isprintable())
    name = re.sub(r'[<>:"|?*]', "-", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    if len(name) > 150:
        stem, _, ext = name.rpartition(".")
        name = stem[: 150 - len(ext) - 1].rstrip() + "." + ext
    if not name or name.startswith(".") or name.lower() in config.LOCAL_SKIP_NAMES or name == config.PACKETS_FOLDER_NAME:
        raise bad_request("The file name is not usable; rename the file and try again.", "invalid_filename")
    return name


def parse_multipart(body: bytes, content_type: str) -> tuple[str, bytes]:
    if not content_type.lower().startswith("multipart/form-data") or "boundary=" not in content_type.lower():
        raise bad_request("Upload must be multipart/form-data with a 'file' field.", "malformed_upload")
    head = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("latin-1", "replace")
    msg = BytesParser(policy=policy.HTTP).parsebytes(head + body)
    if not msg.is_multipart():
        raise bad_request("Upload must be multipart/form-data with a 'file' field.", "malformed_upload")
    parts = [p for p in msg.iter_parts() if p.get_param("name", header="content-disposition") == "file"]
    if len(parts) != 1:
        raise bad_request("Upload exactly one file in the 'file' field.", "malformed_upload")
    part = parts[0]
    filename = part.get_filename()
    if not filename:
        raise bad_request("The uploaded file has no file name.", "malformed_upload")
    data = part.get_payload(decode=True) or b""
    return filename, data


def validate_upload(filename: str, data: bytes) -> tuple[str, list[str]]:
    name = sanitize_filename(filename)
    if not name.lower().endswith(config.SUPPORTED_EXTENSIONS):
        raise ApiError(415, "Unsupported file type. Upload a PDF, TXT or Markdown file.", "unsupported_media_type")
    if not data:
        raise bad_request("The uploaded file is empty.", "empty_file")
    if len(data) > config.MAX_FILE_BYTES:
        raise ApiError(413, "The file is larger than the 4 MiB upload limit.", "file_too_large")
    warnings: list[str] = []
    if name.lower().endswith(".pdf"):
        if not data.lstrip()[:5] == b"%PDF-":
            raise bad_request("The file is not a valid PDF.", "malformed_file")
        try:
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(data))
            n = len(reader.pages)
            text = "".join((reader.pages[i].extract_text() or "") for i in range(min(n, 3)))
        except Exception:
            raise bad_request("The PDF could not be read. It may be damaged or encrypted.", "malformed_file") from None
        if n == 0:
            raise bad_request("The PDF has no pages.", "malformed_file")
        if not text.strip():
            warnings.append("No extractable text found (scanned PDF?). OCR is not supported, so it may not be analyzed.")
    else:
        try:
            data.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise bad_request("Text and Markdown files must be UTF-8 encoded.", "malformed_file") from None
    return name, warnings


def upload_record(project: dict, event_body: bytes, content_type: str) -> dict:
    from . import projects

    filename, data = parse_multipart(event_body, content_type)
    name, warnings = validate_upload(filename, data)
    source = box_client.get_source()
    try:
        ref = source.upload_record(name, data)
    except SourceError as exc:
        raise ApiError(502, str(exc), "box_unavailable") from None
    store = get_store()
    merge_metadata(store, project["project_id"], ref.box_file_id, ref.box_version_id, name=ref.name,
                   original_filename=filename, uploaded=True, content_sha256=hashlib.sha256(data).hexdigest())
    rec = source_record(ref, meta=None, outcome=None, seeded_ids=projects.seeded_record_ids(project, [ref]))
    rec["warnings"] = warnings
    return rec


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")
