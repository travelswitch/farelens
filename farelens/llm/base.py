"""Provider-agnostic chat interface.

Each provider adapter implements `complete` (one-shot) and `stream` (token
stream). Both report token usage when the upstream API exposes it so the
usage dashboard stays accurate regardless of vendor.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Literal

Role = Literal["system", "user", "assistant"]


@dataclass(slots=True)
class LLMMessage:
    role: Role
    content: str


@dataclass(slots=True)
class LLMUsage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None

    @property
    def total_tokens(self) -> int | None:
        if self.prompt_tokens is None and self.completion_tokens is None:
            return None
        return (self.prompt_tokens or 0) + (self.completion_tokens or 0)

    def merge(self, other: LLMUsage | None) -> LLMUsage:
        if other is None:
            return self
        return LLMUsage(
            prompt_tokens=other.prompt_tokens if other.prompt_tokens is not None else self.prompt_tokens,
            completion_tokens=(
                other.completion_tokens if other.completion_tokens is not None else self.completion_tokens
            ),
        )


@dataclass(slots=True)
class LLMResponse:
    text: str
    usage: LLMUsage = field(default_factory=LLMUsage)
    model: str = ""
    finish_reason: str | None = None


@dataclass(slots=True)
class StreamEvent:
    """Either a text delta or the final usage report (exactly one is set)."""

    text: str | None = None
    usage: LLMUsage | None = None


@dataclass(slots=True)
class GenerationOptions:
    temperature: float | None = 0.0
    max_tokens: int = 4096
    timeout_seconds: float = 90.0


class LLMProviderError(Exception):
    """Normalised upstream failure (auth, quota, model not found, ...)."""

    def __init__(self, message: str, *, code: str = "LLM_UPSTREAM_ERROR", status_code: int = 502, retryable: bool = False):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code
        self.retryable = retryable


class LLMProvider(ABC):
    name: str = "base"

    def __init__(self, model: str):
        self.model = model

    @abstractmethod
    async def complete(self, messages: list[LLMMessage], options: GenerationOptions) -> LLMResponse: ...

    @abstractmethod
    def stream(self, messages: list[LLMMessage], options: GenerationOptions) -> AsyncIterator[StreamEvent]: ...

    async def close(self) -> None:  # noqa: B027 - optional hook
        return None

    # Helpers shared by adapters -------------------------------------------
    @staticmethod
    def split_system(messages: list[LLMMessage]) -> tuple[str, list[LLMMessage]]:
        """Return (joined system text, non-system messages)."""
        system_parts = [m.content for m in messages if m.role == "system"]
        rest = [m for m in messages if m.role != "system"]
        return "\n\n".join(system_parts), rest


def classify_http_error(status: int | None, message: str) -> LLMProviderError:
    """Map an upstream HTTP status to a stable error code and client status."""
    text = message or "Upstream LLM request failed"
    if status in (401, 403):
        return LLMProviderError(f"Upstream rejected the credentials: {text}", code="LLM_AUTH_ERROR", status_code=502)
    if status == 404:
        return LLMProviderError(f"Model or endpoint not found: {text}", code="LLM_MODEL_NOT_FOUND", status_code=502)
    if status == 429:
        return LLMProviderError(f"Upstream rate limit / quota exceeded: {text}", code="LLM_RATE_LIMITED", status_code=503, retryable=True)
    if status == 400 or status == 422:
        return LLMProviderError(f"Upstream rejected the request: {text}", code="LLM_BAD_REQUEST", status_code=502)
    if status is not None and status >= 500:
        return LLMProviderError(f"Upstream server error ({status}): {text}", code="LLM_UPSTREAM_ERROR", status_code=502, retryable=True)
    return LLMProviderError(text, code="LLM_UPSTREAM_ERROR", status_code=502)
