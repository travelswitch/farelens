"""Shared application state and FastAPI dependencies (auth)."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Request

from farelens.core.config import Settings
from farelens.core.errors import AppError, get_request_id
from farelens.core.security import SecretBox, decode_session_token
from farelens.db.datastores import DataStores
from farelens.services.api_keys import ApiKeyService
from farelens.services.chat import ChatService
from farelens.services.conversations import ConversationStore
from farelens.services.llm_config import LLMConfigService
from farelens.services.prompt_overrides import PromptOverrideService
from farelens.services.prompts import PromptService
from farelens.services.summary import SummaryService
from farelens.services.usage import UsageService
from farelens.services.users import User, UserService

SESSION_COOKIE = "farelens_session"
CSRF_HEADER = "x-requested-with"


@dataclass(slots=True)
class Services:
    settings: Settings
    app_secret: str
    secret_box: SecretBox
    datastores: DataStores
    prompts: PromptService
    prompt_overrides: PromptOverrideService
    llm_config: LLMConfigService
    usage: UsageService
    summary: SummaryService
    conversations: ConversationStore
    chat: ChatService
    api_keys: ApiKeyService
    users: UserService


@dataclass(slots=True)
class Principal:
    kind: str  # "api_key" | "admin"
    name: str
    api_key_id: int | None = None
    user_id: int | None = None


def get_services(request: Request) -> Services:
    services: Services | None = getattr(request.app.state, "services", None)
    if services is None:
        raise AppError("Application is still starting up.", status_code=503, code="NOT_READY")
    return services


def request_id(request: Request) -> str:
    return get_request_id(request)


def _extract_api_key(request: Request) -> str | None:
    key = request.headers.get("x-api-key")
    if key:
        return key.strip()
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


async def _session_user(request: Request, services: Services) -> User | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    payload = decode_session_token(services.app_secret, token)
    if not payload:
        return None
    try:
        user_id = int(payload.get("sub", ""))
    except ValueError:
        return None
    return await services.users.get(user_id)


async def require_principal(request: Request, services: Services = Depends(get_services)) -> Principal:
    """Public endpoints accept an API key, or an admin session (for the built-in playground)."""
    raw_key = _extract_api_key(request)
    if raw_key:
        identity = await services.api_keys.verify(raw_key)
        if identity is None:
            raise AppError("Invalid or revoked API key.", status_code=401, code="INVALID_API_KEY")
        principal = Principal(kind="api_key", name=identity.name, api_key_id=identity.id)
        request.state.principal = principal
        return principal

    user = await _session_user(request, services)
    if user is not None:
        principal = Principal(kind="admin", name=user.username, user_id=user.id)
        request.state.principal = principal
        return principal

    raise AppError(
        "Authentication required.",
        status_code=401,
        code="UNAUTHORIZED",
        hint="Send the API key in the `X-API-Key` header (create one in the admin UI).",
    )


async def require_admin(request: Request, services: Services = Depends(get_services)) -> User:
    user = await _session_user(request, services)
    if user is None:
        raise AppError("Admin login required.", status_code=401, code="UNAUTHORIZED")
    # CSRF guard: browsers cannot attach custom headers cross-site without CORS approval.
    if request.method not in {"GET", "HEAD", "OPTIONS"} and request.headers.get(CSRF_HEADER, "").lower() != "xmlhttprequest":
        raise AppError("Missing X-Requested-With header.", status_code=403, code="CSRF_REJECTED")
    request.state.principal = Principal(kind="admin", name=user.username, user_id=user.id)
    return user
