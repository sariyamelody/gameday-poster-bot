"""Tests for MLB API client."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from mariners_bot.clients import MLBClient
from mariners_bot.config import Settings
from mariners_bot.models import GameStatus


class TestMLBClient:
    """Test MLB API client."""

    def test_client_initialization(self) -> None:
        """Test client initialization."""
        settings = Settings(telegram_bot_token="test")
        client = MLBClient(settings)

        assert client.settings == settings
        assert client.base_url == "https://statsapi.mlb.com/api/v1"
        assert client.team_id == 136
        assert client.session is None

    def test_parse_game_status(self) -> None:
        """Test game status parsing."""
        settings = Settings(telegram_bot_token="test")
        client = MLBClient(settings)

        assert client._parse_game_status("S") == GameStatus.SCHEDULED
        assert client._parse_game_status("P") == GameStatus.SCHEDULED
        assert client._parse_game_status("L") == GameStatus.LIVE
        assert client._parse_game_status("F") == GameStatus.FINAL
        assert client._parse_game_status("D") == GameStatus.POSTPONED
        assert client._parse_game_status("C") == GameStatus.CANCELLED
        assert client._parse_game_status("UNKNOWN") == GameStatus.SCHEDULED  # Default

    def test_parse_game_data(self) -> None:
        """Test parsing individual game data."""
        settings = Settings(telegram_bot_token="test")
        client = MLBClient(settings)

        game_data = {
            "gamePk": 776428,
            "gameDate": "2025-09-07T16:05:00Z",
            "teams": {
                "home": {"team": {"name": "Atlanta Braves"}},
                "away": {"team": {"name": "Seattle Mariners"}}
            },
            "venue": {"name": "Truist Park"},
            "status": {"abstractGameCode": "S"}
        }

        game = client._parse_game_data(game_data)

        assert game is not None
        assert game.game_id == "776428"
        assert game.date == datetime(2025, 9, 7, 16, 5, tzinfo=UTC)
        assert game.home_team == "Atlanta Braves"
        assert game.away_team == "Seattle Mariners"
        assert game.venue == "Truist Park"
        assert game.status == GameStatus.SCHEDULED
        assert game.is_mariners_game

    def test_parse_game_data_invalid(self) -> None:
        """Test parsing invalid game data."""
        settings = Settings(telegram_bot_token="test")
        client = MLBClient(settings)

        # Missing required fields
        invalid_data = {"gamePk": 12345}

        game = client._parse_game_data(invalid_data)
        assert game is None

    def test_parse_schedule_response(self) -> None:
        """Test parsing full schedule response."""
        settings = Settings(telegram_bot_token="test")
        client = MLBClient(settings)

        schedule_data = {
            "dates": [
                {
                    "games": [
                        {
                            "gamePk": 776428,
                            "gameDate": "2025-09-07T16:05:00Z",
                            "teams": {
                                "home": {"team": {"name": "Atlanta Braves"}},
                                "away": {"team": {"name": "Seattle Mariners"}}
                            },
                            "venue": {"name": "Truist Park"},
                            "status": {"abstractGameCode": "S"}
                        },
                        # Non-Mariners game (should be filtered out)
                        {
                            "gamePk": 999999,
                            "gameDate": "2025-09-07T19:00:00Z",
                            "teams": {
                                "home": {"team": {"name": "Boston Red Sox"}},
                                "away": {"team": {"name": "New York Yankees"}}
                            },
                            "venue": {"name": "Fenway Park"},
                            "status": {"abstractGameCode": "S"}
                        }
                    ]
                }
            ]
        }

        games = client._parse_schedule_response(schedule_data)

        # Should only return Mariners games
        assert len(games) == 1
        assert games[0].game_id == "776428"
        assert games[0].is_mariners_game

    @pytest.mark.asyncio
    async def test_async_context_manager(self) -> None:
        """Test async context manager functionality."""
        settings = Settings(telegram_bot_token="test")

        with patch('aiohttp.ClientSession') as mock_session_class:
            mock_session = AsyncMock()
            mock_session_class.return_value = mock_session

            async with MLBClient(settings) as client:
                assert client.session == mock_session
                mock_session_class.assert_called_once()

            # Session should be closed when exiting context
            mock_session.close.assert_called_once()
