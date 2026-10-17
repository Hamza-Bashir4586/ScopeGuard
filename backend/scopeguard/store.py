"""Persistence. Two implementations with the same interface:

* MemoryStore - in-process dict (STORE=memory, local dev + tests)
* S3Store     - JSON objects in S3 (STORE=s3, default)

Logical keys:  PK=SCAN#<id>  SK=META | FILE#<box_file_id> | FINDING#<finding_id> | PACKET#<finding_id>#<ts>

Rules: the worker writes only `ai` on findings (review created once if missing);
PATCH writes only `review`. Numbers are stored as strings or ints, never floats.
"""

from __future__ import annotations

import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from functools import lru_cache
from typing import Any, Optional

from . import config


class NotFound(Exception):
    pass


def _pk(scan_id: str) -> str:
    return f"SCAN#{scan_id}"


class BaseStore:
    def put_scan_meta(self, scan_id: str, data: dict) -> None: ...
    def update_scan(self, scan_id: str, **fields: Any) -> None: ...
    def get_scan_items(self, scan_id: str) -> list[dict]: ...
    def put_file(self, scan_id: str, file: dict) -> None: ...
    def upsert_finding_ai(self, scan_id: str, finding_id: str, ai: dict, default_review: dict, now: str) -> None: ...
    def get_finding(self, scan_id: str, finding_id: str) -> Optional[dict]: ...
    def update_finding_review(self, scan_id: str, finding_id: str, review: dict) -> None: ...
    def put_packet(self, scan_id: str, finding_id: str, packet: dict) -> None: ...
    def list_packets(self, scan_id: str, finding_id: str) -> list[dict]: ...
    def list_scan_metas(self) -> list[dict]: ...


class MemoryStore(BaseStore):
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], dict] = {}
        self._lock = threading.RLock()

    def _put(self, item: dict) -> None:
        with self._lock:
            self._items[(item["PK"], item["SK"])] = copy.deepcopy(item)

    def put_scan_meta(self, scan_id: str, data: dict) -> None:
        self._put({"PK": _pk(scan_id), "SK": "META", "scan_id": scan_id, **data})

    def update_scan(self, scan_id: str, **fields: Any) -> None:
        with self._lock:
            item = self._items.get((_pk(scan_id), "META"))
            if item is None:
                raise NotFound(scan_id)
            item.update(copy.deepcopy(fields))

    def get_scan_items(self, scan_id: str) -> list[dict]:
        pk = _pk(scan_id)
        with self._lock:
            return [copy.deepcopy(v) for (p, _s), v in sorted(self._items.items()) if p == pk]

    def put_file(self, scan_id: str, file: dict) -> None:
        self._put({"PK": _pk(scan_id), "SK": f"FILE#{file['box_file_id']}", "scan_id": scan_id, **file})

    def upsert_finding_ai(self, scan_id: str, finding_id: str, ai: dict, default_review: dict, now: str) -> None:
        key = (_pk(scan_id), f"FINDING#{finding_id}")
        with self._lock:
            item = self._items.get(key)
            if item is None:
                item = {"PK": key[0], "SK": key[1], "scan_id": scan_id, "finding_id": finding_id, "created_at": now}
                self._items[key] = item
            item["ai"] = copy.deepcopy(ai)
            item.setdefault("review", copy.deepcopy(default_review))
            item["updated_at"] = now

    def get_finding(self, scan_id: str, finding_id: str) -> Optional[dict]:
        with self._lock:
            item = self._items.get((_pk(scan_id), f"FINDING#{finding_id}"))
            return copy.deepcopy(item) if item else None

    def update_finding_review(self, scan_id: str, finding_id: str, review: dict) -> None:
        with self._lock:
            item = self._items.get((_pk(scan_id), f"FINDING#{finding_id}"))
            if item is None:
                raise NotFound(finding_id)
            item["review"] = copy.deepcopy(review)

    def put_packet(self, scan_id: str, finding_id: str, packet: dict) -> None:
        self._put({"PK": _pk(scan_id), "SK": f"PACKET#{finding_id}#{packet['created_at']}", "scan_id": scan_id, "finding_id": finding_id, **packet})

    def list_packets(self, scan_id: str, finding_id: str) -> list[dict]:
        prefix = f"PACKET#{finding_id}#"
        return [i for i in self.get_scan_items(scan_id) if i["SK"].startswith(prefix)]

    def list_scan_metas(self) -> list[dict]:
        with self._lock:
            return [copy.deepcopy(v) for (_p, s), v in self._items.items() if s == "META"]


def _check_no_floats(value: Any) -> None:
    if isinstance(value, float):
        raise TypeError("floats are not allowed in stored items; use Decimal or str")
    if isinstance(value, dict):
        for v in value.values():
            _check_no_floats(v)
    elif isinstance(value, list):
        for v in value:
            _check_no_floats(v)


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else str(value)
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


class S3Store(BaseStore):
    """JSON objects in S3, one per item, under ``<prefix>scans/<scan_id>/``:

        meta.json
        files/<box_file_id>.json
        findings/<finding_id>/ai.json      (worker only)
        findings/<finding_id>/review.json  (created once with IfNoneMatch, then PATCH only)
        packets/<finding_id>/<created_at>.json

    Splitting ai/review into separate objects keeps worker retries from clobbering reviewer edits.
    S3 is strongly read-after-write consistent, so GET /scans sees the worker's latest writes.
    """

    def __init__(self, bucket: str, prefix: str = "", client: Any = None) -> None:
        if client is None:
            import boto3

            client = boto3.client("s3")
        self.s3 = client
        self.bucket = bucket
        self.prefix = prefix

    def _base(self, scan_id: str) -> str:
        return f"{self.prefix}scans/{scan_id}/"

    def _put_json(self, key: str, data: dict, if_none_match: bool = False) -> bool:
        """Returns False if if_none_match was set and the object already exists."""
        from botocore.exceptions import ClientError

        _check_no_floats(data)
        kwargs: dict[str, Any] = {
            "Bucket": self.bucket,
            "Key": key,
            "Body": json.dumps(data, default=_json_default).encode(),
            "ContentType": "application/json",
        }
        if if_none_match:
            kwargs["IfNoneMatch"] = "*"
        try:
            self.s3.put_object(**kwargs)
        except ClientError as exc:
            if if_none_match and exc.response.get("Error", {}).get("Code") in ("PreconditionFailed", "ConditionalRequestConflict"):
                return False
            raise
        return True

    def _get_json(self, key: str) -> Optional[dict]:
        from botocore.exceptions import ClientError

        try:
            resp = self.s3.get_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
                return None
            raise
        return json.loads(resp["Body"].read())

    def _list_keys(self, prefix: str) -> list[str]:
        keys: list[str] = []
        kwargs: dict[str, Any] = {"Bucket": self.bucket, "Prefix": prefix}
        while True:
            resp = self.s3.list_objects_v2(**kwargs)
            keys.extend(o["Key"] for o in resp.get("Contents", []))
            if not resp.get("IsTruncated"):
                return keys
            kwargs["ContinuationToken"] = resp["NextContinuationToken"]

    def put_scan_meta(self, scan_id: str, data: dict) -> None:
        self._put_json(self._base(scan_id) + "meta.json", {"PK": _pk(scan_id), "SK": "META", "scan_id": scan_id, **data})

    def update_scan(self, scan_id: str, **fields: Any) -> None:
        # Read-modify-write: only POST /scans and the scan's single worker write meta.json.
        key = self._base(scan_id) + "meta.json"
        item = self._get_json(key)
        if item is None:
            raise NotFound(scan_id)
        item.update(fields)
        self._put_json(key, item)

    def get_scan_items(self, scan_id: str) -> list[dict]:
        base = self._base(scan_id)
        keys = self._list_keys(base)
        with ThreadPoolExecutor(max_workers=16) as pool:
            blobs = dict(zip(keys, pool.map(self._get_json, keys)))
        items: list[dict] = []
        findings: dict[str, dict] = {}
        for key, data in blobs.items():
            if data is None:
                continue
            rel = key[len(base):]
            if rel.startswith("findings/"):
                _, finding_id, name = rel.split("/", 2)
                item = findings.setdefault(finding_id, {})
                if name == "ai.json":
                    item.update(data)
                elif name == "review.json":
                    item["review"] = data
            else:
                items.append(data)
        items.extend(f for f in findings.values() if "ai" in f)
        return sorted(items, key=lambda i: i["SK"])

    def put_file(self, scan_id: str, file: dict) -> None:
        item = {"PK": _pk(scan_id), "SK": f"FILE#{file['box_file_id']}", "scan_id": scan_id, **file}
        self._put_json(self._base(scan_id) + f"files/{file['box_file_id']}.json", item)

    def upsert_finding_ai(self, scan_id: str, finding_id: str, ai: dict, default_review: dict, now: str) -> None:
        base = self._base(scan_id) + f"findings/{finding_id}/"
        existing = self._get_json(base + "ai.json")
        item = {
            "PK": _pk(scan_id),
            "SK": f"FINDING#{finding_id}",
            "scan_id": scan_id,
            "finding_id": finding_id,
            "created_at": existing["created_at"] if existing else now,
            "updated_at": now,
            "ai": ai,
        }
        self._put_json(base + "ai.json", item)
        self._put_json(base + "review.json", default_review, if_none_match=True)

    def get_finding(self, scan_id: str, finding_id: str) -> Optional[dict]:
        base = self._base(scan_id) + f"findings/{finding_id}/"
        item = self._get_json(base + "ai.json")
        if item is None:
            return None
        item["review"] = self._get_json(base + "review.json") or {}
        return item

    def update_finding_review(self, scan_id: str, finding_id: str, review: dict) -> None:
        base = self._base(scan_id) + f"findings/{finding_id}/"
        if self._get_json(base + "ai.json") is None:
            raise NotFound(finding_id)
        self._put_json(base + "review.json", review)

    def put_packet(self, scan_id: str, finding_id: str, packet: dict) -> None:
        item = {"PK": _pk(scan_id), "SK": f"PACKET#{finding_id}#{packet['created_at']}", "scan_id": scan_id, "finding_id": finding_id, **packet}
        self._put_json(self._base(scan_id) + f"packets/{finding_id}/{packet['created_at']}.json", item)

    def list_packets(self, scan_id: str, finding_id: str) -> list[dict]:
        keys = self._list_keys(self._base(scan_id) + f"packets/{finding_id}/")
        return [p for p in (self._get_json(k) for k in sorted(keys)) if p is not None]

    def list_scan_metas(self) -> list[dict]:
        prefixes: list[str] = []
        kwargs: dict[str, Any] = {"Bucket": self.bucket, "Prefix": f"{self.prefix}scans/", "Delimiter": "/"}
        while True:
            resp = self.s3.list_objects_v2(**kwargs)
            prefixes.extend(p["Prefix"] for p in resp.get("CommonPrefixes", []))
            if not resp.get("IsTruncated"):
                break
            kwargs["ContinuationToken"] = resp["NextContinuationToken"]
        with ThreadPoolExecutor(max_workers=16) as pool:
            metas = pool.map(self._get_json, [p + "meta.json" for p in prefixes])
        return [m for m in metas if m is not None]


@lru_cache(maxsize=1)
def get_store() -> BaseStore:
    """Process-wide store (cached per Lambda container / local server process)."""
    if config.store_mode() == "memory":
        return MemoryStore()
    return S3Store(config.data_bucket(), config.data_prefix())
