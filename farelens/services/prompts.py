"""Prompt templates: file defaults under `prompts/`, optional overrides from the admin UI.

Templates use `[[PLACEHOLDER]]` tokens (not str.format) so that braces in
fare-rules text or markdown examples never need escaping.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

LANGUAGE_NAMES: dict[str, str] = {
    "en": "English",
    "ar": "Arabic",
    "fr": "French",
    "de": "German",
    "es": "Spanish",
    "it": "Italian",
    "pt": "Portuguese",
    "ru": "Russian",
    "tr": "Turkish",
    "ur": "Urdu",
    "hi": "Hindi",
    "bn": "Bengali",
    "id": "Indonesian",
    "ms": "Malay",
    "zh": "Chinese (Simplified)",
    "cn": "Chinese (Simplified)",
    "ja": "Japanese",
    "ko": "Korean",
    "ka": "Georgian",
    "ge": "Georgian",
    "nl": "Dutch",
    "pl": "Polish",
    "th": "Thai",
    "vi": "Vietnamese",
    "fa": "Persian",
}

RTL_LANGUAGES = {"ar", "ur", "fa", "he"}

_PLACEHOLDER_RE = re.compile(r"\[\[([A-Z_]+)\]\]")


def language_name(code: str) -> str:
    return LANGUAGE_NAMES.get((code or "en").lower(), code)


@dataclass(frozen=True, slots=True)
class PromptSpec:
    id: str
    file: str
    feature: str  # summary | chat
    label: str
    description: str
    allowed: tuple[str, ...] = ()
    required: tuple[str, ...] = field(default=())


PROMPTS: dict[str, PromptSpec] = {
    s.id: s
    for s in (
        PromptSpec("summary_system", "fare_rules/summary_system.md", "summary", "Summary · system prompt",
                   "Role, extraction rules and accuracy policy for the fare-rules summary.",
                   ("LANG", "LANG_NAME", "LAYOUT_GUIDELINES"), ("LANG_NAME", "LAYOUT_GUIDELINES")),
        PromptSpec("summary_user", "fare_rules/summary_user.md", "summary", "Summary · user message",
                   "Wraps the fare-rules text sent to the model.",
                   ("FARE_RULES_TEXT", "LANG_NAME"), ("FARE_RULES_TEXT",)),
        PromptSpec("summary_layout_desktop", "fare_rules/summary_layout_desktop.md", "summary", "Summary · desktop layout",
                   "Table columns and formatting rules used when is_mobile_view is false."),
        PromptSpec("summary_layout_mobile", "fare_rules/summary_layout_mobile.md", "summary", "Summary · mobile layout",
                   "Compact-section formatting rules used when is_mobile_view is true."),
        PromptSpec("chat_system", "fare_rules/chat_system.md", "chat", "Chat · system prompt",
                   "Scope, segment/date reasoning and answer style for fare-rules Q&A.",
                   ("LANG", "LANG_NAME", "CURRENT_DATE", "JOURNEY_TABLE", "SEGMENT_FARE_RULES", "RESPONSE_LAYOUT_GUIDELINES"),
                   ("JOURNEY_TABLE", "SEGMENT_FARE_RULES", "RESPONSE_LAYOUT_GUIDELINES")),
        PromptSpec("chat_layout_desktop", "fare_rules/chat_layout_desktop.md", "chat", "Chat · desktop layout",
                   "Formatting rules for chat answers on desktop."),
        PromptSpec("chat_layout_mobile", "fare_rules/chat_layout_mobile.md", "chat", "Chat · mobile layout",
                   "Formatting rules for chat answers on small screens."),
    )
}


def validate_prompt(spec: PromptSpec, content: str) -> list[str]:
    """Return a list of problems (empty = valid)."""
    problems: list[str] = []
    if not content.strip():
        problems.append("Prompt must not be empty.")
    found = set(_PLACEHOLDER_RE.findall(content))
    for req in spec.required:
        if req not in found:
            problems.append(f"Missing required placeholder [[{req}]].")
    for name in sorted(found - set(spec.allowed)):
        problems.append(f"Unknown placeholder [[{name}]] (allowed: {', '.join(spec.allowed) or 'none'}).")
    if len(content) > 60000:
        problems.append("Prompt is too long (max 60,000 characters).")
    return problems


class PromptService:
    def __init__(self, prompts_dir: Path):
        self._dir = prompts_dir
        self._cache: dict[str, tuple[float, str]] = {}
        self._overrides: dict[str, str] = {}  # keyed by relative file path

    # ---------------------------------------------------------------- read
    def load_default(self, relative_path: str) -> str:
        path = self._dir / relative_path
        if not path.exists():
            raise FileNotFoundError(f"Prompt file not found: {path}")
        mtime = path.stat().st_mtime
        cached = self._cache.get(relative_path)
        if cached and cached[0] == mtime:
            return cached[1]
        text = path.read_text(encoding="utf-8").strip()
        self._cache[relative_path] = (mtime, text)
        return text

    def load(self, relative_path: str, temp_overrides: dict[str, str] | None = None) -> str:
        if temp_overrides and relative_path in temp_overrides:
            return temp_overrides[relative_path].strip()
        override = self._overrides.get(relative_path)
        if override is not None:
            return override
        return self.load_default(relative_path)

    # ------------------------------------------------------------ overrides
    def set_overrides(self, by_prompt_id: dict[str, str]) -> None:
        self._overrides = {
            PROMPTS[pid].file: content.strip() for pid, content in by_prompt_id.items() if pid in PROMPTS and content.strip()
        }

    def is_overridden(self, prompt_id: str) -> bool:
        return PROMPTS[prompt_id].file in self._overrides

    @staticmethod
    def render(template: str, **values: str) -> str:
        out = template
        for key, value in values.items():
            out = out.replace(f"[[{key}]]", value)
        return out
