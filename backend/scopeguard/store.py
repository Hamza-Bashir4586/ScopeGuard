"""Persistence: private JSON (and a few binary) objects addressed by key, with S3-style conditional writes.

* MemoryStore - in-process dict (STORE=memory, local dev + tests); emulates S3 ETag semantics exactly:
                ``if_none_match`` (create only) and ``if_match=<etag>`` (update only if unchanged).
* S3Store     - one object per key in DATA_BUCKET under DATA_PREFIX (STORE=s3, default). Conditional writes use
                PutObject ``IfNoneMatch="*"`` / ``IfMatch=<etag>`` (requires boto3 >= 1.35, vendored by sam build).

Key layout (docs/backend-requirements.md §8):

    projects/<project_id>/project.json                         latest completed scan pointer (worker, conditional)
    projects/<project_id>/active-scan.json                     one-active-scan lease (conditional)
    projects/<project_id>/scan-requests/<hash>.json            POST /scans request_id -> scan_id
    projects/<project_id>/records/<file_id>/<version_id>/metadata.json | pages.json | extraction-<prompt>.json
    projects/<project_id>/reviews/<work_item_id>.json          human review (API only, revisioned)
    projects/<project_id>/invoice-draft.json                   current draft (API only, revisioned)
    projects/<project_id>/invoice-generations/<gid>/manifest.json | draft.pdf
    scans/<scan_id>/meta.json | manifest.json | execution-lease.json | facts.json | results.json
    scans/<scan_id>/files/<file_id>.json
    scans/<scan_id>/findings/<finding_id>/ai.json              machine output (worker only)
    scans/<scan_id>/packets/<finding_id>/<created_at>.json     v1 single-finding packets

Ownership: the worker writes scan objects, record caches and the project pointer; the API writes reviews, the
draft and generations. Machine writes never touch reviews. Numbers are stored as strings/ints, never floats.
"""

from __future__ import annotations

import copy
import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from functools import lru_cache
from typing import Any, Callable, Optional

from . import config


class NotFound(Exception):
    pass


class PreconditionFailed(Exception):
    """A conditional write lost: the object exists (if_none_match) or changed (if_match)."""


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
        return str(value)
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def _encode(data: Any) -> bytes:
    _check_no_floats(data)
    return json.dumps(data, default=_json_default, ensure_ascii=False).encode("utf-8")


class BaseStore:
    """Primitive operations (implemented by subclasses) plus JSON helpers and key builders."""

    # ---- primitives
    def get_bytes(self, key: str) -> Optional[tuple[bytes, str]]:  # (body, etag) or None
        raise NotImplementedError

    def put_bytes(self, key: str, body: bytes, content_type: str = "application/octet-stream", *,
                  if_none_match: bool = False, if_match: Optional[str] = None, temporary: bool = False) -> str:
        raise NotImplementedError

    def list_keys(self, prefix: str) -> list[str]:
        raise NotImplementedError

    def list_prefixes(self, prefix: str) -> list[str]:
        """Immediate child "directories" under prefix (with trailing slash)."""
        raise NotImplementedError

    # ---- JSON helpers
    def get_json(self, key: str) -> Optional[Any]:
        got = self.get_bytes(key)
        return json.loads(got[0]) if got else None

    def get_json_etag(self, key: str) -> tuple[Optional[Any], Optional[str]]:
        got = self.get_bytes(key)
        if not got:
            return None, None
        return json.loads(got[0]), got[1]

    def put_json(self, key: str, data: Any, *, if_none_match: bool = False, if_match: Optional[str] = None) -> str:
        return self.put_bytes(key, _encode(data), "application/json", if_none_match=if_none_match, if_match=if_match)

    def create_json(self, key: str, data: Any) -> bool:
        """Create-only write; False when the object already exists."""
        try:
            self.put_json(key, data, if_none_match=True)
            return True
        except PreconditionFailed:
            return False

    def update_json(self, key: str, mutate: Callable[[Optional[dict]], Optional[dict]], retries: int = 8) -> Optional[dict]:
        """Optimistic read-modify-write with IfMatch/IfNoneMatch; retries on conflicts. ``mutate`` returns the
        new object (or None to skip writing)."""
        for _ in range(retries):
            current, etag = self.get_json_etag(key)
            new = mutate(copy.deepcopy(current))
            if new is None:
                return current
            try:
                if etag is None:
                    self.put_json(key, new, if_none_match=True)
                else:
                    self.put_json(key, new, if_match=etag)
                return new
            except PreconditionFailed:
                continue
        raise PreconditionFailed(key)

    def get_many_json(self, keys: list[str]) -> dict[str, Any]:
        if not keys:
            return {}
        with ThreadPoolExecutor(max_workers=min(16, len(keys))) as pool:
            return {k: v for k, v in zip(keys, pool.map(self.get_json, keys)) if v is not None}

    # ---- key builders
    @staticmethod
    def project_key(project_id: str, rel: str) -> str:
        return f"projects/{project_id}/{rel}"

    @staticmethod
    def scan_key(scan_id: str, rel: str) -> str:
        return f"scans/{scan_id}/{rel}"

    @staticmethod
    def record_key(project_id: str, file_id: str, version_id: str, name: str) -> str:
        return f"projects/{project_id}/records/{file_id}/{version_id or 'current'}/{name}"

    @staticmethod
    def hashed(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]

    # ---- scan helpers
    def get_scan_meta(self, scan_id: str) -> Optional[dict]:
        return self.get_json(self.scan_key(scan_id, "meta.json"))

    def update_scan(self, scan_id: str, **fields: Any) -> dict:
        def mutate(cur: Optional[dict]) -> dict:
            if cur is None:
                raise NotFound(scan_id)
            cur.update(copy.deepcopy(fields))
            return cur

        return self.update_json(self.scan_key(scan_id, "meta.json"), mutate)  # type: ignore[return-value]

    def list_scan_metas(self) -> list[dict]:
        prefixes = self.list_prefixes("scans/")
        metas = self.get_many_json([p + "meta.json" for p in prefixes])
        return list(metas.values())

    def list_json_under(self, prefix: str) -> list[dict]:
        keys = [k for k in self.list_keys(prefix) if k.endswith(".json")]
        got = self.get_many_json(keys)
        return [got[k] for k in sorted(got)]


class MemoryStore(BaseStore):
    def __init__(self) -> None:
        self._objects: dict[str, tuple[bytes, str, str]] = {}
        self._lock = threading.RLock()
        self._counter = 0

    def get_bytes(self, key: str) -> Optional[tuple[bytes, str]]:
        with self._lock:
            obj = self._objects.get(key)
            return (obj[0], obj[1]) if obj else None

    def put_bytes(self, key: str, body: bytes, content_type: str = "application/octet-stream", *,
                  if_none_match: bool = False, if_match: Optional[str] = None, temporary: bool = False) -> str:
        with self._lock:
            existing = self._objects.get(key)
            if if_none_match and existing is not None:
                raise PreconditionFailed(key)
            if if_match is not None and (existing is None or existing[1] != if_match):
                raise PreconditionFailed(key)
            self._counter += 1
            etag = f'"{hashlib.md5(body).hexdigest()}-{self._counter}"'
            self._objects[key] = (bytes(body), etag, content_type)
            return etag

    def delete(self, key: str) -> None:
        with self._lock:
            self._objects.pop(key, None)

    def list_keys(self, prefix: str) -> list[str]:
        with self._lock:
            return sorted(k for k in self._objects if k.startswith(prefix))

    def list_prefixes(self, prefix: str) -> list[str]:
        with self._lock:
            out = {prefix + k[len(prefix):].split("/", 1)[0] + "/" for k in self._objects
                   if k.startswith(prefix) and "/" in k[len(prefix):]}
        return sorted(out)


class S3Store(BaseStore):
    """JSON objects in a private S3 bucket. S3 is strongly read-after-write consistent."""

    def __init__(self, bucket: str, prefix: str = "", client: Any = None) -> None:
        if client is None:
            import boto3

            client = boto3.client("s3")
        self.s3 = client
        self.bucket = bucket
        self.prefix = prefix

    def _k(self, key: str) -> str:
        return self.prefix + key

    def get_bytes(self, key: str) -> Optional[tuple[bytes, str]]:
        from botocore.exceptions import ClientError

        try:
            resp = self.s3.get_object(Bucket=self.bucket, Key=self._k(key))
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404", "NotFound"):
                return None
            raise
        return resp["Body"].read(), resp.get("ETag", "")

    def put_bytes(self, key: str, body: bytes, content_type: str = "application/octet-stream", *,
                  if_none_match: bool = False, if_match: Optional[str] = None, temporary: bool = False) -> str:
        from botocore.exceptions import ClientError

        kwargs: dict[str, Any] = {"Bucket": self.bucket, "Key": self._k(key), "Body": body, "ContentType": content_type}
        if if_none_match:
            kwargs["IfNoneMatch"] = "*"
        if if_match is not None:
            kwargs["IfMatch"] = if_match
        if temporary:
            kwargs["Tagging"] = "scopeguard-temporary=true"  # lifecycle rule expires these (template.yaml)
        try:
            resp = self.s3.put_object(**kwargs)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if code in ("PreconditionFailed", "ConditionalRequestConflict") or status in (409, 412):
                raise PreconditionFailed(key) from None
            if code in ("NoSuchKey",) and if_match is not None:
                raise PreconditionFailed(key) from None
            raise
        return resp.get("ETag", "")

    def list_keys(self, prefix: str) -> list[str]:
        keys: list[str] = []
        kwargs: dict[str, Any] = {"Bucket": self.bucket, "Prefix": self._k(prefix)}
        while True:
            resp = self.s3.list_objects_v2(**kwargs)
            keys.extend(o["Key"][len(self.prefix):] for o in resp.get("Contents", []))
            if not resp.get("IsTruncated"):
                return keys
            kwargs["ContinuationToken"] = resp["NextContinuationToken"]

    def list_prefixes(self, prefix: str) -> list[str]:
        out: list[str] = []
        kwargs: dict[str, Any] = {"Bucket": self.bucket, "Prefix": self._k(prefix), "Delimiter": "/"}
        while True:
            resp = self.s3.list_objects_v2(**kwargs)
            out.extend(p["Prefix"][len(self.prefix):] for p in resp.get("CommonPrefixes", []))
            if not resp.get("IsTruncated"):
                return out
            kwargs["ContinuationToken"] = resp["NextContinuationToken"]


@lru_cache(maxsize=1)
def get_store() -> BaseStore:
    """Process-wide store (cached per Lambda container / local server process)."""
    if config.store_mode() == "memory":
        return MemoryStore()
    return S3Store(config.data_bucket(), config.data_prefix())
