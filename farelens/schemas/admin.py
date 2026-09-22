"""Admin API schemas (used by the bundled UI)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=100)
    password: str = Field(..., min_length=1, max_length=256)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=256)
    new_password: str = Field(..., min_length=8, max_length=256)


class RenameUserRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=100, pattern=r"^[A-Za-z0-9._@-]+$")


class LLMConfigRequest(BaseModel):
    provider: str = Field(..., min_length=1)
    model: str = Field(default="", max_length=200)
    settings: dict[str, Any] = Field(default_factory=dict)
    secrets: dict[str, str] = Field(default_factory=dict, description="Leave a secret empty to keep the stored value.")
    options: dict[str, Any] = Field(default_factory=dict)


class ApiKeyCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)


class DataStoreRequest(BaseModel):
    postgres_mode: Literal["bundled", "external"] = "bundled"
    postgres_dsn: str = Field(default="", max_length=1000, description="Leave empty to keep the stored DSN.")
    redis_mode: Literal["bundled", "external"] = "bundled"
    redis_url: str = Field(default="", max_length=1000)
    redis_enabled: bool = True
    carry_over: bool = Field(default=True, description="Copy users, LLM config and API keys to a new empty Postgres.")


class DataStoreTestRequest(BaseModel):
    kind: Literal["postgres", "redis"]
    dsn: str = Field(default="", max_length=1000, description="Leave empty to test the stored/bundled value.")
    mode: Literal["bundled", "external"] = "external"


class PromptSaveRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=60000)
    clear_summary_cache: bool = Field(default=True, description="Clear cached summaries so the new prompt takes effect immediately.")


class PromptPreviewRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=60000)
    lang: str = Field(default="en", min_length=2, max_length=5)
    is_mobile_view: bool = False
