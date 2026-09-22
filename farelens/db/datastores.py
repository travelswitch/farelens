"""Runtime-switchable Postgres and Redis connections.

FareLens ships with a bundled Postgres + Redis (docker compose). Operators can
point the app at their own instances from the admin UI. Because that choice
must be known *before* we can read anything from a database, it is persisted
as a small encrypted JSON file in APP_DATA_DIR (`datastores.json`).

Services never hold a pool reference; they always go through
`datastores.pg` / `datastores.redis` so a swap takes effect immediately.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qs, unquote, urlsplit, urlunsplit

import asyncpg
from pydantic import BaseModel
from redis.asyncio import Redis

from farelens.core.config import Settings
from farelens.core.security import SecretBox
from farelens.db.migrations import apply_migrations

logger = logging.getLogger(__name__)

Mode = Literal["bundled", "external"]

# Tables copied to a freshly configured external Postgres so the operator does
# not lose their admin user, provider config and API keys when switching.
_CARRY_OVER_TABLES = ("users", "llm_config", "api_keys")


class DataStoreConfig(BaseModel):
    postgres_mode: Mode = "bundled"
    postgres_dsn: str = ""
    redis_mode: Mode = "bundled"
    redis_url: str = ""
    redis_enabled: bool = True


def mask_dsn(dsn: str) -> str:
    """Hide the password component of a URL-style DSN."""
    if not dsn:
        return ""
    try:
        parts = urlsplit(dsn)
    except ValueError:
        return "***"
    if "@" not in parts.netloc:
        return dsn
    creds, host = parts.netloc.rsplit("@", 1)
    user = creds.split(":", 1)[0]
    netloc = f"{user}:***@{host}" if ":" in creds else f"{creds}@{host}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def parse_pg_dsn(dsn: str, *, reveal: bool = False) -> dict[str, Any]:
    """Split a postgresql:// DSN into form fields (password only when reveal=True)."""
    out: dict[str, Any] = {"host": "", "port": 5432, "database": "", "username": "", "password": "", "sslmode": ""}
    if not dsn:
        return out
    try:
        parts = urlsplit(dsn)
    except ValueError:
        return out
    out["host"] = parts.hostname or ""
    out["port"] = parts.port or 5432
    out["database"] = (parts.path or "").lstrip("/")
    out["username"] = unquote(parts.username or "")
    out["password"] = unquote(parts.password or "") if reveal else ("" if not parts.password else "********")
    out["sslmode"] = (parse_qs(parts.query).get("sslmode") or [""])[0]
    return out


def parse_redis_url(url: str, *, reveal: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {"host": "", "port": 6379, "db": 0, "username": "", "password": "", "tls": False}
    if not url:
        return out
    try:
        parts = urlsplit(url)
    except ValueError:
        return out
    out["host"] = parts.hostname or ""
    out["port"] = parts.port or 6379
    out["tls"] = parts.scheme == "rediss"
    out["username"] = unquote(parts.username or "")
    out["password"] = unquote(parts.password or "") if reveal else ("" if not parts.password else "********")
    try:
        out["db"] = int((parts.path or "/0").lstrip("/") or 0)
    except ValueError:
        out["db"] = 0
    return out


class DataStores:
    def __init__(self, settings: Settings, secret_box: SecretBox):
        self._settings = settings
        self._box = secret_box
        self._config_path: Path = settings.data_path / "datastores.json"
        self.config = DataStoreConfig()
        self._pg: asyncpg.Pool | None = None
        self._redis: Redis | None = None
        self._lock = asyncio.Lock()
        self.pg_dsn_in_use: str = ""
        self.redis_url_in_use: str = ""
        self.pg_fallback_reason: str | None = None
        self.redis_error: str | None = None

    # ------------------------------------------------------------------ props
    @property
    def pg(self) -> asyncpg.Pool | None:
        return self._pg

    @property
    def redis(self) -> Redis | None:
        return self._redis

    @property
    def pg_connected(self) -> bool:
        return self._pg is not None

    @property
    def redis_connected(self) -> bool:
        return self._redis is not None

    # --------------------------------------------------------------- lifecycle
    async def startup(self) -> None:
        self.config = self._load_config()
        pg_dsn = self._effective_pg_dsn(self.config)
        try:
            self._pg = await self._connect_pg_with_retry(pg_dsn)
            self.pg_dsn_in_use = pg_dsn
        except Exception as exc:
            if self.config.postgres_mode == "external":
                logger.error(
                    "External Postgres unreachable (%s); falling back to bundled DATABASE_URL", exc
                )
                self.pg_fallback_reason = f"External Postgres failed: {exc}"
                bundled = self._settings.database_url.get_secret_value()
                self._pg = await self._connect_pg_with_retry(bundled)
                self.pg_dsn_in_use = bundled
            else:
                raise
        await apply_migrations(self._pg, self._settings.migrations_path)

        await self._connect_redis_best_effort(self._effective_redis_url(self.config), self.config.redis_enabled)

    async def shutdown(self) -> None:
        if self._redis is not None:
            try:
                await self._redis.aclose()
            except Exception:  # pragma: no cover - best effort
                pass
            self._redis = None
        if self._pg is not None:
            await self._pg.close()
            self._pg = None

    # -------------------------------------------------------------- utilities
    def _effective_pg_dsn(self, cfg: DataStoreConfig) -> str:
        if cfg.postgres_mode == "external" and cfg.postgres_dsn.strip():
            return cfg.postgres_dsn.strip()
        return self._settings.database_url.get_secret_value()

    def _effective_redis_url(self, cfg: DataStoreConfig) -> str:
        if cfg.redis_mode == "external" and cfg.redis_url.strip():
            return cfg.redis_url.strip()
        return self._settings.redis_url.get_secret_value()

    async def _connect_pg_with_retry(self, dsn: str, attempts: int = 10, delay: float = 2.0) -> asyncpg.Pool:
        last: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                return await self._connect_pg(dsn)
            except Exception as exc:  # noqa: BLE001 - we retry on anything transient
                last = exc
                logger.warning("postgres connect attempt %s/%s failed: %s", attempt, attempts, exc)
                await asyncio.sleep(delay)
        assert last is not None
        raise last

    async def _connect_pg(self, dsn: str) -> asyncpg.Pool:
        pool = await asyncpg.create_pool(
            dsn=dsn,
            min_size=max(0, self._settings.postgres_pool_min),
            max_size=max(1, self._settings.postgres_pool_max),
            command_timeout=60,
            timeout=10,
        )
        async with pool.acquire() as conn:
            await conn.execute("SELECT 1")
        return pool

    async def _connect_redis_best_effort(self, url: str, enabled: bool) -> None:
        self.redis_error = None
        if not enabled or not url:
            self._redis = None
            return
        try:
            client = Redis.from_url(url, decode_responses=True, socket_connect_timeout=5, socket_timeout=5)
            await client.ping()
            self._redis = client
            self.redis_url_in_use = url
            logger.info("redis connected (%s)", mask_dsn(url))
        except Exception as exc:  # noqa: BLE001
            self._redis = None
            self.redis_error = str(exc)
            logger.warning("redis unavailable (%s); running without Redis cache: %s", mask_dsn(url), exc)

    # --------------------------------------------------------------- testing
    async def test_postgres(self, dsn: str) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            conn = await asyncpg.connect(dsn=dsn, timeout=10)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "message": f"Connection failed: {exc}"}
        try:
            version = await conn.fetchval("SELECT version()")
            can_create = await conn.fetchval(
                "SELECT has_database_privilege(current_user, current_database(), 'CREATE')"
            )
        finally:
            await conn.close()
        return {
            "ok": True,
            "message": "Connected",
            "server_version": " ".join(str(version).split()[:2]),
            "can_create_tables": bool(can_create),
            "latency_ms": int((time.perf_counter() - started) * 1000),
        }

    async def test_redis(self, url: str) -> dict[str, Any]:
        started = time.perf_counter()
        client = Redis.from_url(url, decode_responses=True, socket_connect_timeout=5, socket_timeout=5)
        try:
            await client.ping()
            info = await client.info("server")
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "message": f"Connection failed: {exc}"}
        finally:
            try:
                await client.aclose()
            except Exception:  # pragma: no cover
                pass
        return {
            "ok": True,
            "message": "Connected",
            "server_version": info.get("redis_version"),
            "latency_ms": int((time.perf_counter() - started) * 1000),
        }

    # ----------------------------------------------------------------- apply
    async def apply(self, new_config: DataStoreConfig, *, carry_over: bool = True) -> dict[str, Any]:
        """Switch live connections to `new_config`, migrate, persist. Returns a report."""
        async with self._lock:
            report: dict[str, Any] = {"postgres": "unchanged", "redis": "unchanged", "carried_over": []}

            new_pg_dsn = self._effective_pg_dsn(new_config)
            if new_pg_dsn != self.pg_dsn_in_use:
                new_pool = await self._connect_pg(new_pg_dsn)
                try:
                    await apply_migrations(new_pool, self._settings.migrations_path)
                    if carry_over and self._pg is not None:
                        report["carried_over"] = await self._carry_over(self._pg, new_pool)
                except Exception:
                    await new_pool.close()
                    raise
                old_pool, self._pg = self._pg, new_pool
                self.pg_dsn_in_use = new_pg_dsn
                self.pg_fallback_reason = None
                report["postgres"] = "switched"
                if old_pool is not None:
                    # Give in-flight queries a moment, then close.
                    asyncio.get_running_loop().call_later(5, lambda: asyncio.ensure_future(old_pool.close()))

            new_redis_url = self._effective_redis_url(new_config)
            if new_redis_url != self.redis_url_in_use or new_config.redis_enabled != self.config.redis_enabled or self._redis is None:
                old_redis = self._redis
                await self._connect_redis_best_effort(new_redis_url, new_config.redis_enabled)
                if old_redis is not None and old_redis is not self._redis:
                    try:
                        await old_redis.aclose()
                    except Exception:  # pragma: no cover
                        pass
                report["redis"] = "connected" if self._redis is not None else ("disabled" if not new_config.redis_enabled else f"unavailable: {self.redis_error}")

            self.config = new_config
            self._save_config(new_config)
            return report

    async def _carry_over(self, src: asyncpg.Pool, dst: asyncpg.Pool) -> list[str]:
        """Copy config tables into the new DB when it has no users yet."""
        copied: list[str] = []
        async with dst.acquire() as dconn:
            existing_users = await dconn.fetchval("SELECT COUNT(*) FROM users")
            if existing_users:
                return copied
            async with src.acquire() as sconn, dconn.transaction():
                for table in _CARRY_OVER_TABLES:
                    rows = await sconn.fetch(f"SELECT * FROM {table}")
                    if not rows:
                        continue
                    cols = list(rows[0].keys())
                    placeholders = ", ".join(f"${i + 1}" for i in range(len(cols)))
                    col_list = ", ".join(cols)
                    await dconn.executemany(
                        f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) ON CONFLICT DO NOTHING",
                        [tuple(r[c] for c in cols) for r in rows],
                    )
                    # Keep sequences ahead of copied ids.
                    if "id" in cols:
                        await dconn.execute(
                            f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                            f"COALESCE((SELECT MAX(id) FROM {table}), 0) + 1, false)"
                        )
                    copied.append(table)
        return copied

    # ------------------------------------------------------------ persistence
    def _load_config(self) -> DataStoreConfig:
        if not self._config_path.exists():
            return DataStoreConfig()
        try:
            raw = json.loads(self._config_path.read_text(encoding="utf-8"))
            cfg = DataStoreConfig.model_validate(raw)
            cfg.postgres_dsn = self._box.decrypt(cfg.postgres_dsn)
            cfg.redis_url = self._box.decrypt(cfg.redis_url)
            return cfg
        except Exception as exc:  # noqa: BLE001
            logger.error("could not read %s (%s); using bundled datastores", self._config_path, exc)
            return DataStoreConfig()

    def _save_config(self, cfg: DataStoreConfig) -> None:
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        on_disk = cfg.model_copy()
        on_disk.postgres_dsn = self._box.encrypt(cfg.postgres_dsn)
        on_disk.redis_url = self._box.encrypt(cfg.redis_url)
        self._config_path.write_text(json.dumps(on_disk.model_dump(), indent=2), encoding="utf-8")
        try:
            self._config_path.chmod(0o600)
        except OSError:
            pass

    # ---------------------------------------------------------------- status
    def status(self, *, reveal: bool = False) -> dict[str, Any]:
        return {
            "postgres": {
                "mode": self.config.postgres_mode,
                "connected": self.pg_connected,
                "dsn_masked": mask_dsn(self.pg_dsn_in_use),
                "external_dsn_masked": mask_dsn(self.config.postgres_dsn),
                "fallback_reason": self.pg_fallback_reason,
                "bundled_dsn_masked": mask_dsn(self._settings.database_url.get_secret_value()),
                "external": parse_pg_dsn(self.config.postgres_dsn, reveal=reveal),
                "bundled": parse_pg_dsn(self._settings.database_url.get_secret_value()),
            },
            "redis": {
                "mode": self.config.redis_mode,
                "enabled": self.config.redis_enabled,
                "connected": self.redis_connected,
                "url_masked": mask_dsn(self.redis_url_in_use) if self.redis_connected else "",
                "external_url_masked": mask_dsn(self.config.redis_url),
                "error": self.redis_error,
                "bundled_url_masked": mask_dsn(self._settings.redis_url.get_secret_value()),
                "external": parse_redis_url(self.config.redis_url, reveal=reveal),
                "bundled": parse_redis_url(self._settings.redis_url.get_secret_value()),
            },
        }
