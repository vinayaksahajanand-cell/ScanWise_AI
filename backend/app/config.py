"""Application configuration loaded from environment variables / backend/.env.

Secrets (API keys) are only ever read from the environment. They are never
returned by the API, logged, or editable from the UI.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "ScanWise AI"
    environment: str = "development"
    log_level: str = "INFO"

    # --- storage -----------------------------------------------------------
    data_dir: Path = BACKEND_DIR / "data"
    database_url: str | None = None  # default: sqlite file inside data_dir
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://127.0.0.1:3000"]
    )

    # --- limits ------------------------------------------------------------
    max_upload_mb: int = 40
    max_files_per_request: int = 30
    max_pages_per_document: int = 40
    worker_concurrency: int = 2

    # --- OCR ---------------------------------------------------------------
    ocr_engine: str = "rapidocr"  # rapidocr | paddleocr | tesseract
    ocr_model_size: str = "small"  # rapidocr: tiny | small | medium | server
    ocr_max_side: int = 2400
    ocr_min_token_score: float = 0.3  # tokens below this are dropped by the engine itself
    review_conf_threshold: float = 0.90  # numeric/critical fields below this need review
    review_text_conf_threshold: float = 0.80
    pdf_render_dpi: int = 220
    pdf_ocr_dpi: int = 300
    ocr_warmup: bool = True  # load + prime the OCR models at startup so the first scan isn't slow

    # --- vision LLM --------------------------------------------------------
    llm_provider: str = "auto"  # auto | openai | gemini | anthropic | nvidia | compatible | none
    llm_mode: str = "always"  # always | fallback | off
    llm_timeout_s: float = 180.0
    llm_max_retries: int = 2
    llm_max_pages: int = 12
    llm_pages_per_call: int = 4
    llm_image_max_side: int = 2000
    llm_split_call: bool = True  # ask for header/totals and line items in two parallel requests (faster, same fields)
    llm_parallel_calls: int = 4  # max simultaneous vision requests within one scan

    openai_api_key: str | None = None
    openai_model: str = "gpt-4.1"
    openai_base_url: str = "https://api.openai.com/v1"
    # gpt-5 / o-series only: minimal | low | medium | high ("" = the model's own default, slowest)
    openai_reasoning_effort: str = "low"

    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.5-flash"
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"

    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-opus-5-5"
    anthropic_base_url: str = "https://api.anthropic.com/v1"

    nvidia_api_key: str | None = None
    nvidia_model: str = "meta/llama-3.2-90b-vision-instruct"
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    nvidia_pages_per_call: int = 1

    # Any other OpenAI-compatible vision endpoint (OpenRouter, vLLM, Ollama, LM Studio...)
    compatible_api_key: str | None = None
    compatible_model: str | None = None
    compatible_base_url: str | None = None

    @property
    def files_dir(self) -> Path:
        return self.data_dir / "files"

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{(self.data_dir / 'scanwise.db').as_posix()}"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.files_dir.mkdir(parents=True, exist_ok=True)
    return settings
