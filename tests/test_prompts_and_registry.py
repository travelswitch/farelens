from pathlib import Path

import pytest

from farelens.core.config import PROJECT_ROOT
from farelens.llm.factory import build_provider
from farelens.llm.registry import PROVIDERS, get_spec, provider_catalog
from farelens.services.prompts import PromptService, language_name


@pytest.fixture
def prompts() -> PromptService:
    return PromptService(PROJECT_ROOT / "prompts")


def test_all_prompt_files_exist_and_render(prompts):
    for name in [
        "fare_rules/summary_system.md",
        "fare_rules/summary_user.md",
        "fare_rules/summary_layout_desktop.md",
        "fare_rules/summary_layout_mobile.md",
        "fare_rules/chat_system.md",
        "fare_rules/chat_layout_desktop.md",
        "fare_rules/chat_layout_mobile.md",
    ]:
        assert prompts.load(name)

    system = PromptService.render(
        prompts.load("fare_rules/summary_system.md"),
        LANG="ar",
        LANG_NAME=language_name("ar"),
        LAYOUT_GUIDELINES=prompts.load("fare_rules/summary_layout_mobile.md"),
    )
    assert "[[" not in system
    assert "Arabic" in system and "NEVER use Markdown tables" in system


def test_chat_prompt_has_no_unrendered_placeholders(prompts):
    rendered = PromptService.render(
        prompts.load("fare_rules/chat_system.md"),
        LANG="en",
        LANG_NAME="English",
        CURRENT_DATE="2026-01-01",
        JOURNEY_TABLE="| 1 | DEL | DXB | 2026-02-01 |",
        SEGMENT_FARE_RULES="<segment index=\"1\">RULES</segment>",
        RESPONSE_LAYOUT_GUIDELINES="",
    )
    assert "[[" not in rendered and "RULES" in rendered


def test_registry_catalog_covers_required_providers():
    ids = {p["id"] for p in provider_catalog()}
    assert {"openai", "azure_openai", "anthropic", "google", "groq", "bedrock"} <= ids
    assert get_spec("claude").id == "anthropic"
    assert get_spec("Azure").id == "azure_openai"
    with pytest.raises(ValueError):
        get_spec("nope")


def test_build_provider_validates_required_fields():
    with pytest.raises(ValueError):
        build_provider("openai", "gpt-4.1-mini", {}, timeout=10)
    with pytest.raises(ValueError):
        build_provider("azure_openai", "", {"api_key": "k", "endpoint": "https://x"}, timeout=10)

    provider = build_provider("groq", "", {"api_key": "gsk_test"}, timeout=10)
    assert provider.name == "groq" and provider.model == PROVIDERS["groq"].default_model

    bedrock = build_provider("bedrock", "amazon.nova-lite-v1:0", {"region": "us-east-1"}, timeout=10)
    assert bedrock.name == "bedrock"


def test_migrations_directory_has_initial():
    assert (Path(PROJECT_ROOT) / "migrations" / "0001_initial.sql").exists()
