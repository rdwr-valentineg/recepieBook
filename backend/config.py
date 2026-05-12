"""Application configuration loaded from environment variables."""
from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Optional


class Settings(BaseSettings):
    # Storage paths inside the container (mounted via PVC in k8s)
    data_dir: str = "/data"

    # Database — connect to your existing MariaDB
    database_url: str = "mariadb+pymysql://recipes:recipes@localhost:3306/recipes"

    # Auth
    app_password: str = Field(default="changeme", description="Shared password for the family")
    session_secret: str = Field(default="change-me-to-a-long-random-string-at-least-32-chars")
    session_max_age_days: int = 60

    # Hosts / share URLs
    share_base_url: str = "https://example.com"
    secure_cookies: bool = True

    # ── Direct providers (tried first, in this order) ────────────────────────

    # Anthropic Claude — https://console.anthropic.com
    anthropic_api_key: Optional[str] = None
    anthropic_model: str = "claude-haiku-4-5-20251001"

    # OpenAI GPT — https://platform.openai.com
    openai_api_key: Optional[str] = None
    openai_model: str = "gpt-4o-mini"
    openai_base_url: str = "https://api.openai.com/v1"

    # xAI Grok (free monthly credits) — https://console.x.ai
    xai_api_key: Optional[str] = None
    xai_model: str = "grok-3-mini"
    xai_vision_model: str = "grok-2-vision-1212"

    # Google Gemini (free tier) — https://aistudio.google.com/apikey
    gemini_api_key: Optional[str] = None
    gemini_model: str = "gemini-2.0-flash"

    # Groq (fast inference, free tier) — https://console.groq.com
    groq_api_key: Optional[str] = None
    groq_model: str = "llama-3.3-70b-versatile"
    groq_vision_model: str = "llama-3.2-11b-vision-preview"

    # ── Fallback providers ───────────────────────────────────────────────────

    # OpenRouter (catch-all, :free models need no credits) — https://openrouter.ai
    # Each model is tried in order; if one is rate-limited the next is used automatically.
    openrouter_api_key: Optional[str] = None
    openrouter_text_models: str = (
        "google/gemma-2-9b-it:free,"
        "meta-llama/llama-3.2-3b-instruct:free,"
        "mistralai/mistral-7b-instruct:free,"
        "qwen/qwen-2-7b-instruct:free,"
        "microsoft/phi-3-mini-128k-instruct:free"
    )
    openrouter_vision_models: str = (
        "meta-llama/llama-3.2-11b-vision-instruct:free,"
        "qwen/qwen2-vl-7b-instruct:free"
    )

    # Ollama (local k3s, last resort, no internet needed) — http://ollama:11434/v1
    ollama_base_url: Optional[str] = None
    ollama_model: str = "qwen2.5:1.5b"
    ollama_vision_model: str = "moondream"

    # ── Timeouts ─────────────────────────────────────────────────────────────
    fetch_timeout_seconds: int = 30
    llm_timeout_seconds: int = 90
    capture_timeout_seconds: int = 120
    max_image_size_mb: int = 8

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


settings = Settings()
