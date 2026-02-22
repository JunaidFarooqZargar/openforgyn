"""Shared pytest fixtures for OpenForgyn tests."""

import shutil
from pathlib import Path

import pytest


def pytest_collection_modifyitems(config, items):
    """Auto-skip @pytest.mark.docker tests when Docker is not available."""
    if shutil.which("docker") is None:
        skip_docker = pytest.mark.skip(reason="Docker not available")
        for item in items:
            if "docker" in item.keywords:
                item.add_marker(skip_docker)
        return

    import subprocess

    result = subprocess.run(["docker", "info"], capture_output=True, timeout=5)
    if result.returncode != 0:
        skip_docker = pytest.mark.skip(reason="Docker daemon not running")
        for item in items:
            if "docker" in item.keywords:
                item.add_marker(skip_docker)


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
