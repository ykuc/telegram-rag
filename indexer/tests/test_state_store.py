"""Tests for backfill state persistence."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from state import BackfillStateStore


@pytest.mark.asyncio
async def test_backfill_state_store_roundtrip():
    store = BackfillStateStore(None)

    assert await store.get_last_message_id("chat-1") is None

    await store.update_chat("chat-1", 42)
    assert await store.get_last_message_id("chat-1") == 42


@pytest.mark.asyncio
async def test_backfill_state_store_ignores_regressions():
    store = BackfillStateStore(None)

    await store.update_chat("chat-1", 100)
    await store.update_chat("chat-1", 90)

    assert await store.get_last_message_id("chat-1") == 100


@pytest.mark.asyncio
async def test_oneshot_offset_checkpoint():
    store = BackfillStateStore(None)

    await store.update_oneshot_offset("chat-1", 50_000)
    assert await store.get_oneshot_offset_id("chat-1") == 50_000

    await store.update_oneshot_offset("chat-1", 49_000)
    assert await store.get_oneshot_offset_id("chat-1") == 49_000

    await store.update_oneshot_offset("chat-1", 49_500)
    assert await store.get_oneshot_offset_id("chat-1") == 49_000

    await store.clear_oneshot_offset("chat-1")
    assert await store.get_oneshot_offset_id("chat-1") is None
