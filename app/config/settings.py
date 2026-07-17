"""Pydantic Settings loaded from .env file.

All runtime configuration flows through this module.
"""

from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables / .env file."""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    # --- App ---
    app_env: str = "development"
    app_debug: bool = True
    app_host: str = "0.0.0.0"
    app_port: int = 8000

    # --- Database ---
    database_url: str = "sqlite:///./data/app.db"

    # --- LLM API Keys (optional in dev) ---
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    google_api_key: str | None = None
    deepseek_api_key: str | None = None

    # --- LLM Routing ---
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    llm_mock_mode: bool = False  # force mock router (no real LLM calls)

    @field_validator("llm_mock_mode", mode="before")
    @classmethod
    def _empty_str_is_false(cls, v: object) -> object:
        """Tolerate blank env values (``LLM_MOCK_MODE=``) as False."""
        if isinstance(v, str) and not v.strip():
            return False
        return v

    # --- MCP ---
    mcp_server_host: str = "0.0.0.0"
    mcp_server_port: int = 8080

    # --- Safety Constraints (match agent.yml constraints) ---
    max_result_rows: int = 1000
    statement_timeout_ms: int = 30000
    read_only: bool = True

    # --- Auth: MySQL ---
    auth_mysql_host: str = "127.0.0.1"
    auth_mysql_port: int = 3306
    auth_mysql_user: str = "root"
    auth_mysql_password: str = ""
    auth_mysql_database: str = "nl2sql_auth"

    # --- Auth: JWT ---
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440  # 24 hours

    # --- Agent Config ---
    agent_config_path: str = "app/config/agent.yml"


# Global singleton
settings = Settings()
