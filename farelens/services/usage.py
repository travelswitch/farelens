"""Usage accounting: one row per LLM-backed request, plus dashboard queries."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from farelens.db.datastores import DataStores
from farelens.llm.base import LLMUsage

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class UsageEvent:
    feature: str
    status: str
    provider: str | None = None
    model: str | None = None
    error_code: str | None = None
    cache_status: str | None = None
    usage: LLMUsage | None = None
    latency_ms: int | None = None
    lang: str | None = None
    api_key_id: int | None = None
    convo_id: str | None = None
    request_id: str | None = None


class UsageService:
    def __init__(self, datastores: DataStores):
        self._ds = datastores

    async def record(self, event: UsageEvent) -> None:
        pool = self._ds.pg
        if pool is None:
            return
        usage = event.usage or LLMUsage()
        try:
            await pool.execute(
                """
                INSERT INTO usage_events (
                    feature, provider, model, status, error_code, cache_status,
                    prompt_tokens, completion_tokens, total_tokens, latency_ms,
                    lang, api_key_id, convo_id, request_id
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
                """,
                event.feature,
                event.provider,
                event.model,
                event.status,
                event.error_code,
                event.cache_status,
                usage.prompt_tokens,
                usage.completion_tokens,
                usage.total_tokens,
                event.latency_ms,
                event.lang,
                event.api_key_id,
                event.convo_id,
                event.request_id,
            )
        except Exception:  # noqa: BLE001 - accounting must never break a request
            logger.exception("failed to record usage event")

    # ------------------------------------------------------------- queries
    async def summary(self, days: int = 30) -> dict[str, Any]:
        pool = self._ds.pg
        if pool is None:
            return {"available": False}
        days = max(1, min(365, days))
        since = datetime.now(UTC) - timedelta(days=days)

        totals = await pool.fetchrow(
            """
            SELECT
                COUNT(*)                                             AS requests,
                COUNT(*) FILTER (WHERE status = 'ok')                AS ok_requests,
                COUNT(*) FILTER (WHERE status <> 'ok')               AS error_requests,
                COUNT(*) FILTER (WHERE cache_status IN ('redis-hit','postgres-hit')) AS cache_hits,
                COUNT(*) FILTER (WHERE cache_status = 'generated')   AS llm_calls,
                COALESCE(SUM(prompt_tokens), 0)                      AS prompt_tokens,
                COALESCE(SUM(completion_tokens), 0)                  AS completion_tokens,
                COALESCE(SUM(total_tokens), 0)                       AS total_tokens,
                COALESCE(AVG(latency_ms) FILTER (WHERE status = 'ok'), 0)::int AS avg_latency_ms,
                COALESCE(percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) FILTER (WHERE status = 'ok'), 0)::int AS p95_latency_ms
            FROM usage_events WHERE occurred_at >= $1
            """,
            since,
        )
        today = await pool.fetchrow(
            """
            SELECT COUNT(*) AS requests, COALESCE(SUM(total_tokens), 0) AS total_tokens
            FROM usage_events WHERE occurred_at >= date_trunc('day', NOW())
            """
        )
        by_feature = await pool.fetch(
            """
            SELECT feature,
                   COUNT(*) AS requests,
                   COUNT(*) FILTER (WHERE status <> 'ok') AS errors,
                   COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                   COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                   COALESCE(AVG(latency_ms) FILTER (WHERE status = 'ok'), 0)::int AS avg_latency_ms
            FROM usage_events WHERE occurred_at >= $1
            GROUP BY feature ORDER BY requests DESC
            """,
            since,
        )
        by_model = await pool.fetch(
            """
            SELECT provider, model,
                   COUNT(*) AS requests,
                   COALESCE(SUM(total_tokens), 0) AS total_tokens
            FROM usage_events
            WHERE occurred_at >= $1 AND provider IS NOT NULL
            GROUP BY provider, model ORDER BY requests DESC LIMIT 10
            """,
            since,
        )
        by_key = await pool.fetch(
            """
            SELECT u.api_key_id, k.name AS api_key_name, k.key_prefix,
                   COUNT(*) AS requests,
                   COALESCE(SUM(total_tokens), 0) AS total_tokens
            FROM usage_events u LEFT JOIN api_keys k ON k.id = u.api_key_id
            WHERE occurred_at >= $1
            GROUP BY u.api_key_id, k.name, k.key_prefix ORDER BY requests DESC LIMIT 10
            """,
            since,
        )
        return {
            "available": True,
            "days": days,
            "since": since.isoformat(),
            "totals": dict(totals) if totals else {},
            "today": dict(today) if today else {},
            "by_feature": [dict(r) for r in by_feature],
            "by_model": [dict(r) for r in by_model],
            "by_api_key": [
                {**dict(r), "api_key_name": r["api_key_name"] or ("admin session" if r["api_key_id"] is None else "deleted key")}
                for r in by_key
            ],
        }

    async def daily(self, days: int = 30) -> list[dict[str, Any]]:
        pool = self._ds.pg
        if pool is None:
            return []
        days = max(1, min(365, days))
        rows = await pool.fetch(
            """
            WITH d AS (
                SELECT generate_series(
                    date_trunc('day', NOW()) - ($1::int - 1) * INTERVAL '1 day',
                    date_trunc('day', NOW()),
                    INTERVAL '1 day'
                )::date AS day
            )
            SELECT d.day,
                   COUNT(u.id)                                              AS requests,
                   COUNT(u.id) FILTER (WHERE u.feature = 'summary')         AS summary_requests,
                   COUNT(u.id) FILTER (WHERE u.feature = 'chat')            AS chat_requests,
                   COUNT(u.id) FILTER (WHERE u.status <> 'ok')              AS errors,
                   COUNT(u.id) FILTER (WHERE u.cache_status IN ('redis-hit','postgres-hit')) AS cache_hits,
                   COALESCE(SUM(u.prompt_tokens), 0)                        AS prompt_tokens,
                   COALESCE(SUM(u.completion_tokens), 0)                    AS completion_tokens
            FROM d LEFT JOIN usage_events u ON u.occurred_at::date = d.day
            GROUP BY d.day ORDER BY d.day
            """,
            days,
        )
        return [{**dict(r), "day": r["day"].isoformat()} for r in rows]

    async def recent(self, limit: int = 50, offset: int = 0, feature: str | None = None) -> list[dict[str, Any]]:
        pool = self._ds.pg
        if pool is None:
            return []
        limit = max(1, min(500, limit))
        rows = await pool.fetch(
            """
            SELECT u.id, u.occurred_at, u.feature, u.provider, u.model, u.status, u.error_code,
                   u.cache_status, u.prompt_tokens, u.completion_tokens, u.total_tokens,
                   u.latency_ms, u.lang, u.api_key_id, k.name AS api_key_name, u.convo_id, u.request_id
            FROM usage_events u LEFT JOIN api_keys k ON k.id = u.api_key_id
            WHERE ($3::text IS NULL OR u.feature = $3)
            ORDER BY u.occurred_at DESC
            LIMIT $1 OFFSET $2
            """,
            limit,
            max(0, offset),
            feature,
        )
        return [{**dict(r), "occurred_at": r["occurred_at"].isoformat()} for r in rows]
