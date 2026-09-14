from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    app_name: str = "ANSI Local AI Assistant"
    app_env: str = "development"
    frontend_origin: str = "http://localhost:5173"
    cookie_secure: bool = False
    jwt_secret: str
    bootstrap_admin_username: str | None = None
    bootstrap_admin_password: str | None = None
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_chat_model: str = "qwen3:4b"
    ollama_embedding_model: str = "embeddinggemma"
    # qwen3 emits reasoning before its answer; the stream hides it until </think>.
    ollama_chat_reasoning: bool = True
    document_max_upload_mb: int = 20
    conversation_retention_days: int = 0  # 0 disables automatic purging
    chat_rate_limit_per_minute: int = 12

    model_config = SettingsConfigDict(env_file=PROJECT_DIR / ".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
