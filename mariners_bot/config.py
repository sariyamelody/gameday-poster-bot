"""Configuration management for the Mariners bot."""


from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Telegram Bot Configuration
    telegram_bot_token: str = Field(default="", env="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str | None = Field(default=None, env="TELEGRAM_CHAT_ID")

    # MLB API Configuration
    mlb_api_base_url: str = Field(default="https://statsapi.mlb.com/api/v1", env="MLB_API_BASE_URL")
    mariners_team_id: int = Field(default=136, env="MARINERS_TEAM_ID")

    # Database Configuration
    database_url: str = Field(default="sqlite:///data/mariners_bot.db", env="DATABASE_URL")

    # Scheduler Configuration
    scheduler_timezone: str = Field(default="America/Los_Angeles", env="SCHEDULER_TIMEZONE")
    notification_advance_minutes: int = Field(default=5, env="NOTIFICATION_ADVANCE_MINUTES")
    schedule_sync_hour: int = Field(default=6, env="SCHEDULE_SYNC_HOUR")  # 6 AM PT

    # Observability Configuration
    log_level: str = Field(default="INFO", env="LOG_LEVEL")
    otel_exporter_endpoint: str | None = Field(default=None, env="OTEL_EXPORTER_ENDPOINT")
    otel_service_name: str = Field(default="mariners-bot", env="OTEL_SERVICE_NAME")

    # Health Check Configuration
    health_check_port: int = Field(default=8000, env="HEALTH_CHECK_PORT")

    # Application Configuration
    debug: bool = Field(default=False, env="DEBUG")
    environment: str = Field(default="production", env="ENVIRONMENT")

    class Config:
        """Pydantic configuration."""

        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


# Global settings instance
_settings: Settings | None = None


def get_settings() -> Settings:
    """Get the global settings instance."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
