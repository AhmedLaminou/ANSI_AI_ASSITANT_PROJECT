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
    # Empty falls back to the local SQLite file. For PostgreSQL + pgvector:
    # postgresql+psycopg://user:password@host:5432/ansi_ai
    database_url: str = ""
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
    login_rate_limit_per_minute: int = 5  # failed attempts, per account and per address
    login_rate_limit_window_seconds: int = 300
    # Access requests tolerated per source address per hour. 0 disables registration throttling.
    registration_rate_limit_per_hour: int = 5

    # Local OCR for scanned PDFs. Empty tesseract_cmd disables OCR entirely.
    tesseract_cmd: str = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    tessdata_dir: str = str(BACKEND_DIR / "data" / "tessdata")
    ocr_languages: str = "fra+eng"
    ocr_dpi: int = 300
    ocr_max_pages: int = 40

    # Retrieval attempts before giving up: 1 disables the rewrite-and-retry branch.
    max_retrieval_attempts: int = 2

    # Seconds allowed for one generation. CPU inference on a loaded machine is slow:
    # a reasoning model can spend minutes before producing its first useful token.
    chat_timeout_seconds: int = 300

    model_config = SettingsConfigDict(env_file=PROJECT_DIR / ".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
