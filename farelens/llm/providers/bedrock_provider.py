"""AWS Bedrock adapter using the Converse API (boto3).

boto3 is synchronous, so calls run in a worker thread. Streaming bridges the
blocking event iterator into an asyncio queue.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

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

_SENTINEL = object()


def _wrap_error(exc: Exception) -> LLMProviderError:
    if isinstance(exc, ClientError):
        meta = exc.response.get("ResponseMetadata", {}) if hasattr(exc, "response") else {}
        err = exc.response.get("Error", {}) if hasattr(exc, "response") else {}
        code = err.get("Code", "")
        message = err.get("Message") or str(exc)
        status = meta.get("HTTPStatusCode")
        if code in {"AccessDeniedException", "UnrecognizedClientException", "InvalidSignatureException", "ExpiredTokenException"}:
            status = 403
        elif code == "ResourceNotFoundException":
            status = 404
        elif code in {"ThrottlingException", "ServiceQuotaExceededException"}:
            status = 429
        elif code == "ValidationException":
            status = 400
        return classify_http_error(status, f"{code}: {message}" if code else message)
    if isinstance(exc, BotoCoreError):
        return LLMProviderError(f"AWS client error: {exc}", code="LLM_CONNECTION_ERROR", status_code=502, retryable=True)
    return LLMProviderError(str(exc))


class BedrockProvider(LLMProvider):
    name = "bedrock"

    def __init__(
        self,
        model: str,
        *,
        region: str,
        access_key_id: str | None,
        secret_access_key: str | None,
        session_token: str | None,
        timeout: float,
    ):
        super().__init__(model)
        kwargs: dict[str, Any] = {
            "region_name": region,
            "config": BotoConfig(read_timeout=int(timeout), connect_timeout=10, retries={"max_attempts": 2}),
        }
        if access_key_id and secret_access_key:
            kwargs["aws_access_key_id"] = access_key_id
            kwargs["aws_secret_access_key"] = secret_access_key
            if session_token:
                kwargs["aws_session_token"] = session_token
        self._client = boto3.client("bedrock-runtime", **kwargs)

    def _params(self, messages: list[LLMMessage], options: GenerationOptions) -> dict[str, Any]:
        system, rest = self.split_system(messages)
        inference: dict[str, Any] = {"maxTokens": options.max_tokens}
        if options.temperature is not None:
            inference["temperature"] = options.temperature
        params: dict[str, Any] = {
            "modelId": self.model,
            "messages": [{"role": m.role, "content": [{"text": m.content}]} for m in rest],
            "inferenceConfig": inference,
        }
        if system:
            params["system"] = [{"text": system}]
        return params

    async def complete(self, messages: list[LLMMessage], options: GenerationOptions) -> LLMResponse:
        params = self._params(messages, options)
        try:
            resp = await asyncio.to_thread(self._client.converse, **params)
        except Exception as exc:  # noqa: BLE001
            raise _wrap_error(exc) from exc
        content = resp.get("output", {}).get("message", {}).get("content", [])
        text = "".join(block.get("text", "") for block in content if "text" in block)
        usage = resp.get("usage", {})
        return LLMResponse(
            text=text,
            usage=LLMUsage(prompt_tokens=usage.get("inputTokens"), completion_tokens=usage.get("outputTokens")),
            model=self.model,
            finish_reason=resp.get("stopReason"),
        )

    async def stream(self, messages: list[LLMMessage], options: GenerationOptions) -> AsyncIterator[StreamEvent]:
        params = self._params(messages, options)
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Any] = asyncio.Queue()

        def _produce() -> None:
            try:
                resp = self._client.converse_stream(**params)
                for event in resp.get("stream", []):
                    loop.call_soon_threadsafe(queue.put_nowait, event)
            except Exception as exc:  # noqa: BLE001
                loop.call_soon_threadsafe(queue.put_nowait, exc)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, _SENTINEL)

        producer = loop.run_in_executor(None, _produce)
        usage = LLMUsage()
        try:
            while True:
                item = await queue.get()
                if item is _SENTINEL:
                    break
                if isinstance(item, Exception):
                    raise _wrap_error(item) from item
                if "contentBlockDelta" in item:
                    text = item["contentBlockDelta"].get("delta", {}).get("text")
                    if text:
                        yield StreamEvent(text=text)
                elif "metadata" in item:
                    u = item["metadata"].get("usage", {})
                    usage = LLMUsage(prompt_tokens=u.get("inputTokens"), completion_tokens=u.get("outputTokens"))
            yield StreamEvent(usage=usage)
        finally:
            await producer
