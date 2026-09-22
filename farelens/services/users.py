"""Admin users (username + bcrypt password). A default admin is seeded on startup."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from farelens.core.security import hash_password, verify_password
from farelens.db.datastores import DataStores

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class User:
    id: int
    username: str
    must_change_password: bool


class UserService:
    def __init__(self, datastores: DataStores):
        self._ds = datastores

    async def ensure_default_admin(self, username: str, password: str) -> bool:
        """Create the default admin if no users exist. Returns True when created."""
        pool = self._ds.pg
        if pool is None:
            return False
        count = await pool.fetchval("SELECT COUNT(*) FROM users")
        if count:
            return False
        await pool.execute(
            "INSERT INTO users (username, password_hash, must_change_password) VALUES ($1, $2, TRUE)",
            username.strip() or "admin",
            hash_password(password or "admin"),
        )
        logger.warning(
            "Created default admin user '%s'. Log in to the UI and change the password immediately.",
            username,
        )
        return True

    async def authenticate(self, username: str, password: str) -> User | None:
        pool = self._ds.pg
        if pool is None:
            return None
        row = await pool.fetchrow(
            "SELECT id, username, password_hash, must_change_password FROM users WHERE username = $1",
            username.strip(),
        )
        if row is None or not verify_password(password, row["password_hash"]):
            return None
        await pool.execute("UPDATE users SET last_login_at = NOW() WHERE id = $1", row["id"])
        return User(id=row["id"], username=row["username"], must_change_password=row["must_change_password"])

    async def get(self, user_id: int) -> User | None:
        pool = self._ds.pg
        if pool is None:
            return None
        row = await pool.fetchrow("SELECT id, username, must_change_password FROM users WHERE id = $1", user_id)
        return User(id=row["id"], username=row["username"], must_change_password=row["must_change_password"]) if row else None

    async def change_password(self, user_id: int, current_password: str, new_password: str) -> bool:
        pool = self._ds.pg
        if pool is None:
            return False
        row = await pool.fetchrow("SELECT password_hash FROM users WHERE id = $1", user_id)
        if row is None or not verify_password(current_password, row["password_hash"]):
            return False
        await pool.execute(
            "UPDATE users SET password_hash = $1, must_change_password = FALSE, updated_at = NOW() WHERE id = $2",
            hash_password(new_password),
            user_id,
        )
        return True

    async def rename(self, user_id: int, new_username: str) -> bool:
        pool = self._ds.pg
        if pool is None:
            return False
        try:
            status = await pool.execute(
                "UPDATE users SET username = $1, updated_at = NOW() WHERE id = $2", new_username.strip(), user_id
            )
        except Exception:  # noqa: BLE001 - unique violation
            return False
        return status.endswith("1")

    async def list(self) -> list[dict[str, Any]]:
        pool = self._ds.pg
        if pool is None:
            return []
        rows = await pool.fetch("SELECT id, username, must_change_password, created_at, last_login_at FROM users ORDER BY id")
        return [
            {
                **dict(r),
                "created_at": r["created_at"].isoformat(),
                "last_login_at": r["last_login_at"].isoformat() if r["last_login_at"] else None,
            }
            for r in rows
        ]
