import os
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

FIXTURES = BACKEND / "tests" / "fixtures" / "sample_project"


@pytest.fixture
def local_env(monkeypatch, tmp_path):
    """Local mode against a temp copy of the fixtures (packets get written there)."""
    import shutil

    docs = tmp_path / "docs"
    shutil.copytree(FIXTURES, docs)
    monkeypatch.setenv("STORE", "memory")
    monkeypatch.setenv("BOX_MODE", "local")
    monkeypatch.setenv("LLM_MODE", "fake")
    monkeypatch.setenv("LOCAL_DOCS_DIR", str(docs))
    monkeypatch.delenv("WORKER_FUNCTION_NAME", raising=False)
    from scopeguard import store

    store.get_store.cache_clear()
    yield docs
    store.get_store.cache_clear()
