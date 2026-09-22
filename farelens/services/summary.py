"""Fare-rules summary: Redis -> Postgres -> LLM, keyed by canonical text digest."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Literal

from farelens.core.config import Settings
from farelens.core.errors import AppError, LLMUpstreamError
from farelens.db.datastores import DataStores
from farelens.llm.base import LLMMessage, LLMProviderError
from farelens.services.fare_rules_text import build_summary_cache_key, normalize_for_prompt
from farelens.services.llm_config import LLMConfigService
from farelens.services.prompts import PromptService, language_name
from farelens.services.usage import UsageEvent, UsageService

logger = logging.getLogger(__name__)

CacheStatus = Literal["redis-hit", "postgres-hit", "generated"]


@dataclass(slots=True)
class SummaryResult:
    summary_markdown: str
    cache_status: CacheStatus
    cache_key: str
    provider: str | None
    model: str | None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    latency_ms: int = 0


class SummaryService:
    def __init__(
        self,
        settings: Settings,
        datastores: DataStores,
        prompts: PromptService,
        llm_config: LLMConfigService,
        usage: UsageService,
    ):
        self._settings = settings
        self._ds = datastores
        self._prompts = prompts
        self._llm_config = llm_config
        self._usage = usage

    # ---------------------------------------------------------------- public
    async def summarize(
        self,
        fare_rules_text: str,
        *,
        lang: str = "en",
        is_mobile_view: bool = False,
        api_key_id: int | None = None,
        request_id: str | None = None,
        bypass_cache: bool = False,
    ) -> SummaryResult:
        if len(fare_rules_text) > self._settings.max_fare_rules_chars:
            raise AppError(
                f"fare_rules_text exceeds the maximum of {self._settings.max_fare_rules_chars} characters",
                status_code=413,
                code="PAYLOAD_TOO_LARGE",
            )
        lang = (lang or "en").strip().lower()
        cache_key = build_summary_cache_key(
            fare_rules_text,
            namespace=self._settings.summary_cache_namespace,
            lang=lang,
            is_mobile_view=is_mobile_view,
        )
        started = time.perf_counter()

        if not bypass_cache:
            cached = await self._get_redis(cache_key)
            if cached is not None:
                result = SummaryResult(cached["summary"], "redis-hit", cache_key, cached.get("provider"), cached.get("model"))
                await self._record(result, lang, api_key_id, request_id, started)
                return result

            cached = await self._get_postgres(cache_key)
            if cached is not None:
                await self._set_redis(cache_key, cached)
                result = SummaryResult(cached["summary"], "postgres-hit", cache_key, cached.get("provider"), cached.get("model"))
                await self._record(result, lang, api_key_id, request_id, started)
                return result

        provider, cfg = await self._llm_config.get_provider()
        messages = self.build_messages(fare_rules_text, lang=lang, is_mobile_view=is_mobile_view)
        try:
            response = await provider.complete(messages, cfg.generation_options(self._settings.llm_timeout_seconds))
        except LLMProviderError as exc:
            await self._usage.record(
                UsageEvent(
                    feature="summary", status="error", provider=cfg.provider, model=cfg.model, error_code=exc.code,
                    cache_status="generated", latency_ms=int((time.perf_counter() - started) * 1000),
                    lang=lang, api_key_id=api_key_id, request_id=request_id,
                )
            )
            raise LLMUpstreamError(exc.message, status_code=exc.status_code, code=exc.code) from exc

        summary = _clean_markdown(response.text)
        if not summary:
            await self._usage.record(
                UsageEvent(feature="summary", status="error", provider=cfg.provider, model=cfg.model,
                           error_code="LLM_EMPTY_RESPONSE", cache_status="generated", lang=lang,
                           api_key_id=api_key_id, request_id=request_id)
            )
            raise LLMUpstreamError("The model returned an empty summary.", code="LLM_EMPTY_RESPONSE")

        payload = {"summary": summary, "provider": cfg.provider, "model": response.model or cfg.model}
        await self._set_postgres(cache_key, payload, lang=lang, is_mobile_view=is_mobile_view)
        await self._set_redis(cache_key, payload)

        result = SummaryResult(
            summary, "generated", cache_key, cfg.provider, response.model or cfg.model,
            prompt_tokens=response.usage.prompt_tokens, completion_tokens=response.usage.completion_tokens,
        )
        await self._record(result, lang, api_key_id, request_id, started)
        return result

    # --------------------------------------------------------------- prompt
    def build_messages(
        self, fare_rules_text: str, *, lang: str, is_mobile_view: bool, temp_overrides: dict[str, str] | None = None
    ) -> list[LLMMessage]:
        system_template = self._prompts.load("fare_rules/summary_system.md", temp_overrides)
        layout = self._prompts.load(
            "fare_rules/summary_layout_mobile.md" if is_mobile_view else "fare_rules/summary_layout_desktop.md", temp_overrides
        )
        system_prompt = PromptService.render(
            system_template,
            LANG=lang,
            LANG_NAME=language_name(lang),
            LAYOUT_GUIDELINES=layout,
        )
        user_prompt = PromptService.render(
            self._prompts.load("fare_rules/summary_user.md", temp_overrides),
            FARE_RULES_TEXT=normalize_for_prompt(fare_rules_text),
            LANG_NAME=language_name(lang),
        )
        return [LLMMessage("system", system_prompt), LLMMessage("user", user_prompt)]

    # ---------------------------------------------------------------- cache
    def _redis_key(self, cache_key: str) -> str:
        return f"farelens:summary:{cache_key}"

    async def _get_redis(self, cache_key: str) -> dict[str, Any] | None:
        redis = self._ds.redis
        if redis is None:
            return None
        try:
            raw = await redis.get(self._redis_key(cache_key))
        except Exception:  # noqa: BLE001
            logger.warning("redis read failed", exc_info=True)
            return None
        if not raw:
            return None
        try:
            data = json.loads(raw)
            if isinstance(data, dict) and isinstance(data.get("summary"), str) and data["summary"].strip():
                return data
        except json.JSONDecodeError:
            pass
        return None

    async def _set_redis(self, cache_key: str, payload: dict[str, Any]) -> None:
        redis = self._ds.redis
        if redis is None:
            return
        try:
            await redis.set(self._redis_key(cache_key), json.dumps(payload), ex=self._settings.summary_cache_ttl_seconds)
        except Exception:  # noqa: BLE001
            logger.warning("redis write failed", exc_info=True)

    async def _get_postgres(self, cache_key: str) -> dict[str, Any] | None:
        pool = self._ds.pg
        if pool is None:
            return None
        try:
            row = await pool.fetchrow(
                """
                UPDATE fare_rules_summary_cache
                SET last_accessed_at = NOW(), hit_count = hit_count + 1
                WHERE cache_key = $1
                RETURNING summary_markdown, provider, model
                """,
                cache_key,
            )
        except Exception:  # noqa: BLE001
            logger.warning("postgres cache read failed", exc_info=True)
            return None
        if row is None or not row["summary_markdown"]:
            return None
        return {"summary": row["summary_markdown"], "provider": row["provider"], "model": row["model"]}

    async def _set_postgres(self, cache_key: str, payload: dict[str, Any], *, lang: str, is_mobile_view: bool) -> None:
        pool = self._ds.pg
        if pool is None:
            return
        try:
            await pool.execute(
                """
                INSERT INTO fare_rules_summary_cache (cache_key, lang, is_mobile_view, summary_markdown, provider, model)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (cache_key) DO UPDATE SET
                    summary_markdown = EXCLUDED.summary_markdown,
                    provider = EXCLUDED.provider,
                    model = EXCLUDED.model,
                    last_accessed_at = NOW()
                """,
                cache_key, lang, is_mobile_view, payload["summary"], payload.get("provider"), payload.get("model"),
            )
        except Exception:  # noqa: BLE001
            logger.warning("postgres cache write failed", exc_info=True)

    async def cache_stats(self) -> dict[str, Any]:
        pool = self._ds.pg
        if pool is None:
            return {"entries": 0, "hits": 0}
        row = await pool.fetchrow("SELECT COUNT(*) AS entries, COALESCE(SUM(hit_count), 0) AS hits FROM fare_rules_summary_cache")
        return dict(row) if row else {"entries": 0, "hits": 0}

    async def clear_cache(self) -> dict[str, int]:
        removed_pg = 0
        removed_redis = 0
        pool = self._ds.pg
        if pool is not None:
            status = await pool.execute("DELETE FROM fare_rules_summary_cache")
            removed_pg = int(status.split()[-1]) if status else 0
        redis = self._ds.redis
        if redis is not None:
            batch: list[str] = []
            async for key in redis.scan_iter(match="farelens:summary:*", count=500):
                batch.append(key)
                if len(batch) >= 500:
                    removed_redis += await redis.delete(*batch)
                    batch.clear()
            if batch:
                removed_redis += await redis.delete(*batch)
        return {"postgres": removed_pg, "redis": removed_redis}

    # ---------------------------------------------------------------- usage
    async def _record(self, result: SummaryResult, lang: str, api_key_id: int | None, request_id: str | None, started: float) -> None:
        result.latency_ms = int((time.perf_counter() - started) * 1000)
        from farelens.llm.base import LLMUsage

        await self._usage.record(
            UsageEvent(
                feature="summary",
                status="ok",
                provider=result.provider,
                model=result.model,
                cache_status=result.cache_status,
                usage=LLMUsage(result.prompt_tokens, result.completion_tokens) if result.cache_status == "generated" else None,
                latency_ms=result.latency_ms,
                lang=lang,
                api_key_id=api_key_id,
                request_id=request_id,
            )
        )


def _clean_markdown(text: str) -> str:
    """Strip a wrapping ```markdown fence if the model added one."""
    out = (text or "").strip()
    if out.startswith("```"):
        first_newline = out.find("\n")
        if first_newline != -1:
            out = out[first_newline + 1 :]
        if out.rstrip().endswith("```"):
            out = out.rstrip()[:-3]
    return out.strip()
