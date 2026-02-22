"""Tests for long-term memory with semantic retrieval."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from forgyn.db import (
    delete_expired_memories,
    delete_memory,
    get_memories_with_embeddings,
    get_memory,
    init_db,
    list_memories,
    save_memory,
)
from forgyn.reasoner import Reasoner, _cosine_similarity


@pytest.fixture
def db(tmp_db_path):
    return init_db(tmp_db_path)


# --- DB CRUD ---


def test_save_and_get_memory(db):
    save_memory(db, "user_name", "Dr. Junaid Farooq", "identity")
    mem = get_memory(db, "user_name")
    assert mem is not None
    assert mem["value"] == "Dr. Junaid Farooq"
    assert mem["category"] == "identity"


def test_memory_upsert(db):
    save_memory(db, "city", "London", "preference")
    save_memory(db, "city", "Paris", "preference")
    mem = get_memory(db, "city")
    assert mem["value"] == "Paris"


def test_list_memories_all(db):
    save_memory(db, "name", "Junaid", "identity")
    save_memory(db, "project", "OpenForgyn", "context")
    assert len(list_memories(db)) == 2


def test_list_memories_by_category(db):
    save_memory(db, "name", "Junaid", "identity")
    save_memory(db, "project", "OpenForgyn", "context")
    assert len(list_memories(db, category="identity")) == 1
    assert len(list_memories(db, category="context")) == 1
    assert len(list_memories(db, category="preference")) == 0


def test_delete_memory(db):
    save_memory(db, "temp", "value", "context")
    assert delete_memory(db, "temp") is True
    assert get_memory(db, "temp") is None


def test_delete_nonexistent_memory(db):
    assert delete_memory(db, "nope") is False


def test_save_memory_with_embedding(db):
    emb = [0.1, 0.2, 0.3]
    save_memory(db, "fact", "test", "knowledge", embedding=emb)
    mem = get_memory(db, "fact")
    assert mem["embedding"] is not None
    assert json.loads(mem["embedding"]) == emb


def test_get_memories_with_embeddings(db):
    save_memory(db, "name", "Junaid", "identity", embedding=[0.1, 0.2])
    save_memory(db, "project", "OpenForgyn", "context", embedding=[0.3, 0.4])
    save_memory(db, "no_emb", "test", "knowledge")

    result = get_memories_with_embeddings(db)
    assert len(result) == 1  # Only context/knowledge with embeddings
    assert result[0]["key"] == "project"


# --- TTL Expiry ---


def test_delete_expired_memories(db):
    # Insert a context memory with updated_at 10 days ago (TTL is 7)
    old_date = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    db.execute(
        "INSERT INTO memories (key, value, category, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?)",
        ("old_context", "stale", "context", old_date, old_date),
    )
    # Insert a fresh identity memory (never expires)
    save_memory(db, "name", "Junaid", "identity")
    db.commit()

    deleted = delete_expired_memories(db)
    assert deleted == 1
    assert get_memory(db, "old_context") is None
    assert get_memory(db, "name") is not None


def test_identity_never_expires(db):
    old_date = (datetime.now(timezone.utc) - timedelta(days=3650)).isoformat()
    db.execute(
        "INSERT INTO memories (key, value, category, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?)",
        ("name", "Junaid", "identity", old_date, old_date),
    )
    db.commit()
    deleted = delete_expired_memories(db)
    assert deleted == 0
    assert get_memory(db, "name") is not None


# --- Cosine Similarity ---


def test_cosine_identical_vectors():
    assert _cosine_similarity([1, 0, 0], [1, 0, 0]) == pytest.approx(1.0)


def test_cosine_orthogonal_vectors():
    assert _cosine_similarity([1, 0], [0, 1]) == pytest.approx(0.0)


def test_cosine_opposite_vectors():
    assert _cosine_similarity([1, 0], [-1, 0]) == pytest.approx(-1.0)


def test_cosine_empty_vectors():
    assert _cosine_similarity([], []) == 0.0


def test_cosine_mismatched_lengths():
    assert _cosine_similarity([1, 2], [1, 2, 3]) == 0.0


def test_cosine_zero_vector():
    assert _cosine_similarity([0, 0], [1, 1]) == 0.0


# --- Memory in System Prompt ---


def test_core_memories_in_system_prompt(db, tmp_data_dir):
    from forgyn.models import ModelConfig

    save_memory(db, "user_name", "Junaid", "identity")
    save_memory(db, "timezone", "UTC+5", "preference")

    config = ModelConfig(provider="openai", model="gpt-4o", api_key="test")
    reasoner = Reasoner(model_config=config, db=db, skills_dir=tmp_data_dir / "skills")

    section = reasoner._build_memory_section()
    assert "user_name: Junaid" in section
    assert "timezone: UTC+5" in section
    assert "What you know about the user" in section


def test_no_memory_section_when_empty(db, tmp_data_dir):
    from forgyn.models import ModelConfig

    config = ModelConfig(provider="openai", model="gpt-4o", api_key="test")
    reasoner = Reasoner(model_config=config, db=db, skills_dir=tmp_data_dir / "skills")

    section = reasoner._build_memory_section()
    assert section == ""


# --- Tool Execution ---


@pytest.mark.asyncio
async def test_remember_tool(db, tmp_data_dir):
    from forgyn.models import ModelConfig

    config = ModelConfig(provider="anthropic", model="claude-sonnet-4-20250514", api_key="test")
    reasoner = Reasoner(model_config=config, db=db, skills_dir=tmp_data_dir / "skills")

    result = await reasoner._tool_remember({
        "key": "user_name",
        "value": "Dr. Junaid Farooq",
        "category": "identity",
    })
    assert "Remembered" in result

    mem = get_memory(db, "user_name")
    assert mem is not None
    assert mem["value"] == "Dr. Junaid Farooq"
    assert mem["category"] == "identity"


@pytest.mark.asyncio
async def test_forget_tool(db, tmp_data_dir):
    from forgyn.models import ModelConfig

    save_memory(db, "temp", "temp value", "context")

    config = ModelConfig(provider="openai", model="gpt-4o", api_key="test")
    reasoner = Reasoner(model_config=config, db=db, skills_dir=tmp_data_dir / "skills")

    result = reasoner._tool_forget({"key": "temp"})
    assert "Forgot" in result
    assert get_memory(db, "temp") is None


@pytest.mark.asyncio
async def test_forget_nonexistent(db, tmp_data_dir):
    from forgyn.models import ModelConfig

    config = ModelConfig(provider="openai", model="gpt-4o", api_key="test")
    reasoner = Reasoner(model_config=config, db=db, skills_dir=tmp_data_dir / "skills")

    result = reasoner._tool_forget({"key": "nope"})
    assert "No memory found" in result


@pytest.mark.asyncio
async def test_remember_invalid_category(db, tmp_data_dir):
    from forgyn.models import ModelConfig

    config = ModelConfig(provider="openai", model="gpt-4o", api_key="test")
    reasoner = Reasoner(model_config=config, db=db, skills_dir=tmp_data_dir / "skills")

    result = await reasoner._tool_remember({"key": "k", "value": "v", "category": "invalid"})
    assert "Error" in result


# --- Semantic Retrieval ---


@pytest.mark.asyncio
async def test_retrieve_with_no_embedding_support(db, tmp_data_dir):
    """Anthropic provider returns empty embeddings, so retrieval returns nothing."""
    from forgyn.models import ModelConfig

    config = ModelConfig(provider="anthropic", model="claude-sonnet-4-20250514", api_key="test")
    reasoner = Reasoner(model_config=config, db=db, skills_dir=tmp_data_dir / "skills")

    result = await reasoner._retrieve_relevant_memories("test query")
    assert result == []


@pytest.mark.asyncio
async def test_retrieve_with_embeddings(db, tmp_data_dir):
    """Mock embed() to test semantic retrieval end-to-end."""
    from forgyn.models import ModelConfig

    # Store memories with embeddings
    save_memory(db, "project", "OpenForgyn", "context", embedding=[0.9, 0.1, 0.0])
    save_memory(db, "server", "192.168.1.1", "knowledge", embedding=[0.1, 0.9, 0.0])

    config = ModelConfig(provider="openai", model="gpt-4o", api_key="test")
    reasoner = Reasoner(model_config=config, db=db, skills_dir=tmp_data_dir / "skills")

    # Mock embed to return a vector similar to "project"
    with patch("forgyn.reasoner.embed", new_callable=AsyncMock, return_value=[0.85, 0.15, 0.0]):
        result = await reasoner._retrieve_relevant_memories("what project am I working on?")

    assert len(result) >= 1
    assert result[0]["key"] == "project"  # Most similar
