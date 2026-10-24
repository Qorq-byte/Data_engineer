"""Pydantic Settings loaded from .env file.

All runtime configuration flows through this module.
"""

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables / .env file."""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    # --- App ---
    app_env: str = "development"
    app_debug: bool = True
    app_host: str = "0.0.0.0"
    app_port: int = 8001

    # --- Database ---
    database_url: str = "sqlite:///./data/app.db"
    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_user: str = "root"
    mysql_password: str = ""
    mysql_database: str = "nl2sql_engine"

    # --- LLM API Keys (optional in dev) ---
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    google_api_key: str | None = None
    deepseek_api_key: str | None = None

    # --- LLM Routing ---
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    llm_mock_mode: bool = False  # force mock router (no real LLM calls)

    llm_max_concurrency: int = Field(default=4, ge=1)
    llm_max_retries: int = Field(default=1, ge=0, le=3)
    llm_timeout_seconds: float = Field(default=30, gt=0)
    llm_cooldown_seconds: float = Field(default=5, ge=0)
    llm_rate_limit_rpm: int = Field(default=60, ge=0)

    query_timeout_seconds: float = Field(default=120, gt=0)
    max_concurrent_queries: int = Field(default=8, ge=1)
    schema_cache_seconds: float = Field(default=30, ge=0)
    session_max_entries: int = Field(default=1000, ge=1)
    session_max_versions: int = Field(default=100, ge=1)
    session_max_turns: int = Field(default=50, ge=1)
    session_ttl_seconds: int = Field(default=3600, gt=0)

    # --- Optional local request usage audit ---
    llm_audit_path: str = ""  # Empty disables persistence
    llm_audit_max_records: int = Field(default=1000, ge=1, le=10000)

    # --- RAG Embedding ---
    rag_embedding_provider: str = "ollama"  # ollama | openai | bge | mock
    rag_lancedb_dir: str = ""  # Empty uses data/lancedb; switch for explicit versioned rebuilds
    rag_bm25_path: str = ""  # Empty uses data/bm25.db; migrate together with the vector index
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_embedding_model: str = "embeddinggemma"
    ollama_embedding_dim: int = 768

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

    admin_user_ids: list[int] = [1]
    registration_token: str | None = None
    cors_origins: list[str] = ["http://localhost:8001", "http://127.0.0.1:8001"]

    # --- Auth: JWT ---
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440  # 24 hours

    # --- Agent Config ---
    agent_config_path: str = "app/config/agent.yml"


# Global singleton
settings = Settings()
