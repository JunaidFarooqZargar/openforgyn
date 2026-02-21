"""Tests for CLI entry point (Step 1.5)."""

from click.testing import CliRunner

import forgyn
from forgyn.cli import main


def test_version_flag():
    runner = CliRunner()
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0
    assert forgyn.__version__ in result.output


def test_skills_command_empty(tmp_path):
    runner = CliRunner()
    result = runner.invoke(main, ["--data-dir", str(tmp_path), "skills"])
    assert result.exit_code == 0
    assert "No skills installed" in result.output


def test_data_dir_created(tmp_path):
    data_dir = tmp_path / "test_forgyn"
    runner = CliRunner()
    runner.invoke(main, ["--data-dir", str(data_dir), "skills"])
    assert data_dir.exists()
    assert (data_dir / "skills").exists()
