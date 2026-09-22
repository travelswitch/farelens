"""Fare-rules Q&A chat over one or more journey segments (streaming + one-shot)."""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4

from farelens.core.config import Settings
from farelens.core.errors import AppError, LLMUpstreamError
from farelens.llm.base import LLMMessage, LLMProviderError, LLMUsage, StreamEvent
from farelens.services.conversations import ConversationStore, Segment
from farelens.services.fare_rules_text import normalize_for_prompt
from farelens.services.llm_config import LLMConfigService
from farelens.services.prompts import PromptService, language_name
from farelens.services.usage import UsageEvent, UsageService

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ChatTurn:
    role: str
    content: str


@dataclass(slots=True)
class ChatContext:
    convo_id: str
    segments: list[Segment]
    created: bool


@dataclass(slots=True)
class ChatStreamResult:
    """Populated as the stream progresses; complete once the generator finishes."""

    convo_id: str
    provider: str
    model: str
    text_parts: list[str] = field(default_factory=list)
    usage: LLMUsage = field(default_factory=LLMUsage)
    token_events: int = 0

    @property
    def text(self) -> str:
        return "".join(self.text_parts)


class ChatService:
    def __init__(
        self,
        settings: Settings,
        conversations: ConversationStore,
        prompts: PromptService,
        llm_config: LLMConfigService,
        usage: UsageService,
    ):
        self._settings = settings
        self._conversations = conversations
        self._prompts = prompts
        self._llm_config = llm_config
        self._usage = usage

    # -------------------------------------------------------------- context
    async def resolve_context(self, convo_id: str | None, segments: list[Segment] | None) -> ChatContext:
        if segments:
            self._validate_segments(segments)

        if convo_id:
            convo_id = convo_id.strip()
            if segments:
                await self._conversations.put(convo_id, segments)
                return ChatContext(convo_id, segments, created=False)
            stored = await self._conversations.get(convo_id)
            if stored is None:
                raise AppError(
                    "Conversation context expired or unknown. Send `segments` again to start a new conversation.",
                    status_code=422,
                    code="CONVERSATION_EXPIRED",
                )
            return ChatContext(convo_id, stored, created=False)

        if not segments:
            raise AppError("`segments` are required when `convo_id` is not provided.", status_code=422, code="SEGMENTS_REQUIRED")
        convo_id = str(uuid4())
        await self._conversations.put(convo_id, segments)
        return ChatContext(convo_id, segments, created=True)

    async def forget(self, convo_id: str) -> bool:
        return await self._conversations.delete(convo_id)

    def _validate_segments(self, segments: list[Segment]) -> None:
        limit = self._settings.max_fare_rules_chars
        for index, seg in enumerate(segments, start=1):
            if len(seg.get("fare_rules_text", "")) > limit:
                raise AppError(
                    f"segments[{index}].fare_rules_text exceeds the maximum of {limit} characters",
                    status_code=413,
                    code="PAYLOAD_TOO_LARGE",
                )

    # -------------------------------------------------------------- prompts
    def build_messages(
        self,
        segments: list[Segment],
        *,
        user_message: str,
        history: list[ChatTurn],
        lang: str,
        is_mobile_view: bool,
        temp_overrides: dict[str, str] | None = None,
    ) -> list[LLMMessage]:
        template = self._prompts.load("fare_rules/chat_system.md", temp_overrides)
        layout = self._prompts.load(
            "fare_rules/chat_layout_mobile.md" if is_mobile_view else "fare_rules/chat_layout_desktop.md", temp_overrides
        )
        system_prompt = PromptService.render(
            template,
            LANG=lang,
            LANG_NAME=language_name(lang),
            CURRENT_DATE=datetime.now(UTC).date().isoformat(),
            JOURNEY_TABLE=_segments_table(segments),
            SEGMENT_FARE_RULES=_segments_fare_rules(segments),
            RESPONSE_LAYOUT_GUIDELINES=layout,
        )
        messages: list[LLMMessage] = [LLMMessage("system", system_prompt)]
        for turn in history[-self._settings.max_history_messages :]:
            role = "assistant" if turn.role == "assistant" else "user"
            if turn.content.strip():
                messages.append(LLMMessage(role, turn.content))
        messages.append(LLMMessage("user", user_message))
        return messages

    # -------------------------------------------------------------- calls
    async def stream(
        self,
        context: ChatContext,
        *,
        user_message: str,
        history: list[ChatTurn],
        lang: str,
        is_mobile_view: bool,
        api_key_id: int | None,
        request_id: str | None,
        result: ChatStreamResult,
    ) -> AsyncIterator[str]:
        """Yield text deltas; fills `result` and records usage when finished."""
        provider, cfg = await self._llm_config.get_provider()
        result.provider, result.model = cfg.provider, cfg.model
        messages = self.build_messages(context.segments, user_message=user_message, history=history, lang=lang, is_mobile_view=is_mobile_view)
        started = time.perf_counter()
        status, error_code = "ok", None
        try:
            async for event in provider.stream(messages, cfg.generation_options(self._settings.llm_timeout_seconds)):
                if event.text:
                    result.text_parts.append(event.text)
                    result.token_events += 1
                    yield event.text
                if event.usage is not None:
                    result.usage = result.usage.merge(event.usage)
        except LLMProviderError as exc:
            status, error_code = "error", exc.code
            raise LLMUpstreamError(exc.message, status_code=exc.status_code, code=exc.code) from exc
        except Exception:
            status, error_code = "error", "STREAM_FAILED"
            raise
        finally:
            await self._usage.record(
                UsageEvent(
                    feature="chat", status=status, provider=cfg.provider, model=cfg.model, error_code=error_code,
                    cache_status="n/a", usage=result.usage, latency_ms=int((time.perf_counter() - started) * 1000),
                    lang=lang, api_key_id=api_key_id, convo_id=context.convo_id, request_id=request_id,
                )
            )

    async def answer(
        self,
        context: ChatContext,
        *,
        user_message: str,
        history: list[ChatTurn],
        lang: str,
        is_mobile_view: bool,
        api_key_id: int | None,
        request_id: str | None,
    ) -> ChatStreamResult:
        result = ChatStreamResult(convo_id=context.convo_id, provider="", model="")
        async for _ in self.stream(
            context, user_message=user_message, history=history, lang=lang, is_mobile_view=is_mobile_view,
            api_key_id=api_key_id, request_id=request_id, result=result,
        ):
            pass
        return result


# ---------------------------------------------------------------- helpers


def _segments_table(segments: list[Segment]) -> str:
    lines = ["| # | From | To | Departure date |", "| --- | --- | --- | --- |"]
    for index, seg in enumerate(segments, start=1):
        lines.append(
            f"| {index} | {seg.get('source_airport', '-').upper()} | {seg.get('destination_airport', '-').upper()} | {seg.get('departure_date', '-')} |"
        )
    return "\n".join(lines)


def _segments_fare_rules(segments: list[Segment]) -> str:
    blocks: list[str] = []
    for index, seg in enumerate(segments, start=1):
        header = (
            f"<segment index=\"{index}\" route=\"{seg.get('source_airport', '-').upper()}-"
            f"{seg.get('destination_airport', '-').upper()}\" departure_date=\"{seg.get('departure_date', '-')}\">"
        )
        body = normalize_for_prompt(seg.get("fare_rules_text", "")) or "(no fare rules text provided)"
        blocks.append(f"{header}\n{body}\n</segment>")
    return "\n\n".join(blocks)


def stream_event_text(event: StreamEvent) -> str | None:  # small helper used in tests
    return event.text
