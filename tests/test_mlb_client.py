"""Tests for MLB API client."""

from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest

from mariners_bot.clients import MLBClient
from mariners_bot.clients.mlb_client import _is_retryable
from mariners_bot.config import Settings
from mariners_bot.models import GameStatus, GameType


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
            "gameDate": "2026-09-02T23:40:00Z",
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
        assert game.game_date == datetime(2026, 9, 2, 23, 40, tzinfo=UTC)
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

    def test_parse_game_data_prefers_own_gameType_over_fallback(self) -> None:
        """A game's own `gameType` field wins over the caller-supplied default.

        get_team_schedule_by_day fetches a whole day's slate in one request (no
        gameType filter), so it can't assume every game is the same type the way
        the old per-gameType-filtered query could.
        """
        settings = Settings(telegram_bot_token="test")
        client = MLBClient(settings)

        game_data = {
            "gamePk": 776428,
            "gameDate": "2025-09-07T16:05:00Z",
            "gameType": "P",
            "teams": {
                "home": {"team": {"name": "Atlanta Braves"}},
                "away": {"team": {"name": "Seattle Mariners"}}
            },
            "venue": {"name": "Truist Park"},
            "status": {"abstractGameCode": "S"}
        }

        game = client._parse_game_data(game_data, game_type="R")

        assert game is not None
        assert game.game_type == GameType.POSTSEASON

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


class TestIsRetryable:
    """Test the retry predicate that decides whether a failed MLB API request is worth retrying.

    4xx responses other than 429 mean the request was rejected outright — retrying
    just triples our request volume against a server that already said no. This
    matters a lot for a hard block like a 406 from an IP-reputation filter, where
    retrying makes an already-bad situation worse instead of better.
    """

    def test_5xx_is_retryable(self) -> None:
        error = aiohttp.ClientResponseError(request_info=None, history=(), status=500)  # type: ignore[arg-type]
        assert _is_retryable(error) is True

    def test_429_is_retryable(self) -> None:
        error = aiohttp.ClientResponseError(request_info=None, history=(), status=429)  # type: ignore[arg-type]
        assert _is_retryable(error) is True

    def test_406_is_not_retryable(self) -> None:
        error = aiohttp.ClientResponseError(request_info=None, history=(), status=406)  # type: ignore[arg-type]
        assert _is_retryable(error) is False

    def test_404_is_not_retryable(self) -> None:
        error = aiohttp.ClientResponseError(request_info=None, history=(), status=404)  # type: ignore[arg-type]
        assert _is_retryable(error) is False

    def test_timeout_is_retryable(self) -> None:
        assert _is_retryable(TimeoutError()) is True

    def test_connection_error_is_retryable(self) -> None:
        assert _is_retryable(aiohttp.ClientConnectionError()) is True

    def test_unrelated_exception_is_not_retryable(self) -> None:
        assert _is_retryable(ValueError("not an API error")) is False


class TestGetTeamScheduleByDay:
    """Test the day-by-day schedule fetch that replaced the range-query approach.

    MLB's /schedule endpoint now rejects startDate/endDate range queries outright
    (406) — verified live, repeatedly, against the real API — so this fetches one
    date=<day> request per day in the window instead, which is still reliably
    accepted, and filters for Mariners games client-side.
    """

    @pytest.mark.asyncio
    async def test_fetches_one_request_per_day_and_dedups(self) -> None:
        settings = Settings(telegram_bot_token="test")
        client = MLBClient(settings)

        def make_response(day: str, mariners_game: bool) -> dict[str, Any]:
            return {
                "dates": [
                    {
                        "games": [
                            {
                                "gamePk": 1 if mariners_game else 2,
                                "gameDate": f"{day}T23:00:00Z",
                                "gameType": "R",
                                "teams": {
                                    "home": {"team": {"name": "Seattle Mariners" if mariners_game else "Boston Red Sox"}},
                                    "away": {"team": {"name": "Houston Astros"}},
                                },
                                "venue": {"name": "T-Mobile Park"},
                                "status": {"abstractGameCode": "S"},
                            }
                        ]
                    }
                ]
            }

        responses = {
            "2026-09-02": make_response("2026-09-02", mariners_game=True),
            "2026-09-03": make_response("2026-09-03", mariners_game=False),
        }

        with patch.object(client, "_make_request", new=AsyncMock(side_effect=lambda _endpoint, params: responses[params["date"]])) as mock_request:
            games = await client.get_team_schedule_by_day(date(2026, 9, 2), date(2026, 9, 3))

        assert mock_request.call_count == 2
        assert {call.kwargs["params"]["date"] for call in mock_request.call_args_list} == {"2026-09-02", "2026-09-03"}
        # Only the Mariners game survives the client-side filter
        assert len(games) == 1
        assert games[0].is_mariners_game

    @pytest.mark.asyncio
    async def test_one_bad_day_does_not_abort_the_rest(self) -> None:
        settings = Settings(telegram_bot_token="test")
        client = MLBClient(settings)

        good_response = {
            "dates": [
                {
                    "games": [
                        {
                            "gamePk": 1,
                            "gameDate": "2026-09-03T23:00:00Z",
                            "gameType": "R",
                            "teams": {
                                "home": {"team": {"name": "Seattle Mariners"}},
                                "away": {"team": {"name": "Houston Astros"}},
                            },
                            "venue": {"name": "T-Mobile Park"},
                            "status": {"abstractGameCode": "S"},
                        }
                    ]
                }
            ]
        }

        async def flaky_request(_endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
            if params["date"] == "2026-09-02":
                raise ValueError("406 Not Acceptable")
            return good_response

        with patch.object(client, "_make_request", new=AsyncMock(side_effect=flaky_request)):
            games = await client.get_team_schedule_by_day(date(2026, 9, 2), date(2026, 9, 3))

        assert len(games) == 1
        assert games[0].game_id == "1"
