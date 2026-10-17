import base64
import json
import shutil
import sys
import time
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

FIXTURES = BACKEND / "tests" / "fixtures" / "riverside"
DEMO_PDFS = BACKEND.parent / "demo-data" / "riverside"


def _env(monkeypatch, docs: Path) -> None:
    monkeypatch.setenv("STORE", "memory")
    monkeypatch.setenv("BOX_MODE", "local")
    monkeypatch.setenv("LLM_MODE", "fake")
    monkeypatch.setenv("LOCAL_DOCS_DIR", str(docs))
    monkeypatch.delenv("WORKER_FUNCTION_NAME", raising=False)
    monkeypatch.delenv("SCAN_DEADLINE_SECONDS", raising=False)


@pytest.fixture
def local_env(monkeypatch, tmp_path):
    """Local mode (memory store, local folder, fake LLM) against a temp copy of the Riverside text fixtures."""
    docs = tmp_path / "docs"
    shutil.copytree(FIXTURES, docs)
    _env(monkeypatch, docs)
    from scopeguard import store

    store.get_store.cache_clear()
    yield docs
    store.get_store.cache_clear()


@pytest.fixture
def pdf_env(monkeypatch, tmp_path):
    """Same, against a temp copy of the generated demo PDFs (demo-data/riverside)."""
    docs = tmp_path / "pdfs"
    shutil.copytree(DEMO_PDFS, docs, ignore=shutil.ignore_patterns("ScopeGuard Packets"))
    _env(monkeypatch, docs)
    from scopeguard import store

    store.get_store.cache_clear()
    yield docs
    store.get_store.cache_clear()


class Api:
    """Calls api_handler.handler with API Gateway HTTP API v2-shaped events."""

    def __call__(self, method, path, body=None, query=None, raw=None, headers=None):
        import api_handler

        event = {"version": "2.0", "rawPath": path, "requestContext": {"http": {"method": method, "path": path}},
                 "queryStringParameters": query, "headers": headers or {}, "isBase64Encoded": False, "body": None}
        if raw is not None:
            event["body"] = base64.b64encode(raw).decode()
            event["isBase64Encoded"] = True
        elif body is not None:
            event["body"] = json.dumps(body)
        resp = api_handler.handler(event, None)
        if resp.get("isBase64Encoded"):
            return resp["statusCode"], base64.b64decode(resp["body"]), resp["headers"]
        return resp["statusCode"], (json.loads(resp["body"]) if resp["body"] else None)

    def wait(self, scan_id, timeout=30):
        deadline = time.time() + timeout
        while time.time() < deadline:
            status, scan = self("GET", f"/scans/{scan_id}")
            assert status == 200
            if scan["status"] in ("completed", "failed"):
                return scan
            time.sleep(0.05)
        raise AssertionError("scan did not finish")

    def audit(self, **body):
        status, started = self("POST", "/scans", {"project_id": "riverside", **body})
        assert status == 202, started
        return self.wait(started["scan_id"])

    def multipart(self, filename, data, content_type="application/octet-stream", field="file"):
        boundary = "----sgtestboundary"
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{filename}\"\r\n"
                f"Content-Type: {content_type}\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
        return self("POST", "/projects/riverside/records", raw=body,
                    headers={"content-type": f"multipart/form-data; boundary={boundary}"})


@pytest.fixture
def api():
    return Api()


def by_code(scan):
    return {f["code"]: f for f in scan["findings"]}
