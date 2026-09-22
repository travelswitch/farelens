# Deploying FareLens

FareLens is a single container plus Postgres and Redis. The bundled `docker-compose.yml` is production-ready for a single host; this page covers what to change before exposing it, and how to run it on your own infrastructure.

## 1. Before going live (checklist)

- [ ] **Set `APP_SECRET_KEY`** in `.env` to a long random value and never change it afterwards (it encrypts stored LLM credentials and datastore DSNs). Generate one:
  `python -c "import secrets; print(secrets.token_urlsafe(48))"`
- [ ] **Change the admin password** (Account → Change password). The UI shows a banner until you do.
- [ ] **Change the bundled Postgres password** (`POSTGRES_PASSWORD` and the matching `DATABASE_URL`) or use your own Postgres.
- [ ] **Serve over HTTPS** behind a reverse proxy (below). The admin session cookie is marked `Secure` automatically when the request scheme is HTTPS.
- [ ] **Restrict `CORS_ALLOW_ORIGINS`** to your front-end origins if browsers call the API directly.
- [ ] **Keep Postgres/Redis private.** The compose file does not publish their ports; leave it that way.
- [ ] Decide on **rate limits** (`RATE_LIMIT_REQUESTS` / `RATE_LIMIT_WINDOW_SECONDS`, per API key).

## 2. Reverse proxy (TLS)

FareLens listens on plain HTTP on port 8000 and honours `X-Forwarded-*` headers (`--proxy-headers` is on). Put any TLS terminator in front of it.

**Caddy** (automatic certificates):

```
farelens.example.com {
    reverse_proxy app:8000
}
```

**nginx**:

```nginx
server {
    listen 443 ssl http2;
    server_name farelens.example.com;
    # ssl_certificate / ssl_certificate_key ...

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Required for Server-Sent Events (chat streaming)
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 300s;
    }
}
```

> SSE needs response buffering **off** on every proxy in the path (nginx, Cloudflare, load balancers). FareLens already sends `X-Accel-Buffering: no` and `Cache-Control: no-cache`.

## 3. Running with your own Postgres / Redis

Either:

- set `DATABASE_URL` / `REDIS_URL` in `.env` and remove the `postgres` / `redis` services from the compose file, **or**
- keep the bundled ones for bootstrap and switch from the UI (**Data stores → My own PostgreSQL / Redis**). The choice is persisted, encrypted, in the `/data` volume and survives restarts. If the external database becomes unreachable at boot, the app falls back to the bundled one and shows why on the Overview.

The Postgres user needs `CREATE` on the database (tables are created automatically by the built-in migration runner). Redis needs nothing special; `rediss://` (TLS) and ACL users are supported.

## 4. Scaling out

- Run several `app` replicas behind the proxy. Enable Redis so chat context and the summary cache are shared; provider-config changes propagate to all replicas within 30 s.
- Without Redis, keep a single replica (chat context lives in process memory).
- The rate limiter is per process; with N replicas the effective limit is ≈ N × configured.

## 5. Persistence & backups

| Data | Where | Back up? |
|---|---|---|
| Users, LLM config (encrypted), API keys, summary cache, usage | Postgres (`farelens_postgres` volume) | Yes (`pg_dump`) |
| App secret (if not set via env), datastore overrides | `/data` (`farelens_data` volume) | Yes — losing `secret.key` makes stored credentials unreadable |
| Redis cache / chat context | `farelens_redis` volume | Optional (regenerated) |

Usage rows are small but unbounded; prune `usage_events` on your own schedule if you have heavy traffic:

```sql
DELETE FROM usage_events WHERE occurred_at < NOW() - INTERVAL '180 days';
```

## 6. Upgrading

```bash
git pull
docker compose build app
docker compose up -d
```

Schema migrations run automatically at startup (forward-only, idempotent, serialised with an advisory lock so multiple replicas can start together). Cached summaries survive upgrades; if a release changes prompts materially it will say so in the changelog — bump `SUMMARY_CACHE_NAMESPACE` or clear the cache from the UI.

## 7. Health & observability

- `GET /health` — liveness (always 200 while the process runs; reports Postgres/Redis connectivity).
- `GET /health/ready` — readiness (503 until Postgres is reachable; reports whether an LLM provider is configured).
- Every response carries `X-Request-Id`; pass your own to correlate with upstream logs.
- Logs go to stdout in `time | level | logger | message` form; set `LOG_LEVEL=DEBUG` for verbose output.

## 8. Running without Docker

```bash
pip install -r requirements.txt
export DATABASE_URL=postgresql://user:pass@host:5432/farelens
export REDIS_URL=redis://host:6379/0          # optional
export APP_SECRET_KEY=...
export APP_DATA_DIR=/var/lib/farelens
uvicorn farelens.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips '*'
```

Run it under systemd or your process manager of choice; `prompts/`, `ui/` and `migrations/` must sit next to the `farelens` package (they are resolved relative to the project root).
