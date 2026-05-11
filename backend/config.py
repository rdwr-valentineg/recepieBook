"""Application configuration loaded from environment variables."""
from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Optional


class Settings(BaseSettings):
    # Storage paths inside the container (mounted via PVC in k8s)
    data_dir: str = "/data"

    # Database — connect to your existing MariaDB
    # e.g. mariadb+pymysql://recipes:secret@mariadb.home:3306/recipes
    database_url: str = "mariadb+pymysql://recipes:recipes@localhost:3306/recipes"

    # Auth
    app_password: str = Field(default="changeme", description="Shared password for the family")
    session_secret: str = Field(default="change-me-to-a-long-random-string-at-least-32-chars")
    session_max_age_days: int = 60

    # Hosts / share URLs
    # SHARE_BASE_URL is what the user copies/sends in WhatsApp.
    # When you share with someone outside the LAN, use the external hostname.
    share_base_url: str = "https://example.com"
    secure_cookies: bool = True  # set False for plain http during local dev

    # LLM - Anthropic
    anthropic_api_key: Optional[str] = None
    anthropic_model: str = "claude-haiku-4-5-20251001"

    # LLM - OpenAI
    openai_api_key: Optional[str] = None
    openai_model: str = "gpt-4o-mini"
    openai_base_url: str = "https://api.openai.com/v1"

    # Capture / scrape
    fetch_timeout_seconds: int = 30
    llm_timeout_seconds: int = 90
    capture_timeout_seconds: int = 60
    max_image_size_mb: int = 8

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


settings = Settings()
