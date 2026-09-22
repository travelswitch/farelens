"""Conversation context (journey segments) keyed by convo_id.

Stored in Redis when available (shared across workers/instances, TTL-based),
with an in-memory LRU fallback for single-process deployments.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import OrderedDict
from typing import Any

from farelens.db.datastores import DataStores

logger = logging.getLogger(__name__)

Segment = dict[str, str]


class ConversationStore:
    def __init__(self, datastores: DataStores, *, ttl_seconds: int, max_items: int):
        self._ds = datastores
        self._ttl = max(60, ttl_seconds)
        self._max_items = max(1, max_items)
        self._memory: OrderedDict[str, tuple[float, list[Segment]]] = OrderedDict()
        self._lock = asyncio.Lock()

    def _key(self, convo_id: str) -> str:
        return f"farelens:convo:{convo_id}"

    async def get(self, convo_id: str) -> list[Segment] | None:
        convo_id = convo_id.strip()
        redis = self._ds.redis
        if redis is not None:
            try:
                raw = await redis.get(self._key(convo_id))
                if raw:
                    data = json.loads(raw)
                    if isinstance(data, list):
                        await redis.expire(self._key(convo_id), self._ttl)
                        return data
                    return None
            except Exception:  # noqa: BLE001
                logger.warning("redis conversation read failed; using memory", exc_info=True)

        async with self._lock:
            self._evict_expired()
            item = self._memory.get(convo_id)
            if item is None:
                return None
            self._memory.move_to_end(convo_id)
            self._memory[convo_id] = (time.monotonic(), item[1])
            return [dict(s) for s in item[1]]

    async def put(self, convo_id: str, segments: list[Segment]) -> None:
        convo_id = convo_id.strip()
        redis = self._ds.redis
        if redis is not None:
            try:
                await redis.set(self._key(convo_id), json.dumps(segments), ex=self._ttl)
                return
            except Exception:  # noqa: BLE001
                logger.warning("redis conversation write failed; using memory", exc_info=True)

        async with self._lock:
            self._memory[convo_id] = (time.monotonic(), [dict(s) for s in segments])
            self._memory.move_to_end(convo_id)
            while len(self._memory) > self._max_items:
                self._memory.popitem(last=False)

    async def delete(self, convo_id: str) -> bool:
        convo_id = convo_id.strip()
        removed = False
        redis = self._ds.redis
        if redis is not None:
            try:
                removed = bool(await redis.delete(self._key(convo_id)))
            except Exception:  # noqa: BLE001
                logger.warning("redis conversation delete failed", exc_info=True)
        async with self._lock:
            if self._memory.pop(convo_id, None) is not None:
                removed = True
        return removed

    def _evict_expired(self) -> None:
        now = time.monotonic()
        expired = [k for k, (ts, _) in self._memory.items() if now - ts > self._ttl]
        for k in expired:
            self._memory.pop(k, None)

    def stats(self) -> dict[str, Any]:
        return {"backend": "redis" if self._ds.redis is not None else "memory", "memory_items": len(self._memory), "ttl_seconds": self._ttl}
