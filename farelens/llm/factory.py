"""Build a provider adapter from a stored (decrypted) configuration."""

from __future__ import annotations

from typing import Any

from farelens.llm.base import LLMProvider
from farelens.llm.registry import get_spec


def build_provider(provider_id: str, model: str, config: dict[str, Any], *, timeout: float) -> LLMProvider:
    """`config` holds both settings and *decrypted* secrets keyed by field key."""
    spec = get_spec(provider_id)
    model = (model or spec.default_model or "").strip()
    if not model:
        raise ValueError(f"{spec.label}: {spec.model_label.lower()} is required")

    for f in spec.fields:
        if f.required and not str(config.get(f.key) or "").strip():
            raise ValueError(f"{spec.label}: '{f.label}' is required")

    get = lambda key, default="": str(config.get(key) or default).strip()  # noqa: E731

    if spec.id == "openai":
        from farelens.llm.providers.openai_compat import OpenAICompatProvider

        return OpenAICompatProvider.for_openai(model, get("api_key"), get("base_url") or None, timeout)

    if spec.id == "groq":
        from farelens.llm.providers.openai_compat import OpenAICompatProvider

        return OpenAICompatProvider.for_groq(model, get("api_key"), timeout)

    if spec.id == "azure_openai":
        from farelens.llm.providers.openai_compat import OpenAICompatProvider

        return OpenAICompatProvider.for_azure(
            model, get("api_key"), get("endpoint"), get("api_version", "2024-10-21"), timeout
        )

    if spec.id == "anthropic":
        from farelens.llm.providers.anthropic_provider import AnthropicProvider

        return AnthropicProvider(model, get("api_key"), timeout)

    if spec.id == "google":
        from farelens.llm.providers.google_provider import GoogleProvider

        return GoogleProvider(model, get("api_key"), timeout)

    if spec.id == "bedrock":
        from farelens.llm.providers.bedrock_provider import BedrockProvider

        return BedrockProvider(
            model,
            region=get("region"),
            access_key_id=get("access_key_id") or None,
            secret_access_key=get("secret_access_key") or None,
            session_token=get("session_token") or None,
            timeout=timeout,
        )

    raise ValueError(f"No adapter for provider '{spec.id}'")  # pragma: no cover
