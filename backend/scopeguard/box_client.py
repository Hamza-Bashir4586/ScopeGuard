"""Document source + packet destination.

* BoxSource   - Box via box-sdk-gen, Client Credentials Grant (enterprise subject). BOX_MODE=box
* LocalSource - a local directory with fake stable IDs.                         BOX_MODE=local

Both list the configured folder non-recursively (skipping the "ScopeGuard Packets" subfolder),
download .pdf/.txt/.md files, and upload packets into a "ScopeGuard Packets" subfolder.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from . import config
from .validate import box_url

log = logging.getLogger(__name__)


@dataclass
class SourceFileRef:
    box_file_id: str
    box_version_id: str
    name: str


@dataclass
class UploadedFile:
    box_file_id: str
    box_url: str


def _supported(name: str) -> bool:
    return name.lower().endswith(config.SUPPORTED_EXTENSIONS)


class LocalSource:
    def __init__(self, directory: Path) -> None:
        self.dir = Path(directory)

    @staticmethod
    def _fake_id(*parts: str) -> str:
        # Box IDs are numeric strings; mimic that with a stable hash.
        return str(int(hashlib.sha256("|".join(parts).encode()).hexdigest()[:13], 16))

    def list_files(self) -> list[SourceFileRef]:
        if not self.dir.is_dir():
            raise FileNotFoundError(f"LOCAL_DOCS_DIR not found: {self.dir}")
        refs = []
        for p in sorted(self.dir.iterdir(), key=lambda p: p.name.lower()):
            if not p.is_file() or not _supported(p.name) or p.name.lower() in config.LOCAL_SKIP_NAMES:
                continue
            st = p.stat()
            refs.append(SourceFileRef(
                box_file_id=self._fake_id("file", p.name),
                box_version_id=self._fake_id("version", p.name, str(st.st_mtime_ns), str(st.st_size)),
                name=p.name,
            ))
        return refs

    def download(self, ref: SourceFileRef) -> bytes:
        return (self.dir / ref.name).read_bytes()

    def upload_packet(self, filename: str, data: bytes) -> UploadedFile:
        out_dir = self.dir / config.PACKETS_FOLDER_NAME
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / filename
        path.write_bytes(data)
        fid = self._fake_id("packet", filename)
        log.info("Wrote packet to %s", path)
        return UploadedFile(box_file_id=fid, box_url=box_url(fid))


# ---------------------------------------------------------------- Box (CCG)

_client_lock = threading.Lock()
_cached_client: Any = None


def _load_box_credentials() -> dict:
    import boto3

    sm = boto3.client("secretsmanager")
    raw = sm.get_secret_value(SecretId=config.box_secret_arn())["SecretString"]
    data = json.loads(raw)
    # Canonical keys are BOX_CLIENT_ID / BOX_CLIENT_SECRET / BOX_ENTERPRISE_ID (same secret as backend/config.py);
    # lowercase client_id / client_secret / enterprise_id are still accepted.
    creds = {k: str(data.get(f"BOX_{k.upper()}") or data.get(k) or "").strip()
             for k in ("client_id", "client_secret", "enterprise_id")}
    missing = [k for k, v in creds.items() if not v or v == "REPLACE"]
    if missing:
        raise RuntimeError(f"Box secret is missing values for: {', '.join(missing)} (update the scopeguard/box/ccg secret)")
    return creds


def get_box_client() -> Any:
    """BoxClient cached per Lambda container (the SDK refreshes CCG tokens itself)."""
    global _cached_client
    with _client_lock:
        if _cached_client is None:
            from box_sdk_gen import BoxCCGAuth, BoxClient, CCGConfig

            creds = _load_box_credentials()
            auth = BoxCCGAuth(config=CCGConfig(
                client_id=creds["client_id"],
                client_secret=creds["client_secret"],
                enterprise_id=str(creds["enterprise_id"]),
            ))
            _cached_client = BoxClient(auth=auth)
        return _cached_client


class BoxSource:
    def __init__(self, folder_id: str, client: Any = None) -> None:
        self.folder_id = folder_id
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = get_box_client()
        return self._client

    def _entries(self, folder_id: str) -> list[Any]:
        entries: list[Any] = []
        offset, limit = 0, 1000
        while True:
            page = self.client.folders.get_folder_items(
                folder_id, fields=["id", "type", "name", "file_version"], offset=offset, limit=limit
            )
            batch = list(page.entries or [])
            entries.extend(batch)
            total = page.total_count or 0
            offset += len(batch)
            if not batch or offset >= total:
                break
        return entries

    def list_files(self) -> list[SourceFileRef]:
        refs = []
        for e in self._entries(self.folder_id):
            etype = getattr(e.type, "value", e.type)
            if etype != "file" or not _supported(e.name or ""):
                continue  # folders (incl. "ScopeGuard Packets") and unsupported types are skipped
            version = getattr(e, "file_version", None)
            refs.append(SourceFileRef(box_file_id=e.id, box_version_id=getattr(version, "id", "") or "", name=e.name))
        refs.sort(key=lambda r: r.name.lower())
        return refs

    def download(self, ref: SourceFileRef) -> bytes:
        stream = self.client.downloads.download_file(ref.box_file_id, version=ref.box_version_id or None)
        if stream is None:
            raise RuntimeError(f"Empty download for {ref.name}")
        return stream.read()

    def _packets_folder_id(self) -> str:
        for e in self._entries(self.folder_id):
            if getattr(e.type, "value", e.type) == "folder" and e.name == config.PACKETS_FOLDER_NAME:
                return e.id
        from box_sdk_gen import CreateFolderParent

        folder = self.client.folders.create_folder(config.PACKETS_FOLDER_NAME, CreateFolderParent(id=self.folder_id))
        return folder.id

    def upload_packet(self, filename: str, data: bytes) -> UploadedFile:
        from box_sdk_gen import UploadFileAttributes, UploadFileAttributesParentField

        parent_id = self._packets_folder_id()
        files = self.client.uploads.upload_file(
            UploadFileAttributes(name=filename, parent=UploadFileAttributesParentField(id=parent_id)),
            io.BytesIO(data),
        )
        entry = files.entries[0]
        return UploadedFile(box_file_id=entry.id, box_url=box_url(entry.id))


def get_source(local_dir: Optional[Path] = None):
    if config.box_mode() == "local":
        return LocalSource(local_dir or config.local_docs_dir())
    return BoxSource(config.box_folder_id())
