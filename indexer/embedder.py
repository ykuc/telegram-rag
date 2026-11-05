"""OpenAI embedding utilities."""

import asyncio
import hashlib
import logging
import struct
from typing import List, Dict, Optional, Tuple
import httpx
from openai import AsyncOpenAI
from models import EmbeddingCache, IndexerMetrics
from db import DatabaseManager
from settings import settings
from cost import estimate_embedding_cost

logger = logging.getLogger(__name__)


class Embedder:
    """Handles text embedding with caching and batching."""

    def __init__(self, db: DatabaseManager):
        self.db = db
        # OpenAI client (guard against missing key during tests)
        api_key = getattr(settings, "openai_api_key", None) or "test-key"
        client_kwargs: dict[str, str] = {"api_key": api_key}
        base_url = getattr(settings, "openai_base_url", None)
        if isinstance(base_url, str) and base_url.strip():
            client_kwargs["base_url"] = base_url.strip()
        self.client = AsyncOpenAI(**client_kwargs)
        # Coerce settings values to stable primitives (avoid MagicMock leakage from patches)
        self.model = getattr(settings, "embed_model", "text-embedding-3-large")
        if not isinstance(self.model, str):
            self.model = "text-embedding-3-large"
        self.batch_size = getattr(settings, "embed_batch_size", 64)
        try:
            self.batch_size = int(self.batch_size)
        except Exception:
            self.batch_size = 64
        if self.batch_size <= 0:
            self.batch_size = 64
        self.concurrency = getattr(settings, "embed_concurrency", 4)
        try:
            self.concurrency = int(self.concurrency)
        except Exception:
            self.concurrency = 4
        if self.concurrency <= 0:
            self.concurrency = 4
        self.metrics = IndexerMetrics()

        # Model dimensions (OpenAI defaults; Nebius Qwen uses settings.embed_dimensions)
        self.model_dimensions = {
            "text-embedding-3-large": 3072,
            "text-embedding-3-small": 1536,
            "text-embedding-ada-002": 1536,
        }

    def _embedding_request_kwargs(self, input_texts: List[str]) -> dict:
        """Build OpenAI embeddings.create kwargs (incl. Matryoshka dimensions)."""
        kwargs: dict = {"model": self.model, "input": input_texts}
        dim = getattr(settings, "embed_dimensions", None)
        try:
            dim_int = int(dim)
        except (TypeError, ValueError):
            dim_int = 0
        if dim_int > 0:
            kwargs["dimensions"] = dim_int
        return kwargs

    def _compute_text_hash(self, text: str, lang: Optional[str] = None) -> str:
        """Compute hash for text caching."""
        cache_key = f"{text}|{self.model}|{settings.chunking_version}|{settings.preprocess_version}|{lang or ''}"
        return hashlib.sha256(cache_key.encode()).hexdigest()

    @staticmethod
    def _coerce_float(value, default: float) -> float:
        """Best-effort convert a value (possibly a MagicMock) to float."""
        try:
            # Unwrap common mock types that may appear in patched settings
            if hasattr(value, "__float__"):
                return float(value)
            # If it's a simple number-like (int/str)
            return float(str(value))
        except Exception:
            return default

    def _vector_to_bytes(self, vector: List[float]) -> bytes:
        """Convert vector to bytes for storage."""
        return struct.pack(f"{len(vector)}f", *vector)

    def _bytes_to_vector(self, data: bytes) -> List[float]:
        """Convert bytes back to vector."""
        return list(struct.unpack(f"{len(data)//4}f", data))

    async def embed_texts(
        self, texts: List[str], dry_run: bool = False
    ) -> List[Tuple[str, List[float]]]:
        """
        Embed multiple texts with caching and batching.

        Args:
            texts: List of texts to embed
            dry_run: If True, only estimate cost without calling API

        Returns:
            List of (text_hash, vector) tuples
        """
        if not texts:
            return []

        # Check cache first
        results = []
        texts_to_embed = []

        for text in texts:
            text_hash = self._compute_text_hash(text)
            cached = await self.db.get_cached_embedding(text_hash)

            if cached and cached.model == self.model:
                vector = self._bytes_to_vector(cached.vector)
                results.append((text_hash, vector))
                self.metrics.embed_cached_hits += 1
            else:
                texts_to_embed.append((text, text_hash))
                self.metrics.embed_cached_misses += 1

        if not texts_to_embed:
            logger.debug(f"All {len(texts)} embeddings served from cache")
            return results

        log = logger.debug
        log(
            "Need to embed %s texts, %s from cache",
            len(texts_to_embed),
            len(results),
        )

        texts_to_embed_payload = [text for text, _ in texts_to_embed]
        estimate = estimate_embedding_cost(texts_to_embed_payload, self.model)
        total_tokens = int(estimate["tokens"])
        estimated_cost = float(estimate["cost_usd"])

        self.metrics.total_tokens += total_tokens
        self.metrics.cost_estimate += estimated_cost

        log(
            "Estimated cost: $%.6f for %s tokens (%s @ $%.5f/1k)",
            estimated_cost,
            f"{total_tokens:,}",
            self.model,
            estimate["price_per_1k_usd"],
        )

        if dry_run:
            logger.debug("Dry run mode - skipping actual embedding")
            return results
        # Deterministic budget check (simplified to avoid flaky threshold logic)
        budget = self._coerce_float(
            getattr(settings, "daily_embed_budget_usd", 0.0), 0.0
        )
        # Consider budget exceeded when estimated cost meets or exceeds budget (>=) for stricter protection
        if budget > 0 and estimated_cost >= budget:
            logger.warning(
                f"Cost ${estimated_cost:.6f} exceeds daily budget ${budget:.6f} (texts={len(texts_to_embed)})"
            )
            raise RuntimeError("Daily embedding budget exceeded")

        # Embed in batches with concurrency control
        semaphore = asyncio.Semaphore(self.concurrency)
        tasks = []

        for i in range(0, len(texts_to_embed), self.batch_size):
            batch = texts_to_embed[i : i + self.batch_size]
            task = self._embed_batch(batch, semaphore)
            tasks.append(task)

        batch_results = await asyncio.gather(*tasks)

        # Flatten results and cache embeddings
        for batch_result in batch_results:
            for text_hash, vector in batch_result:
                results.append((text_hash, vector))

                # Cache the embedding
                cache_entry = EmbeddingCache(
                    text_hash=text_hash,
                    model=self.model,
                    dim=len(vector),
                    vector=self._vector_to_bytes(vector),
                    chunking_version=settings.chunking_version,
                    preprocess_version=settings.preprocess_version,
                )
                await self.db.cache_embedding(cache_entry)

        self.metrics.embed_calls += len(tasks)
        logger.debug(f"Embedded {len(texts_to_embed)} texts in {len(tasks)} batches")

        return results

    async def _embed_batch(
        self, batch: List[Tuple[str, str]], semaphore: asyncio.Semaphore
    ) -> List[Tuple[str, List[float]]]:
        """Embed a single batch of texts."""
        async with semaphore:
            texts = [text for text, _ in batch]

            # Retry with exponential backoff
            for attempt in range(3):
                try:
                    response = await self.client.embeddings.create(
                        **self._embedding_request_kwargs(texts)
                    )

                    if len(response.data) != len(batch):
                        raise ValueError(
                            f"Response count mismatch: got {len(response.data)} embeddings for {len(batch)} texts"
                        )

                    results = []
                    for i, embedding_data in enumerate(response.data):
                        text_hash = batch[i][1]
                        vector = embedding_data.embedding
                        results.append((text_hash, vector))

                    return results

                except Exception as e:
                    base_ms = self._coerce_float(
                        getattr(settings, "backoff_base_ms", 500), 500.0
                    )
                    wait_time = (base_ms * (2**attempt)) / 1000
                    logger.warning(
                        f"Embedding batch failed (attempt {attempt + 1}): {e}"
                    )

                    if attempt < 2:
                        await asyncio.sleep(wait_time)
                    else:
                        raise
