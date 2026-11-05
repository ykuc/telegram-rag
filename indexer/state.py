"""State management helpers for resumable indexing."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Union


@dataclass
class BackfillRecord:
    """Progress record for a single chat."""

    chat_id: str
    last_message_id: int
    updated_at: str
    oneshot_offset_id: Optional[int] = None


class BackfillStateStore:
    """Persist per-chat backfill progress to a JSON file.

    ``path=None`` keeps progress in memory only (used by tests).
    """

    def __init__(self, path: Optional[str] = None):
        self.path = _as_path(path)
        self._lock = asyncio.Lock()
        self._state: Dict[str, BackfillRecord] = {}
        self._loaded = False

    async def load(self) -> None:
        """Load state from disk if present."""
        if self._loaded:
            return

        if self.path is None:
            self._loaded = True
            return

        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._loaded = True
            return

        try:
            content = await asyncio.to_thread(self.path.read_text)
            data = json.loads(content)
            chats = data.get("chats", {}) if isinstance(data, dict) else {}
            for chat_id, record in chats.items():
                last_message_id = int(record.get("last_message_id", 0))
                updated_at = str(record.get("updated_at", _now_iso()))
                raw_offset = record.get("oneshot_offset_id")
                oneshot_offset_id = int(raw_offset) if raw_offset is not None else None
                self._state[chat_id] = BackfillRecord(
                    chat_id=chat_id,
                    last_message_id=last_message_id,
                    updated_at=updated_at,
                    oneshot_offset_id=oneshot_offset_id,
                )
        except FileNotFoundError:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        finally:
            self._loaded = True

    async def get_last_message_id(self, chat_id: str) -> Optional[int]:
        """Return the last processed message ID for a chat."""
        await self.load()
        record = self._state.get(chat_id)
        return record.last_message_id if record else None

    async def update_chat(self, chat_id: str, message_id: int) -> None:
        """Persist progress for a chat."""
        await self.load()

        async with self._lock:
            existing = self._state.get(chat_id)
            if existing and existing.last_message_id >= message_id:
                # No progress to persist.
                return

            record = BackfillRecord(
                chat_id=chat_id,
                last_message_id=message_id,
                updated_at=_now_iso(),
                oneshot_offset_id=existing.oneshot_offset_id if existing else None,
            )
            self._state[chat_id] = record
            await self._persist()

    async def get_oneshot_offset_id(self, chat_id: str) -> Optional[int]:
        """Exclusive offset_id for resuming one-shot history (newest-first scan)."""
        await self.load()
        record = self._state.get(chat_id)
        if not record or not record.oneshot_offset_id:
            return None
        return record.oneshot_offset_id

    async def update_oneshot_offset(self, chat_id: str, offset_id: int) -> None:
        """Persist one-shot scan position (smallest message id reached)."""
        if offset_id <= 0:
            return
        await self.load()

        async with self._lock:
            existing = self._state.get(chat_id)
            prior = existing.oneshot_offset_id if existing else None
            if prior is not None and offset_id >= prior:
                return

            record = BackfillRecord(
                chat_id=chat_id,
                last_message_id=existing.last_message_id if existing else 0,
                updated_at=_now_iso(),
                oneshot_offset_id=offset_id,
            )
            self._state[chat_id] = record
            await self._persist()

    async def clear_oneshot_offset(self, chat_id: str) -> None:
        """Clear one-shot checkpoint after a successful full pass."""
        await self.load()

        async with self._lock:
            existing = self._state.get(chat_id)
            if not existing or existing.oneshot_offset_id is None:
                return
            record = BackfillRecord(
                chat_id=chat_id,
                last_message_id=existing.last_message_id,
                updated_at=_now_iso(),
                oneshot_offset_id=None,
            )
            self._state[chat_id] = record
            await self._persist()

    async def snapshot(self) -> Dict[str, BackfillRecord]:
        """Return a copy of the in-memory state."""
        await self.load()
        return dict(self._state)

    async def _persist(self) -> None:
        payload = {
            "chats": {
                chat_id: {
                    "last_message_id": record.last_message_id,
                    "updated_at": record.updated_at,
                    **(
                        {"oneshot_offset_id": record.oneshot_offset_id}
                        if record.oneshot_offset_id is not None
                        else {}
                    ),
                }
                for chat_id, record in self._state.items()
            }
        }

        if self.path is None:
            return

        content = json.dumps(payload, indent=2, sort_keys=True)
        await asyncio.to_thread(self.path.write_text, content)


def _as_path(path: Optional[Union[str, os.PathLike]]) -> Optional[Path]:
    """Return a real filesystem path, or None for an in-memory store."""
    if path is None:
        return None
    return Path(path)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
