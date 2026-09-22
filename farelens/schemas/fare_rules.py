"""Public API schemas for the fare-rules summary and chat endpoints."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

LangCode = Field(
    default="en",
    min_length=2,
    max_length=5,
    description="Language code for the response (e.g. en, ar, fr, ur, de).",
    examples=["en"],
)


class SummaryRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    fare_rules_text: str = Field(
        ...,
        min_length=1,
        description="Raw airline fare-rules text (HTML is tolerated and stripped).",
        validation_alias=AliasChoices("fare_rules_text", "fareRulesText"),
    )
    lang: str = LangCode
    is_mobile_view: bool = Field(
        default=False,
        validation_alias=AliasChoices("is_mobile_view", "isMobileView"),
        description="Format for small screens (compact sections instead of tables).",
    )
    bypass_cache: bool = Field(
        default=False,
        validation_alias=AliasChoices("bypass_cache", "bypassCache"),
        description="Force regeneration even if a cached summary exists.",
    )

    @field_validator("lang")
    @classmethod
    def _norm_lang(cls, value: str) -> str:
        return value.strip().lower()


class SummaryResponse(BaseModel):
    summary_markdown: str = Field(..., description="Traveller-friendly summary in Markdown.")
    cache: Literal["redis-hit", "postgres-hit", "generated"]
    provider: str | None = None
    model: str | None = None
    lang: str
    is_mobile_view: bool
    latency_ms: int


class JourneySegment(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    source_airport: str = Field(..., min_length=3, max_length=3, description="IATA code, e.g. DEL", validation_alias=AliasChoices("source_airport", "sourceAirport", "origin"))
    destination_airport: str = Field(..., min_length=3, max_length=3, description="IATA code, e.g. DXB", validation_alias=AliasChoices("destination_airport", "destinationAirport", "destination"))
    departure_date: date = Field(..., description="YYYY-MM-DD", validation_alias=AliasChoices("departure_date", "departureDate"))
    fare_rules_text: str = Field(..., min_length=1, validation_alias=AliasChoices("fare_rules_text", "fareRulesText"))

    @field_validator("source_airport", "destination_airport")
    @classmethod
    def _iata(cls, value: str) -> str:
        code = value.strip().upper()
        if len(code) != 3 or not code.isalpha():
            raise ValueError("airport code must be exactly 3 letters")
        return code

    @field_validator("fare_rules_text")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("fare_rules_text must not be blank")
        return value.strip()

    def to_segment(self) -> dict[str, str]:
        return {
            "source_airport": self.source_airport,
            "destination_airport": self.destination_airport,
            "departure_date": self.departure_date.isoformat(),
            "fare_rules_text": self.fare_rules_text,
        }


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1)


class ChatRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    convo_id: str | None = Field(default=None, min_length=1, max_length=128, validation_alias=AliasChoices("convo_id", "convoId", "conversation_id"), description="Reuse a conversation's stored segments. Omit to start a new one.")
    segments: list[JourneySegment] | None = Field(default=None, min_length=1, max_length=12, description="Journey segments with their fare rules. Required for a new conversation; optional to refresh an existing one.")
    user_message: str = Field(..., min_length=1, max_length=4000, validation_alias=AliasChoices("user_message", "userMessage", "message"))
    history: list[ChatMessage] = Field(default_factory=list, max_length=100, description="Prior turns of this conversation (client-managed).")
    lang: str = LangCode
    is_mobile_view: bool = Field(default=False, validation_alias=AliasChoices("is_mobile_view", "isMobileView"))

    @field_validator("lang")
    @classmethod
    def _norm_lang(cls, value: str) -> str:
        return value.strip().lower()

    @model_validator(mode="after")
    def _require_context(self) -> ChatRequest:
        if not self.convo_id and not self.segments:
            raise ValueError("segments are required when convo_id is not provided")
        return self


class ChatResponse(BaseModel):
    convo_id: str
    answer_markdown: str
    provider: str
    model: str
    usage: dict[str, int | None]
    latency_ms: int


class ConversationDeleteResponse(BaseModel):
    convo_id: str
    removed: bool
