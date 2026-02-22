"""Tests for built-in web search via LLM providers."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forgyn.db import init_db
from forgyn.models import ModelConfig, web_search
from forgyn.reasoner import Reasoner


# --- OpenAI web search ---


OPENAI_RESPONSE = {
    "output": [
        {"type": "web_search_call", "id": "ws_1", "status": "completed"},
        {
            "type": "message",
            "content": [
                {
                    "type": "output_text",
                    "text": "The AI summit in Delhi concluded on Feb 20.",
                    "annotations": [
                        {
                            "type": "url_citation",
                            "title": "AI Summit Delhi - The Hindu",
                            "url": "https://thehindu.com/ai-summit",
                            "start_index": 0,
                            "end_index": 20,
                        },
                        {
                            "type": "url_citation",
                            "title": "Summit Recap - Times",
                            "url": "https://timesofindia.com/summit",
                            "start_index": 21,
                            "end_index": 45,
                        },
                    ],
                }
            ],
        },
    ]
}


@pytest.mark.asyncio
async def test_openai_web_search():
    config = ModelConfig(provider="openai", model="gpt-4o", api_key="test-key")

    mock_resp = MagicMock()
    mock_resp.json.return_value = OPENAI_RESPONSE
    mock_resp.raise_for_status = MagicMock()

    with patch("forgyn.models.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await web_search(config, "AI summit Delhi")

    assert "AI summit in Delhi" in result["summary"]
    assert len(result["sources"]) == 2
    assert result["sources"][0]["url"] == "https://thehindu.com/ai-summit"
    assert "error" not in result


@pytest.mark.asyncio
async def test_openai_web_search_deduplicates_urls():
    """Duplicate URLs in annotations should appear only once in sources."""
    response = {
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": "Results here.",
                        "annotations": [
                            {"type": "url_citation", "title": "Same", "url": "https://a.com",
                             "start_index": 0, "end_index": 5},
                            {"type": "url_citation", "title": "Same", "url": "https://a.com",
                             "start_index": 6, "end_index": 10},
                        ],
                    }
                ],
            }
        ]
    }
    config = ModelConfig(provider="openai", model="gpt-4o", api_key="test")
    mock_resp = MagicMock()
    mock_resp.json.return_value = response
    mock_resp.raise_for_status = MagicMock()

    with patch("forgyn.models.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await web_search(config, "test")

    assert len(result["sources"]) == 1


# --- Anthropic web search ---


ANTHROPIC_RESPONSE = {
    "content": [
        {"type": "text", "text": "I'll search for that."},
        {
            "type": "server_tool_use",
            "id": "srvtoolu_1",
            "name": "web_search",
            "input": {"query": "AI summit Delhi"},
        },
        {
            "type": "web_search_tool_result",
            "tool_use_id": "srvtoolu_1",
            "content": [
                {
                    "type": "web_search_result",
                    "url": "https://example.com/summit",
                    "title": "AI Summit Coverage",
                    "page_age": "1 day ago",
                },
                {
                    "type": "web_search_result",
                    "url": "https://example.com/recap",
                    "title": "Summit Recap",
                    "page_age": "2 days ago",
                },
            ],
        },
        {"type": "text", "text": "The AI summit concluded with 88 countries signing a declaration."},
    ]
}


@pytest.mark.asyncio
async def test_anthropic_web_search():
    config = ModelConfig(provider="anthropic", model="claude-sonnet-4-20250514", api_key="test-key")

    mock_resp = MagicMock()
    mock_resp.json.return_value = ANTHROPIC_RESPONSE
    mock_resp.raise_for_status = MagicMock()

    with patch("forgyn.models.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await web_search(config, "AI summit Delhi")

    assert "88 countries" in result["summary"]
    assert len(result["sources"]) == 2
    assert result["sources"][0]["url"] == "https://example.com/summit"
    assert "error" not in result


# --- Unsupported providers ---


@pytest.mark.asyncio
async def test_ollama_returns_unsupported():
    config = ModelConfig(provider="ollama", model="llama3")
    result = await web_search(config, "test query")
    assert result["error"] == "Web search not available for ollama"
    assert result["sources"] == []


# --- Error handling ---


@pytest.mark.asyncio
async def test_web_search_connection_error():
    config = ModelConfig(provider="openai", model="gpt-4o", api_key="test")

    with patch("forgyn.models.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.side_effect = Exception("Connection refused")
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await web_search(config, "test")

    assert result.get("error")


# --- Reasoner tool integration ---


@pytest.mark.asyncio
async def test_web_search_tool_returns_summary_and_sources(tmp_db_path, tmp_data_dir):
    db = init_db(tmp_db_path)
    config = ModelConfig(provider="openai", model="gpt-4o", api_key="test")
    reasoner = Reasoner(model_config=config, db=db, skills_dir=tmp_data_dir / "skills")

    mock_result = {
        "summary": "The summit ended on Feb 20.",
        "sources": [
            {"title": "The Hindu", "url": "https://thehindu.com/summit"},
        ],
    }
    with patch("forgyn.reasoner.web_search", new_callable=AsyncMock, return_value=mock_result):
        result = await reasoner._tool_web_search({"query": "AI summit"})

    assert "summit ended on Feb 20" in result
    assert "https://thehindu.com/summit" in result
    assert "Sources:" in result


@pytest.mark.asyncio
async def test_web_search_tool_handles_error(tmp_db_path, tmp_data_dir):
    db = init_db(tmp_db_path)
    config = ModelConfig(provider="openai", model="gpt-4o", api_key="test")
    reasoner = Reasoner(model_config=config, db=db, skills_dir=tmp_data_dir / "skills")

    mock_result = {"summary": "", "sources": [], "error": "Connection refused"}
    with patch("forgyn.reasoner.web_search", new_callable=AsyncMock, return_value=mock_result):
        result = await reasoner._tool_web_search({"query": "test"})

    assert "Web search failed" in result


@pytest.mark.asyncio
async def test_web_search_tool_missing_query(tmp_db_path, tmp_data_dir):
    db = init_db(tmp_db_path)
    config = ModelConfig(provider="openai", model="gpt-4o", api_key="test")
    reasoner = Reasoner(model_config=config, db=db, skills_dir=tmp_data_dir / "skills")

    result = await reasoner._tool_web_search({"query": ""})
    assert "Error" in result
