"""Runtime configuration, read from environment variables at call time.

Production defaults: STORE=s3, BOX_MODE=box, LLM_MODE=bedrock.
Local development: STORE=memory, BOX_MODE=local, LLM_MODE=fake (local_server.py sets these).
"""

from __future__ import annotations

import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent
FIXTURES_DIR = BACKEND_DIR / "tests" / "fixtures" / "sample_project"
DEFAULT_DEMO_DIR = REPO_ROOT / "demo-data"

PACKETS_FOLDER_NAME = "ScopeGuard Packets"
SUPPORTED_EXTENSIONS = (".pdf", ".txt", ".md")
# Files skipped in BOX_MODE=local (so demo-data/README.md is not treated as a project document).
LOCAL_SKIP_NAMES = {"readme.md", ".ds_store"}


def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def store_mode() -> str:
    return _env("STORE", "s3").lower()


def box_mode() -> str:
    return _env("BOX_MODE", "box").lower()


def llm_mode() -> str:
    return _env("LLM_MODE", "bedrock").lower()


def data_bucket() -> str:
    return _env("DATA_BUCKET", "")


def data_prefix() -> str:
    """Optional key prefix inside DATA_BUCKET, e.g. "scopeguard/"."""
    return _env("DATA_PREFIX", "")


def box_secret_arn() -> str:
    return _env("BOX_SECRET_ARN", "scopeguard/box")


def box_folder_id() -> str:
    return _env("BOX_FOLDER_ID", "0")


def bedrock_model_id() -> str:
    return _env("BEDROCK_MODEL_ID", "")


def bedrock_max_tokens() -> int:
    return int(_env("BEDROCK_MAX_TOKENS", "16000"))


def worker_function_name() -> str:
    """When set, POST /scans invokes this Lambda asynchronously; otherwise a local thread runs the worker."""
    return _env("WORKER_FUNCTION_NAME", "")


def allowed_origin() -> str:
    return _env("ALLOWED_ORIGIN", "*")


def _has_documents(directory: Path) -> bool:
    if not directory.is_dir():
        return False
    for p in directory.iterdir():
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS and p.name.lower() not in LOCAL_SKIP_NAMES:
            return True
    return False


def local_docs_dir() -> Path:
    """LOCAL_DOCS_DIR, else demo-data/; falls back to the test fixtures if demo-data has no documents."""
    configured = _env("LOCAL_DOCS_DIR")
    if configured:
        path = Path(configured)
        if not path.is_absolute():
            # Relative paths resolve against the current directory first, then the repo root.
            path = path if path.exists() else REPO_ROOT / configured
        return path
    if _has_documents(DEFAULT_DEMO_DIR):
        return DEFAULT_DEMO_DIR
    return FIXTURES_DIR
