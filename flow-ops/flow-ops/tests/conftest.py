from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


@pytest.fixture(scope="session")
def workflows_dir() -> Path:
    return ROOT / "workflows"


@pytest.fixture(scope="session")
def built():
    from flowops.workflows import build_all

    return build_all()


@pytest.fixture
def mcp():
    from flowops.mcp_server import FlowOpsServer

    return FlowOpsServer()
