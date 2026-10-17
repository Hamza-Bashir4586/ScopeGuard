"""Worker Lambda: invoked asynchronously by the API with {"scan_id": "..."}.

Runs the full scan pipeline (Box -> text -> Bedrock -> Python verification -> S3 JSON).
Failures are recorded on the scan META item (status "failed"); async retries are disabled.
"""

from __future__ import annotations

import logging

from scopeguard import analyze

logging.getLogger().setLevel(logging.INFO)


def handler(event: dict, context: object = None) -> dict:
    scan_id = (event or {}).get("scan_id")
    if not scan_id:
        raise ValueError("scan_id is required")
    analyze.run_scan(scan_id)
    return {"scan_id": scan_id}
