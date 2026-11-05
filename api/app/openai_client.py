"""Shared OpenAI-compatible async client factory."""

from openai import AsyncOpenAI

from .settings import settings


def create_openai_client(*, api_key: str | None = None) -> AsyncOpenAI:
    """Build AsyncOpenAI for OpenAI or compatible providers (e.g. Token Factory)."""
    key = api_key if api_key is not None else settings.openai_api_key
    if not key:
        raise ValueError("OPENAI_API_KEY is required")
    kwargs: dict[str, str] = {"api_key": key}
    base_url = settings.openai_base_url
    if isinstance(base_url, str) and base_url.strip():
        kwargs["base_url"] = base_url.strip()
    return AsyncOpenAI(**kwargs)
