"""Tests for skill writer (Step 2.4)."""

import json
from unittest.mock import patch

import pytest

from forgyn.db import get_skill, init_db
from forgyn.models import ChatResponse, ModelConfig
from forgyn.skill_writer import SkillWriter, _extract_json, _strip_code_fences


@pytest.fixture
def model_config():
    return ModelConfig(provider="openai", model="gpt-4o", api_key="sk-test")


@pytest.fixture
def db(tmp_db_path):
    return init_db(tmp_db_path)


@pytest.fixture
def skills_dir(tmp_data_dir):
    d = tmp_data_dir / "skills"
    d.mkdir(exist_ok=True)
    return d


@pytest.fixture
def writer(model_config, db, skills_dir):
    return SkillWriter(
        model_config=model_config,
        db=db,
        skills_dir=skills_dir,
        permission_prompt_fn=lambda _: "y",  # Auto-approve
    )


# --- Utility tests ---


def test_strip_code_fences():
    text = "```python\nprint('hello')\n```"
    assert _strip_code_fences(text) == "print('hello')"


def test_strip_code_fences_no_fences():
    text = "print('hello')"
    assert _strip_code_fences(text) == "print('hello')"


def test_extract_json():
    text = '```json\n{"name": "test"}\n```'
    result = _extract_json(text)
    assert result == {"name": "test"}


def test_extract_json_no_fences():
    text = '{"name": "test"}'
    result = _extract_json(text)
    assert result == {"name": "test"}


# --- Mock LLM responses ---

MOCK_MANIFEST = json.dumps({
    "name": "echo",
    "description": "Echoes input back",
    "permissions": [],
    "dependencies": [],
    "entry_point": "handler.py",
    "test_file": "test_handler.py",
})

MOCK_HANDLER = '''\
async def run(args: dict) -> dict:
    text = args.get("text", "")
    return {"result": text, "error": None}
'''

MOCK_TESTS = '''\
import pytest
from handler import run

@pytest.mark.asyncio
async def test_echo():
    result = await run({"text": "hello"})
    assert result["result"] == "hello"
    assert result["error"] is None

@pytest.mark.asyncio
async def test_empty():
    result = await run({})
    assert result["result"] == ""
'''

MOCK_SMOKE_PASS = "REAL — This handler echoes the input back, which is real functionality."

MOCK_WEB_SEARCH_RESULT = {"summary": "", "sources": []}


def _make_chat_sequence(responses: list[str]):
    """Create a mock chat function that returns responses in order."""
    idx = 0

    async def _chat(*args, **kwargs):
        nonlocal idx
        text = responses[idx % len(responses)]
        idx += 1
        return ChatResponse(content=text)

    return _chat


# --- Skill writer tests ---


@pytest.mark.asyncio
async def test_generate_manifest(writer):
    mock = _make_chat_sequence([MOCK_MANIFEST])
    with patch("forgyn.skill_writer.chat", new=mock):
        manifest = await writer._generate_manifest("echo", "Echoes input")
    assert manifest["name"] == "echo"
    assert "permissions" in manifest


@pytest.mark.asyncio
async def test_generate_handler(writer):
    mock = _make_chat_sequence([MOCK_HANDLER])
    with patch("forgyn.skill_writer.chat", new=mock):
        code = await writer._generate_handler("echo", "Echoes input", [])
    assert "async def run" in code


@pytest.mark.asyncio
async def test_generate_tests(writer):
    mock = _make_chat_sequence([MOCK_TESTS])
    with patch("forgyn.skill_writer.chat", new=mock):
        code = await writer._generate_tests("echo", "Echoes input", MOCK_HANDLER)
    assert "test_echo" in code


@pytest.mark.asyncio
async def test_full_write_flow_mocked(writer, skills_dir):
    """Full flow with mocked LLM and mocked sandbox (no Docker needed)."""
    from forgyn.sandbox import SandboxResult

    # Sequence: manifest, handler, tests, smoke test
    mock_chat = _make_chat_sequence([MOCK_MANIFEST, MOCK_HANDLER, MOCK_TESTS, MOCK_SMOKE_PASS])

    async def mock_sandbox(*args, **kwargs):
        return SandboxResult(exit_code=0, stdout="1 passed", stderr="")

    async def mock_ws(*args, **kwargs):
        return MOCK_WEB_SEARCH_RESULT

    with patch("forgyn.skill_writer.chat", new=mock_chat), \
         patch("forgyn.skill_writer.run_in_sandbox", new=mock_sandbox), \
         patch("forgyn.skill_writer.web_search", new=mock_ws):
        result = await writer.write_skill("echo", "Echoes input back")

    assert result.success is True
    assert result.skill_name == "echo"
    assert (skills_dir / "echo" / "handler.py").exists()
    assert (skills_dir / "echo" / "test_handler.py").exists()
    assert (skills_dir / "echo" / "manifest.json").exists()


@pytest.mark.asyncio
async def test_permission_denied_aborts(model_config, db, skills_dir):
    """If user denies permissions, skill is not deployed."""
    writer = SkillWriter(
        model_config=model_config, db=db, skills_dir=skills_dir,
        permission_prompt_fn=lambda _: "n",  # Deny
    )

    # Manifest with a permission to trigger the prompt
    manifest_with_perm = json.dumps({
        "name": "danger", "description": "Dangerous skill",
        "permissions": ["network"],
        "dependencies": [], "entry_point": "handler.py", "test_file": "test_handler.py",
    })
    mock_chat = _make_chat_sequence([manifest_with_perm])

    with patch("forgyn.skill_writer.chat", new=mock_chat):
        result = await writer.write_skill("danger", "Something dangerous")

    assert result.success is False
    assert "denied" in result.error.lower()
    assert not (skills_dir / "danger" / "handler.py").exists()


@pytest.mark.asyncio
async def test_test_failure_retries(writer, skills_dir):
    """Sandbox fails twice then passes — should succeed after retries."""
    from forgyn.sandbox import SandboxResult

    call_count = 0

    async def mock_sandbox(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            return SandboxResult(exit_code=1, stdout="FAILED", stderr="assertion error")
        return SandboxResult(exit_code=0, stdout="1 passed", stderr="")

    # LLM calls: manifest, handler, tests, fix1, fix2, smoke test
    mock_chat = _make_chat_sequence([
        MOCK_MANIFEST, MOCK_HANDLER, MOCK_TESTS, MOCK_HANDLER, MOCK_HANDLER, MOCK_SMOKE_PASS,
    ])

    async def mock_ws(*args, **kwargs):
        return MOCK_WEB_SEARCH_RESULT

    with patch("forgyn.skill_writer.chat", new=mock_chat), \
         patch("forgyn.skill_writer.run_in_sandbox", new=mock_sandbox), \
         patch("forgyn.skill_writer.web_search", new=mock_ws):
        result = await writer.write_skill("echo", "Echoes input")

    assert result.success is True
    assert call_count == 3  # 2 failures + 1 success


@pytest.mark.asyncio
async def test_test_failure_aborts_after_max(writer, skills_dir):
    """Sandbox always fails — should abort after MAX_RETRIES."""
    from forgyn.sandbox import SandboxResult

    async def mock_sandbox(*args, **kwargs):
        return SandboxResult(exit_code=1, stdout="FAILED", stderr="error")

    mock_chat = _make_chat_sequence([
        MOCK_MANIFEST, MOCK_HANDLER, MOCK_TESTS,
        MOCK_HANDLER, MOCK_HANDLER,  # fix attempts
    ])

    async def mock_ws(*args, **kwargs):
        return MOCK_WEB_SEARCH_RESULT

    with patch("forgyn.skill_writer.chat", new=mock_chat), \
         patch("forgyn.skill_writer.run_in_sandbox", new=mock_sandbox), \
         patch("forgyn.skill_writer.web_search", new=mock_ws):
        result = await writer.write_skill("echo", "Echoes input")

    assert result.success is False
    assert "failed" in result.error.lower()


@pytest.mark.asyncio
async def test_skill_saved_to_db(writer, db, skills_dir):
    """After successful write, skill should be in the database."""
    from forgyn.sandbox import SandboxResult

    mock_chat = _make_chat_sequence([MOCK_MANIFEST, MOCK_HANDLER, MOCK_TESTS, MOCK_SMOKE_PASS])

    async def mock_sandbox(*args, **kwargs):
        return SandboxResult(exit_code=0, stdout="1 passed", stderr="")

    async def mock_ws(*args, **kwargs):
        return MOCK_WEB_SEARCH_RESULT

    with patch("forgyn.skill_writer.chat", new=mock_chat), \
         patch("forgyn.skill_writer.run_in_sandbox", new=mock_sandbox), \
         patch("forgyn.skill_writer.web_search", new=mock_ws):
        await writer.write_skill("echo", "Echoes input")

    skill = get_skill(db, "echo")
    assert skill is not None
    assert skill["status"] == "active"


@pytest.mark.asyncio
async def test_audit_commit_created(writer, skills_dir):
    """After successful write, a git commit should exist."""
    from forgyn.sandbox import SandboxResult
    from forgyn.audit import get_skill_history

    mock_chat = _make_chat_sequence([MOCK_MANIFEST, MOCK_HANDLER, MOCK_TESTS, MOCK_SMOKE_PASS])

    async def mock_sandbox(*args, **kwargs):
        return SandboxResult(exit_code=0, stdout="1 passed", stderr="")

    async def mock_ws(*args, **kwargs):
        return MOCK_WEB_SEARCH_RESULT

    with patch("forgyn.skill_writer.chat", new=mock_chat), \
         patch("forgyn.skill_writer.run_in_sandbox", new=mock_sandbox), \
         patch("forgyn.skill_writer.web_search", new=mock_ws):
        await writer.write_skill("echo", "Echoes input")

    history = get_skill_history(skills_dir, "echo")
    assert len(history) >= 1
    assert "echo" in history[0]["message"].lower()


# --- Smoke test ---


@pytest.mark.asyncio
async def test_smoke_test_rejects_stub(writer, skills_dir):
    """Smoke test should reject skills with stub/placeholder output."""
    from forgyn.sandbox import SandboxResult

    mock_chat = _make_chat_sequence([
        MOCK_MANIFEST, MOCK_HANDLER, MOCK_TESTS,
        "STUB — This handler returns hardcoded fake data.",
    ])

    async def mock_sandbox(*args, **kwargs):
        return SandboxResult(exit_code=0, stdout="1 passed", stderr="")

    async def mock_ws(*args, **kwargs):
        return MOCK_WEB_SEARCH_RESULT

    with patch("forgyn.skill_writer.chat", new=mock_chat), \
         patch("forgyn.skill_writer.run_in_sandbox", new=mock_sandbox), \
         patch("forgyn.skill_writer.web_search", new=mock_ws):
        result = await writer.write_skill("fake", "Something fake")

    assert result.success is False
    assert "smoke test" in result.error.lower()


# --- Modify skill ---


@pytest.mark.asyncio
async def test_modify_skill(writer, skills_dir):
    """modify_skill should rewrite handler, re-test, and redeploy."""
    from forgyn.sandbox import SandboxResult

    # First, create the skill files manually
    skill_dir = skills_dir / "echo"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "handler.py").write_text(MOCK_HANDLER)
    (skill_dir / "manifest.json").write_text(MOCK_MANIFEST)

    new_handler = '''\
async def run(args: dict) -> dict:
    text = args.get("text", "")
    return {"result": text.upper(), "error": None}
'''
    # Sequence: rewritten handler, tests
    mock_chat = _make_chat_sequence([new_handler, MOCK_TESTS])

    async def mock_sandbox(*args, **kwargs):
        return SandboxResult(exit_code=0, stdout="1 passed", stderr="")

    with patch("forgyn.skill_writer.chat", new=mock_chat), \
         patch("forgyn.skill_writer.run_in_sandbox", new=mock_sandbox):
        result = await writer.modify_skill("echo", "Make it uppercase")

    assert result.success is True
    assert "upper()" in (skill_dir / "handler.py").read_text()


@pytest.mark.asyncio
async def test_modify_nonexistent_skill(writer):
    """modify_skill should fail gracefully for missing skills."""
    result = await writer.modify_skill("nonexistent", "fix it")
    assert result.success is False
    assert "not found" in result.error.lower()
