"""Shared pytest fixtures for OpenForgyn tests."""

from pathlib import Path

import pytest


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    """Temporary data directory mimicking ~/.forgyn/."""
    data_dir = tmp_path / "forgyn_data"
    data_dir.mkdir()
    (data_dir / "skills").mkdir()
    return data_dir


@pytest.fixture
def tmp_db_path(tmp_data_dir: Path) -> Path:
    """Path for a temporary SQLite database."""
    return tmp_data_dir / "forgyn.db"
