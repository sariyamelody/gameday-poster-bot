"""Tests for configuration management."""

import os
from unittest.mock import patch

from mariners_bot.config import Settings, get_settings


class TestSettings:
    """Test Settings configuration."""

    def test_default_settings(self) -> None:
        """Test default configuration values."""
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "test_token"}, clear=True):
            settings = Settings()

            assert settings.telegram_bot_token == "test_token"
            assert settings.telegram_chat_id is None
            assert settings.mlb_api_base_url == "https://statsapi.mlb.com/api/v1"
            assert settings.mariners_team_id == 136
            assert settings.database_url == "sqlite:///data/mariners_bot.db"
            assert settings.scheduler_timezone == "America/Los_Angeles"
            assert settings.notification_advance_minutes == 5
            assert settings.schedule_sync_hour == 6
            assert settings.log_level == "INFO"
            assert settings.otel_service_name == "mariners-bot"
            assert settings.health_check_port == 8000
            assert settings.debug is False
            assert settings.environment == "production"

    def test_environment_override(self) -> None:
        """Test environment variable overrides."""
        env_vars = {
            "TELEGRAM_BOT_TOKEN": "custom_token",
            "TELEGRAM_CHAT_ID": "-1001234567890",
            "MARINERS_TEAM_ID": "999",
            "NOTIFICATION_ADVANCE_MINUTES": "10",
            "LOG_LEVEL": "DEBUG",
            "DEBUG": "true",
            "ENVIRONMENT": "development"
        }

        with patch.dict(os.environ, env_vars, clear=True):
            settings = Settings()

            assert settings.telegram_bot_token == "custom_token"
            assert settings.telegram_chat_id == "-1001234567890"
            assert settings.mariners_team_id == 999
            assert settings.notification_advance_minutes == 10
            assert settings.log_level == "DEBUG"
            assert settings.debug is True
            assert settings.environment == "development"

    def test_get_settings_singleton(self) -> None:
        """Test that get_settings returns the same instance."""
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "test"}, clear=True):
            settings1 = get_settings()
            settings2 = get_settings()

            # Should be the same instance
            assert settings1 is settings2
