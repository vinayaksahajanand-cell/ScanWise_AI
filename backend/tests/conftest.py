"""Test configuration: isolated data dir + database, no vision provider, one worker."""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

_TMP = tempfile.mkdtemp(prefix="scanwise-test-")
os.environ["DATA_DIR"] = _TMP
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_TMP, 'test.db').as_posix()}"
os.environ["LLM_PROVIDER"] = "none"
os.environ["WORKER_CONCURRENCY"] = "1"
for key in ("OPENAI_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY", "NVIDIA_API_KEY", "COMPATIBLE_BASE_URL"):
    os.environ.pop(key, None)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


def wait_for(client, doc_id: str, timeout: float = 180.0) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        s = client.get(f"/api/documents/{doc_id}/status").json()
        if s["status"] not in ("queued", "processing"):
            return s
        time.sleep(0.25)
    raise AssertionError(f"document {doc_id} did not finish within {timeout}s")


@pytest.fixture(scope="session")
def settings():
    from app.config import get_settings

    return get_settings()


@pytest.fixture(autouse=True)
def _forget_endpoint_capabilities():
    """Providers remember what an endpoint rejected (per process); tests must not leak that to each other."""
    from app.pipeline.llm import providers

    providers._CAPS.clear()
    yield
    providers._CAPS.clear()
