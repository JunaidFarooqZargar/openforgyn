"""Tests for audit logger (Step 2.3)."""

import pytest

from forgyn.audit import (
    commit_skill,
    get_last_diff,
    get_skill_history,
    init_skills_repo,
    rollback_skill,
)


@pytest.fixture
def skills_dir(tmp_path):
    d = tmp_path / "skills"
    d.mkdir()
    return d


def test_init_creates_git_repo(skills_dir):
    init_skills_repo(skills_dir)
    assert (skills_dir / ".git").exists()


def test_idempotent_init(skills_dir):
    init_skills_repo(skills_dir)
    init_skills_repo(skills_dir)  # Should not error
    assert (skills_dir / ".git").exists()


def test_commit_skill(skills_dir):
    init_skills_repo(skills_dir)
    (skills_dir / "echo").mkdir()
    (skills_dir / "echo" / "handler.py").write_text("print('hello')")

    commit_hash = commit_skill(skills_dir, "echo", "Added echo skill")
    assert len(commit_hash) == 40  # Full SHA


def test_commit_message(skills_dir):
    init_skills_repo(skills_dir)
    (skills_dir / "echo").mkdir()
    (skills_dir / "echo" / "handler.py").write_text("print('hello')")
    commit_skill(skills_dir, "echo", "Added echo skill")

    history = get_skill_history(skills_dir, "echo")
    assert len(history) == 1
    assert history[0]["message"] == "Added echo skill"


def test_get_history_multiple(skills_dir):
    init_skills_repo(skills_dir)
    (skills_dir / "echo").mkdir()

    (skills_dir / "echo" / "handler.py").write_text("v1")
    commit_skill(skills_dir, "echo", "v1")

    (skills_dir / "echo" / "handler.py").write_text("v2")
    commit_skill(skills_dir, "echo", "v2")

    (skills_dir / "echo" / "handler.py").write_text("v3")
    commit_skill(skills_dir, "echo", "v3")

    history = get_skill_history(skills_dir, "echo")
    assert len(history) == 3


def test_get_last_diff(skills_dir):
    init_skills_repo(skills_dir)
    (skills_dir / "echo").mkdir()
    (skills_dir / "echo" / "handler.py").write_text("print('hello')")
    commit_skill(skills_dir, "echo", "Added echo skill")

    diff = get_last_diff(skills_dir)
    assert "handler.py" in diff


def test_rollback_skill(skills_dir):
    init_skills_repo(skills_dir)
    (skills_dir / "echo").mkdir()

    # Commit v1
    (skills_dir / "echo" / "handler.py").write_text("version_1")
    commit_skill(skills_dir, "echo", "v1")
    history = get_skill_history(skills_dir, "echo")
    v1_hash = history[0]["hash"]

    # Commit v2
    (skills_dir / "echo" / "handler.py").write_text("version_2")
    commit_skill(skills_dir, "echo", "v2")
    assert (skills_dir / "echo" / "handler.py").read_text() == "version_2"

    # Rollback to v1
    rollback_skill(skills_dir, "echo", v1_hash)
    assert (skills_dir / "echo" / "handler.py").read_text() == "version_1"
