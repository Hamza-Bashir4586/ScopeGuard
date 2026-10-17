"""Store conditional-write semantics: MemoryStore emulates S3; S3Store maps IfMatch/IfNoneMatch to PutObject."""

import io

import pytest
from botocore.exceptions import ClientError

from scopeguard import analyze, scans
from scopeguard.store import MemoryStore, NotFound, PreconditionFailed, S3Store


class FakeS3:
    """Minimal S3 client: put (IfNoneMatch / IfMatch with ETags), get, list_objects_v2 (with Delimiter)."""

    def __init__(self):
        self.objects = {}
        self.n = 0

    def put_object(self, Bucket, Key, Body, ContentType=None, IfNoneMatch=None, IfMatch=None, Tagging=None):
        cur = self.objects.get((Bucket, Key))
        if IfNoneMatch == "*" and cur is not None:
            raise ClientError({"Error": {"Code": "PreconditionFailed"}, "ResponseMetadata": {"HTTPStatusCode": 412}}, "PutObject")
        if IfMatch is not None and (cur is None or cur[1] != IfMatch):
            code = "NoSuchKey" if cur is None else "PreconditionFailed"
            raise ClientError({"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": 404 if cur is None else 412}}, "PutObject")
        self.n += 1
        etag = f'"etag-{self.n}"'
        self.objects[(Bucket, Key)] = (Body, etag, Tagging)
        return {"ETag": etag}

    def get_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        body, etag, _ = self.objects[(Bucket, Key)]
        return {"Body": io.BytesIO(body), "ETag": etag}

    def list_objects_v2(self, Bucket, Prefix, ContinuationToken=None, Delimiter=None):
        keys = sorted(k for b, k in self.objects if b == Bucket and k.startswith(Prefix))
        if Delimiter:
            prefixes = sorted({Prefix + k[len(Prefix):].split(Delimiter)[0] + Delimiter for k in keys if Delimiter in k[len(Prefix):]})
            return {"CommonPrefixes": [{"Prefix": p} for p in prefixes], "IsTruncated": False}
        return {"Contents": [{"Key": k} for k in keys], "IsTruncated": False}


@pytest.fixture(params=["memory", "s3"])
def store(request):
    return MemoryStore() if request.param == "memory" else S3Store("bucket", "sg/", client=FakeS3())


def test_conditional_writes(store):
    etag = store.put_json("a.json", {"v": 1}, if_none_match=True)
    with pytest.raises(PreconditionFailed):
        store.put_json("a.json", {"v": 2}, if_none_match=True)
    assert store.create_json("a.json", {"v": 3}) is False
    etag2 = store.put_json("a.json", {"v": 2}, if_match=etag)
    with pytest.raises(PreconditionFailed):
        store.put_json("a.json", {"v": 9}, if_match=etag)  # stale ETag
    with pytest.raises(PreconditionFailed):
        store.put_json("missing.json", {"v": 1}, if_match=etag2)
    data, current = store.get_json_etag("a.json")
    assert data == {"v": 2} and current == etag2
    assert store.get_json("nope.json") is None and store.get_json_etag("nope.json") == (None, None)


def test_update_json_retries_and_lists(store):
    store.update_json("c.json", lambda cur: {"n": (cur or {}).get("n", 0) + 1})
    store.update_json("c.json", lambda cur: {"n": cur["n"] + 1})
    assert store.get_json("c.json") == {"n": 2}
    store.put_json("scans/s1/meta.json", {"scan_id": "s1"})
    store.put_json("scans/s2/meta.json", {"scan_id": "s2"})
    store.put_json("scans/s2/files/x.json", {"file_id": "x"})
    assert sorted(m["scan_id"] for m in store.list_scan_metas()) == ["s1", "s2"]
    assert store.list_json_under("scans/s2/files/") == [{"file_id": "x"}]
    with pytest.raises(NotFound):
        store.update_scan("nope", status="running")


def test_rejects_floats(store):
    with pytest.raises(TypeError):
        store.put_json("f.json", {"amount": 1.5})


def test_s3_keys_prefix_and_temporary_tag():
    s3 = FakeS3()
    store = S3Store("bucket", "sg/", client=s3)
    store.put_bytes("projects/p/invoice-generations/g/draft.pdf", b"%PDF", "application/pdf", temporary=True)
    (body, _etag, tagging), = s3.objects.values()
    assert list(s3.objects)[0] == ("bucket", "sg/projects/p/invoice-generations/g/draft.pdf") and tagging == "scopeguard-temporary=true"
    assert store.get_bytes("projects/p/invoice-generations/g/draft.pdf")[0] == b"%PDF"


def test_full_scan_on_s3_store(local_env, monkeypatch):
    s3 = FakeS3()
    store = S3Store("bucket", "sg/", client=s3)
    monkeypatch.setattr(scans, "get_store", lambda: store)
    monkeypatch.setattr(scans, "_start_worker", lambda sid: None)
    from scopeguard import records, reviews

    monkeypatch.setattr(records, "get_store", lambda: store)
    monkeypatch.setattr(reviews, "get_store", lambda: store)
    started = scans.start_scan({"project_id": "riverside", "request_id": "s3"})
    analyze.run_scan(started["scan_id"], store=store)
    analyze.run_scan(started["scan_id"], store=store)  # duplicate delivery: no-op
    view = scans.scan_view(started["scan_id"])
    assert view["status"] == "completed" and [f["amount"] for f in view["findings"]] == ["8500.00", "1600.00", "900.00"]
    assert all(k.startswith("sg/") for _b, k in s3.objects)
    lease = store.get_json("projects/riverside/active-scan.json")
    assert lease["released"] is True and lease["scan_id"] == started["scan_id"]
    assert store.get_json("projects/riverside/project.json")["latest_completed_scan_id"] == started["scan_id"]
