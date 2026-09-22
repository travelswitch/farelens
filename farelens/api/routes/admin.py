"""Admin API consumed by the bundled UI (session-cookie auth)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from farelens import __version__
from farelens.api.deps import CSRF_HEADER, SESSION_COOKIE, Services, get_services, require_admin
from farelens.core.errors import AppError
from farelens.core.security import create_session_token
from farelens.db.datastores import DataStoreConfig
from farelens.llm.registry import provider_catalog
from farelens.schemas.admin import (
    ApiKeyCreateRequest,
    ChangePasswordRequest,
    DataStoreRequest,
    DataStoreTestRequest,
    LLMConfigRequest,
    LoginRequest,
    PromptPreviewRequest,
    PromptSaveRequest,
    RenameUserRequest,
)
from farelens.services.users import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"], include_in_schema=False)


def _set_session_cookie(response: Response, token: str, *, hours: int, secure: bool) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=hours * 3600,
        httponly=True,
        samesite="strict",
        secure=secure,
        path="/",
    )


def _user_dict(user: User) -> dict[str, Any]:
    return {"id": user.id, "username": user.username, "must_change_password": user.must_change_password}


# ------------------------------------------------------------------- auth
@router.post("/auth/login")
async def login(payload: LoginRequest, request: Request, response: Response, services: Services = Depends(get_services)) -> dict:
    if request.headers.get(CSRF_HEADER, "").lower() != "xmlhttprequest":
        raise AppError("Missing X-Requested-With header.", status_code=403, code="CSRF_REJECTED")
    user = await services.users.authenticate(payload.username, payload.password)
    if user is None:
        raise AppError("Invalid username or password.", status_code=401, code="INVALID_CREDENTIALS")
    token = create_session_token(services.app_secret, user_id=user.id, username=user.username, hours=services.settings.admin_session_hours)
    _set_session_cookie(response, token, hours=services.settings.admin_session_hours, secure=request.url.scheme == "https")
    logger.info("admin login user=%s", user.username)
    return {"user": _user_dict(user)}


@router.post("/auth/logout")
async def logout(response: Response, _: User = Depends(require_admin)) -> dict:
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/auth/me")
async def me(user: User = Depends(require_admin)) -> dict:
    return {"user": _user_dict(user)}


@router.post("/auth/change-password")
async def change_password(payload: ChangePasswordRequest, user: User = Depends(require_admin), services: Services = Depends(get_services)) -> dict:
    if payload.current_password == payload.new_password:
        raise AppError("New password must differ from the current one.", status_code=400)
    ok = await services.users.change_password(user.id, payload.current_password, payload.new_password)
    if not ok:
        raise AppError("Current password is incorrect.", status_code=400, code="INVALID_CREDENTIALS")
    return {"ok": True}


@router.post("/auth/rename")
async def rename_user(payload: RenameUserRequest, user: User = Depends(require_admin), services: Services = Depends(get_services)) -> dict:
    if not await services.users.rename(user.id, payload.username):
        raise AppError("Username is already taken.", status_code=409)
    return {"ok": True, "username": payload.username}


# ---------------------------------------------------------------- overview
@router.get("/overview")
async def overview(services: Services = Depends(get_services), _: User = Depends(require_admin)) -> dict:
    llm = await services.llm_config.get_public()
    return {
        "version": __version__,
        "app_env": services.settings.app_env,
        "llm": llm,
        "datastores": services.datastores.status(),
        "conversations": services.conversations.stats(),
        "summary_cache": await services.summary.cache_stats(),
        "limits": {
            "max_fare_rules_chars": services.settings.max_fare_rules_chars,
            "max_history_messages": services.settings.max_history_messages,
            "rate_limit": f"{services.settings.rate_limit_requests}/{services.settings.rate_limit_window_seconds}s",
            "summary_cache_ttl_seconds": services.settings.summary_cache_ttl_seconds,
            "conversation_ttl_seconds": services.settings.conversation_ttl_seconds,
        },
    }


# -------------------------------------------------------------- llm config
@router.get("/llm/providers")
async def llm_providers(_: User = Depends(require_admin)) -> dict:
    return {"providers": provider_catalog()}


@router.get("/llm/config")
async def llm_config_get(reveal: bool = False, services: Services = Depends(get_services), _: User = Depends(require_admin)) -> dict:
    """`reveal=true` returns stored secrets in plaintext (admin session only)."""
    return await services.llm_config.get_public(include_secrets=reveal)


@router.put("/llm/config")
async def llm_config_put(payload: LLMConfigRequest, user: User = Depends(require_admin), services: Services = Depends(get_services)) -> dict:
    try:
        await services.llm_config.save(
            provider=payload.provider,
            model=payload.model,
            settings=payload.settings,
            secrets=payload.secrets,
            options=payload.options,
            updated_by=user.username,
        )
    except ValueError as exc:
        raise AppError(str(exc), status_code=422, code="INVALID_LLM_CONFIG") from exc
    return await services.llm_config.get_public()


@router.post("/llm/config/test")
async def llm_config_test(payload: LLMConfigRequest, _: User = Depends(require_admin), services: Services = Depends(get_services)) -> dict:
    try:
        return await services.llm_config.test_connection(
            provider=payload.provider, model=payload.model, settings=payload.settings, secrets=payload.secrets, options=payload.options
        )
    except ValueError as exc:
        return {"ok": False, "message": str(exc), "code": "INVALID_LLM_CONFIG"}


@router.delete("/llm/config")
async def llm_config_delete(_: User = Depends(require_admin), services: Services = Depends(get_services)) -> dict:
    await services.llm_config.delete()
    return {"configured": False}


# ---------------------------------------------------------------- api keys
@router.get("/api-keys")
async def api_keys_list(services: Services = Depends(get_services), _: User = Depends(require_admin)) -> dict:
    return {"keys": await services.api_keys.list()}


@router.post("/api-keys", status_code=201)
async def api_keys_create(payload: ApiKeyCreateRequest, user: User = Depends(require_admin), services: Services = Depends(get_services)) -> dict:
    return await services.api_keys.create(payload.name, created_by=user.username)


@router.delete("/api-keys/{key_id}")
async def api_keys_revoke(key_id: int, _: User = Depends(require_admin), services: Services = Depends(get_services)) -> dict:
    if not await services.api_keys.revoke(key_id):
        raise AppError("API key not found or already revoked.", status_code=404)
    return {"ok": True}


# ------------------------------------------------------------------- usage
@router.get("/usage/summary")
async def usage_summary(days: int = 30, services: Services = Depends(get_services), _: User = Depends(require_admin)) -> dict:
    return await services.usage.summary(days)


@router.get("/usage/daily")
async def usage_daily(days: int = 30, services: Services = Depends(get_services), _: User = Depends(require_admin)) -> dict:
    return {"days": await services.usage.daily(days)}


@router.get("/usage/events")
async def usage_events(limit: int = 50, offset: int = 0, feature: str | None = None, services: Services = Depends(get_services), _: User = Depends(require_admin)) -> dict:
    return {"events": await services.usage.recent(limit=limit, offset=offset, feature=feature or None)}


# -------------------------------------------------------------- datastores
@router.get("/datastores")
async def datastores_get(reveal: bool = False, services: Services = Depends(get_services), _: User = Depends(require_admin)) -> dict:
    return services.datastores.status(reveal=reveal)


@router.post("/datastores/test")
async def datastores_test(payload: DataStoreTestRequest, services: Services = Depends(get_services), _: User = Depends(require_admin)) -> dict:
    ds = services.datastores
    if payload.kind == "postgres":
        dsn = payload.dsn.strip()
        if payload.mode == "bundled":
            dsn = services.settings.database_url.get_secret_value()
        elif not dsn:
            dsn = ds.config.postgres_dsn
        if not dsn:
            return {"ok": False, "message": "Postgres DSN is required"}
        return await ds.test_postgres(dsn)
    url = payload.dsn.strip()
    if payload.mode == "bundled":
        url = services.settings.redis_url.get_secret_value()
    elif not url:
        url = ds.config.redis_url
    if not url:
        return {"ok": False, "message": "Redis URL is required"}
    return await ds.test_redis(url)


@router.put("/datastores")
async def datastores_put(payload: DataStoreRequest, services: Services = Depends(get_services), _: User = Depends(require_admin)) -> dict:
    ds = services.datastores
    new_cfg = DataStoreConfig(
        postgres_mode=payload.postgres_mode,
        postgres_dsn=(payload.postgres_dsn.strip() or ds.config.postgres_dsn) if payload.postgres_mode == "external" else ds.config.postgres_dsn,
        redis_mode=payload.redis_mode,
        redis_url=(payload.redis_url.strip() or ds.config.redis_url) if payload.redis_mode == "external" else ds.config.redis_url,
        redis_enabled=payload.redis_enabled,
    )
    if new_cfg.postgres_mode == "external" and not new_cfg.postgres_dsn:
        raise AppError("Postgres DSN is required for external mode.", status_code=422)
    if new_cfg.redis_mode == "external" and new_cfg.redis_enabled and not new_cfg.redis_url:
        raise AppError("Redis URL is required for external mode.", status_code=422)
    try:
        report = await ds.apply(new_cfg, carry_over=payload.carry_over)
    except Exception as exc:  # noqa: BLE001
        logger.exception("datastore switch failed")
        raise AppError(f"Could not switch datastores: {exc}", status_code=400, code="DATASTORE_SWITCH_FAILED") from exc
    # Provider cache may point at the old DB's config; force a reload.
    services.llm_config._last_check = 0.0  # noqa: SLF001
    return {"report": report, "status": ds.status()}


@router.post("/cache/summary/clear")
async def clear_summary_cache(services: Services = Depends(get_services), _: User = Depends(require_admin)) -> dict:
    return await services.summary.clear_cache()


# ----------------------------------------------------------------- prompts
@router.get("/prompts")
async def prompts_list(services: Services = Depends(get_services), _: User = Depends(require_admin)) -> dict:
    return {"prompts": services.prompt_overrides.list()}


@router.put("/prompts/{prompt_id}")
async def prompts_save(prompt_id: str, payload: PromptSaveRequest, user: User = Depends(require_admin), services: Services = Depends(get_services)) -> dict:
    result = await services.prompt_overrides.save(prompt_id, payload.content, updated_by=user.username)
    cleared = None
    if payload.clear_summary_cache and result["feature"] == "summary":
        cleared = await services.summary.clear_cache()
    return {"prompt": result, "cache_cleared": cleared}


@router.delete("/prompts/{prompt_id}")
async def prompts_reset(prompt_id: str, _: User = Depends(require_admin), services: Services = Depends(get_services)) -> dict:
    return {"prompt": await services.prompt_overrides.reset(prompt_id)}


@router.post("/prompts/{prompt_id}/preview")
async def prompts_preview(prompt_id: str, payload: PromptPreviewRequest, _: User = Depends(require_admin), services: Services = Depends(get_services)) -> dict:
    return await services.prompt_overrides.preview(
        prompt_id, payload.content, summary=services.summary, chat=services.chat, llm_config=services.llm_config,
        timeout_seconds=services.settings.llm_timeout_seconds, lang=payload.lang, is_mobile_view=payload.is_mobile_view,
    )
