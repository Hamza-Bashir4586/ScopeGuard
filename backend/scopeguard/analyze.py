"""Scan pipeline run by the worker: list -> download -> extract text -> LLM steps -> verify -> store."""

from __future__ import annotations

import logging
import traceback
from dataclasses import dataclass
from typing import Optional

from . import box_client, llm as llm_mod
from .extract import extract_pages
from .serialize import default_review, now_iso
from .store import BaseStore, get_store
from .validate import Fact, box_url, check_quote, evidence_id_for, validate_finding, validate_timeline

log = logging.getLogger(__name__)


@dataclass
class LoadedDoc:
    ref: box_client.SourceFileRef
    pages: list[str]
    doc_type: str = "other"


def _progress(store: BaseStore, scan_id: str, stage: str, progress: int) -> None:
    log.info("[%s] %s (%s%%)", scan_id, stage, progress)
    store.update_scan(scan_id, stage=stage, progress=int(progress))


def run_scan(scan_id: str, store: Optional[BaseStore] = None, source=None, llm=None) -> None:
    store = store or get_store()
    try:
        source = source or box_client.get_source()
        llm = llm or llm_mod.get_llm()
        _run(scan_id, store, source, llm)
    except Exception as exc:  # report every failure on the scan item
        log.error("Scan %s failed: %s\n%s", scan_id, exc, traceback.format_exc())
        try:
            store.update_scan(scan_id, status="failed", error=f"{type(exc).__name__}: {exc}"[:2000], finished_at=now_iso())
        except Exception:  # pragma: no cover
            log.exception("Could not record failure for scan %s", scan_id)


def _run(scan_id: str, store: BaseStore, source, llm) -> None:
    store.update_scan(scan_id, status="running", stage="Listing files", progress=2, error=None)

    # ---- 1. list + download + extract text
    refs = source.list_files()
    if not refs:
        raise RuntimeError("No .pdf, .txt or .md files found in the project folder")
    docs: list[LoadedDoc] = []
    for i, ref in enumerate(refs, start=1):
        _progress(store, scan_id, f"Reading files ({i}/{len(refs)})", 2 + int(13 * i / len(refs)))
        try:
            pages = extract_pages(ref.name, source.download(ref))
        except Exception as exc:
            log.warning("Skipping %s: %s", ref.name, exc)
            continue
        docs.append(LoadedDoc(ref=ref, pages=pages))
        store.put_file(scan_id, _file_item(ref, "other", len(pages)))

    # ---- 2. per-document classification + fact extraction, then quote verification
    facts: dict[str, Fact] = {}
    for i, doc in enumerate(docs, start=1):
        _progress(store, scan_id, f"Extracting facts ({i}/{len(docs)})", 15 + int(50 * (i - 1) / max(1, len(docs))))
        if not any(p.strip() for p in doc.pages):
            log.warning("No extractable text in %s (scanned PDF?)", doc.ref.name)
            continue
        extraction = llm.extract_document(doc.ref.name, doc.pages)
        doc.doc_type = extraction.doc_type
        store.put_file(scan_id, _file_item(doc.ref, doc.doc_type, len(doc.pages)))
        for n, ef in enumerate(extraction.facts, start=1):
            qc = check_quote(ef.quote, doc.pages, ef.page)
            fact_id = f"F{i:02d}-{n:03d}"
            facts[fact_id] = Fact(
                fact_id=fact_id, box_file_id=doc.ref.box_file_id, box_version_id=doc.ref.box_version_id,
                file_name=doc.ref.name, doc_type=doc.doc_type, kind=ef.kind, page=qc.page, quote=ef.quote,
                summary=ef.summary, verified=qc.verified, date=ef.date or extraction.doc_date, topic=ef.topic,
                person=ef.person, qty=ef.qty, unit=ef.unit, rate=ef.rate, amount=ef.amount,
                co_number=ef.co_number, co_status=ef.co_status,
            )

    # ---- 3. group / compare / match -> proposed findings
    _progress(store, scan_id, "Comparing against scope and change-order log", 68)
    documents = [{"file_name": d.ref.name, "doc_type": d.doc_type, "page_count": len(d.pages)} for d in docs]
    proposal = llm.propose_findings(list(facts.values()), documents) if facts else None
    proposed = proposal.findings if proposal else []

    # ---- 4. verify each finding in Python, then draft narrative from verified facts only
    seen: set[str] = set()
    for i, pf in enumerate(proposed, start=1):
        _progress(store, scan_id, f"Drafting findings ({i}/{len(proposed)})", 75 + int(22 * (i - 1) / max(1, len(proposed))))
        ai = validate_finding(scan_id, pf, facts)
        if ai["finding_id"] in seen:
            continue
        seen.add(ai["finding_id"])
        evidence_ids = {e["evidence_id"] for e in ai["evidence"]}
        finding_facts = [f for f in facts.values() if f.verified and evidence_id_for(f) in evidence_ids]
        narrative = llm.draft_narrative({k: ai[k] for k in ("title", "summary", "finding_status", "co_match", "scope_baseline",
                                                            "requested_work", "questions", "ai_cost_items")}, finding_facts)
        ai["ai_draft"] = narrative.draft
        ai["timeline"] = validate_timeline(narrative.timeline, {f.fact_id: f for f in finding_facts}, ai)
        store.upsert_finding_ai(scan_id, ai["finding_id"], ai, default_review(), now_iso())

    store.update_scan(scan_id, status="completed", stage=f"Completed - {len(seen)} finding(s)", progress=100, finished_at=now_iso())


def _file_item(ref: box_client.SourceFileRef, doc_type: str, page_count: int) -> dict:
    return {
        "box_file_id": ref.box_file_id,
        "box_version_id": ref.box_version_id,
        "name": ref.name,
        "doc_type": doc_type,
        "page_count": int(page_count),
        "box_url": box_url(ref.box_file_id),
    }
