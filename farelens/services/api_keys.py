"""API keys for the public endpoints. Only a SHA-256 hash is stored."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

from farelens.core.security import generate_api_key, hash_api_key
from farelens.db.datastores import DataStores

logger = logging.getLogger(__name__)

_VERIFY_CACHE_TTL = 60.0


@dataclass(slots=True)
class ApiKeyIdentity:
    id: int
    name: str


class ApiKeyService:
    def __init__(self, datastores: DataStores):
        self._ds = datastores
        self._cache: dict[str, tuple[float, ApiKeyIdentity | None]] = {}
        self._touch_pending: set[int] = set()
        self._lock = asyncio.Lock()

    async def create(self, name: str, created_by: str | None) -> dict[str, Any]:
        pool = self._ds.pg
        if pool is None:
            raise RuntimeError("Postgres is not connected")
        plaintext, prefix, digest = generate_api_key()
        row = await pool.fetchrow(
            "INSERT INTO api_keys (name, key_prefix, key_hash, created_by) VALUES ($1, $2, $3, $4) RETURNING id, created_at",
            name.strip()[:100] or "unnamed",
            prefix,
            digest,
            created_by,
        )
        return {"id": row["id"], "name": name, "key_prefix": prefix, "created_at": row["created_at"].isoformat(), "key": plaintext}

    async def list(self) -> list[dict[str, Any]]:
        pool = self._ds.pg
        if pool is None:
            return []
        rows = await pool.fetch(
            """
            SELECT k.id, k.name, k.key_prefix, k.created_at, k.created_by, k.last_used_at, k.revoked_at,
                   (SELECT COUNT(*) FROM usage_events u WHERE u.api_key_id = k.id) AS requests
            FROM api_keys k ORDER BY k.created_at DESC
            """
        )
        return [
            {
                **dict(r),
                "created_at": r["created_at"].isoformat(),
                "last_used_at": r["last_used_at"].isoformat() if r["last_used_at"] else None,
                "revoked_at": r["revoked_at"].isoformat() if r["revoked_at"] else None,
            }
            for r in rows
        ]

    async def revoke(self, key_id: int) -> bool:
        pool = self._ds.pg
        if pool is None:
            return False
        status = await pool.execute("UPDATE api_keys SET revoked_at = NOW() WHERE id = $1 AND revoked_at IS NULL", key_id)
        self._cache.clear()
        return status.endswith("1")

    async def verify(self, plaintext: str) -> ApiKeyIdentity | None:
        if not plaintext:
            return None
        digest = hash_api_key(plaintext.strip())
        now = time.monotonic()
        cached = self._cache.get(digest)
        if cached and now - cached[0] < _VERIFY_CACHE_TTL:
            if cached[1] is not None:
                await self._touch(cached[1].id)
            return cached[1]

        pool = self._ds.pg
        if pool is None:
            return None
        row = await pool.fetchrow("SELECT id, name FROM api_keys WHERE key_hash = $1 AND revoked_at IS NULL", digest)
        identity = ApiKeyIdentity(id=row["id"], name=row["name"]) if row else None
        self._cache[digest] = (now, identity)
        if len(self._cache) > 5000:
            self._cache.clear()
        if identity is not None:
            await self._touch(identity.id)
        return identity

    async def _touch(self, key_id: int) -> None:
        """Update last_used_at at most once per minute per key (cheap + async)."""
        async with self._lock:
            if key_id in self._touch_pending:
                return
            self._touch_pending.add(key_id)

        async def _run() -> None:
            try:
                pool = self._ds.pg
                if pool is not None:
                    await pool.execute("UPDATE api_keys SET last_used_at = NOW() WHERE id = $1", key_id)
            except Exception:  # noqa: BLE001
                logger.debug("api key touch failed", exc_info=True)
            finally:
                await asyncio.sleep(60)
                async with self._lock:
                    self._touch_pending.discard(key_id)

        asyncio.create_task(_run())
