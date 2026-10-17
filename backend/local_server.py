"""Local dev server: adapts plain HTTP requests into API Gateway HTTP API (v2) events and
calls the same api_handler.handler used in Lambda. No AWS or Box required.

    cd backend && STORE=memory BOX_MODE=local LLM_MODE=fake python3.12 local_server.py

Those three settings are also the defaults when this script is run directly.
LOCAL_DOCS_DIR defaults to ../demo-data, or the sample fixtures if demo-data has no documents.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

os.environ.setdefault("STORE", "memory")
os.environ.setdefault("BOX_MODE", "local")
os.environ.setdefault("LLM_MODE", "fake")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import api_handler  # noqa: E402
from scopeguard import config  # noqa: E402

CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET,POST,PATCH,OPTIONS",
    "Access-Control-Allow-Headers": "content-type",
    "Access-Control-Max-Age": "600",
}


class Handler(BaseHTTPRequestHandler):
    server_version = "ScopeGuardLocal/1.0"

    def _send(self, status: int, headers: dict, body: bytes) -> None:
        self.send_response(status)
        merged = {**headers, **CORS}
        for k, v in merged.items():
            if k.lower() != "content-length":
                self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _dispatch(self) -> None:
        if self.command == "OPTIONS":
            self._send(204, {}, b"")
            return
        url = urlsplit(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode("utf-8") if length else None
        now = time.time()
        event = {
            "version": "2.0",
            "routeKey": "$default",
            "rawPath": url.path,
            "rawQueryString": url.query,
            "headers": {k.lower(): v for k, v in self.headers.items()},
            "requestContext": {
                "http": {"method": self.command, "path": url.path, "protocol": "HTTP/1.1", "sourceIp": self.client_address[0],
                         "userAgent": self.headers.get("User-Agent", "")},
                "requestId": f"local-{int(now * 1000)}",
                "stage": "$default",
                "timeEpoch": int(now * 1000),
            },
            "body": body,
            "isBase64Encoded": False,
        }
        resp = api_handler.handler(event, None)
        out = resp.get("body") or ""
        self._send(int(resp.get("statusCode", 200)), resp.get("headers") or {}, out.encode("utf-8"))

    do_GET = do_POST = do_PATCH = do_PUT = do_DELETE = do_OPTIONS = _dispatch

    def log_message(self, fmt: str, *args) -> None:  # quieter, single-line logs
        sys.stderr.write("[local_server] %s - %s\n" % (self.address_string(), fmt % args))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    port = int(os.environ.get("PORT", "8000"))
    print(json.dumps({
        "listening": f"http://localhost:{port}",
        "STORE": config.store_mode(), "BOX_MODE": config.box_mode(), "LLM_MODE": config.llm_mode(),
        "LOCAL_DOCS_DIR": str(config.local_docs_dir()) if config.box_mode() == "local" else None,
    }, indent=1), flush=True)
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
