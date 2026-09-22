# Contributing to FareLens

Thanks for your interest! Issues and pull requests are welcome.

## Development setup

```bash
git clone https://github.com/travelswitch/farelens.git && cd farelens
python -m venv .venv && . .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
docker compose up -d postgres redis
```

Publish the datastore ports (uncomment `ports:` in `docker-compose.yml`) or set `DATABASE_URL` / `REDIS_URL` to reachable instances, then:

```bash
uvicorn farelens.main:app --reload
```

## Before opening a PR

```bash
ruff check farelens tests
pytest -q
```

- Keep changes focused; one topic per PR.
- Add or update tests for behaviour changes.
- Do not commit secrets, `.env` files or anything under `data/`.
- Prompt changes: describe the before/after behaviour in the PR (a short example fare-rules text and both outputs is ideal).

## Adding an LLM provider

1. Add a `ProviderSpec` in `farelens/llm/registry.py` (fields, suggested models).
2. Implement an adapter in `farelens/llm/providers/` that subclasses `LLMProvider` (`complete` + `stream`, reporting `LLMUsage`).
3. Wire it in `farelens/llm/factory.py`.
4. The admin UI form is generated from the registry - no UI changes needed.

## Code of conduct

Be kind and constructive. Harassment or discrimination of any kind is not tolerated.
