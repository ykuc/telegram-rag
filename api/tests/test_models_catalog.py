"""Tests for provider chat model catalog."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.append(str(BASE_DIR))

from app.models import (
    entry_to_model_option,
    fetch_chat_models_from_provider,
    is_chat_model_entry,
)
from app.settings import settings


def test_is_chat_model_entry_keeps_missing_modality():
    assert is_chat_model_entry({"id": "gpt-4o"}) is True


def test_is_chat_model_entry_filters_non_text():
    assert (
        is_chat_model_entry(
            {
                "id": "BAAI/bge-en-icl",
                "architecture": {"modality": "text->embedding"},
            }
        )
        is False
    )
    assert (
        is_chat_model_entry(
            {
                "id": "meta-llama/Meta-Llama-3.1-70B-Instruct",
                "architecture": {"modality": "text->text"},
            }
        )
        is True
    )


def test_entry_to_model_option_uses_name():
    option = entry_to_model_option(
        {"id": "meta-llama/Meta-Llama-3.1-70B-Instruct", "name": "Llama 70B"}
    )
    assert option == {
        "id": "meta-llama/Meta-Llama-3.1-70B-Instruct",
        "label": "Llama 70B",
    }


@pytest.mark.asyncio
async def test_fetch_chat_models_from_provider(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr(
        settings,
        "openai_base_url",
        "https://api.tokenfactory.nebius.com/v1/",
    )

    payload = {
        "data": [
            {
                "id": "BAAI/bge-en-icl",
                "architecture": {"modality": "text->embedding"},
            },
            {
                "id": "meta-llama/Meta-Llama-3.1-70B-Instruct",
                "name": "Llama 70B",
                "architecture": {"modality": "text->text"},
            },
            {"id": "gpt-4o"},
        ]
    }

    mock_response = httpx.Response(200, json=payload, request=httpx.Request("GET", "http://test"))
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("app.models.httpx.AsyncClient", return_value=mock_client):
        models = await fetch_chat_models_from_provider()

    assert len(models) == 2
    ids = {m["id"] for m in models}
    assert "meta-llama/Meta-Llama-3.1-70B-Instruct" in ids
    assert "gpt-4o" in ids
    assert "BAAI/bge-en-icl" not in ids


def test_models_endpoint_provider_error(monkeypatch):
    from test_auth import get_client, login, reset_attempts

    async def _fail():
        raise httpx.HTTPStatusError(
            "bad",
            request=httpx.Request("GET", "http://test"),
            response=httpx.Response(401),
        )

    monkeypatch.setattr("app.main.fetch_chat_models_from_provider", _fail)
    reset_attempts()
    client = get_client()
    assert login(client).status_code == 200
    response = client.get("/models")
    assert response.status_code == 502
