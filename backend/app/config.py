
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        case_sensitive=False
    )

    env: str = "dev"

    database_url: str = "postgresql+asyncpg://tradewise:change-me@db:5432/tradewise"
    redis_url: str = "redis://redis:6379/0"

    agent_jwt_secret: str = "change-me-agent"
    customer_jwt_secret: str = "change-me-customer"

    # Load API key from environment
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash"

    cors_origins: str = "http://localhost:8080"

    auto_create_tables: bool = True
    bot_rate_limit_per_min: int = 12
    agent_wait_timeout_seconds: int = 45

    stun_urls: str = "stun:stun.l.google.com:19302"
    turn_url: str = ""
    turn_username: str = ""
    turn_credential: str = ""

    bot_global_rate_limit_per_min: int = 120
    static_dir: str = ""
    seed_agents: str = ""
    max_active_chats_per_agent: int = 1


settings = Settings()
