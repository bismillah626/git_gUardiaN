"""
Git Guardian AI — Central configuration.

All settings are loaded from environment variables (or a .env file via pydantic-settings).
"""

from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Optional


class Settings(BaseSettings):
    """Application-wide settings sourced from environment variables."""

    # --- App ---
    app_name: str = "Git Guardian AI"
    debug: bool = False

    # --- GitHub ---
    github_token: str = Field(default="", description="GitHub personal-access or app token")
    github_webhook_secret: str = Field(default="", description="Webhook HMAC secret for signature verification")

    # --- LLM Providers ---
    groq_api_key: str = Field(default="", description="Groq free-tier API key")
    groq_model: str = Field(default="llama-3.3-70b-versatile", description="Default Groq chat model")
    groq_embeddings_model: str = Field(default="", description="Groq embeddings model (if available)")

    claude_api_key: str = Field(default="", description="Anthropic Claude API key (fallback)")
    claude_model: str = Field(default="claude-sonnet-4-20250514", description="Default Claude model")

    # --- Database ---
    database_url: str = Field(
        default="postgresql+psycopg2://git_guardian:git_guardian@localhost:5432/git_guardian",
        description="Postgres connection string",
    )
    database_url_async: str = Field(
        default="postgresql+asyncpg://git_guardian:git_guardian@localhost:5432/git_guardian",
        description="Async Postgres connection string",
    )

    # --- ChromaDB ---
    chroma_persist_dir: str = Field(default="./chroma_data", description="ChromaDB persistence directory")

    # --- Rate Limiting ---
    groq_max_retries: int = 5
    groq_retry_base_delay: float = 1.0  # seconds
    agent_timeout_seconds: int = Field(
        default=300,
        ge=30,
        description="Maximum runtime for one review agent before it is marked failed",
    )
    # LLM rate limiting (requests per minute)
    llm_requests_per_minute: int = Field(
        default=30,
        ge=1,
        description="Max LLM requests per minute (shared across all agents)",
    )
    # GitHub API rate limiting
    github_requests_per_minute: int = Field(
        default=60,
        ge=1,
        description="Max GitHub API requests per minute",
    )
    # Max concurrent reviews
    max_concurrent_reviews: int = Field(
        default=2,
        ge=1,
        description="Maximum number of concurrent review pipelines",
    )
    # Security tool timeouts (seconds)
    security_tool_timeout: int = Field(
        default=300,
        ge=30,
        description="Timeout for individual security tools (bandit, semgrep, gitleaks)",
    )

    # --- Security ---
    max_diff_lines: int = 500  # Max lines sent to LLM per chunk
    auto_fix_branch_prefix: str = "git_guardian/auto-fix/"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


# Singleton
settings = Settings()
