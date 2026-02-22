"""End-to-end CLI tests (Step 4.1)."""

from click.testing import CliRunner

from forgyn.cli import main


def test_doctor_checks_docker(tmp_path):
    runner = CliRunner()
    result = runner.invoke(main, ["--data-dir", str(tmp_path), "doctor"])
    assert result.exit_code == 0
    assert "Docker installed" in result.output
    assert "Docker running" in result.output


def test_doctor_checks_python(tmp_path):
    runner = CliRunner()
    result = runner.invoke(main, ["--data-dir", str(tmp_path), "doctor"])
    assert "Python" in result.output
    assert "[OK]" in result.output  # Python 3.12+ should pass


def test_doctor_checks_api_key(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    runner = CliRunner()
    result = runner.invoke(main, ["--data-dir", str(tmp_path), "doctor"])
    assert "openai API key" in result.output
    assert "set" in result.output


def test_doctor_missing_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    runner = CliRunner()
    result = runner.invoke(main, ["--data-dir", str(tmp_path), "doctor"])
    assert "not set" in result.output


def test_history_empty(tmp_path):
    runner = CliRunner()
    result = runner.invoke(main, ["--data-dir", str(tmp_path), "history"])
    assert result.exit_code == 0
    assert "No conversations" in result.output


def test_history_shows_conversations(tmp_path):
    from forgyn.db import init_db, create_conversation
    db = init_db(tmp_path / "forgyn.db")
    create_conversation(db, title="Test chat")
    create_conversation(db, title="Another chat")

    runner = CliRunner()
    result = runner.invoke(main, ["--data-dir", str(tmp_path), "history"])
    assert result.exit_code == 0
    assert "Test chat" in result.output
    assert "Another chat" in result.output


def test_rollback_no_history(tmp_path):
    (tmp_path / "skills").mkdir()
    runner = CliRunner()
    result = runner.invoke(main, ["--data-dir", str(tmp_path), "rollback", "nonexistent", "abc123"])
    assert result.exit_code == 0
    assert "No history" in result.output


def test_rollback_invalid_hash(tmp_path):
    from forgyn.audit import init_skills_repo, commit_skill
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    init_skills_repo(skills_dir)
    (skills_dir / "echo").mkdir()
    (skills_dir / "echo" / "handler.py").write_text("v1")
    commit_skill(skills_dir, "echo", "v1")

    runner = CliRunner()
    result = runner.invoke(main, ["--data-dir", str(tmp_path), "rollback", "echo", "badhash"])
    assert "not found" in result.output
    assert "Available commits" in result.output


def test_rollback_success(tmp_path):
    from forgyn.audit import init_skills_repo, commit_skill, get_skill_history
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    init_skills_repo(skills_dir)
    (skills_dir / "echo").mkdir()

    (skills_dir / "echo" / "handler.py").write_text("version_1")
    commit_skill(skills_dir, "echo", "v1")
    v1_hash = get_skill_history(skills_dir, "echo")[0]["hash"]

    (skills_dir / "echo" / "handler.py").write_text("version_2")
    commit_skill(skills_dir, "echo", "v2")

    runner = CliRunner()
    result = runner.invoke(main, ["--data-dir", str(tmp_path), "rollback", "echo", v1_hash])
    assert "Rolled back" in result.output
    assert (skills_dir / "echo" / "handler.py").read_text() == "version_1"
