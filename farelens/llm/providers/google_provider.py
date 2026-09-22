"""Google Gemini adapter using the official `google-genai` SDK."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

from farelens.llm.base import (
    GenerationOptions,
    LLMMessage,
    LLMProvider,
    LLMProviderError,
    LLMResponse,
    LLMUsage,
    StreamEvent,
    classify_http_error,
)

logger = logging.getLogger(__name__)


def _wrap_error(exc: Exception) -> LLMProviderError:
    if isinstance(exc, genai_errors.APIError):
        return classify_http_error(getattr(exc, "code", None), getattr(exc, "message", None) or str(exc))
    return LLMProviderError(str(exc))


def _usage_from(resp: Any) -> LLMUsage | None:
    meta = getattr(resp, "usage_metadata", None)
    if meta is None:
        return None
    return LLMUsage(
        prompt_tokens=getattr(meta, "prompt_token_count", None),
        completion_tokens=getattr(meta, "candidates_token_count", None),
    )


def _text_from(resp: Any) -> str:
    # `resp.text` raises/warns when the candidate has no text parts; walk parts instead.
    candidates = getattr(resp, "candidates", None) or []
    out: list[str] = []
    for cand in candidates:
        content = getattr(cand, "content", None)
        for part in (getattr(content, "parts", None) or []):
            text = getattr(part, "text", None)
            if text and not getattr(part, "thought", False):
                out.append(text)
    return "".join(out)


class GoogleProvider(LLMProvider):
    name = "google"

    def __init__(self, model: str, api_key: str, timeout: float):
        super().__init__(model)
        self._client = genai.Client(
            api_key=api_key,
            http_options=genai_types.HttpOptions(timeout=int(timeout * 1000)),
        )

    def _build(self, messages: list[LLMMessage], options: GenerationOptions) -> tuple[list[genai_types.Content], genai_types.GenerateContentConfig]:
        system, rest = self.split_system(messages)
        contents = [
            genai_types.Content(
                role="model" if m.role == "assistant" else "user",
                parts=[genai_types.Part.from_text(text=m.content)],
            )
            for m in rest
        ]
        config = genai_types.GenerateContentConfig(
            system_instruction=system or None,
            temperature=options.temperature,
            max_output_tokens=options.max_tokens,
        )
        return contents, config

    async def complete(self, messages: list[LLMMessage], options: GenerationOptions) -> LLMResponse:
        contents, config = self._build(messages, options)
        try:
            resp = await self._client.aio.models.generate_content(model=self.model, contents=contents, config=config)
        except Exception as exc:  # noqa: BLE001
            raise _wrap_error(exc) from exc
        finish = None
        if resp.candidates:
            finish = str(getattr(resp.candidates[0], "finish_reason", "") or "") or None
        return LLMResponse(text=_text_from(resp), usage=_usage_from(resp) or LLMUsage(), model=self.model, finish_reason=finish)

    async def stream(self, messages: list[LLMMessage], options: GenerationOptions) -> AsyncIterator[StreamEvent]:
        contents, config = self._build(messages, options)
        usage: LLMUsage | None = None
        try:
            stream = await self._client.aio.models.generate_content_stream(model=self.model, contents=contents, config=config)
            async for chunk in stream:
                chunk_usage = _usage_from(chunk)
                if chunk_usage is not None and chunk_usage.total_tokens:
                    usage = chunk_usage
                text = _text_from(chunk)
                if text:
                    yield StreamEvent(text=text)
            yield StreamEvent(usage=usage or LLMUsage())
        except LLMProviderError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _wrap_error(exc) from exc
