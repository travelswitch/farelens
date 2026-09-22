"""Tiny forward-only SQL migration runner (no Alembic dependency).

Files in `migrations/` are named `NNNN_description.sql` and applied in order
inside a transaction. Applied versions are recorded in `schema_migrations`.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import asyncpg

logger = logging.getLogger(__name__)

_FILE_RE = re.compile(r"^(\d{4})_[A-Za-z0-9_\-]+\.sql$")


def discover(migrations_dir: Path) -> list[tuple[int, Path]]:
    found: list[tuple[int, Path]] = []
    for path in sorted(migrations_dir.glob("*.sql")):
        match = _FILE_RE.match(path.name)
        if match:
            found.append((int(match.group(1)), path))
    return found


async def apply_migrations(pool: asyncpg.Pool, migrations_dir: Path) -> list[int]:
    """Apply pending migrations. Returns the versions applied in this call."""
    applied: list[int] = []
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version    INTEGER PRIMARY KEY,
                name       TEXT NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        # Serialise concurrent app instances starting at the same time.
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(7340123)")
            rows = await conn.fetch("SELECT version FROM schema_migrations")
            done = {int(r["version"]) for r in rows}
            for version, path in discover(migrations_dir):
                if version in done:
                    continue
                sql = path.read_text(encoding="utf-8")
                logger.info("applying migration %s", path.name)
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO schema_migrations (version, name) VALUES ($1, $2)",
                    version,
                    path.name,
                )
                applied.append(version)
    return applied
