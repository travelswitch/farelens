"""OpenAI Chat Completions adapter - used for OpenAI, Azure OpenAI and Groq.

Groq exposes an OpenAI-compatible API, so one adapter covers all three; only
client construction differs.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

import openai
from openai import AsyncAzureOpenAI, AsyncOpenAI

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

GROQ_BASE_URL = "https://api.groq.com/openai/v1"


def _wrap_error(exc: Exception) -> LLMProviderError:
    if isinstance(exc, openai.APIStatusError):
        body = getattr(exc, "body", None)
        detail = ""
        if isinstance(body, dict):
            err = body.get("error") if isinstance(body.get("error"), dict) else body
            detail = str((err or {}).get("message") or "")
        return classify_http_error(exc.status_code, detail or str(exc))
    if isinstance(exc, openai.APITimeoutError):
        return LLMProviderError("Upstream LLM request timed out", code="LLM_TIMEOUT", status_code=504, retryable=True)
    if isinstance(exc, openai.APIConnectionError):
        return LLMProviderError(f"Could not reach the LLM endpoint: {exc}", code="LLM_CONNECTION_ERROR", status_code=502, retryable=True)
    return LLMProviderError(str(exc))


def _usage_from(obj: Any) -> LLMUsage | None:
    usage = getattr(obj, "usage", None)
    if usage is None:
        # Groq reports usage under x_groq on the final chunk.
        extra = getattr(obj, "model_extra", None) or {}
        x_groq = extra.get("x_groq") if isinstance(extra, dict) else None
        if isinstance(x_groq, dict) and isinstance(x_groq.get("usage"), dict):
            u = x_groq["usage"]
            return LLMUsage(prompt_tokens=u.get("prompt_tokens"), completion_tokens=u.get("completion_tokens"))
        return None
    return LLMUsage(
        prompt_tokens=getattr(usage, "prompt_tokens", None),
        completion_tokens=getattr(usage, "completion_tokens", None),
    )


class OpenAICompatProvider(LLMProvider):
    name = "openai"

    def __init__(self, model: str, client: AsyncOpenAI, *, name: str, include_stream_usage: bool = True):
        super().__init__(model)
        self.name = name
        self._client = client
        self._include_stream_usage = include_stream_usage

    # ------------------------------------------------------------ factories
    @classmethod
    def for_openai(cls, model: str, api_key: str, base_url: str | None, timeout: float) -> OpenAICompatProvider:
        client = AsyncOpenAI(api_key=api_key, base_url=base_url or None, timeout=timeout, max_retries=2)
        return cls(model, client, name="openai")

    @classmethod
    def for_groq(cls, model: str, api_key: str, timeout: float) -> OpenAICompatProvider:
        client = AsyncOpenAI(api_key=api_key, base_url=GROQ_BASE_URL, timeout=timeout, max_retries=2)
        return cls(model, client, name="groq")

    @classmethod
    def for_azure(cls, deployment: str, api_key: str, endpoint: str, api_version: str, timeout: float) -> OpenAICompatProvider:
        client = AsyncAzureOpenAI(
            api_key=api_key,
            azure_endpoint=endpoint,
            api_version=api_version or "2024-10-21",
            timeout=timeout,
            max_retries=2,
        )
        return cls(deployment, client, name="azure_openai")

    # ------------------------------------------------------------- helpers
    @staticmethod
    def _to_messages(messages: list[LLMMessage]) -> list[dict[str, str]]:
        return [{"role": m.role, "content": m.content} for m in messages]

    def _params(self, messages: list[LLMMessage], options: GenerationOptions) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": self.model,
            "messages": self._to_messages(messages),
            "max_tokens": options.max_tokens,
        }
        if options.temperature is not None:
            params["temperature"] = options.temperature
        return params

    # ---------------------------------------------------------------- calls
    async def complete(self, messages: list[LLMMessage], options: GenerationOptions) -> LLMResponse:
        try:
            resp = await self._client.with_options(timeout=options.timeout_seconds).chat.completions.create(
                **self._params(messages, options)
            )
        except Exception as exc:  # noqa: BLE001
            raise _wrap_error(exc) from exc

        choice = resp.choices[0] if resp.choices else None
        text = (choice.message.content or "") if choice and choice.message else ""
        return LLMResponse(
            text=text,
            usage=_usage_from(resp) or LLMUsage(),
            model=resp.model or self.model,
            finish_reason=getattr(choice, "finish_reason", None) if choice else None,
        )

    async def stream(self, messages: list[LLMMessage], options: GenerationOptions) -> AsyncIterator[StreamEvent]:
        params = self._params(messages, options)
        params["stream"] = True
        if self._include_stream_usage:
            params["stream_options"] = {"include_usage": True}

        client = self._client.with_options(timeout=options.timeout_seconds)
        try:
            try:
                stream = await client.chat.completions.create(**params)
            except openai.BadRequestError as exc:
                # Some OpenAI-compatible gateways reject stream_options; retry without it.
                if "stream_options" in str(exc) and "stream_options" in params:
                    params.pop("stream_options")
                    stream = await client.chat.completions.create(**params)
                else:
                    raise
            usage: LLMUsage | None = None
            async for chunk in stream:
                chunk_usage = _usage_from(chunk)
                if chunk_usage is not None:
                    usage = chunk_usage
                if chunk.choices:
                    delta = chunk.choices[0].delta
                    if delta and delta.content:
                        yield StreamEvent(text=delta.content)
            yield StreamEvent(usage=usage or LLMUsage())
        except LLMProviderError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _wrap_error(exc) from exc

    async def close(self) -> None:
        try:
            await self._client.close()
        except Exception:  # pragma: no cover - best effort
            pass
