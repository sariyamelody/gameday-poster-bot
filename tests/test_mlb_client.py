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

    def test_parse_scoreboard_game(self) -> None:
        """Test parsing a scoreboard entry (real API shape, trimmed)."""
        settings = Settings(telegram_bot_token="test")
        client = MLBClient(settings)

        game_data = {
            "gamePk": 822942,
            "gameType": "R",
            "status": {"abstractGameState": "Live"},
            "teams": {
                "away": {"team": {"name": "Baltimore Orioles"}, "score": 6},
                "home": {"team": {"name": "Tampa Bay Rays"}, "score": 2},
            },
            "linescore": {
                "currentInning": 9,
                "isTopInning": False,
                "scheduledInnings": 9,
                "innings": [
                    {
                        "num": 1,
                        "home": {"runs": 0, "hits": 1, "errors": 0},
                        "away": {"runs": 0, "hits": 0, "errors": 0},
                    },
                    {
                        "num": 2,
                        "home": {"runs": 1, "hits": 3, "errors": 1},
                        "away": {"runs": 1, "hits": 1, "errors": 0},
                    },
                ],
                "teams": {
                    "home": {"runs": 2, "hits": 8, "errors": 1},
                    "away": {"runs": 6, "hits": 12, "errors": 0},
                },
                "defense": {
                    "pitcher": {"id": 666974, "fullName": "Yennier Cano"},
                },
            },
        }

        game = client._parse_scoreboard_game(game_data)

        assert game is not None
        assert game.game_pk == 822942
        assert game.is_live is True
        assert game.home_team == "Tampa Bay Rays"
        assert game.away_team == "Baltimore Orioles"
        assert game.home_score == 2
        assert game.away_score == 6
        assert game.current_inning == 9
        assert game.is_top_inning is False
        assert game.current_pitcher_id == 666974
        assert game.current_pitcher_name == "Yennier Cano"
        assert len(game.innings) == 2
        assert game.innings[1].home.errors == 1  # home team's error, attributed to the top half

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
