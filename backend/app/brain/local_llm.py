"""Этап 1 — локальная модель (Qwen3-8B / Qwen3.5-9B).

ollama  — нативный /api/chat: format = JSON-схема (constrained decoding), think = режим рассуждения.
openai  — OpenAI-совместимый сервер (vLLM/SGLang): response_format json_schema + enable_thinking.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from app.brain.llm_common import LLMError, LLMResponse
from app.config import Settings, get_settings


def _ollama_base(url: str) -> str:
    url = url.rstrip("/")
    return url[:-3] if url.endswith("/v1") else url


class OllamaClient:
    provider = "ollama"
    kind = "local"

    def __init__(self, model_key: str, settings: Settings | None = None):
        s = settings or get_settings()
        self.settings = s
        self.key, self.model, self.display_name = s.resolve_local_model(model_key)
        self.base = _ollama_base(s.local_llm_base_url)
        self._sem = asyncio.Semaphore(max(1, s.local_concurrency))
        self._send_think = True

    def _num_ctx(self, system: str, user: str) -> int:
        """Контекст по длине промпта: меньше KV-кэш → больше слоёв модели в видеопамяти → быстрее.

        Две ступени (8K — чаты, LOCAL_NUM_CTX — длинные звонки), т.к. смена num_ctx перезагружает модель.
        ~2.5 символа на токен для RU/KZ (замер на DEV: ~2.8) плюс ~2K на ответ (замер: 0.8–1.5K).
        Если ответ всё же упрётся в контекст, complete_json повторит запрос со следующей ступенью.
        """
        need = int((len(system) + len(user)) / 2.5) + 2048 + 256
        return next((ctx for ctx in self._ladder() if need <= ctx), 32768)

    def _ladder(self) -> tuple[int, ...]:
        return tuple(sorted({8192, max(8192, self.settings.local_num_ctx), 32768}))

    async def complete_json(self, system: str, user: str, schema: dict[str, Any], schema_name: str) -> LLMResponse:
        return await self._complete_with_ctx(system, user, schema, schema_name, self._num_ctx(system, user))

    async def _complete_with_ctx(self, system: str, user: str, schema: dict[str, Any], schema_name: str,
                                 num_ctx: int) -> LLMResponse:
        s = self.settings
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "stream": False,
            "format": schema,
            "keep_alive": "30m",
            "options": {"temperature": 0, "seed": 42, "num_ctx": num_ctx, "num_predict": s.local_max_tokens},
        }
        if self._send_think:
            payload["think"] = bool(s.local_think)
        async with self._sem:
            t0 = time.perf_counter()
            try:
                async with httpx.AsyncClient(timeout=s.local_timeout_sec) as client:
                    resp = await client.post(f"{self.base}/api/chat", json=payload)
                    if resp.status_code == 400 and "think" in resp.text.lower() and "think" in payload:
                        self._send_think = False  # модель не поддерживает параметр think
                        payload.pop("think")
                        resp = await client.post(f"{self.base}/api/chat", json=payload)
            except httpx.ConnectError as exc:
                raise LLMError(f"Ollama недоступна по адресу {self.base}: {exc}") from exc
            except httpx.TimeoutException as exc:
                raise LLMError(f"Ollama не ответила за {s.local_timeout_sec:.0f} с") from exc
            dur = int((time.perf_counter() - t0) * 1000)
        if resp.status_code == 404:
            raise LLMError(f"Модель {self.model} не найдена в Ollama — выполните `ollama pull {self.model}`")
        if resp.status_code != 200:
            raise LLMError(f"Ollama HTTP {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        msg = data.get("message") or {}
        if data.get("done_reason") == "length":
            bigger = [c for c in self._ladder() if c > num_ctx]
            used = int(data.get("prompt_eval_count") or 0) + int(data.get("eval_count") or 0)
            if bigger and used >= num_ctx - 64:  # упёрлись в контекст, а не в num_predict — расширяем
                return await self._complete_with_ctx(system, user, schema, schema_name, bigger[0])
            raise LLMError("Ответ локальной модели обрезан по длине (увеличьте LOCAL_MAX_TOKENS)")
        return LLMResponse(
            text=msg.get("content") or "",
            model=self.model,
            usage={"input_tokens": int(data.get("prompt_eval_count") or 0),
                   "output_tokens": int(data.get("eval_count") or 0)},
            cost_usd=0.0,
            duration_ms=dur,
            meta={"think": payload.get("think"), "thinking_chars": len(msg.get("thinking") or ""),
                  "num_ctx": num_ctx, "load_ms": int((data.get("load_duration") or 0) / 1e6),
                  "prompt_eval_ms": int((data.get("prompt_eval_duration") or 0) / 1e6),
                  "eval_ms": int((data.get("eval_duration") or 0) / 1e6)},
        )


class OpenAICompatLocalClient:
    """vLLM / SGLang / OpenRouter через OpenAI-совместимый API.

    OpenRouter: та же модель семейства Qwen, но на чужом сервере — ключ из OPENROUTER_API_KEY, reasoning
    выключается параметром reasoning.enabled, провайдер обязан поддерживать structured outputs
    (provider.require_parameters), стоимость берётся из usage.cost ответа.
    """
    kind = "local"

    def __init__(self, model_key: str, settings: Settings | None = None):
        from openai import AsyncOpenAI

        s = settings or get_settings()
        self.settings = s
        self.key, self.model, self.display_name = s.resolve_local_model(model_key)
        self.openrouter = s.local_is_openrouter()
        self.provider = "openrouter" if self.openrouter else "openai-compatible"
        if self.openrouter:
            self.display_name = f"{self.display_name} via OpenRouter"
        base = s.local_llm_base_url.rstrip("/")
        self._client = AsyncOpenAI(base_url=base if base.endswith("/v1") else base + "/v1", api_key=s.local_api_key(),
                                   timeout=s.local_timeout_sec, max_retries=2 if self.openrouter else 0)
        self._sem = asyncio.Semaphore(max(1, s.local_concurrency))

    def _extra_body(self) -> dict[str, Any]:
        think = bool(self.settings.local_think)
        if self.openrouter:
            return {"reasoning": {"enabled": think, "exclude": True},
                    "provider": {"require_parameters": True},
                    "usage": {"include": True}}
        return {"chat_template_kwargs": {"enable_thinking": think}}

    async def complete_json(self, system: str, user: str, schema: dict[str, Any], schema_name: str) -> LLMResponse:
        import openai

        s = self.settings
        async with self._sem:
            t0 = time.perf_counter()
            try:
                resp = await self._client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                    temperature=0, seed=42, max_tokens=s.local_max_tokens,
                    response_format={"type": "json_schema",
                                     "json_schema": {"name": schema_name, "schema": schema, "strict": True}},
                    extra_body=self._extra_body(),
                )
            except openai.APIConnectionError as exc:
                raise LLMError(f"Сервер модели недоступен: {exc}") from exc
            except openai.APIStatusError as exc:
                raise LLMError(f"Сервер модели HTTP {exc.status_code}: {exc.message}") from exc
            dur = int((time.perf_counter() - t0) * 1000)
        if not resp.choices:
            raise LLMError(f"Пустой ответ сервера модели: {getattr(resp, 'error', None) or resp}")
        choice = resp.choices[0]
        if choice.finish_reason == "length":
            raise LLMError("Ответ модели обрезан по длине (увеличьте LOCAL_MAX_TOKENS)")
        usage = resp.usage
        extra = (getattr(usage, "model_extra", None) or {}) if usage else {}
        cost = extra.get("cost")
        return LLMResponse(text=choice.message.content or "", model=self.model,
                           cost_usd=float(cost) if cost is not None else 0.0, duration_ms=dur,
                           usage={"input_tokens": usage.prompt_tokens if usage else 0,
                                  "output_tokens": usage.completion_tokens if usage else 0},
                           meta={"served_by": getattr(resp, "provider", None) or (resp.model_extra or {}).get("provider"),
                                 "think": bool(s.local_think)} if self.openrouter else {})


async def local_status(settings: Settings | None = None) -> dict[str, Any]:
    """Доступность локального сервера и список моделей (для /api/system/status)."""
    s = settings or get_settings()
    out: dict[str, Any] = {"backend": s.local_llm_backend, "base_url": s.local_llm_base_url, "reachable": False,
                           "models": [], "error": None}
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            if s.local_llm_backend == "ollama":
                r = await client.get(f"{_ollama_base(s.local_llm_base_url)}/api/tags")
                r.raise_for_status()
                out["models"] = [m.get("name") for m in r.json().get("models", [])]
            else:
                base = s.local_llm_base_url.rstrip("/")
                r = await client.get((base if base.endswith("/v1") else base + "/v1") + "/models")
                r.raise_for_status()
                out["models"] = [m.get("id") for m in r.json().get("data", [])]
        out["reachable"] = True
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def make_local_client(model_key: str, settings: Settings | None = None):
    s = settings or get_settings()
    if s.local_llm_backend == "openai":
        return OpenAICompatLocalClient(model_key, s)
    return OllamaClient(model_key, s)
