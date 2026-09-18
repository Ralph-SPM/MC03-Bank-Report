"""Shared pytest bootstrap for the src-layout project."""

from pathlib import Path

import pytest


@pytest.fixture
def local_storage_root(tmp_path: Path) -> Path:
    """Return an isolated local root for protected-storage tests."""
    return tmp_path / "mc03"
