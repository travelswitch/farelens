"""Catalogue of supported LLM providers.

The admin UI renders its provider form from this catalogue, so adding a
provider is: add a `ProviderSpec` here + an adapter in `factory.py`.
Model lists are *suggestions* only - any model id can be typed in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ProviderField:
    key: str
    label: str
    secret: bool = False
    required: bool = True
    placeholder: str = ""
    help: str = ""
    default: str = ""


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    id: str
    label: str
    description: str
    docs_url: str
    fields: tuple[ProviderField, ...]
    suggested_models: tuple[str, ...]
    default_model: str
    model_label: str = "Model"
    model_help: str = ""
    supports_temperature: bool = True
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def secret_keys(self) -> tuple[str, ...]:
        return tuple(f.key for f in self.fields if f.secret)

    @property
    def setting_keys(self) -> tuple[str, ...]:
        return tuple(f.key for f in self.fields if not f.secret)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "docs_url": self.docs_url,
            "model_label": self.model_label,
            "model_help": self.model_help,
            "default_model": self.default_model,
            "suggested_models": list(self.suggested_models),
            "supports_temperature": self.supports_temperature,
            "fields": [
                {
                    "key": f.key,
                    "label": f.label,
                    "secret": f.secret,
                    "required": f.required,
                    "placeholder": f.placeholder,
                    "help": f.help,
                    "default": f.default,
                }
                for f in self.fields
            ],
        }


PROVIDERS: dict[str, ProviderSpec] = {
    "openai": ProviderSpec(
        id="openai",
        label="OpenAI",
        description="OpenAI platform (GPT models). Also works with any OpenAI-compatible endpoint via Base URL.",
        docs_url="https://platform.openai.com/docs/models",
        fields=(
            ProviderField("api_key", "API key", secret=True, placeholder="sk-..."),
            ProviderField(
                "base_url",
                "Base URL (optional)",
                required=False,
                placeholder="https://api.openai.com/v1",
                help="Leave empty for OpenAI. Set for OpenAI-compatible gateways (e.g. a proxy, OpenRouter, vLLM).",
            ),
        ),
        suggested_models=("gpt-4.1", "gpt-4.1-mini", "gpt-4o", "gpt-4o-mini"),
        default_model="gpt-4.1-mini",
    ),
    "azure_openai": ProviderSpec(
        id="azure_openai",
        label="Azure AI (Azure OpenAI)",
        description="Models deployed through Azure OpenAI / Azure AI Foundry. The model field is your deployment name.",
        docs_url="https://learn.microsoft.com/azure/ai-services/openai/",
        fields=(
            ProviderField(
                "endpoint",
                "Endpoint",
                placeholder="https://<resource>.openai.azure.com",
                help="Your Azure OpenAI resource endpoint.",
            ),
            ProviderField("api_key", "API key", secret=True),
            ProviderField(
                "api_version",
                "API version",
                required=False,
                default="2024-10-21",
                placeholder="2024-10-21",
            ),
        ),
        suggested_models=(),
        default_model="",
        model_label="Deployment name",
        model_help="The name you gave the deployment in Azure (not the underlying model id).",
    ),
    "anthropic": ProviderSpec(
        id="anthropic",
        label="Anthropic (Claude)",
        description="Claude models via the Anthropic API.",
        docs_url="https://docs.claude.com/en/docs/about-claude/models",
        fields=(ProviderField("api_key", "API key", secret=True, placeholder="sk-ant-..."),),
        suggested_models=("claude-sonnet-5", "claude-opus-5", "claude-haiku-4-5", "claude-sonnet-4-6"),
        default_model="claude-sonnet-5",
        # Current Claude models reject sampling parameters; adaptive thinking is the default.
        supports_temperature=False,
    ),
    "google": ProviderSpec(
        id="google",
        label="Google (Gemini)",
        description="Gemini models via Google AI Studio API keys.",
        docs_url="https://ai.google.dev/gemini-api/docs/models",
        fields=(ProviderField("api_key", "API key", secret=True, placeholder="AIza..."),),
        suggested_models=("gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.5-flash-lite", "gemini-2.0-flash"),
        default_model="gemini-2.5-flash",
    ),
    "groq": ProviderSpec(
        id="groq",
        label="Groq",
        description="Fast open-weight models (Llama, GPT-OSS, Qwen, ...) on GroqCloud.",
        docs_url="https://console.groq.com/docs/models",
        fields=(ProviderField("api_key", "API key", secret=True, placeholder="gsk_..."),),
        suggested_models=(
            "openai/gpt-oss-120b",
            "openai/gpt-oss-20b",
            "qwen/qwen3.8-27b",
            "qwen/qwen3.6-27b",
        ),
        default_model="openai/gpt-oss-120b",
        model_help="Groq retires models regularly; check the console for the current list.",
    ),
    "bedrock": ProviderSpec(
        id="bedrock",
        label="AWS Bedrock",
        description="Any Bedrock model that supports the Converse API (Claude, Nova, Llama, Mistral, ...).",
        docs_url="https://docs.aws.amazon.com/bedrock/latest/userguide/models-supported.html",
        fields=(
            ProviderField("region", "AWS region", placeholder="us-east-1"),
            ProviderField(
                "access_key_id",
                "Access key ID",
                secret=True,
                required=False,
                help="Leave both keys empty to use the container's IAM role / default AWS credential chain.",
            ),
            ProviderField("secret_access_key", "Secret access key", secret=True, required=False),
            ProviderField("session_token", "Session token (optional)", secret=True, required=False),
        ),
        suggested_models=(
            "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
            "us.anthropic.claude-haiku-4-5-20251001-v1:0",
            "amazon.nova-pro-v1:0",
            "amazon.nova-lite-v1:0",
            "meta.llama3-3-70b-instruct-v1:0",
        ),
        default_model="amazon.nova-lite-v1:0",
        model_label="Model ID / inference profile",
        model_help="Use a cross-region inference profile id (us./eu. prefix) where the model requires one.",
    ),
}


def get_spec(provider_id: str) -> ProviderSpec:
    key = (provider_id or "").strip().lower()
    aliases = {
        "azure": "azure_openai",
        "azure-openai": "azure_openai",
        "azureopenai": "azure_openai",
        "azure_ai": "azure_openai",
        "claude": "anthropic",
        "gemini": "google",
        "google_ai": "google",
        "aws": "bedrock",
        "aws_bedrock": "bedrock",
    }
    key = aliases.get(key, key)
    if key not in PROVIDERS:
        raise ValueError(f"Unsupported LLM provider '{provider_id}'. Supported: {', '.join(PROVIDERS)}")
    return PROVIDERS[key]


def provider_catalog() -> list[dict[str, Any]]:
    return [spec.to_public_dict() for spec in PROVIDERS.values()]
