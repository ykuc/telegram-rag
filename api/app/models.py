"""Chat model catalog from the configured OpenAI-compatible provider."""

from __future__ import annotations

import logging
from typing import Any, Dict, List

import httpx

from .settings import settings

logger = logging.getLogger(__name__)

DEFAULT_OPENAI_V1 = "https://api.openai.com/v1"


def _provider_models_url() -> str:
    base = (settings.openai_base_url or DEFAULT_OPENAI_V1).rstrip("/")
    return f"{base}/models?verbose=true"


def is_chat_model_entry(entry: Dict[str, Any]) -> bool:
    """Include text chat models; keep entries without modality (official OpenAI)."""
    architecture = entry.get("architecture")
    if not isinstance(architecture, dict):
        return True
    modality = architecture.get("modality")
    if modality is None:
        return True
    return modality == "text->text"


def entry_to_model_option(entry: Dict[str, Any]) -> Dict[str, str]:
    model_id = entry.get("id")
    if not model_id or not isinstance(model_id, str):
        raise ValueError("Model entry missing id")
    name = entry.get("name")
    label = name if isinstance(name, str) and name.strip() else model_id
    return {"id": model_id, "label": label}


async def fetch_chat_models_from_provider() -> List[Dict[str, str]]:
    """List chat models from GET /v1/models?verbose=true."""
    if not settings.openai_api_key:
        raise ValueError("OPENAI_API_KEY is required")

    url = _provider_models_url()
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(
            url,
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
        )
        response.raise_for_status()
        payload = response.json()

    data = payload.get("data")
    if not isinstance(data, list):
        raise ValueError("Invalid models list response from provider")

    models: List[Dict[str, str]] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        if not is_chat_model_entry(entry):
            continue
        try:
            models.append(entry_to_model_option(entry))
        except ValueError:
            logger.warning("Skipping invalid model entry: %s", entry)
            continue

    models.sort(key=lambda item: item["label"].lower())
    if not models:
        raise ValueError("No chat models returned from provider")
    return models
