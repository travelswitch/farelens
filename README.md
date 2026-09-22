<h1 align="center">FareLens</h1>

<p align="center"><strong>Airline fare rules, explained.</strong><br/>
Self-hosted API + admin UI that turns raw airline fare-rules text into traveller-friendly summaries and answers questions about them — using the LLM provider <em>you</em> choose.</p>

<p align="center">
  <a href="LICENSE"><img alt="MIT" src="https://img.shields.io/badge/license-MIT-6928d9.svg"></a>
  <img alt="Python" src="https://img.shields.io/badge/python-3.11%20%7C%203.12-3776ab.svg">
  <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-async-009688.svg">
  <img alt="Docker" src="https://img.shields.io/badge/docker%20compose-one%20command-2496ed.svg">
  <img alt="Providers" src="https://img.shields.io/badge/LLM%20providers-6-6d4dff.svg">
</p>

<p align="center">
  Built by <a href="https://travelswitch.com">TravelSwitch</a> — where it powers fare-rule explanations in production booking flows — and shared with the travel-tech community so any airline, OTA or travel-tech team can run it on their own stack.
</p>

---

## Why

Airline fare rules are long, uppercase, telegraphic and full of ATPCO jargon (`NON-REF`, `RFND`, `CHG PEN`, `NOSHOW`…). Travellers just want to know *"what does it cost me to cancel or change?"*. FareLens answers that in plain language, in the traveller's language, on desktop or mobile — and caches the result so repeated fare rules cost zero tokens.

### The problem this solves (GDS / NDC integrators)

If you consume airline content through a GDS — we run on **Amadeus** — you will have hit this: in **NDC flows the fare-family / price-class descriptions and fare rules are passed through exactly as the airline supplies them**. There is no GDS-generated short text (unlike EDIFACT), no parameter to control length, and no standardisation across carriers. The result is long, inconsistent blobs — often with detailed conditions or URLs embedded — that are unusable in a B2C booking flow. When we raised it, the answer was that this is by design and compliant with the NDC standard, i.e. **the integrator has to solve it**.

FareLens is that solution: feed it whatever the airline returned, get back a consistent, short, structured summary (and a Q&A channel for the follow-up questions travellers actually ask). It is content-source agnostic — Amadeus, Sabre, Travelport, direct-connect NDC or an airline API all produce the same clean output.

## What you get

| | |
|---|---|
| **Summary API** | `POST /api/v1/fare-rules/summary` → Markdown "mini rules" (table on desktop, compact sections on mobile). |
| **Chat API** | `POST /api/v1/fare-rules/chat/stream` → Server-Sent Events Q&A over one or many itinerary segments, segment- and date-aware. |
| **Bring your own model** | OpenAI · Azure AI (Azure OpenAI) · Anthropic Claude · Google Gemini · Groq · AWS Bedrock — pick one in the UI, test it, switch any time. |
| **Multilingual** | Arabic, Urdu, French, German, Hindi, Chinese… response language is a request parameter. |
| **Caching** | Content-digest cache: Redis → Postgres → model. |
| **Admin UI** | Provider setup, API keys, usage dashboard, datastore management, playground, account. |
| **One command** | `docker compose up -d` brings up the API, Postgres and Redis. |

---

## Quick start

```bash
git clone https://github.com/travelswitch/farelens.git
cd farelens
docker compose up -d
```

Open **http://localhost:8000** and sign in with `admin` / `admin` (you'll be asked to change it).

<p align="center"><img src="docs/screenshots/02-overview.png" alt="Overview" width="900" /></p>

### 1 · Choose an LLM provider

Pick a provider, paste your key, **Test connection**, **Save & activate**. Credentials are encrypted at rest.

<p align="center"><img src="docs/screenshots/03-llm-provider.png" alt="LLM provider" width="900" /></p>

### 2 · Create an API key

<p align="center"><img src="docs/screenshots/04-api-keys.png" alt="API keys" width="900" /></p>

### 3 · Call the API

```bash
curl -X POST http://localhost:8000/api/v1/fare-rules/summary \
  -H "X-API-Key: fl_..." \
  -H "Content-Type: application/json" \
  -d '{"fare_rules_text": "CANCELLATIONS BEFORE DEPARTURE CHARGE SAR 150 FOR CANCEL/REFUND ...", "lang": "en"}'
```

Interactive docs: **http://localhost:8000/docs**

### 4 · Try it in the Playground

Both endpoints, no API key needed (uses your admin session).

<p align="center"><img src="docs/screenshots/05-playground-summary.png" alt="Playground - summary" width="900" /></p>
<p align="center"><img src="docs/screenshots/06-playground-chat.png" alt="Playground - chat" width="900" /></p>

### 5 · Watch usage

Requests, tokens, latency, cache hit rate — per day, per feature, per model, per API key.

<p align="center"><img src="docs/screenshots/07-usage.png" alt="Usage" width="900" /></p>

---

## API

All public endpoints need `X-API-Key: fl_…` (or `Authorization: Bearer fl_…`). Errors share one envelope:

```json
{"request_id": "…", "error": {"code": "LLM_NOT_CONFIGURED", "message": "…", "http_status": 503}}
```

### `POST /api/v1/fare-rules/summary`

| Field | Type | Notes |
|---|---|---|
| `fare_rules_text` | string | Raw fare rules; HTML is stripped. Max `MAX_FARE_RULES_CHARS` (60 000). |
| `lang` | string | Response language code, default `en`. |
| `is_mobile_view` | bool | Compact sections instead of tables. |
| `bypass_cache` | bool | Force regeneration. |

```json
{
  "summary_markdown": "| Section | Condition / Timing | Penalty | Applicability | Notes |\n…",
  "cache": "generated",
  "provider": "groq",
  "model": "openai/gpt-oss-120b",
  "lang": "en",
  "is_mobile_view": false,
  "latency_ms": 1840
}
```

`X-Cache` header: `redis-hit` | `postgres-hit` | `generated`.

### `POST /api/v1/fare-rules/chat/stream`

Start a conversation with the itinerary's segments; follow-ups only need `convo_id`, the new `user_message` and the client-side `history`.

```json
{
  "segments": [
    {"source_airport": "DEL", "destination_airport": "DXB", "departure_date": "2026-11-06", "fare_rules_text": "…"},
    {"source_airport": "DXB", "destination_airport": "DEL", "departure_date": "2026-11-12", "fare_rules_text": "…"}
  ],
  "user_message": "Can I change the return flight?",
  "history": [],
  "lang": "en",
  "is_mobile_view": false
}
```

Response (`text/event-stream`):

```
event: start   data: {"convo_id": "…", "segments": [...]}
event: token   data: {"content": "Yes — "}
event: token   data: {"content": "changes on the return …"}
event: end     data: {"convo_id": "…", "usage": {"prompt_tokens": 1450, "completion_tokens": 92, "total_tokens": 1542}, "latency_ms": 2100}
event: error   data: {"error": {"code": "…", "message": "…"}}
```

Follow-up:

```json
{"convo_id": "…", "user_message": "And if I no-show?", "history": [{"role": "user", "content": "…"}, {"role": "assistant", "content": "…"}]}
```

Segments are kept for `CONVERSATION_TTL_SECONDS` (1 h). An expired `convo_id` returns `422 CONVERSATION_EXPIRED`; resend `segments`.

Also: `POST /api/v1/fare-rules/chat` (single JSON response) · `DELETE /api/v1/fare-rules/chat/{convo_id}` · `GET /health` · `GET /health/ready`.

---

## Configuration

Works with zero configuration. Copy `.env.example` to `.env` to change anything.

| Variable | Default | Purpose |
|---|---|---|
| `APP_SECRET_KEY` | *(generated)* | Signs sessions, encrypts stored credentials. **Set it in production.** If empty, one is generated into `APP_DATA_DIR/secret.key`. |
| `APP_DATA_DIR` | `/data` (Docker) | Local state: generated secret, datastore overrides. |
| `DATABASE_URL` / `REDIS_URL` | bundled containers | Default datastores. |
| `ADMIN_DEFAULT_USERNAME` / `ADMIN_DEFAULT_PASSWORD` | `admin` / `admin` | Seeded once, on first start with an empty database. |
| `MAX_FARE_RULES_CHARS` | `60000` | Max fare-rules length per request / segment. |
| `SUMMARY_CACHE_NAMESPACE` | `v1` | Bump to invalidate all cached summaries (e.g. after editing prompts). |
| `SUMMARY_CACHE_TTL_SECONDS` | `2592000` | Redis TTL for summaries; Postgres keeps them indefinitely. |
| `CONVERSATION_TTL_SECONDS` | `3600` | Retention of chat segments per `convo_id`. |
| `RATE_LIMIT_REQUESTS` / `RATE_LIMIT_WINDOW_SECONDS` | `120` / `60` | Sliding window per API key. |
| `CORS_ALLOW_ORIGINS` | `*` | Comma-separated origins for browser clients. |
| `LLM_TIMEOUT_SECONDS` | `90` | Upstream request timeout. |

### LLM providers

| Provider | Credentials | Notes |
|---|---|---|
| OpenAI | API key, optional base URL | Base URL makes it work with any OpenAI-compatible gateway. |
| Azure AI (Azure OpenAI) | Endpoint, API key, API version | Model = your **deployment name**. |
| Anthropic (Claude) | API key | Sampling params omitted (current Claude models reject them). |
| Google (Gemini) | API key | Google AI Studio keys. |
| Groq | API key | Fast open-weight models. |
| AWS Bedrock | Region, optional access key/secret | Empty keys → container IAM role. Converse API, so any Converse-capable model works. |

Credentials are Fernet-encrypted with `APP_SECRET_KEY`. The public API never sees them; the admin UI shows them masked and can reveal them to a logged-in admin.

### Your own Postgres / Redis

**Data stores** lets you point FareLens at an existing Postgres and/or Redis with plain host/port/user/password fields (or a pasted DSN). It tests the connection, creates the schema, copies users, LLM config and API keys if the target is empty, and switches live — no restart. Redis is optional: without it, summaries are cached in Postgres only and chat context lives in memory (single instance).

<p align="center"><img src="docs/screenshots/08-data-stores.png" alt="Data stores" width="900" /></p>

### Prompts

<p align="center"><img src="docs/screenshots/09-prompts.png" alt="Prompt editor" width="900" /></p>

Edit them from the admin UI (**Prompts** page: placeholder validation, live preview against sample fare rules, reset to default) or on disk in `prompts/fare_rules/*.md`. UI edits are stored in Postgres and take precedence; the files are the defaults. `summary_system.md` and `chat_system.md` carry the domain rules (ATPCO vocabulary, before/after-departure and no-show logic, strict no-hallucination policy); `*_layout_*.md` control desktop vs mobile formatting. After changing a prompt, bump `SUMMARY_CACHE_NAMESPACE` or clear the cache from the UI.

---

## How it works

```mermaid
flowchart LR
    C[Your app] -- X-API-Key --> A[FareLens API]
    A --> N[Normalise text\nSHA-256 digest]
    N --> R{Redis?}
    R -- hit --> C
    R -- miss --> P{Postgres?}
    P -- hit --> R
    P -- miss --> L[LLM provider\nOpenAI · Azure · Claude · Gemini · Groq · Bedrock]
    L --> P
    L --> U[(usage_events)]
    Admin[Admin UI] --> A
```

```
farelens/
  api/        FastAPI routes (public v1 + admin), auth, middleware
  services/   summary (cache → LLM), chat (SSE), usage, API keys, users, LLM config
  llm/        provider registry + adapters (openai/azure/groq, anthropic, google, bedrock)
  db/         asyncpg / redis managers, live datastore switching, SQL migrations
  core/       settings, security (bcrypt · JWT · Fernet), error envelope
prompts/      editable prompt templates
migrations/   forward-only SQL applied at startup
ui/           admin SPA (vanilla JS, no build step)
```

---

## Deployment

See **[docs/deployment.md](docs/deployment.md)** for the production checklist (secrets, TLS/reverse proxy, scaling, backups, upgrades).

Short version:

```bash
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"   # → APP_SECRET_KEY
docker compose up -d
```

Then change the admin password, restrict `CORS_ALLOW_ORIGINS`, and put the service behind HTTPS.

## Development

```bash
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
docker compose up -d postgres redis                # publish their ports in docker-compose.yml, or use your own
export DATABASE_URL=postgresql://farelens:farelens@localhost:5432/farelens
export REDIS_URL=redis://localhost:6379/0
uvicorn farelens.main:app --reload
pytest -q && ruff check farelens tests
```

## Contributing & security

Contributions welcome — see [CONTRIBUTING.md](CONTRIBUTING.md). To report a vulnerability, see [SECURITY.md](SECURITY.md).

## Credits

Provider icons from [lobe-icons](https://github.com/lobehub/lobe-icons) (MIT).

## License

MIT © TravelSwitch — see [LICENSE](LICENSE).
