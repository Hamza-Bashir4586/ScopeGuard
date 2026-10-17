"""Document source + output destination for the configured project folder.

* BoxSource   - Box via box-sdk-gen, Client Credentials Grant (enterprise service account). BOX_MODE=box
* LocalSource - a local directory with fake, stable numeric IDs.                          BOX_MODE=local

Both list the configured folder non-recursively. Folders - including the "ScopeGuard Packets" output folder -
are never inventoried, so generated outputs never become evidence for themselves [DOC-03]. Every file is
listed (unsupported types too, so they can be reported); only .pdf/.txt/.md are analysed.
Downloads are pinned to a version ID [DOC-06]. CCG credentials never leave the server [VIEW-06].
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import mimetypes
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from . import config

log = logging.getLogger(__name__)


class SourceUnavailable(Exception):
    """The record/version cannot be opened (missing version, deleted file, access denied). Safe message."""


class SourceError(Exception):
    """Upstream (Box) failure. Safe message; never contains SDK response bodies or tokens."""


@dataclass
class SourceFileRef:
    box_file_id: str
    box_version_id: str
    name: str
    size_bytes: int = 0
    modified_at: Optional[str] = None

    @property
    def extension(self) -> str:
        return self.name.rsplit(".", 1)[-1].lower() if "." in self.name else ""

    @property
    def supported(self) -> bool:
        return self.name.lower().endswith(config.SUPPORTED_EXTENSIONS)

    @property
    def mime_type(self) -> str:
        ext = self.extension
        if ext == "pdf":
            return "application/pdf"
        if ext in ("txt", "md"):
            return "text/markdown" if ext == "md" else "text/plain"
        return mimetypes.guess_type(self.name)[0] or "application/octet-stream"


@dataclass
class OutputFile:
    file_id: str
    version_id: str
    url: str


def box_url(file_id: str) -> str:
    return f"https://app.box.com/file/{file_id}"


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class LocalSource:
    def __init__(self, directory: Path) -> None:
        self.dir = Path(directory)

    @staticmethod
    def _fake_id(*parts: str) -> str:
        # Box IDs are numeric strings; mimic that with a stable hash.
        return str(int(hashlib.sha256("|".join(parts).encode()).hexdigest()[:13], 16))

    def _ref(self, p: Path) -> SourceFileRef:
        st = p.stat()
        content = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
        return SourceFileRef(
            box_file_id=self._fake_id("file", p.name),
            box_version_id=self._fake_id("version", p.name, content),
            name=p.name, size_bytes=int(st.st_size), modified_at=_iso(st.st_mtime),
        )

    def list_files(self) -> list[SourceFileRef]:
        if not self.dir.is_dir():
            raise SourceError("The configured local documents folder does not exist.")
        refs = [self._ref(p) for p in self.dir.iterdir()
                if p.is_file() and p.name.lower() not in config.LOCAL_SKIP_NAMES and not p.name.startswith(".")]
        refs.sort(key=lambda r: r.name.lower())
        return refs

    def _path_for(self, file_id: str) -> Path:
        for p in self.dir.iterdir():
            if p.is_file() and self._fake_id("file", p.name) == file_id:
                return p
        raise SourceUnavailable("The record is no longer in the project folder.")

    def download(self, file_id: str, version_id: Optional[str] = None) -> bytes:
        p = self._path_for(file_id)
        data = p.read_bytes()
        if version_id and self._ref(p).box_version_id != version_id:
            raise SourceUnavailable("That version of the record is no longer available.")
        return data

    def version_available(self, file_id: str, version_id: str) -> bool:
        try:
            return self._ref(self._path_for(file_id)).box_version_id == version_id
        except SourceUnavailable:
            return False

    def upload_record(self, name: str, data: bytes) -> SourceFileRef:
        path = self.dir / name
        path.write_bytes(data)  # same name -> new version of the same file ID (like Box upload_file_version)
        return self._ref(path)

    def _out_dir(self) -> Path:
        return self.dir / config.PACKETS_FOLDER_NAME

    def find_output(self, filename: str) -> Optional[OutputFile]:
        path = self._out_dir() / filename
        if not path.is_file():
            return None
        fid = self._fake_id("output", filename)
        return OutputFile(fid, self._fake_id("output-version", filename, hashlib.sha256(path.read_bytes()).hexdigest()), box_url(fid))

    def upload_output(self, filename: str, data: bytes) -> OutputFile:
        existing = self.find_output(filename)
        if existing:
            return existing  # never overwrite: Box would reject a duplicate name in the folder too
        self._out_dir().mkdir(parents=True, exist_ok=True)
        (self._out_dir() / filename).write_bytes(data)
        log.info("Wrote output %s", filename)
        return self.find_output(filename)  # type: ignore[return-value]

    def download_output(self, file_id: str) -> bytes:
        out = self._out_dir()
        if out.is_dir():
            for p in out.iterdir():
                if self._fake_id("output", p.name) == file_id:
                    return p.read_bytes()
        raise SourceUnavailable("The saved output can no longer be opened.")

    # v1 POST /packets
    def upload_packet(self, filename: str, data: bytes) -> OutputFile:
        return self.upload_output(filename, data)


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
        raise SourceError("Box credentials are not configured (update the scopeguard/box/ccg secret).")
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


def _box_status(exc: Exception) -> Optional[int]:
    info = getattr(exc, "response_info", None)
    return getattr(info, "status_code", None)


def _dt(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(value)


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
        try:
            while True:
                page = self.client.folders.get_folder_items(
                    folder_id, fields=["id", "type", "name", "file_version", "size", "modified_at", "content_modified_at"],
                    offset=offset, limit=limit,
                )
                batch = list(page.entries or [])
                entries.extend(batch)
                total = page.total_count or 0
                offset += len(batch)
                if not batch or offset >= total:
                    break
        except SourceError:
            raise
        except Exception as exc:
            log.warning("Box folder listing failed: %s", type(exc).__name__)
            raise SourceError("Box could not list the project folder. Check the service account's folder access.") from None
        return entries

    @staticmethod
    def _ref_from_entry(e: Any) -> SourceFileRef:
        version = getattr(e, "file_version", None)
        return SourceFileRef(
            box_file_id=str(e.id), box_version_id=str(getattr(version, "id", "") or ""), name=e.name or "",
            size_bytes=int(getattr(e, "size", 0) or 0),
            modified_at=_dt(getattr(e, "content_modified_at", None) or getattr(e, "modified_at", None)),
        )

    def list_files(self) -> list[SourceFileRef]:
        refs = []
        for e in self._entries(self.folder_id):
            if getattr(e.type, "value", e.type) != "file":
                continue  # folders (incl. "ScopeGuard Packets") are never inputs [DOC-03]
            refs.append(self._ref_from_entry(e))
        refs.sort(key=lambda r: r.name.lower())
        return refs

    def download(self, file_id: str, version_id: Optional[str] = None) -> bytes:
        try:
            stream = self.client.downloads.download_file(file_id, version=version_id or None)
        except Exception as exc:
            status = _box_status(exc)
            if status in (403, 404, 410):
                raise SourceUnavailable("This record or version can no longer be opened in Box.") from None
            log.warning("Box download failed: %s %s", type(exc).__name__, status)
            raise SourceError("Box download failed. Try again shortly.") from None
        if stream is None:
            raise SourceUnavailable("Box returned no content for this record.")
        return stream.read()

    def version_available(self, file_id: str, version_id: str) -> bool:
        """True when a (previous) version can still be opened; Box keeps version history."""
        try:
            self.client.file_versions.get_file_version_by_id(file_id, version_id)
            return True
        except Exception as exc:
            if _box_status(exc) in (403, 404, 410):
                return False
            raise SourceError("Box could not check the record version.") from None

    def upload_record(self, name: str, data: bytes) -> SourceFileRef:
        from box_sdk_gen import UploadFileAttributes, UploadFileAttributesParentField, UploadFileVersionAttributes

        existing = next((e for e in self._entries(self.folder_id)
                         if getattr(e.type, "value", e.type) == "file" and e.name == name), None)
        try:
            if existing is not None:  # same name -> new version, keeps the file ID stable
                files = self.client.uploads.upload_file_version(existing.id, UploadFileVersionAttributes(name=name), io.BytesIO(data))
            else:
                files = self.client.uploads.upload_file(
                    UploadFileAttributes(name=name, parent=UploadFileAttributesParentField(id=self.folder_id)), io.BytesIO(data))
            entry = files.entries[0]
        except Exception as exc:
            log.warning("Box upload failed: %s %s", type(exc).__name__, _box_status(exc))
            raise SourceError("Box upload failed. Check the service account's folder access and try again.") from None
        ref = self._ref_from_entry(entry)
        if not ref.box_version_id or not ref.size_bytes:
            full = self.client.files.get_file_by_id(ref.box_file_id, fields=["id", "name", "file_version", "size", "modified_at", "content_modified_at"])
            ref = self._ref_from_entry(full)
        return ref

    # ---- output folder
    def _packets_folder_id(self) -> str:
        for e in self._entries(self.folder_id):
            if getattr(e.type, "value", e.type) == "folder" and e.name == config.PACKETS_FOLDER_NAME:
                return str(e.id)
        from box_sdk_gen import CreateFolderParent

        try:
            folder = self.client.folders.create_folder(config.PACKETS_FOLDER_NAME, CreateFolderParent(id=self.folder_id))
            return str(folder.id)
        except Exception as exc:
            if _box_status(exc) == 409:  # created concurrently
                for e in self._entries(self.folder_id):
                    if getattr(e.type, "value", e.type) == "folder" and e.name == config.PACKETS_FOLDER_NAME:
                        return str(e.id)
            raise SourceError("Box could not create the output folder.") from None

    def find_output(self, filename: str) -> Optional[OutputFile]:
        for e in self._entries(self._packets_folder_id()):
            if getattr(e.type, "value", e.type) == "file" and e.name == filename:
                ref = self._ref_from_entry(e)
                return OutputFile(ref.box_file_id, ref.box_version_id, box_url(ref.box_file_id))
        return None

    def upload_output(self, filename: str, data: bytes) -> OutputFile:
        from box_sdk_gen import UploadFileAttributes, UploadFileAttributesParentField

        parent_id = self._packets_folder_id()
        try:
            files = self.client.uploads.upload_file(
                UploadFileAttributes(name=filename, parent=UploadFileAttributesParentField(id=parent_id)), io.BytesIO(data))
        except Exception as exc:
            if _box_status(exc) == 409:  # same generation already uploaded (uncertain earlier outcome)
                found = self.find_output(filename)
                if found:
                    return found
            log.warning("Box output upload failed: %s %s", type(exc).__name__, _box_status(exc))
            raise SourceError("Saving to Box failed. Try again shortly.") from None
        ref = self._ref_from_entry(files.entries[0])
        return OutputFile(ref.box_file_id, ref.box_version_id, box_url(ref.box_file_id))

    def download_output(self, file_id: str) -> bytes:
        return self.download(file_id)

    def upload_packet(self, filename: str, data: bytes) -> OutputFile:
        return self.upload_output(filename, data)


def get_source(local_dir: Optional[Path] = None):
    if config.box_mode() == "local":
        return LocalSource(local_dir or config.local_docs_dir())
    return BoxSource(config.box_folder_id())
