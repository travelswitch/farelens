# Security

## Reporting a vulnerability

Please do **not** open a public issue for security problems. Email the maintainers at security@travelswitch.com with a description and, if possible, reproduction steps. We aim to acknowledge reports within 3 business days.

## Deployment checklist

- Set `APP_SECRET_KEY` to a long random value and keep it stable (it encrypts stored LLM credentials and datastore DSNs; changing it requires re-entering them).
- Change the default admin password on first login (the UI nags until you do).
- Serve behind TLS. The admin session cookie is `HttpOnly`, `SameSite=Strict`, and `Secure` when served over HTTPS.
- Restrict `CORS_ALLOW_ORIGINS` to your front-end origins.
- Keep Postgres/Redis on a private network; the compose file does not publish their ports by default.
- Treat API keys like passwords: they are shown once and stored as SHA-256 hashes.

## What FareLens stores

| Data | Where | Protection |
|---|---|---|
| LLM API keys / cloud credentials | Postgres `llm_config.secrets` | Fernet-encrypted with `APP_SECRET_KEY` |
| External datastore DSNs | `APP_DATA_DIR/datastores.json` | Fernet-encrypted, file mode 600 |
| Admin passwords | Postgres `users` | bcrypt (12 rounds) |
| API keys | Postgres `api_keys` | SHA-256 hash + display prefix |
| Fare-rules summaries | Redis + Postgres cache | Plain text (no PII expected) |
| Chat context (segments) | Redis (TTL) or memory | Plain text, expires |
| Usage events | Postgres | Metadata only (tokens, latency, status); no request bodies |
