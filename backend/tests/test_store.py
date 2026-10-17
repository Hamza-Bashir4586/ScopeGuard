from scopeguard import analyze
from scopeguard.serialize import apply_patch, serialize_scan
import io

import pytest
from botocore.exceptions import ClientError

from scopeguard.store import MemoryStore, NotFound, S3Store


def _new_scan(store, scan_id="s1"):
    store.put_scan_meta(scan_id, {"status": "queued", "stage": "Queued", "progress": 0, "error": None,
                                  "started_at": "2026-10-16T00:00:00.000Z", "finished_at": None})


def test_rerun_worker_is_idempotent_and_keeps_review(local_env):
    store = MemoryStore()
    _new_scan(store)
    analyze.run_scan("s1", store=store)
    first = serialize_scan(store.get_scan_items("s1"))
    assert first["status"] == "completed" and len(first["findings"]) == 3

    target = first["findings"][0]
    review = apply_patch(store.get_finding("s1", target["finding_id"])["review"],
                         {"status": "confirmed", "draft": "Reviewer text"}, target["finding_id"])
    store.update_finding_review("s1", target["finding_id"], review)

    analyze.run_scan("s1", store=store)  # e.g. an async retry
    second = serialize_scan(store.get_scan_items("s1"))
    assert sorted(f["finding_id"] for f in second["findings"]) == sorted(f["finding_id"] for f in first["findings"])
    assert len(second["files"]) == len(first["files"])
    again = next(f for f in second["findings"] if f["finding_id"] == target["finding_id"])
    assert again["review"]["status"] == "confirmed"
    assert again["draft"] == "Reviewer text"
    assert again["ai_draft"] == target["ai_draft"]


class _FakeS3:
    """Minimal in-memory S3 client: put (with IfNoneMatch), get, list_objects_v2."""

    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body, ContentType=None, IfNoneMatch=None):
        if IfNoneMatch == "*" and (Bucket, Key) in self.objects:
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
        self.objects[(Bucket, Key)] = Body

    def get_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}

    def list_objects_v2(self, Bucket, Prefix, ContinuationToken=None, Delimiter=None):
        keys = sorted(k for b, k in self.objects if b == Bucket and k.startswith(Prefix))
        if Delimiter:
            prefixes = sorted({Prefix + k[len(Prefix):].split(Delimiter)[0] + Delimiter
                               for k in keys if Delimiter in k[len(Prefix):]})
            return {"CommonPrefixes": [{"Prefix": p} for p in prefixes], "IsTruncated": False}
        return {"Contents": [{"Key": k} for k in keys], "IsTruncated": False}


def test_s3_store_runs_full_scan_and_keeps_review(local_env):
    s3 = _FakeS3()
    store = S3Store("bucket", "sg/", client=s3)
    _new_scan(store)
    analyze.run_scan("s1", store=store)
    first = serialize_scan(store.get_scan_items("s1"))
    assert first["status"] == "completed" and len(first["findings"]) == 3
    assert all(k.startswith("sg/scans/s1/") for _b, k in s3.objects)

    target = first["findings"][0]
    review = apply_patch(store.get_finding("s1", target["finding_id"])["review"],
                         {"status": "confirmed", "draft": "Reviewer text"}, target["finding_id"])
    store.update_finding_review("s1", target["finding_id"], review)

    analyze.run_scan("s1", store=store)  # async retry must not reset review.json
    second = serialize_scan(store.get_scan_items("s1"))
    assert len(second["findings"]) == 3
    again = next(f for f in second["findings"] if f["finding_id"] == target["finding_id"])
    assert again["review"]["status"] == "confirmed" and again["draft"] == "Reviewer text"


def test_s3_store_packets_and_not_found():
    store = S3Store("bucket", client=_FakeS3())
    _new_scan(store)
    with pytest.raises(NotFound):
        store.update_finding_review("s1", "missing", {"status": "confirmed"})
    with pytest.raises(NotFound):
        store.update_scan("nope", status="running")
    store.upsert_finding_ai("s1", "f1", {"title": "x"}, {"status": "unreviewed"}, "t1")
    store.put_packet("s1", "f1", {"box_file_id": "9", "box_url": "u", "created_at": "2026-10-16T01:00:00Z"})
    assert [p["box_file_id"] for p in store.list_packets("s1", "f1")] == ["9"]
    assert store.get_finding("s1", "f1")["review"] == {"status": "unreviewed"}
    assert store.get_finding("s1", "nope") is None


def test_s3_store_rejects_floats():
    store = S3Store("bucket", client=_FakeS3())
    with pytest.raises(TypeError):
        store.upsert_finding_ai("s1", "f1", {"amount": 1.5}, {}, "now")


def test_s3_store_lists_scan_metas():
    store = S3Store("bucket", "sg/", client=_FakeS3())
    _new_scan(store, "s1")
    _new_scan(store, "s2")
    store.upsert_finding_ai("s1", "f1", {"title": "x"}, {}, "now")
    assert sorted(m["scan_id"] for m in store.list_scan_metas()) == ["s1", "s2"]
