"""Этап 2 — облачная модель: Claude (Anthropic) или OpenAI, выбирается переключателем.

Claude: структурированный вывод через output_config.format (JSON-схема). Принудительный tool_choice
и temperature у claude-sonnet-5-5 / claude-opus-5-5 не поддерживаются (400), поэтому не используются.
Системный промпт с чек-листом помечен cache_control — он одинаков для всех диалогов.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any

from app.brain.llm_common import LLMError, LLMResponse, cost_from_usage
from app.config import Settings, get_settings

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicClient:
    provider = "anthropic"
    kind = "cloud"

    def __init__(self, settings: Settings | None = None, model: str | None = None):
        s = settings or get_settings()
        self.settings = s
        self.model = model or s.anthropic_model
        self.display_name = self.model
        self._client = None
        self._sem = asyncio.Semaphore(max(1, s.cloud_concurrency))

    def _get_client(self):
        if self._client is None:
            import anthropic

            kwargs: dict[str, Any] = {"timeout": self.settings.cloud_timeout_sec, "max_retries": 2}
            if self.settings.anthropic_api_key:
                kwargs["api_key"] = self.settings.anthropic_api_key
            self._client = anthropic.AsyncAnthropic(**kwargs)
        return self._client

    async def complete_json(self, system: str, user: str, schema: dict[str, Any], schema_name: str) -> LLMResponse:
        import anthropic

        s = self.settings
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": s.cloud_max_tokens,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": user}],
            "output_config": {"format": {"type": "json_schema", "schema": schema}, "effort": s.anthropic_effort},
        }
        if s.anthropic_fallbacks and "haiku" not in self.model:
            kwargs["betas"] = [FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
        json_mode = "output_config.format"
        async with self._sem:
            t0 = time.perf_counter()
            try:
                client = self._get_client()
                try:
                    resp = await client.beta.messages.create(**kwargs)
                except anthropic.BadRequestError as exc:
                    if "grammar" not in str(exc).lower():
                        raise
                    # Схема слишком велика для constrained decoding — передаём её текстом, ответ валидирует код.
                    json_mode = "prompt"
                    kwargs["output_config"] = {"effort": s.anthropic_effort}
                    kwargs["messages"] = [{"role": "user", "content": user + _schema_note(schema)}]
                    resp = await client.beta.messages.create(**kwargs)
            except TypeError as exc:
                if "authentication" in str(exc).lower():
                    raise LLMError("Claude: не задан ANTHROPIC_API_KEY в .env") from exc
                raise
            except anthropic.AuthenticationError as exc:
                raise LLMError("Claude: неверный или отсутствующий ANTHROPIC_API_KEY") from exc
            except anthropic.RateLimitError as exc:
                raise LLMError(f"Claude: превышен лимит запросов ({exc.message})") from exc
            except anthropic.BadRequestError as exc:
                raise LLMError(f"Claude: некорректный запрос — {exc.message}") from exc
            except anthropic.APIStatusError as exc:
                raise LLMError(f"Claude: HTTP {exc.status_code} — {exc.message}") from exc
            except anthropic.APIConnectionError as exc:
                raise LLMError(f"Claude недоступен: {exc}") from exc
            dur = int((time.perf_counter() - t0) * 1000)

        if resp.stop_reason == "refusal":
            raise LLMError("Claude отказался отвечать (stop_reason=refusal)")
        if resp.stop_reason == "max_tokens":
            raise LLMError("Ответ Claude обрезан по max_tokens")
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        u = resp.usage
        usage = {
            "input_tokens": int(getattr(u, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(u, "output_tokens", 0) or 0),
            "cache_read_input_tokens": int(getattr(u, "cache_read_input_tokens", 0) or 0),
            "cache_creation_input_tokens": int(getattr(u, "cache_creation_input_tokens", 0) or 0),
        }
        fallback_used = any(getattr(b, "type", "") == "fallback" for b in resp.content)
        return LLMResponse(
            text=text, model=getattr(resp, "model", self.model) or self.model, usage=usage,
            cost_usd=cost_from_usage(s.price_for("anthropic", self.model), usage), duration_ms=dur,
            meta={"request_id": getattr(resp, "_request_id", None), "fallback_used": fallback_used,
                  "effort": s.anthropic_effort, "json_mode": json_mode},
        )


def _schema_note(schema: dict[str, Any]) -> str:
    return ("\n\n# JSON-схема ответа\nВерни ТОЛЬКО один JSON-объект по этой схеме, без пояснений и без markdown.\n"
            + json.dumps(schema, ensure_ascii=False))


class OpenAIClient:
    provider = "openai"
    kind = "cloud"

    def __init__(self, settings: Settings | None = None, model: str | None = None):
        s = settings or get_settings()
        self.settings = s
        self.model = model or s.openai_model
        self.display_name = self.model or "openai"
        self._client = None
        self._sem = asyncio.Semaphore(max(1, s.cloud_concurrency))
        t = (s.openai_temperature or "").strip()
        self._temperature: float | None = float(t) if t else None

    def _get_client(self):
        if self._client is None:
            from openai import AsyncOpenAI

            kwargs: dict[str, Any] = {"timeout": self.settings.cloud_timeout_sec, "max_retries": 2}
            if self.settings.openai_api_key:
                kwargs["api_key"] = self.settings.openai_api_key
            if self.settings.openai_base_url:
                kwargs["base_url"] = self.settings.openai_base_url
            self._client = AsyncOpenAI(**kwargs)
        return self._client

    async def complete_json(self, system: str, user: str, schema: dict[str, Any], schema_name: str) -> LLMResponse:
        import openai

        if not self.model:
            raise LLMError("Не задан OPENAI_MODEL в .env")
        s = self.settings
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": schema_name, "schema": schema, "strict": True}},
            "max_completion_tokens": s.cloud_max_tokens,
            "seed": 42,
        }
        if self._temperature is not None:
            kwargs["temperature"] = self._temperature
        async with self._sem:
            t0 = time.perf_counter()
            if not (self.settings.openai_api_key or os.environ.get("OPENAI_API_KEY")):
                raise LLMError("OpenAI: не задан OPENAI_API_KEY в .env")
            try:
                client = self._get_client()
                try:
                    resp = await client.chat.completions.create(**kwargs)
                except openai.BadRequestError as exc:
                    # Reasoning-модели OpenAI не принимают temperature — повторяем без неё один раз.
                    if "temperature" in kwargs and "temperature" in str(exc).lower():
                        self._temperature = None
                        kwargs.pop("temperature")
                        resp = await client.chat.completions.create(**kwargs)
                    else:
                        raise
            except openai.AuthenticationError as exc:
                raise LLMError("OpenAI: неверный или отсутствующий OPENAI_API_KEY") from exc
            except openai.RateLimitError as exc:
                raise LLMError(f"OpenAI: превышен лимит запросов ({exc.message})") from exc
            except openai.APIStatusError as exc:
                raise LLMError(f"OpenAI: HTTP {exc.status_code} — {exc.message}") from exc
            except openai.APIConnectionError as exc:
                raise LLMError(f"OpenAI недоступен: {exc}") from exc
            dur = int((time.perf_counter() - t0) * 1000)
        choice = resp.choices[0]
        if getattr(choice.message, "refusal", None):
            raise LLMError(f"OpenAI отказался отвечать: {choice.message.refusal}")
        if choice.finish_reason == "length":
            raise LLMError("Ответ OpenAI обрезан по длине")
        u = resp.usage
        cached = 0
        if u is not None and getattr(u, "prompt_tokens_details", None) is not None:
            cached = int(getattr(u.prompt_tokens_details, "cached_tokens", 0) or 0)
        usage = {
            "input_tokens": int((u.prompt_tokens if u else 0) - cached),
            "output_tokens": int(u.completion_tokens if u else 0),
            "cache_read_input_tokens": cached,
        }
        return LLMResponse(text=choice.message.content or "", model=resp.model or self.model, usage=usage,
                           cost_usd=cost_from_usage(s.price_for("openai", self.model), usage), duration_ms=dur,
                           meta={"temperature": kwargs.get("temperature")})


def cloud_configured(provider: str, settings: Settings | None = None) -> bool:
    s = settings or get_settings()
    if provider == "anthropic":
        return bool(s.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
    return bool((s.openai_api_key or os.environ.get("OPENAI_API_KEY")) and s.openai_model)


def make_cloud_client(provider: str, settings: Settings | None = None):
    return AnthropicClient(settings) if provider == "anthropic" else OpenAIClient(settings)
