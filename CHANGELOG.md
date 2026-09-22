# Changelog

All notable changes to FareLens are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/) and the project uses [Semantic Versioning](https://semver.org/).

## [1.0.0] - 2026-09-22

Initial open-source release.

### Added
- Fare-rules **summary** endpoint with desktop (table) and mobile (compact sections) layouts, multilingual output and Redis → Postgres → LLM caching by content digest.
- Fare-rules **chat** endpoint (Server-Sent Events and single-response variants) with segment-aware and date-aware reasoning over multi-segment itineraries.
- Six LLM providers behind one interface: OpenAI, Azure AI (Azure OpenAI), Anthropic Claude, Google Gemini, Groq, AWS Bedrock — configurable and testable from the admin UI, credentials encrypted at rest.
- Admin UI: overview dashboard, prompt editor (validated placeholders, live preview, reset to default), usage analytics (tokens, latency, cache hit rate, per feature/model/key), API key management, datastore management (bundled or your own Postgres/Redis with live switching and config carry-over), playground, account settings.
- Docker Compose stack (app + Postgres 16 + Redis 7) that boots with zero configuration; default admin user seeded on first start.
- Production hardening: API-key auth with hashed keys, session cookies with CSRF guard, sliding-window rate limiting, uniform error envelope, request ids, security headers, health/readiness probes.
