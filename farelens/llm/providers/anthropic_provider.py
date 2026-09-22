"""Anthropic (Claude) adapter using the official `anthropic` SDK."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

import anthropic

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
    if isinstance(exc, anthropic.APIStatusError):
        return classify_http_error(exc.status_code, getattr(exc, "message", None) or str(exc))
    if isinstance(exc, anthropic.APITimeoutError):
        return LLMProviderError("Upstream LLM request timed out", code="LLM_TIMEOUT", status_code=504, retryable=True)
    if isinstance(exc, anthropic.APIConnectionError):
        return LLMProviderError(f"Could not reach Anthropic: {exc}", code="LLM_CONNECTION_ERROR", status_code=502, retryable=True)
    return LLMProviderError(str(exc))


def _usage_from(message: Any) -> LLMUsage:
    usage = getattr(message, "usage", None)
    if usage is None:
        return LLMUsage()
    prompt = getattr(usage, "input_tokens", None)
    # Cached tokens are still input tokens for cost visibility.
    for attr in ("cache_read_input_tokens", "cache_creation_input_tokens"):
        extra = getattr(usage, attr, None)
        if prompt is not None and extra:
            prompt += int(extra)
    return LLMUsage(prompt_tokens=prompt, completion_tokens=getattr(usage, "output_tokens", None))


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, model: str, api_key: str, timeout: float):
        super().__init__(model)
        self._client = anthropic.AsyncAnthropic(api_key=api_key, timeout=timeout, max_retries=2)

    def _params(self, messages: list[LLMMessage], options: GenerationOptions) -> dict[str, Any]:
        system, rest = self.split_system(messages)
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": options.max_tokens,
            "messages": [{"role": m.role, "content": m.content} for m in rest],
        }
        if system:
            params["system"] = system
        # Sampling parameters are intentionally omitted: current Claude models
        # reject temperature/top_p, and older ones default to sensible values.
        return params

    async def complete(self, messages: list[LLMMessage], options: GenerationOptions) -> LLMResponse:
        try:
            msg = await self._client.with_options(timeout=options.timeout_seconds).messages.create(
                **self._params(messages, options)
            )
        except Exception as exc:  # noqa: BLE001
            raise _wrap_error(exc) from exc

        if msg.stop_reason == "refusal":
            raise LLMProviderError("The model declined to answer this request.", code="LLM_REFUSAL", status_code=502)
        text = "".join(block.text for block in msg.content if getattr(block, "type", "") == "text")
        return LLMResponse(text=text, usage=_usage_from(msg), model=msg.model, finish_reason=msg.stop_reason)

    async def stream(self, messages: list[LLMMessage], options: GenerationOptions) -> AsyncIterator[StreamEvent]:
        try:
            async with self._client.with_options(timeout=options.timeout_seconds).messages.stream(
                **self._params(messages, options)
            ) as stream:
                async for text in stream.text_stream:
                    if text:
                        yield StreamEvent(text=text)
                final = await stream.get_final_message()
            if final.stop_reason == "refusal":
                raise LLMProviderError("The model declined to answer this request.", code="LLM_REFUSAL", status_code=502)
            yield StreamEvent(usage=_usage_from(final))
        except LLMProviderError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _wrap_error(exc) from exc

    async def close(self) -> None:
        try:
            await self._client.close()
        except Exception:  # pragma: no cover
            pass
