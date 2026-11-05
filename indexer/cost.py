"""Cost estimation utilities for embedding and indexing."""

from __future__ import annotations

import logging
from typing import Dict, List

import tiktoken

logger = logging.getLogger(__name__)

# USD per 1k input tokens (embedding APIs bill on input tokens).
# Nebius Qwen3-Embedding-8B: $0.01 / 1M tokens (Token Factory catalog, 2025).
EMBEDDING_USD_PER_1K_TOKENS: Dict[str, float] = {
    "text-embedding-3-large": 0.00013,
    "text-embedding-3-small": 0.00002,
    "text-embedding-ada-002": 0.0001,
    "Qwen/Qwen3-Embedding-8B": 0.00001,
    "BAAI/bge-en-icl": 0.00001,
}

# Fallback when model id is unknown (conservative Nebius embedding tier).
DEFAULT_EMBEDDING_USD_PER_1K = 0.00001

_TOKEN_ENCODER = tiktoken.encoding_for_model("gpt-3.5-turbo")


def embedding_price_per_1k(model: str) -> float:
    """Return USD price per 1k embedding input tokens for a model id."""
    if model in EMBEDDING_USD_PER_1K_TOKENS:
        return EMBEDDING_USD_PER_1K_TOKENS[model]
    logger.debug(
        "No listed price for embed model %s; using default $%s/1k",
        model,
        DEFAULT_EMBEDDING_USD_PER_1K,
    )
    return DEFAULT_EMBEDDING_USD_PER_1K


def count_embedding_tokens(texts: List[str]) -> int:
    """Count tokens for embedding inputs (tiktoken cl100k_base via gpt-3.5-turbo)."""
    return sum(len(_TOKEN_ENCODER.encode(text)) for text in texts)


def estimate_embedding_cost(
    texts: List[str], model: str
) -> Dict[str, float | int | str]:
    """Estimate token count and USD cost for embedding a list of texts."""
    tokens = count_embedding_tokens(texts)
    price_per_1k = embedding_price_per_1k(model)
    cost_usd = (tokens / 1000.0) * price_per_1k
    return {
        "tokens": tokens,
        "cost_usd": cost_usd,
        "model": model,
        "text_count": len(texts),
        "price_per_1k_usd": price_per_1k,
    }


class CostEstimator:
    """Estimates costs for various operations."""

    def estimate_embedding_cost(
        self, texts: list, model: str
    ) -> Dict[str, float | int | str]:
        return estimate_embedding_cost(texts, model)

    def format_cost_summary(
        self, total_tokens: int, total_cost: float, model: str
    ) -> str:
        """Format a human-readable cost summary."""
        price = embedding_price_per_1k(model)
        return (
            f"Embedding summary: {total_tokens:,} tokens, "
            f"${total_cost:.6f} estimated ({model} @ ${price:.5f}/1k tokens)"
        )
