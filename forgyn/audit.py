"""Git-based audit logging for the skills directory."""

from __future__ import annotations

import subprocess
from pathlib import Path


def _git(skills_dir: Path, *args: str) -> subprocess.CompletedProcess:
    """Run a git command in the skills directory."""
    return subprocess.run(
        ["git", *args],
        cwd=str(skills_dir),
        capture_output=True,
        text=True,
        timeout=30,
    )


def init_skills_repo(skills_dir: Path) -> None:
    """Initialize the skills directory as a git repo if it isn't one already."""
    git_dir = skills_dir / ".git"
    if git_dir.exists():
        return
    skills_dir.mkdir(parents=True, exist_ok=True)
    _git(skills_dir, "init")
    _git(skills_dir, "config", "user.email", "forgyn@openforgyn.local")
    _git(skills_dir, "config", "user.name", "Forgyn")


def commit_skill(skills_dir: Path, skill_name: str, message: str) -> str:
    """Stage and commit changes for a skill. Returns the commit hash."""
    init_skills_repo(skills_dir)

    skill_path = skills_dir / skill_name
    if skill_path.exists():
        _git(skills_dir, "add", skill_name)
    else:
        # Skill was deleted — stage the removal
        _git(skills_dir, "add", "-A")

    result = _git(skills_dir, "commit", "-m", message, "--allow-empty")
    if result.returncode != 0:
        # Nothing to commit, or error
        return ""

    # Get the commit hash
    hash_result = _git(skills_dir, "rev-parse", "HEAD")
    return hash_result.stdout.strip()


def get_skill_history(skills_dir: Path, skill_name: str) -> list[dict]:
    """Get commit history for a specific skill."""
    init_skills_repo(skills_dir)
    result = _git(
        skills_dir, "log",
        "--format=%H|%s|%aI",
        "--", skill_name,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return []

    history = []
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        parts = line.split("|", 2)
        if len(parts) == 3:
            history.append({
                "hash": parts[0],
                "message": parts[1],
                "date": parts[2],
            })
    return history


def get_last_diff(skills_dir: Path) -> str:
    """Get the diff of the most recent commit."""
    init_skills_repo(skills_dir)
    result = _git(skills_dir, "diff", "HEAD~1", "HEAD")
    if result.returncode != 0:
        # Might be the first commit, show the full tree
        result = _git(skills_dir, "show", "--stat", "HEAD")
    return result.stdout


def rollback_skill(skills_dir: Path, skill_name: str, commit_hash: str) -> None:
    """Restore a skill to a previous version by checking out its files."""
    init_skills_repo(skills_dir)

    # Checkout the skill directory from the target commit
    _git(skills_dir, "checkout", commit_hash, "--", skill_name)

    # Commit the rollback
    commit_skill(skills_dir, skill_name, f"Rollback {skill_name} to {commit_hash[:8]}")
