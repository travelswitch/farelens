"""FareLens application factory."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from farelens import __version__
from farelens.api.deps import Services
from farelens.api.middleware import (
    RateLimitMiddleware,
    RequestIdMiddleware,
    SecurityHeadersMiddleware,
)
from farelens.api.routes import admin, fare_rules, health
from farelens.core.config import Settings, get_settings
from farelens.core.errors import register_exception_handlers
from farelens.core.logging import configure_logging
from farelens.core.security import SecretBox, load_or_create_secret
from farelens.db.datastores import DataStores
from farelens.services.api_keys import ApiKeyService
from farelens.services.chat import ChatService
from farelens.services.conversations import ConversationStore
from farelens.services.llm_config import LLMConfigService
from farelens.services.prompt_overrides import PromptOverrideService
from farelens.services.prompts import PromptService
from farelens.services.summary import SummaryService
from farelens.services.usage import UsageService
from farelens.services.users import UserService

logger = logging.getLogger(__name__)

API_DESCRIPTION = """
**FareLens** turns raw airline fare rules into traveller-friendly summaries and answers
questions about them, using the LLM provider you configure (OpenAI, Azure OpenAI, Anthropic,
Google Gemini, Groq or AWS Bedrock).

Authenticate public endpoints with an API key created in the admin UI:

```
X-API-Key: fl_...
```
"""


async def build_services(settings: Settings) -> Services:
    settings.data_path.mkdir(parents=True, exist_ok=True)
    app_secret = load_or_create_secret(settings.app_secret_key.get_secret_value(), settings.data_path)
    secret_box = SecretBox(app_secret)

    datastores = DataStores(settings, secret_box)
    await datastores.startup()

    prompts = PromptService(settings.prompts_path)
    prompt_overrides = PromptOverrideService(datastores, prompts)
    await prompt_overrides.refresh()
    llm_config = LLMConfigService(datastores, secret_box, timeout_seconds=settings.llm_timeout_seconds)
    usage = UsageService(datastores)
    summary = SummaryService(settings, datastores, prompts, llm_config, usage)
    conversations = ConversationStore(
        datastores, ttl_seconds=settings.conversation_ttl_seconds, max_items=settings.max_cached_conversations
    )
    chat = ChatService(settings, conversations, prompts, llm_config, usage)
    api_keys = ApiKeyService(datastores)
    users = UserService(datastores)

    await users.ensure_default_admin(settings.admin_default_username, settings.admin_default_password.get_secret_value())

    return Services(
        settings=settings,
        app_secret=app_secret,
        secret_box=secret_box,
        datastores=datastores,
        prompts=prompts,
        prompt_overrides=prompt_overrides,
        llm_config=llm_config,
        usage=usage,
        summary=summary,
        conversations=conversations,
        chat=chat,
        api_keys=api_keys,
        users=users,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info("%s %s starting (env=%s)", settings.app_name, __version__, settings.app_env)
        services = await build_services(settings)
        app.state.services = services
        refresh_task = asyncio.create_task(services.prompt_overrides.run_periodic_refresh())
        try:
            cfg = await services.llm_config.load()
            if cfg is None:
                logger.warning("No LLM provider configured yet - open the admin UI to set one up.")
            else:
                logger.info("LLM provider: %s / %s", cfg.provider, cfg.model)
            yield
        finally:
            refresh_task.cancel()
            await services.datastores.shutdown()
            logger.info("shutdown complete")

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description=API_DESCRIPTION,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )
    register_exception_handlers(app)

    # Middleware order: last added runs first.
    app.add_middleware(RateLimitMiddleware, requests=settings.rate_limit_requests, window_seconds=settings.rate_limit_window_seconds)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=settings.cors_origins != ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-Id", "X-Cache"],
    )
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestIdMiddleware)

    app.include_router(health.router)
    app.include_router(fare_rules.router, prefix="/api/v1")
    app.include_router(admin.router, prefix="/api")

    ui_dir = settings.ui_path
    if ui_dir.exists():
        app.mount("/ui/assets", StaticFiles(directory=str(ui_dir / "assets")), name="ui-assets")

        @app.get("/", include_in_schema=False)
        async def root() -> RedirectResponse:
            return RedirectResponse(url="/ui/")

        @app.get("/ui", include_in_schema=False)
        @app.get("/ui/", include_in_schema=False)
        @app.get("/ui/{path:path}", include_in_schema=False)
        async def ui(path: str = "") -> FileResponse:
            return FileResponse(ui_dir / "index.html", headers={"Cache-Control": "no-cache"})

    else:  # pragma: no cover - UI folder removed on purpose

        @app.get("/", include_in_schema=False)
        async def root_no_ui() -> dict:
            return {"service": settings.app_name, "version": __version__, "docs": "/docs"}

    return app


app = create_app()
