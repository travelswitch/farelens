# syntax=docker/dockerfile:1.7
# ---------------------------------------------------------------------------
# FareLens - production image
# Multi-stage: build wheels in a full image, run in a slim one as non-root.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build
COPY requirements.txt .
RUN pip wheel --wheel-dir /wheels -r requirements.txt


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_DATA_DIR=/data

# curl is used by the container healthcheck only.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --system farelens \
 && useradd --system --gid farelens --home-dir /app --shell /usr/sbin/nologin farelens \
 && mkdir -p /app /data \
 && chown -R farelens:farelens /app /data

WORKDIR /app

COPY --from=builder /wheels /wheels
RUN pip install --no-index --find-links=/wheels /wheels/*.whl \
 && rm -rf /wheels

COPY --chown=farelens:farelens farelens ./farelens
COPY --chown=farelens:farelens prompts ./prompts
COPY --chown=farelens:farelens ui ./ui
COPY --chown=farelens:farelens migrations ./migrations
COPY --chown=farelens:farelens README.md ./

USER farelens

EXPOSE 8000
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["uvicorn", "farelens.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
