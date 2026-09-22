"""Persisted LLM provider configuration + cached provider instance.

Secrets are encrypted with the app secret before they reach Postgres. The
provider adapter is rebuilt lazily whenever the stored config changes (also
across multiple worker processes, via the `updated_at` stamp).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from farelens.core.errors import LLMNotConfiguredError
from farelens.core.security import SecretBox, mask_secret
from farelens.db.datastores import DataStores
from farelens.llm.base import GenerationOptions, LLMMessage, LLMProvider, LLMProviderError
from farelens.llm.factory import build_provider
from farelens.llm.registry import ProviderSpec, get_spec

logger = logging.getLogger(__name__)

_REFRESH_INTERVAL_SECONDS = 30.0

DEFAULT_OPTIONS: dict[str, Any] = {
    "temperature": 0.0,
    "max_tokens": 4096,
}


@dataclass(slots=True)
class StoredLLMConfig:
    provider: str
    model: str
    settings: dict[str, Any]
    secrets: dict[str, str]  # decrypted
    options: dict[str, Any]
    updated_at: datetime | None
    updated_by: str | None

    @property
    def spec(self) -> ProviderSpec:
        return get_spec(self.provider)

    def merged(self) -> dict[str, Any]:
        return {**self.settings, **self.secrets}

    def generation_options(self, timeout_seconds: float) -> GenerationOptions:
        temperature: float | None = None
        if self.spec.supports_temperature:
            raw = self.options.get("temperature", DEFAULT_OPTIONS["temperature"])
            temperature = float(raw) if raw is not None else None
        return GenerationOptions(
            temperature=temperature,
            max_tokens=int(self.options.get("max_tokens") or DEFAULT_OPTIONS["max_tokens"]),
            timeout_seconds=timeout_seconds,
        )

    def to_public_dict(self, *, include_secrets: bool = False) -> dict[str, Any]:
        spec = self.spec
        return {
            "configured": True,
            "provider": spec.id,
            "provider_label": spec.label,
            "model": self.model,
            "settings": {k: self.settings.get(k, "") for k in spec.setting_keys},
            "secrets_masked": {k: mask_secret(self.secrets.get(k, "")) for k in spec.secret_keys},
            "secrets_set": {k: bool(self.secrets.get(k)) for k in spec.secret_keys},
            **({"secrets": {k: self.secrets.get(k, "") for k in spec.secret_keys}} if include_secrets else {}),
            "options": {**DEFAULT_OPTIONS, **self.options},
            "supports_temperature": spec.supports_temperature,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "updated_by": self.updated_by,
        }


class LLMConfigService:
    def __init__(self, datastores: DataStores, secret_box: SecretBox, *, timeout_seconds: float):
        self._ds = datastores
        self._box = secret_box
        self._timeout = timeout_seconds
        self._lock = asyncio.Lock()
        self._config: StoredLLMConfig | None = None
        self._provider: LLMProvider | None = None
        self._provider_stamp: datetime | None = None
        self._last_check: float = 0.0

    # ----------------------------------------------------------------- read
    async def load(self) -> StoredLLMConfig | None:
        pool = self._ds.pg
        if pool is None:
            return None
        row = await pool.fetchrow(
            "SELECT provider, model, settings, secrets, options, updated_at, updated_by FROM llm_config WHERE id = 1"
        )
        if row is None:
            return None
        secrets_enc = _as_dict(row["secrets"])
        secrets = {k: self._box.decrypt(v) for k, v in secrets_enc.items() if v}
        return StoredLLMConfig(
            provider=row["provider"],
            model=row["model"],
            settings=_as_dict(row["settings"]),
            secrets=secrets,
            options=_as_dict(row["options"]),
            updated_at=row["updated_at"],
            updated_by=row["updated_by"],
        )

    async def get_public(self, *, include_secrets: bool = False) -> dict[str, Any]:
        cfg = await self.load()
        if cfg is None:
            return {"configured": False}
        return cfg.to_public_dict(include_secrets=include_secrets)

    # ---------------------------------------------------------------- write
    async def save(
        self,
        *,
        provider: str,
        model: str,
        settings: dict[str, Any],
        secrets: dict[str, str],
        options: dict[str, Any],
        updated_by: str | None,
        keep_existing_secrets: bool = True,
    ) -> StoredLLMConfig:
        spec = get_spec(provider)
        model = (model or spec.default_model or "").strip()
        clean_settings = {k: str(settings.get(k) or "").strip() for k in spec.setting_keys}
        for f in spec.fields:
            if not f.secret and not clean_settings.get(f.key) and f.default:
                clean_settings[f.key] = f.default

        existing = await self.load() if keep_existing_secrets else None
        clean_secrets: dict[str, str] = {}
        for key in spec.secret_keys:
            incoming = str(secrets.get(key) or "").strip()
            if incoming:
                clean_secrets[key] = incoming
            elif existing and existing.provider == spec.id and existing.secrets.get(key):
                clean_secrets[key] = existing.secrets[key]

        # Validate eagerly so a broken config is never persisted.
        build_provider(spec.id, model, {**clean_settings, **clean_secrets}, timeout=self._timeout)

        clean_options = {**DEFAULT_OPTIONS, **(existing.options if existing else {}), **_clean_options(options)}
        encrypted = {k: self._box.encrypt(v) for k, v in clean_secrets.items()}

        pool = self._ds.pg
        if pool is None:
            raise RuntimeError("Postgres is not connected")
        row = await pool.fetchrow(
            """
            INSERT INTO llm_config (id, provider, model, settings, secrets, options, updated_at, updated_by)
            VALUES (1, $1, $2, $3::jsonb, $4::jsonb, $5::jsonb, NOW(), $6)
            ON CONFLICT (id) DO UPDATE SET
                provider = EXCLUDED.provider,
                model = EXCLUDED.model,
                settings = EXCLUDED.settings,
                secrets = EXCLUDED.secrets,
                options = EXCLUDED.options,
                updated_at = NOW(),
                updated_by = EXCLUDED.updated_by
            RETURNING updated_at
            """,
            spec.id,
            model,
            json.dumps(clean_settings),
            json.dumps(encrypted),
            json.dumps(clean_options),
            updated_by,
        )
        cfg = StoredLLMConfig(
            provider=spec.id,
            model=model,
            settings=clean_settings,
            secrets=clean_secrets,
            options=clean_options,
            updated_at=row["updated_at"],
            updated_by=updated_by,
        )
        await self._replace_provider(cfg)
        logger.info("llm config saved provider=%s model=%s by=%s", spec.id, model, updated_by)
        return cfg

    async def delete(self) -> None:
        pool = self._ds.pg
        if pool is not None:
            await pool.execute("DELETE FROM llm_config WHERE id = 1")
        await self._replace_provider(None)

    # ------------------------------------------------------------- provider
    async def get_provider(self) -> tuple[LLMProvider, StoredLLMConfig]:
        """Return the live provider, refreshing if the stored config changed."""
        now = time.monotonic()
        if self._provider is not None and now - self._last_check < _REFRESH_INTERVAL_SECONDS:
            assert self._config is not None
            return self._provider, self._config

        async with self._lock:
            self._last_check = time.monotonic()
            cfg = await self.load()
            if cfg is None:
                await self._replace_provider(None)
                raise LLMNotConfiguredError()
            if self._provider is None or cfg.updated_at != self._provider_stamp:
                await self._replace_provider(cfg)
            assert self._provider is not None and self._config is not None
            return self._provider, self._config

    async def _replace_provider(self, cfg: StoredLLMConfig | None) -> None:
        old = self._provider
        if cfg is None:
            self._provider, self._config, self._provider_stamp = None, None, None
        else:
            self._provider = build_provider(cfg.provider, cfg.model, cfg.merged(), timeout=self._timeout)
            self._config = cfg
            self._provider_stamp = cfg.updated_at
        self._last_check = time.monotonic()
        if old is not None and old is not self._provider:
            try:
                await old.close()
            except Exception:  # pragma: no cover
                pass

    # ----------------------------------------------------------------- test
    async def test_connection(
        self,
        *,
        provider: str,
        model: str,
        settings: dict[str, Any],
        secrets: dict[str, str],
        options: dict[str, Any],
    ) -> dict[str, Any]:
        """Run a tiny completion against a candidate config without saving it."""
        spec = get_spec(provider)
        model = (model or spec.default_model or "").strip()
        existing = await self.load()
        merged: dict[str, Any] = {k: str(settings.get(k) or "").strip() for k in spec.setting_keys}
        for f in spec.fields:
            if not f.secret and not merged.get(f.key) and f.default:
                merged[f.key] = f.default
        for key in spec.secret_keys:
            incoming = str(secrets.get(key) or "").strip()
            if incoming:
                merged[key] = incoming
            elif existing and existing.provider == spec.id:
                merged[key] = existing.secrets.get(key, "")

        candidate = build_provider(spec.id, model, merged, timeout=min(self._timeout, 45))
        # Reasoning models spend output tokens on thinking before replying; keep headroom.
        temp_cfg = StoredLLMConfig(spec.id, model, {}, {}, {**DEFAULT_OPTIONS, **_clean_options(options), "max_tokens": 512}, None, None)
        started = time.perf_counter()
        try:
            resp = await candidate.complete(
                [
                    LLMMessage(role="system", content="You are a connectivity check. Reply with exactly: OK"),
                    LLMMessage(role="user", content="Reply with OK."),
                ],
                temp_cfg.generation_options(timeout_seconds=45),
            )
        except LLMProviderError as exc:
            return {"ok": False, "message": exc.message, "code": exc.code}
        finally:
            await candidate.close()
        return {
            "ok": True,
            "message": "Connected",
            "model": resp.model or model,
            "reply": resp.text.strip()[:80],
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "usage": {"prompt_tokens": resp.usage.prompt_tokens, "completion_tokens": resp.usage.completion_tokens},
        }


def _as_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    return dict(value) if isinstance(value, dict) else {}


def _clean_options(options: dict[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if not options:
        return out
    if "temperature" in options and options["temperature"] is not None:
        out["temperature"] = min(2.0, max(0.0, float(options["temperature"])))
    if "max_tokens" in options and options["max_tokens"]:
        out["max_tokens"] = min(32768, max(256, int(options["max_tokens"])))
    return out
