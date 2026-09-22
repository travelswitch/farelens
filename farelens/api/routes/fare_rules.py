"""Public API: fare-rules summary and Q&A chat."""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Response
from fastapi.responses import StreamingResponse

from farelens.api.deps import Principal, Services, get_services, request_id, require_principal
from farelens.api.sse import SSE_HEADERS, sse_event
from farelens.core.errors import AppError
from farelens.schemas.fare_rules import (
    ChatRequest,
    ChatResponse,
    ConversationDeleteResponse,
    SummaryRequest,
    SummaryResponse,
)
from farelens.services.chat import ChatStreamResult, ChatTurn

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/fare-rules", tags=["fare-rules"])


@router.post(
    "/summary",
    response_model=SummaryResponse,
    summary="Summarise fare rules into traveller-friendly Markdown",
    description=(
        "Turns raw airline fare-rules text into a concise Markdown summary covering cancellation/refund, "
        "change/reissue and no-show conditions. Results are cached (Redis → Postgres) by content digest; "
        "the `X-Cache` response header reports `redis-hit`, `postgres-hit` or `generated`."
    ),
)
async def summarize_fare_rules(
    payload: SummaryRequest,
    response: Response,
    principal: Principal = Depends(require_principal),
    services: Services = Depends(get_services),
    rid: str = Depends(request_id),
) -> SummaryResponse:
    result = await services.summary.summarize(
        payload.fare_rules_text,
        lang=payload.lang,
        is_mobile_view=payload.is_mobile_view,
        api_key_id=principal.api_key_id,
        request_id=rid,
        bypass_cache=payload.bypass_cache,
    )
    response.headers["X-Cache"] = result.cache_status
    logger.info("summary ok cache=%s provider=%s lang=%s by=%s", result.cache_status, result.provider, payload.lang, principal.name)
    return SummaryResponse(
        summary_markdown=result.summary_markdown,
        cache=result.cache_status,
        provider=result.provider,
        model=result.model,
        lang=payload.lang,
        is_mobile_view=payload.is_mobile_view,
        latency_ms=result.latency_ms,
    )


@router.post(
    "/chat/stream",
    summary="Ask a question about the fare rules (Server-Sent Events)",
    description=(
        "Streams the answer as `text/event-stream`. Events: `start` (convo_id + segment metadata), "
        "`token` (text delta), `end` (usage), `error`. Start a conversation by sending `segments`; "
        "follow-ups only need `convo_id`, `user_message` and the client-side `history`."
    ),
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def chat_stream(
    payload: ChatRequest,
    principal: Principal = Depends(require_principal),
    services: Services = Depends(get_services),
    rid: str = Depends(request_id),
) -> StreamingResponse:
    segments = [s.to_segment() for s in payload.segments] if payload.segments else None
    context = await services.chat.resolve_context(payload.convo_id, segments)
    history = [ChatTurn(m.role, m.content) for m in payload.history]
    result = ChatStreamResult(convo_id=context.convo_id, provider="", model="")

    async def generator() -> AsyncIterator[str]:
        started = time.perf_counter()
        yield sse_event(
            "start",
            {
                "convo_id": context.convo_id,
                "request_id": rid,
                "segments": [
                    {k: s[k] for k in ("source_airport", "destination_airport", "departure_date")}
                    for s in context.segments
                ],
            },
        )
        try:
            async for delta in services.chat.stream(
                context,
                user_message=payload.user_message,
                history=history,
                lang=payload.lang,
                is_mobile_view=payload.is_mobile_view,
                api_key_id=principal.api_key_id,
                request_id=rid,
                result=result,
            ):
                yield sse_event("token", {"content": delta})
            if result.token_events == 0:
                yield sse_event("info", {"message": "The model returned no text."})
            yield sse_event(
                "end",
                {
                    "convo_id": context.convo_id,
                    "request_id": rid,
                    "provider": result.provider,
                    "model": result.model,
                    "usage": {
                        "prompt_tokens": result.usage.prompt_tokens,
                        "completion_tokens": result.usage.completion_tokens,
                        "total_tokens": result.usage.total_tokens,
                    },
                    "latency_ms": int((time.perf_counter() - started) * 1000),
                },
            )
        except AppError as exc:
            logger.warning("chat stream error convo_id=%s code=%s: %s", context.convo_id, exc.code, exc.message)
            yield sse_event("error", {"request_id": rid, "convo_id": context.convo_id, "error": {"code": exc.code, "message": exc.message, "http_status": exc.status_code}})
        except Exception:
            logger.exception("chat stream failed convo_id=%s", context.convo_id)
            yield sse_event("error", {"request_id": rid, "convo_id": context.convo_id, "error": {"code": "STREAM_FAILED", "message": "Streaming failed.", "http_status": 500}})

    return StreamingResponse(generator(), media_type="text/event-stream; charset=utf-8", headers=SSE_HEADERS)


@router.post(
    "/chat",
    response_model=ChatResponse,
    summary="Ask a question about the fare rules (single JSON response)",
)
async def chat_once(
    payload: ChatRequest,
    principal: Principal = Depends(require_principal),
    services: Services = Depends(get_services),
    rid: str = Depends(request_id),
) -> ChatResponse:
    segments = [s.to_segment() for s in payload.segments] if payload.segments else None
    context = await services.chat.resolve_context(payload.convo_id, segments)
    started = time.perf_counter()
    result = await services.chat.answer(
        context,
        user_message=payload.user_message,
        history=[ChatTurn(m.role, m.content) for m in payload.history],
        lang=payload.lang,
        is_mobile_view=payload.is_mobile_view,
        api_key_id=principal.api_key_id,
        request_id=rid,
    )
    return ChatResponse(
        convo_id=context.convo_id,
        answer_markdown=result.text.strip(),
        provider=result.provider,
        model=result.model,
        usage={
            "prompt_tokens": result.usage.prompt_tokens,
            "completion_tokens": result.usage.completion_tokens,
            "total_tokens": result.usage.total_tokens,
        },
        latency_ms=int((time.perf_counter() - started) * 1000),
    )


@router.delete(
    "/chat/{convo_id}",
    response_model=ConversationDeleteResponse,
    summary="Forget a conversation's stored segments",
)
async def delete_conversation(
    convo_id: str,
    _: Principal = Depends(require_principal),
    services: Services = Depends(get_services),
) -> ConversationDeleteResponse:
    removed = await services.chat.forget(convo_id)
    return ConversationDeleteResponse(convo_id=convo_id, removed=removed)
