"""Tests for the reasoner (Step 1.4)."""

import json
from unittest.mock import patch

import pytest

from forgyn.db import get_messages, init_db, save_skill
from forgyn.models import ChatResponse, ModelConfig
from forgyn.reasoner import Reasoner
from forgyn.scheduler import Scheduler


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
def reasoner(model_config, db, skills_dir):
    return Reasoner(model_config=model_config, db=db, skills_dir=skills_dir)


def _mock_chat_text(text: str):
    """Create a mock that returns a simple text response."""
    async def _chat(*args, **kwargs):
        return ChatResponse(content=text)
    return _chat


def _mock_chat_tool_then_text(tool_name: str, tool_args: dict, final_text: str):
    """Create a mock that returns a tool call first, then text."""
    call_count = 0
    async def _chat(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return ChatResponse(
                content=None,
                tool_calls=[{
                    "id": "tc_1",
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "arguments": json.dumps(tool_args),
                    },
                }],
            )
        return ChatResponse(content=final_text)
    return _chat


@pytest.mark.asyncio
async def test_simple_conversation(reasoner):
    with patch("forgyn.reasoner.chat", new=_mock_chat_text("Hello there!")):
        cid = reasoner.new_conversation()
        response = await reasoner.run(cid, "Hi")
        assert response == "Hello there!"


@pytest.mark.asyncio
async def test_tool_call_loop(reasoner):
    mock = _mock_chat_tool_then_text("list_skills", {}, "You have no skills yet.")
    with patch("forgyn.reasoner.chat", new=mock):
        cid = reasoner.new_conversation()
        response = await reasoner.run(cid, "What skills do I have?")
        assert "no skills" in response.lower() or "You have" in response


@pytest.mark.asyncio
async def test_max_iterations(reasoner):
    """LLM that always returns tool calls should hit the iteration limit."""
    async def _always_tool_call(*args, **kwargs):
        return ChatResponse(
            content=None,
            tool_calls=[{
                "id": "tc_1", "type": "function",
                "function": {"name": "list_skills", "arguments": "{}"},
            }],
        )

    with patch("forgyn.reasoner.chat", new=_always_tool_call):
        cid = reasoner.new_conversation()
        response = await reasoner.run(cid, "Loop forever")
        assert "maximum" in response.lower()


@pytest.mark.asyncio
async def test_messages_persisted(reasoner, db):
    with patch("forgyn.reasoner.chat", new=_mock_chat_text("Saved!")):
        cid = reasoner.new_conversation()
        await reasoner.run(cid, "Test persistence")

    msgs = get_messages(db, cid)
    assert len(msgs) == 2  # user + assistant
    assert msgs[0]["role"] == "user"
    assert msgs[1]["role"] == "assistant"


@pytest.mark.asyncio
async def test_conversation_history(reasoner, db):
    """Second run includes messages from first run."""
    call_count = 0

    async def _counting_chat(config, messages, tools=None, temperature=0.7):
        nonlocal call_count
        call_count += 1
        # On second call, verify history is present
        if call_count == 2:
            user_msgs = [m for m in messages if m.role == "user"]
            assert len(user_msgs) == 2  # both user messages
        return ChatResponse(content=f"Response {call_count}")

    with patch("forgyn.reasoner.chat", new=_counting_chat):
        cid = reasoner.new_conversation()
        await reasoner.run(cid, "First message")
        await reasoner.run(cid, "Second message")


@pytest.mark.asyncio
async def test_list_skills_tool(reasoner):
    mock = _mock_chat_tool_then_text("list_skills", {}, "No skills found.")
    with patch("forgyn.reasoner.chat", new=mock):
        cid = reasoner.new_conversation()
        await reasoner.run(cid, "List skills")
        # Verify tool was called (message in DB)
        msgs = get_messages(reasoner.db, cid)
        tool_msgs = [m for m in msgs if m["role"] == "tool"]
        assert len(tool_msgs) == 1
        assert "No skills" in tool_msgs[0]["content"]


@pytest.mark.asyncio
async def test_file_tools_restricted(reasoner, skills_dir):
    """write_file outside skills/ should be rejected."""
    result = reasoner._tool_write_file({"path": "../../etc/passwd", "content": "hacked"})
    assert "Error" in result


@pytest.mark.asyncio
async def test_read_file_in_skills(reasoner, skills_dir):
    """read_file within skills/ should work."""
    test_file = skills_dir / "test.txt"
    test_file.write_text("hello")
    result = reasoner._tool_read_file({"path": "test.txt"})
    assert result == "hello"


@pytest.mark.asyncio
async def test_read_file_not_found(reasoner):
    result = reasoner._tool_read_file({"path": "nonexistent.txt"})
    assert "Error" in result


@pytest.mark.asyncio
async def test_read_file_strips_skills_prefix(reasoner, skills_dir):
    """LLMs often prepend 'skills/' — the tool should strip it."""
    skill_dir = skills_dir / "reminder"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "handler.py").write_text("async def run(args): pass")

    # Both with and without the prefix should work
    result_direct = reasoner._tool_read_file({"path": "reminder/handler.py"})
    result_prefixed = reasoner._tool_read_file({"path": "skills/reminder/handler.py"})
    assert result_direct == "async def run(args): pass"
    assert result_prefixed == result_direct


@pytest.mark.asyncio
async def test_write_file_strips_skills_prefix(reasoner, skills_dir):
    """write_file should also handle the 'skills/' prefix."""
    reasoner._tool_write_file({"path": "skills/test_skill/config.json", "content": "{}"})
    assert (skills_dir / "test_skill" / "config.json").read_text() == "{}"


@pytest.mark.asyncio
async def test_read_file_not_found_lists_existing(reasoner, skills_dir):
    """Error message should list existing files when the directory exists."""
    skill_dir = skills_dir / "reminder"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "handler.py").write_text("code")
    (skill_dir / "manifest.json").write_text("{}")

    result = reasoner._tool_read_file({"path": "reminder/wrong_name.py"})
    assert "Error" in result
    assert "handler.py" in result


# --- schedule_task tool ---


def test_schedule_task_tool(model_config, db, skills_dir):
    """Reasoner with scheduler can create schedules via the tool."""
    scheduler = Scheduler(db=db)
    r = Reasoner(model_config=model_config, db=db, skills_dir=skills_dir, scheduler=scheduler)

    result = r._tool_schedule_task({
        "schedule_type": "once",
        "schedule_value": "2099-01-01T00:00:00+00:00",
        "message": "Drink water",
    })
    assert "Scheduled" in result
    assert "Drink water" in result

    # Verify schedule was persisted with _system skill_name
    row = db.execute("SELECT * FROM schedules WHERE skill_name = '_system'").fetchone()
    assert row is not None
    assert row["message"] == "Drink water"


def test_schedule_task_tool_appears_in_tools(model_config, db, skills_dir):
    """schedule_task should appear in tool list when scheduler is present."""
    scheduler = Scheduler(db=db)
    r = Reasoner(model_config=model_config, db=db, skills_dir=skills_dir, scheduler=scheduler)
    tool_names = [t.name for t in r._get_tools()]
    assert "schedule_task" in tool_names


def test_schedule_task_tool_absent_without_scheduler(model_config, db, skills_dir):
    """schedule_task should NOT appear when no scheduler is set."""
    r = Reasoner(model_config=model_config, db=db, skills_dir=skills_dir)
    tool_names = [t.name for t in r._get_tools()]
    assert "schedule_task" not in tool_names


def test_schedule_task_tool_validation(model_config, db, skills_dir):
    """schedule_task should reject invalid inputs."""
    scheduler = Scheduler(db=db)
    r = Reasoner(model_config=model_config, db=db, skills_dir=skills_dir, scheduler=scheduler)

    result = r._tool_schedule_task({"schedule_type": "", "schedule_value": "x", "message": "m"})
    assert "Error" in result

    result = r._tool_schedule_task({"schedule_type": "bad", "schedule_value": "x", "message": "m"})
    assert "Error" in result
