"""Uniform JSON error envelope and exception handlers.

Every error response has the shape:

    {"request_id": "...", "error": {"code": "...", "message": "...", "http_status": 4xx}}
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class AppError(Exception):
    """Domain error with an explicit HTTP status and machine-readable code."""

    def __init__(self, message: str, *, status_code: int = 400, code: str | None = None, hint: str | None = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code or _default_code(status_code)
        self.hint = hint


class LLMNotConfiguredError(AppError):
    def __init__(self) -> None:
        super().__init__(
            "No LLM provider is configured.",
            status_code=503,
            code="LLM_NOT_CONFIGURED",
            hint="Open the admin UI and configure a provider under 'LLM provider'.",
        )


class LLMUpstreamError(AppError):
    def __init__(self, message: str, *, status_code: int = 502, code: str = "LLM_UPSTREAM_ERROR"):
        super().__init__(message, status_code=status_code, code=code)


def _default_code(status_code: int) -> str:
    return {
        400: "BAD_REQUEST",
        401: "UNAUTHORIZED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        405: "METHOD_NOT_ALLOWED",
        409: "CONFLICT",
        413: "PAYLOAD_TOO_LARGE",
        422: "VALIDATION_ERROR",
        429: "TOO_MANY_REQUESTS",
        500: "INTERNAL_ERROR",
        502: "BAD_GATEWAY",
        503: "SERVICE_UNAVAILABLE",
        504: "GATEWAY_TIMEOUT",
    }.get(status_code, f"HTTP_{status_code}")


def get_request_id(request: Request) -> str:
    rid = getattr(request.state, "request_id", None)
    if not rid:
        rid = str(uuid.uuid4())
        request.state.request_id = rid
    return str(rid)


def error_response(
    request: Request,
    *,
    status_code: int,
    message: str,
    code: str | None = None,
    hint: str | None = None,
    extra: dict[str, Any] | None = None,
) -> JSONResponse:
    request_id = get_request_id(request)
    err: dict[str, Any] = {
        "code": code or _default_code(status_code),
        "message": message,
        "http_status": status_code,
    }
    if hint:
        err["hint"] = hint
    if extra:
        err.update(extra)
    return JSONResponse(
        status_code=status_code,
        content={"request_id": request_id, "error": err},
        headers={"X-Request-Id": request_id},
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        return error_response(
            request, status_code=exc.status_code, message=exc.message, code=exc.code, hint=exc.hint
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail
        if isinstance(detail, dict):
            message = str(detail.get("message") or detail)
        else:
            message = str(detail or "Request failed")
        if exc.status_code == 404 and message.lower() in {"not found", ""}:
            message = f"No route for {request.method} {request.url.path}."
        return error_response(request, status_code=exc.status_code, message=message, code=None)

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = exc.errors()
        fields = [
            {
                "loc": [str(x) for x in e.get("loc", ())],
                "msg": str(e.get("msg", "")),
                "type": str(e.get("type", "")),
            }
            for e in errors[:25]
        ]
        first = errors[0] if errors else {}
        loc = ".".join(str(x) for x in first.get("loc", ()) if x != "body")
        msg = str(first.get("msg", "Validation failed"))
        summary = f"{loc}: {msg}" if loc else msg
        return error_response(
            request,
            status_code=422,
            message=f"Validation failed: {summary}",
            code="VALIDATION_ERROR",
            hint="See /docs for the expected request schema.",
            extra={"fields": fields},
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error request_id=%s", get_request_id(request))
        return error_response(
            request,
            status_code=500,
            message="Internal server error.",
            code="INTERNAL_ERROR",
            hint="Retry once. If it persists, share the request_id with the operator.",
        )
