"""Configuration management for the Mariners bot."""


from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Telegram Bot Configuration
    telegram_bot_token: str = Field(default="")
    telegram_chat_id: str | None = Field(default=None)

    # MLB API Configuration
    mlb_api_base_url: str = Field(default="https://statsapi.mlb.com/api/v1")
    mariners_team_id: int = Field(default=136)

    # Database Configuration
    database_url: str = Field(default="sqlite:///data/mariners_bot.db")

    # Scheduler Configuration
    scheduler_timezone: str = Field(default="America/Los_Angeles")
    notification_advance_minutes: int = Field(default=5)
    schedule_sync_hour: int = Field(default=6)  # 6 AM PT

    # Observability Configuration
    log_level: str = Field(default="INFO")
    otel_service_name: str = Field(default="mariners-bot")
    otel_traces_to_stdout: bool = Field(default=False)
    otel_traces_exporter: str = Field(default="none")  # none, console, otlp

    # Health Check Configuration
    health_check_port: int = Field(default=8000)

    # Application Configuration
    debug: bool = Field(default=False)
    environment: str = Field(default="production")

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
    }



# Global settings instance
_settings: Settings | None = None


def get_settings() -> Settings:
    """Get the global settings instance."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
