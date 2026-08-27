"""Конфигурация приложения (переменные окружения / .env)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(ROOT / ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = "Реестр обращений по персональным данным"
    data_dir: str = str(ROOT / "var")
    database_url: str = ""
    sql_echo: bool = False

    #: Каталог для сохранённых вложений.
    upload_dir: str = ""
    max_upload_mb: int = 30

    #: ИИ-разбор. Без ключа система работает на детерминированных правилах.
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-5"
    llm_enabled: bool = True
    llm_timeout_s: int = 120

    #: OCR
    tesseract_lang: str = "rus+eng"
    ocr_dpi: int = 300

    #: IMAP-приём обращений (опционально; ящики настраиваются в интерфейсе).
    imap_poll_seconds: int = 300

    cors_origins: str = "*"

    def model_post_init(self, __context) -> None:
        if not self.database_url:
            object.__setattr__(self, "database_url", f"sqlite:///{Path(self.data_dir) / 'dpo.db'}")
        if not self.upload_dir:
            object.__setattr__(self, "upload_dir", str(Path(self.data_dir) / "uploads"))
        Path(self.data_dir).mkdir(parents=True, exist_ok=True)
        Path(self.upload_dir).mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
