"""Настройки приложения: env, цены моделей, режимы."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_DIR = Path(__file__).resolve().parent
BACKEND_DIR = APP_DIR.parent
REPO_ROOT = BACKEND_DIR.parent

Mode = Literal["local", "cloud", "hybrid"]
Provider = Literal["anthropic", "openai"]
Strategy = Literal["review", "blind"]

MODES: tuple[str, ...] = ("local", "cloud", "hybrid")
PROVIDERS: tuple[str, ...] = ("anthropic", "openai")
STRATEGIES: tuple[str, ...] = ("review", "blind")

# Локальные модели: ключ интерфейса → тег Ollama и отображаемое имя.
LOCAL_MODELS: dict[str, dict[str, str]] = {
    "qwen3-8b": {"tag": "qwen3:8b", "display": "Qwen3-8B"},
    "qwen3.5-9b": {"tag": "qwen3.5:9b", "display": "Qwen3.5-9B"},
}

# Цены, $ за 1 млн токенов. Сверены с platform.claude.com 07.11.2026.
# cache_write = 1.25 × input (TTL 5 мин), cache_read — из таблицы цен.
CLAUDE_PRICES: dict[str, dict[str, float]] = {
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00, "cache_read": 0.20, "cache_write": 2.50},
    "claude-haiku-5-5": {"input": 0.10, "output": 0.50, "cache_read": 0.01, "cache_write": 0.125},
    "claude-opus-5-5": {"input": 4.00, "output": 20.00, "cache_read": 0.20, "cache_write": 5.00},
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(BACKEND_DIR / ".env"), str(REPO_ROOT / ".env")),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Данные
    organizers_api_url: str = ""
    database_url: str = f"sqlite:///{(REPO_ROOT / 'data' / 'cqa.db').as_posix()}"
    frontend_dir: str = str(REPO_ROOT / "frontend")
    raw_data_dir: str = str(REPO_ROOT / "data" / "raw")

    # Локальная модель
    local_llm_backend: Literal["ollama", "openai"] = "ollama"
    local_llm_base_url: str = "http://localhost:11434"
    local_model: str = "qwen3-8b"
    local_model_tags: str = ""
    local_think: bool = False
    local_num_ctx: int = 16384
    local_max_tokens: int = 4096
    local_timeout_sec: float = 900.0
    local_concurrency: int = 1
    # Ключ OpenAI-совместимого сервера (для vLLM не нужен). Для OpenRouter берётся OPENROUTER_API_KEY.
    local_llm_api_key: str = ""
    # OpenRouter: LOCAL_LLM_BACKEND=openai, LOCAL_LLM_BASE_URL=https://openrouter.ai/api/v1,
    # LOCAL_MODEL_TAGS="qwen3.5-9b=qwen/qwen3.5-9b". Принимается и имя openrouter-ai_api_key.
    openrouter_api_key: str = Field(default="", validation_alias=AliasChoices(
        "OPENROUTER_API_KEY", "openrouter_api_key", "openrouter-ai_api_key"))

    # Облако
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5-5"
    anthropic_effort: str = "medium"
    anthropic_fallbacks: bool = True
    openai_api_key: str = ""
    openai_model: str = ""
    openai_base_url: str = ""
    openai_price_in: float = 0.0
    openai_price_out: float = 0.0
    openai_temperature: str = "0"
    cloud_max_tokens: int = 16000
    cloud_timeout_sec: float = 300.0
    cloud_concurrency: int = 4

    # Режимы по умолчанию
    default_mode: Mode = "hybrid"
    default_provider: Provider = "anthropic"
    hybrid_strategy: Strategy = "review"

    # Прочее
    team_name: str = "Freedom_Bank_BEZUMIE"
    quote_match_threshold: float = 0.85

    # --- вычисляемые помощники ---
    def local_is_openrouter(self) -> bool:
        return self.local_llm_backend == "openai" and "openrouter.ai" in self.local_llm_base_url

    def local_api_key(self) -> str:
        if self.local_llm_api_key:
            return self.local_llm_api_key
        return self.openrouter_api_key if self.local_is_openrouter() else "EMPTY"

    def local_tag_map(self) -> dict[str, str]:
        tags = {k: v["tag"] for k, v in LOCAL_MODELS.items()}
        for part in filter(None, (p.strip() for p in self.local_model_tags.split(","))):
            if "=" in part:
                key, tag = part.split("=", 1)
                tags[key.strip()] = tag.strip()
        return tags

    def resolve_local_model(self, key: str | None = None) -> tuple[str, str, str]:
        """→ (ключ, тег для сервера, отображаемое имя)."""
        key = (key or self.local_model).strip()
        tags = self.local_tag_map()
        if key in tags:
            display = LOCAL_MODELS.get(key, {}).get("display", key)
            return key, tags[key], display
        for k, tag in tags.items():  # передали сам тег (например qwen3:8b)
            if tag == key:
                return k, tag, LOCAL_MODELS.get(k, {}).get("display", k)
        return key, key, key

    def cloud_model_for(self, provider: str) -> str:
        return self.anthropic_model if provider == "anthropic" else (self.openai_model or "openai-model-not-set")

    def price_for(self, provider: str, model: str) -> dict[str, float] | None:
        if provider == "anthropic":
            return CLAUDE_PRICES.get(model)
        if provider == "openai":
            if self.openai_price_in or self.openai_price_out:
                return {"input": self.openai_price_in, "output": self.openai_price_out,
                        "cache_read": self.openai_price_in, "cache_write": self.openai_price_in}
            return None
        return None


@lru_cache
def get_settings() -> Settings:
    return Settings()
