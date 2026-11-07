"""Настройки приложения: .env, модель-судья, цены."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

APP_DIR = Path(__file__).resolve().parent
BACKEND_DIR = APP_DIR.parent
REPO_ROOT = BACKEND_DIR.parent

# anthropic — Claude напрямую (по умолчанию); openai — любой OpenAI-совместимый API (OpenAI, OpenRouter и т.п.).
Provider = Literal["anthropic", "openai"]
PROVIDERS: tuple[str, ...] = ("anthropic", "openai")

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

    # Модель-судья
    default_provider: Provider = "anthropic"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-5-5"
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

    # Прочее
    team_name: str = "Freedom_Bank_BEZUMIE"
    quote_match_threshold: float = 0.85

    def model_for(self, provider: str) -> str:
        return self.anthropic_model if provider == "anthropic" else (self.openai_model or "openai-model-not-set")

    def price_for(self, provider: str, model: str) -> dict[str, float] | None:
        if provider == "anthropic":
            return CLAUDE_PRICES.get(model)
        if provider == "openai" and (self.openai_price_in or self.openai_price_out):
            return {"input": self.openai_price_in, "output": self.openai_price_out,
                    "cache_read": self.openai_price_in, "cache_write": self.openai_price_in}
        return None


@lru_cache
def get_settings() -> Settings:
    return Settings()
