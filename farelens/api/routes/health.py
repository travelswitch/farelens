from __future__ import annotations

from fastapi import APIRouter, Request

from farelens import __version__
from farelens.api.deps import Services

router = APIRouter(tags=["health"])


@router.get("/health", summary="Liveness probe")
async def health(request: Request) -> dict:
    services: Services | None = getattr(request.app.state, "services", None)
    return {
        "status": "ok",
        "version": __version__,
        "postgres": bool(services and services.datastores.pg_connected),
        "redis": bool(services and services.datastores.redis_connected),
    }


@router.get("/health/ready", summary="Readiness probe")
async def ready(request: Request) -> dict:
    services: Services | None = getattr(request.app.state, "services", None)
    if services is None or not services.datastores.pg_connected:
        from farelens.core.errors import AppError

        raise AppError("Not ready: database unavailable", status_code=503, code="NOT_READY")
    cfg = await services.llm_config.load()
    return {"status": "ready", "llm_configured": cfg is not None, "redis": services.datastores.redis_connected}
