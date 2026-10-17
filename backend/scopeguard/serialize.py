"""Shared response shapes for docs/api-contract-v2.md (scan summaries, stages, timestamps)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

STAGE_LABELS = {
    "queued": "Queued",
    "reading_box_records": "Reading Box records",
    "connecting_evidence": "Connecting evidence",
    "checking_billing": "Checking billing",
    "findings_ready": "Findings ready",
}
FINDING_TYPES = ("approved_unbilled", "scope_change_proposed", "authorization_unresolved", "invoice_balance_unresolved",
                 "needs_information")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def scan_summary(meta: dict, counts: Optional[dict]) -> dict:
    return {
        "scan_id": meta["scan_id"],
        "project_id": meta.get("project_id", ""),
        "status": meta.get("status", "queued"),
        "stage_id": meta.get("stage_id", "queued"),
        "stage": meta.get("stage") or STAGE_LABELS.get(meta.get("stage_id", "queued"), ""),
        "progress": max(0, min(100, _int(meta.get("progress")))),
        "error": meta.get("error"),
        "error_code": meta.get("error_code"),
        "cutoff_date": meta.get("cutoff_date"),
        "started_at": meta.get("started_at"),
        "updated_at": meta.get("updated_at") or meta.get("started_at"),
        "finished_at": meta.get("finished_at"),
        "summary": counts if meta.get("status") == "completed" else None,
    }
