"""Scan worker: manifest -> pinned Box versions -> page text -> Bedrock facts -> Python verification ->
reconciliation -> S3 JSON. Invoked asynchronously with {"scan_id"} only [AUD-02].

* Claims scans/<id>/execution-lease.json with a create-only write: a duplicate async delivery exits without
  re-running anything [AUD-11].
* Persists structured stages with real events (the record being read, the comparison being made) [AUD-06/07].
* Coverage (complete + warnings) is kept separate from execution status [AUD-05].
* The project's latest completed scan pointer moves only after all results are written [AUD-09]; a failed
  refresh leaves the previous results and reviews untouched.
* Extraction is cached by file ID + version ID + prompt version + model [§8]; reuse is reported honestly.
"""

from __future__ import annotations

import logging
import re
import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional

from . import box_client, config, projects, records
from . import llm as llm_mod
from .box_client import SourceError, SourceFileRef, SourceUnavailable
from .models import DocumentExtraction
from .reconcile import Fact, agreement_from_facts, reconcile, short_hash
from .scans import by_type_counts, release_lease
from .serialize import STAGE_LABELS, now_iso
from .store import BaseStore, get_store
from .validate import check_quote

log = logging.getLogger(__name__)


class ScanFailure(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class _BudgetExceeded(Exception):
    pass


class _Progress:
    def __init__(self, store: BaseStore, scan_id: str) -> None:
        self.store = store
        self.scan_id = scan_id
        self.lock = threading.Lock()
        self.progress = 0

    def update(self, **fields: Any) -> None:
        with self.lock:
            if "progress" in fields:
                fields["progress"] = self.progress = max(self.progress, int(fields["progress"]))
            if "stage_id" in fields and "stage" not in fields:
                fields["stage"] = STAGE_LABELS[fields["stage_id"]]
            fields["updated_at"] = now_iso()  # heartbeat
            self.store.update_scan(self.scan_id, **fields)

    def event(self, stage_id: str, progress: float, message: str, source: Optional[dict] = None, **extra: Any) -> None:
        log.info("[%s] %s (%s%%)", self.scan_id, message, int(progress))
        self.update(stage_id=stage_id, progress=int(progress), current_event={"message": message, "source": source}, **extra)


def _file_source(f: dict, page: Optional[int] = None) -> dict:
    return {"file_id": f["file_id"], "version_id": f["version_id"], "file_name": f["name"], "page": page, "quote": None,
            "available": True}


def _ref(f: dict) -> SourceFileRef:
    return SourceFileRef(f["file_id"], f["version_id"], f["name"], int(f.get("size_bytes") or 0), f.get("modified_at"))


def run_scan(scan_id: str, store: Optional[BaseStore] = None, source=None, llm=None) -> None:
    store = store or get_store()
    meta = store.get_scan_meta(scan_id)
    if meta is None:
        log.error("Scan %s not found", scan_id)
        return
    if not store.create_json(BaseStore.scan_key(scan_id, "execution-lease.json"),
                             {"scan_id": scan_id, "claimed_at": now_iso(), "token": uuid.uuid4().hex}):
        log.warning("Scan %s already claimed by another worker invocation; skipping duplicate delivery", scan_id)
        return
    if meta.get("status") != "queued":
        log.warning("Scan %s is %s; not running", scan_id, meta.get("status"))
        return
    try:
        _run(scan_id, store, source or box_client.get_source(), llm or llm_mod.get_llm())
    except ScanFailure as exc:
        _fail(store, scan_id, exc.code, exc.message)
    except Exception as exc:  # never surface raw SDK text to the UI
        log.error("Scan %s failed: %s\n%s", scan_id, type(exc).__name__, traceback.format_exc())
        _fail(store, scan_id, "internal_error", "The audit failed unexpectedly. Run it again.")
    finally:
        release_lease(store, meta["project_id"], scan_id)


def _fail(store: BaseStore, scan_id: str, code: str, message: str) -> None:
    try:
        now = now_iso()
        store.update_scan(scan_id, status="failed", error_code=code, error=message, finished_at=now, updated_at=now,
                          current_event=None)
    except Exception:  # pragma: no cover
        log.exception("Could not record failure for scan %s", scan_id)


def _run(scan_id: str, store: BaseStore, source, llm) -> None:
    manifest = store.get_json(BaseStore.scan_key(scan_id, "manifest.json"))
    if not manifest:
        raise ScanFailure("internal_error", "The audit input manifest is missing. Run the audit again.")
    pid = manifest["project_id"]
    project = projects.get_project(pid)
    files = manifest["files"]
    included = [f for f in files if f["included"]]
    P = _Progress(store, scan_id)
    P.update(status="running", stage_id="reading_box_records", progress=3, files_processed=0, files_total=len(included),
             current_event={"message": f"Reading {len(included)} records from Box", "source": None})
    seeded = projects.seeded_record_ids(project, [_ref(f) for f in files])
    outcomes: dict[str, dict] = {}
    file_warnings: list[str] = []
    required_failed = False

    def write_outcome(f: dict, status: str, warnings: list[str], meta: Optional[dict] = None) -> None:
        rec = records.source_record(_ref(f), meta=meta, outcome={"ingestion_status": status, "warnings": warnings}, seeded_ids=seeded)
        outcomes[f["file_id"]] = rec
        store.put_json(BaseStore.scan_key(scan_id, f"files/{f['file_id']}.json"), rec)

    # ---- 1. reading_box_records: pinned versions -> page text
    docs: list[tuple[dict, list[str]]] = []
    total_pages = 0
    for i, f in enumerate(included):
        P.event("reading_box_records", 3 + 30 * i / max(1, len(included)), f"Reading {f['name']}", _file_source(f))
        warning = None
        try:
            pages = records.load_pages(store, pid, f["file_id"], f["version_id"], f["name"], source)
        except SourceUnavailable:
            pages, warning = None, "This version could not be opened in Box."
        except SourceError:
            pages, warning = None, "Box download failed for this record."
        except ValueError:
            pages, warning = None, "Larger than the 4 MiB per-record limit; not analyzed."
        except Exception as exc:
            log.warning("Extraction failed for %s: %s", f["name"], type(exc).__name__)
            pages, warning = None, "The file could not be read (damaged or unsupported content)."
        if pages is not None and not any(p.strip() for p in pages):
            pages, warning = None, "No extractable text (scanned PDF?). OCR is not supported."
        if pages is not None and total_pages + len(pages) > config.MAX_DATASET_PAGES:
            pages, warning = None, f"Over the {config.MAX_DATASET_PAGES}-page dataset limit; not analyzed."
        if pages is None:
            required_failed = True
            file_warnings.append(f"{f['name']}: {warning}")
            write_outcome(f, "failed", [warning])
        else:
            total_pages += len(pages)
            docs.append((f, pages))
        P.update(files_processed=i + 1)
    for f in files:
        if not f["included"]:
            file_warnings.append(f"{f['name']}: {f.get('reason')}")
            write_outcome(f, f.get("status") or "excluded", [f.get("reason") or "Not analyzed."])
    baseline_id = manifest.get("baseline_file_id")
    if not baseline_id:
        raise ScanFailure("baseline_missing", "The signed agreement record was not found in the project folder.")
    if baseline_id not in {f["file_id"] for f, _ in docs}:
        raise ScanFailure("baseline_unreadable", "The signed agreement could not be read, so scope cannot be compared.")

    # ---- 2. connecting_evidence: per-record extraction (cached) + quote verification
    tag = re.sub(r"[^A-Za-z0-9._-]+", "_", getattr(llm, "label", "llm"))[:80]
    lock = threading.Lock()
    done = [0]

    def extract_one(f: dict, pages: list[str]) -> tuple[DocumentExtraction, bool]:
        key = BaseStore.record_key(pid, f["file_id"], f["version_id"], f"extraction-{config.PROMPT_VERSION}-{tag}.json")
        cached = store.get_json(key)
        if cached is not None:
            return DocumentExtraction.model_validate(cached["extraction"]), True
        if sum(len(p) for p in pages) > config.MAX_DOC_CHARS:
            raise _BudgetExceeded()
        ext = llm.extract_document(f["name"], pages)
        store.put_json(key, {"prompt_version": config.PROMPT_VERSION, "model": getattr(llm, "label", ""), "created_at": now_iso(),
                             "extraction": ext.model_dump(mode="json")})
        return ext, False

    P.event("connecting_evidence", 35, f"Extracting facts from {len(docs)} records", None)
    extractions: dict[str, DocumentExtraction] = {}
    with ThreadPoolExecutor(max_workers=min(config.extraction_workers(), len(docs))) as pool:
        futures = {pool.submit(extract_one, f, pages): (f, pages) for f, pages in docs}
        for fut in as_completed(futures):
            f, pages = futures[fut]
            try:
                ext, from_cache = fut.result()
                extractions[f["file_id"]] = ext
                msg = (f"Reused cached extraction for {f['name']}" if from_cache
                       else f"Extracted {len(ext.facts)} facts from {f['name']}")
            except _BudgetExceeded:
                ext, msg = None, f"{f['name']} exceeds the model context budget; not analyzed"
                warn = "Exceeds the model context budget; not analyzed."
            except llm_mod.LLMError:
                ext, msg = None, f"Model output for {f['name']} was invalid after a retry"
                warn = "The model output could not be validated after a retry."
            except Exception as exc:
                log.warning("Model call failed for %s: %s", f["name"], type(exc).__name__)
                ext, msg = None, f"Model call failed for {f['name']}"
                warn = "The model call failed for this record."
            if ext is None:
                if f["file_id"] == baseline_id:
                    raise ScanFailure("model_failure", "The signed agreement could not be analyzed by the model. Try again.")
                required_failed = True
                file_warnings.append(f"{f['name']}: {warn}")
                write_outcome(f, "failed", [warn])
            with lock:
                done[0] += 1
                P.event("connecting_evidence", 35 + 28 * done[0] / max(1, len(docs)), msg, _file_source(f, 1))
    if not extractions:
        raise ScanFailure("model_failure", "No records could be analyzed by the model. Try again.")

    facts: list[Fact] = []
    for idx, (f, pages) in enumerate(docs, start=1):
        ext = extractions.get(f["file_id"])
        if ext is None:
            continue
        cid, clabel = records.normalize_category(ext.category_id, ext.category_label)
        meta = records.merge_metadata(store, pid, f["file_id"], f["version_id"], name=f["name"], page_count=len(pages),
                                      category_id=cid, category_label=clabel, classification_basis="ai",
                                      document_date=ext.doc_date, doc_reference=ext.doc_reference)
        unverified = 0
        for n, ef in enumerate(ext.facts, start=1):
            qc = check_quote(ef.quote, pages, ef.page)
            unverified += 0 if qc.verified else 1
            facts.append(Fact(
                fact_id=f"F{idx:02d}-{n:03d}", file_id=f["file_id"], version_id=f["version_id"], file_name=f["name"],
                category_id=cid, fact_type=ef.fact_type, page=qc.page, quote=ef.quote, summary=ef.summary,
                verified=qc.verified, doc_reference=ext.doc_reference, doc_date=ext.doc_date, date=ef.date or ext.doc_date,
                reference=ef.reference, related_refs=list(ef.related_refs), entity=ef.entity, location=ef.location,
                qty=ef.qty, unit=ef.unit, rate=ef.rate, amount=ef.amount, status=ef.status, all_in=ef.all_in))
        warnings = [f"{unverified} extracted quote(s) could not be verified against the text and were not used."] if unverified else []
        write_outcome(f, "ingested", warnings, meta)
    store.put_json(BaseStore.scan_key(scan_id, "facts.json"), {"facts": [x.to_dict() for x in facts]})

    verified = [x for x in facts if x.verified]
    P.event("connecting_evidence", 66, f"Linking work items across {len(extractions)} records ({len(verified)} verified facts)", None)
    link_warnings: list[str] = []
    try:
        links = llm.propose_links(verified).links if verified else []
    except Exception as exc:
        log.warning("Link proposal failed: %s", type(exc).__name__)
        links = []
        link_warnings.append("Work-item link suggestions were unavailable; records were matched by their references only.")

    # ---- 3. checking_billing: deterministic reconciliation
    cutoff = manifest.get("cutoff_date")
    cutoff_warnings: list[str] = []
    if cutoff:
        late = {x.file_id for x in facts if x.category_id != "original_agreement" and x.doc_date and x.doc_date > cutoff}
        if late:
            names = sorted({x.file_name for x in facts if x.file_id in late})
            cutoff_warnings.append(f"{len(names)} record(s) dated after the cutoff {cutoff} were not compared: {', '.join(names)}.")
            facts = [x for x in facts if x.file_id not in late]
    P.event("checking_billing", 72, "Checking approvals, completed work and invoices", None)
    result = reconcile(pid, facts, links)
    for i, check in enumerate(result.checks):
        P.event("checking_billing", 74 + 20 * (i + 1) / max(1, len(result.checks)), check.message, check.source)
    agreement = agreement_from_facts(facts)
    agreement["baseline_version"] = manifest.get("baseline_version")
    finding_ids = []
    for n, ai in enumerate(result.findings, start=1):
        fid = "f_" + short_hash(scan_id, ai["work_item_id"], length=16)
        ai.update(finding_id=fid, scan_id=scan_id, code=f"D-{n:03d}")
        store.put_json(BaseStore.scan_key(scan_id, f"findings/{fid}/ai.json"), ai)
        finding_ids.append(fid)
    warnings = file_warnings + result.warnings + cutoff_warnings + link_warnings
    complete = not required_failed and all(f["included"] for f in files)
    store.put_json(BaseStore.scan_key(scan_id, "results.json"), {
        "finding_ids": finding_ids, "existing_items": result.existing_items, "agreement": agreement,
        "warnings": warnings, "links_accepted": len(links),
    })
    summary_base = {"file_count": len(files), "by_type": by_type_counts(result.findings),
                    "items": [{"work_item_id": a["work_item_id"], "amount": a["amount"], "finding_type": a["finding_type"]}
                              for a in result.findings]}
    n = len(result.findings)
    now = now_iso()
    P.update(status="completed", stage_id="findings_ready", stage=f"{n} finding{'s' if n != 1 else ''} ready to review",
             progress=100, finished_at=now,
             current_event={"message": f"{n} discrepanc{'ies' if n != 1 else 'y'} ready to review · every finding links to source evidence",
                            "source": None},
             coverage={"complete": complete, "warnings": warnings, "required_failed": required_failed},
             summary_base=summary_base)

    # Only now, with every result object written, point the project at this scan [AUD-09].
    started = manifest.get("requested_at") or now

    def mutate(cur: Optional[dict]) -> Optional[dict]:
        cur = cur or {"project_id": pid}
        if (cur.get("latest_completed_started_at") or "") > started:
            return None  # a newer scan already completed
        cur.update(latest_completed_scan_id=scan_id, latest_completed_started_at=started, latest_completed_at=now)
        return cur

    store.update_json(BaseStore.project_key(pid, "project.json"), mutate)
