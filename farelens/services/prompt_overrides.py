"""Admin-editable prompt overrides stored in Postgres (files stay the defaults)."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from farelens.core.errors import AppError, LLMUpstreamError
from farelens.db.datastores import DataStores
from farelens.llm.base import LLMProviderError
from farelens.services.prompts import PROMPTS, PromptService, validate_prompt

if TYPE_CHECKING:  # avoid import cycles
    from farelens.services.chat import ChatService
    from farelens.services.llm_config import LLMConfigService
    from farelens.services.summary import SummaryService

logger = logging.getLogger(__name__)

SAMPLE_RULES = (
    "CANCELLATIONS BEFORE DEPARTURE CHARGE SAR 150 FOR CANCEL/REFUND. CHILD/INFANT DISCOUNTS APPLY. "
    "AFTER DEPARTURE TICKET IS NON-REFUNDABLE. REFUND OF UNUSED TAXES PERMITTED. NO-SHOW NON-REFUNDABLE. "
    "CHANGES BEFORE DEPARTURE CHARGE SAR 100 FOR REISSUE. FARE DIFFERENCE APPLIES. CHANGES PERMITTED UP TO 4 HOURS BEFORE DEPARTURE. "
    "AFTER DEPARTURE CHARGE SAR 200 FOR REISSUE. NO-SHOW CHARGE SAR 250 FOR REISSUE. TICKET VALID FOR 1 YEAR FROM DATE OF ISSUE."
)
SAMPLE_SEGMENTS = [
    {"source_airport": "DEL", "destination_airport": "DXB", "departure_date": "2026-11-06", "fare_rules_text": SAMPLE_RULES},
    {"source_airport": "DXB", "destination_airport": "DEL", "departure_date": "2026-11-12", "fare_rules_text": SAMPLE_RULES.replace("SAR 150", "SAR 300")},
]
SAMPLE_QUESTION = "What does it cost to cancel each flight before departure?"


class PromptOverrideService:
    def __init__(self, datastores: DataStores, prompts: PromptService):
        self._ds = datastores
        self._prompts = prompts
        self._meta: dict[str, dict[str, Any]] = {}
        self._last_refresh = 0.0

    # ------------------------------------------------------------- refresh
    async def refresh(self) -> None:
        pool = self._ds.pg
        if pool is None:
            return
        try:
            rows = await pool.fetch("SELECT prompt_id, content, updated_at, updated_by FROM prompt_overrides")
        except Exception:  # noqa: BLE001
            logger.warning("prompt override refresh failed", exc_info=True)
            return
        self._meta = {r["prompt_id"]: {"updated_at": r["updated_at"], "updated_by": r["updated_by"]} for r in rows if r["prompt_id"] in PROMPTS}
        self._prompts.set_overrides({r["prompt_id"]: r["content"] for r in rows})
        self._last_refresh = time.monotonic()

    async def run_periodic_refresh(self, interval: float = 30.0) -> None:
        while True:
            await asyncio.sleep(interval)
            await self.refresh()

    # ---------------------------------------------------------------- read
    def list(self) -> list[dict[str, Any]]:
        out = []
        for spec in PROMPTS.values():
            meta = self._meta.get(spec.id, {})
            default = self._prompts.load_default(spec.file)
            current = self._prompts.load(spec.file)
            out.append({
                "id": spec.id,
                "feature": spec.feature,
                "label": spec.label,
                "description": spec.description,
                "allowed_placeholders": list(spec.allowed),
                "required_placeholders": list(spec.required),
                "is_overridden": self._prompts.is_overridden(spec.id),
                "default": default,
                "content": current,
                "updated_at": meta.get("updated_at").isoformat() if meta.get("updated_at") else None,
                "updated_by": meta.get("updated_by"),
            })
        return out

    # --------------------------------------------------------------- write
    async def save(self, prompt_id: str, content: str, *, updated_by: str | None) -> dict[str, Any]:
        spec = PROMPTS.get(prompt_id)
        if spec is None:
            raise AppError("Unknown prompt.", status_code=404)
        problems = validate_prompt(spec, content)
        if problems:
            raise AppError(" ".join(problems), status_code=422, code="INVALID_PROMPT")
        pool = self._ds.pg
        if pool is None:
            raise AppError("Postgres is not connected.", status_code=503)
        if content.strip() == self._prompts.load_default(spec.file):
            await pool.execute("DELETE FROM prompt_overrides WHERE prompt_id = $1", prompt_id)
        else:
            await pool.execute(
                """
                INSERT INTO prompt_overrides (prompt_id, content, updated_at, updated_by) VALUES ($1, $2, NOW(), $3)
                ON CONFLICT (prompt_id) DO UPDATE SET content = EXCLUDED.content, updated_at = NOW(), updated_by = EXCLUDED.updated_by
                """,
                prompt_id, content.strip(), updated_by,
            )
        await self.refresh()
        logger.info("prompt %s saved by %s", prompt_id, updated_by)
        return next(p for p in self.list() if p["id"] == prompt_id)

    async def reset(self, prompt_id: str) -> dict[str, Any]:
        if prompt_id not in PROMPTS:
            raise AppError("Unknown prompt.", status_code=404)
        pool = self._ds.pg
        if pool is not None:
            await pool.execute("DELETE FROM prompt_overrides WHERE prompt_id = $1", prompt_id)
        await self.refresh()
        return next(p for p in self.list() if p["id"] == prompt_id)

    # ------------------------------------------------------------- preview
    async def preview(
        self,
        prompt_id: str,
        content: str,
        *,
        summary: SummaryService,
        chat: ChatService,
        llm_config: LLMConfigService,
        timeout_seconds: float,
        lang: str = "en",
        is_mobile_view: bool = False,
    ) -> dict[str, Any]:
        spec = PROMPTS.get(prompt_id)
        if spec is None:
            raise AppError("Unknown prompt.", status_code=404)
        problems = validate_prompt(spec, content)
        if problems:
            raise AppError(" ".join(problems), status_code=422, code="INVALID_PROMPT")
        temp = {spec.file: content}
        if spec.feature == "summary":
            messages = summary.build_messages(SAMPLE_RULES, lang=lang, is_mobile_view=is_mobile_view, temp_overrides=temp)
        else:
            messages = chat.build_messages(
                SAMPLE_SEGMENTS, user_message=SAMPLE_QUESTION, history=[], lang=lang, is_mobile_view=is_mobile_view, temp_overrides=temp
            )
        provider, cfg = await llm_config.get_provider()
        started = time.perf_counter()
        try:
            resp = await provider.complete(messages, cfg.generation_options(timeout_seconds))
        except LLMProviderError as exc:
            raise LLMUpstreamError(exc.message, status_code=exc.status_code, code=exc.code) from exc
        return {
            "output_markdown": resp.text.strip(),
            "rendered_prompt": "\n\n".join(f"[{m.role}]\n{m.content}" for m in messages),
            "provider": cfg.provider,
            "model": resp.model or cfg.model,
            "usage": {"prompt_tokens": resp.usage.prompt_tokens, "completion_tokens": resp.usage.completion_tokens},
            "latency_ms": int((time.perf_counter() - started) * 1000),
        }
