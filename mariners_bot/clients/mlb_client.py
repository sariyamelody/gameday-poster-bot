"""MLB Stats API client."""

from datetime import UTC, date, datetime, timedelta
from typing import Any

import aiohttp
import structlog
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from ..config import Settings
from ..models import Game, GameStatus, GameType, LiveScoreboardGame, ScoreboardInning, Transaction
from ..models.scoreboard import InningLine

logger = structlog.get_logger(__name__)


def _is_retryable(exc: BaseException) -> bool:
    """Whether a failed MLB API request is worth retrying.

    4xx responses (other than 429, which signals "back off and retry") mean the
    request itself was rejected and a retry will fail the same way — retrying it
    anyway just triples our request volume for nothing. This matters especially
    for a hard block like a 406 from an IP-reputation filter: retrying 3x per
    call instead of 1x makes an already-bad situation worse. 5xx and connection
    errors are transient and worth retrying.
    """
    if isinstance(exc, aiohttp.ClientResponseError):
        return exc.status == 429 or exc.status >= 500
    return isinstance(exc, aiohttp.ClientError | TimeoutError)


class MLBClient:
    """Client for the MLB Stats API."""

    def __init__(self, settings: Settings) -> None:
        """Initialize the MLB client."""
        self.settings = settings
        self.base_url = settings.mlb_api_base_url
        self.team_id = settings.mariners_team_id
        self.session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> "MLBClient":
        """Async context manager entry."""
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30),
            headers={"User-Agent": "mariners-bot/0.1.0"}
        )
        return self

    async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        """Async context manager exit."""
        if self.session:
            await self.session.close()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception(_is_retryable),
    )
    async def _make_request(self, endpoint: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Make a request to the MLB API with retry logic."""
        if not self.session:
            raise RuntimeError("Client not initialized. Use async context manager.")

        url = f"{self.base_url}/{endpoint.lstrip('/')}"

        logger.info("Making MLB API request", url=url, params=params)

        try:
            async with self.session.get(url, params=params) as response:
                response.raise_for_status()
                data = await response.json()

                logger.info("MLB API request successful", status=response.status)
                return data  # type: ignore[no-any-return]

        except aiohttp.ClientError as e:
            logger.error("MLB API request failed", error=str(e), url=url)
            raise
        except TimeoutError:
            logger.error("MLB API request timed out", url=url)
            raise


    async def get_team_schedule_by_day(self, start_date: date, end_date: date) -> list[Game]:
        """Get the Mariners schedule for a date range, one request per day.

        get_team_schedule's single-request-per-gameType approach (filtering via
        `teamId`/`gameType`/`startDate`/`endDate` query params) is no longer
        reliable — MLB's API now rejects `startDate`/`endDate` range queries
        outright (406), and rejects `teamId` without `hydrate`. The `date=<day>`
        (single day, no range) shape is the one still reliably accepted, so this
        fetches one day at a time and filters for Mariners games client-side —
        the same pattern the silly-baseball scoreboard poller already relies on.

        Every game type (regular season, postseason, spring training) comes back
        in the same per-day response, so this also replaces the old per-gameType
        looping — no separate postseason request needed.

        Intended for a bounded lookahead window (days, not months) — callers
        needing the whole remaining season should call this repeatedly as part
        of a daily-rolling sync rather than requesting a huge range up front.
        """
        all_games: list[Game] = []
        current = start_date

        while current <= end_date:
            params = {"sportId": 1, "date": current.isoformat()}
            try:
                data = await self._make_request("schedule", params=params)
                all_games.extend(self._parse_schedule_response(data))
            except Exception as e:
                logger.warning("Failed to fetch schedule for day", date=current.isoformat(), error=str(e))
                # Continue with other days even if one fails
            current += timedelta(days=1)

        unique_games = {game.game_id: game for game in all_games}
        sorted_games = sorted(unique_games.values(), key=lambda g: g.date)

        logger.info(
            "Fetched schedule by day",
            total_games=len(sorted_games),
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
        )

        return sorted_games

    async def get_game_score(self, game_id: str) -> dict[str, Any] | None:
        """Get the current score and status for a specific game."""
        params = {
            "gamePk": game_id,
            "hydrate": "linescore"
        }

        try:
            data = await self._make_request("schedule", params=params)

            for date_entry in data.get("dates", []):
                for game_data in date_entry.get("games", []):
                    if str(game_data.get("gamePk")) == game_id:
                        return self._parse_game_score(game_data)

            logger.warning("Game not found in score data", game_id=game_id)
            return None

        except Exception as e:
            logger.error("Failed to fetch game score", game_id=game_id, error=str(e))
            return None

    def _parse_game_score(self, game_data: dict[str, Any]) -> dict[str, Any] | None:
        """Parse score information from game data."""
        try:
            status_code = game_data.get("status", {}).get("abstractGameCode", "")
            is_final = status_code == "F"

            teams = game_data.get("teams", {})
            home = teams.get("home", {})
            away = teams.get("away", {})

            home_score = home.get("score")
            away_score = away.get("score")
            home_name = home.get("team", {}).get("name", "")
            away_name = away.get("team", {}).get("name", "")
            home_winner = home.get("isWinner", False)
            away_winner = away.get("isWinner", False)

            linescore = game_data.get("linescore", {})
            innings = linescore.get("currentInning")

            return {
                "is_final": is_final,
                "home_team": home_name,
                "away_team": away_name,
                "home_score": home_score,
                "away_score": away_score,
                "home_winner": home_winner,
                "away_winner": away_winner,
                "innings": innings,
            }

        except Exception as e:
            logger.warning("Failed to parse game score", error=str(e))
            return None

    async def get_probable_pitchers(self, game_id: str) -> dict[str, str] | None:
        """Get probable pitchers for a specific game."""
        params = {
            "gamePk": game_id,
            "hydrate": "probablePitcher"
        }

        try:
            data = await self._make_request("schedule", params=params)

            for date_entry in data.get("dates", []):
                for game_data in date_entry.get("games", []):
                    if str(game_data.get("gamePk")) == game_id:
                        return self._parse_probable_pitchers(game_data)

            logger.warning("Game not found in pitcher data", game_id=game_id)
            return None

        except Exception as e:
            logger.error("Failed to fetch probable pitchers", game_id=game_id, error=str(e))
            return None

    def _parse_probable_pitchers(self, game_data: dict[str, Any]) -> dict[str, str] | None:
        """Parse probable pitcher information from game data."""
        try:
            teams = game_data.get("teams", {})
            pitchers = {}

            # Get home pitcher
            home_pitcher = (
                teams.get("home", {})
                .get("probablePitcher", {})
                .get("fullName")
            )

            # Get away pitcher
            away_pitcher = (
                teams.get("away", {})
                .get("probablePitcher", {})
                .get("fullName")
            )

            if home_pitcher:
                pitchers["home"] = home_pitcher
            if away_pitcher:
                pitchers["away"] = away_pitcher

            return pitchers if pitchers else None

        except Exception as e:
            logger.warning("Failed to parse probable pitchers", error=str(e))
            return None

    def _parse_schedule_response(self, data: dict[str, Any], game_type: str = "R") -> list[Game]:
        """Parse the MLB API schedule response into Game objects."""
        games = []

        for date_entry in data.get("dates", []):
            for game_data in date_entry.get("games", []):
                try:
                    game = self._parse_game_data(game_data, game_type)
                    if game and game.is_mariners_game:
                        games.append(game)
                except Exception as e:
                    logger.warning(
                        "Failed to parse game data",
                        game_id=game_data.get("gamePk"),
                        error=str(e)
                    )
                    continue

        logger.info("Parsed schedule", total_games=len(games), game_type=game_type)
        return games

    def _parse_game_data(self, game_data: dict[str, Any], game_type: str = "R") -> Game | None:
        """Parse individual game data from MLB API response.

        `game_type` is a fallback for callers that already know every game in the
        response is one type (e.g. the old gameType-filtered query). Callers that
        fetch a whole day's slate (get_team_schedule_by_day) get a mix of types
        back, so the game's own "gameType" field — present on every entry — takes
        precedence when available.
        """
        try:
            # Extract basic game information
            game_id = str(game_data["gamePk"])
            game_date_str = game_data["gameDate"]
            game_type = game_data.get("gameType", game_type)

            # Parse the datetime (MLB API returns ISO format with timezone)
            game_date = datetime.fromisoformat(
                game_date_str.replace("Z", "+00:00")
            ).replace(tzinfo=UTC)

            # Extract team information
            teams = game_data["teams"]
            home_team = teams["home"]["team"]["name"]
            away_team = teams["away"]["team"]["name"]

            # Extract venue information
            venue = game_data.get("venue", {}).get("name", "Unknown Venue")

            # Parse game status
            status_code = game_data.get("status", {}).get("abstractGameCode", "S")
            status = self._parse_game_status(status_code)

            return Game(
                game_id=game_id,
                date=game_date,
                home_team=home_team,
                away_team=away_team,
                venue=venue,
                status=status,
                game_type=self._parse_game_type(game_type)  # Convert string to GameType enum
            )

        except (KeyError, ValueError, TypeError) as e:
            logger.error("Failed to parse game data", error=str(e), data=game_data)
            return None

    def _parse_game_status(self, status_code: str) -> GameStatus:
        """Parse MLB game status code to our GameStatus enum."""
        status_mapping = {
            "S": GameStatus.SCHEDULED,  # Scheduled
            "P": GameStatus.SCHEDULED,  # Pre-Game
            "L": GameStatus.LIVE,       # Live
            "F": GameStatus.FINAL,      # Final
            "D": GameStatus.POSTPONED,  # Delayed/Postponed
            "C": GameStatus.CANCELLED,  # Cancelled
        }

        return status_mapping.get(status_code, GameStatus.SCHEDULED)

    def _parse_game_type(self, game_type: str) -> GameType:
        """Parse MLB game type string to our GameType enum."""
        type_mapping = {
            "R": GameType.REGULAR,
            "S": GameType.SPRING,
            "P": GameType.POSTSEASON,
            "D": GameType.DIVISION_SERIES,
            "L": GameType.LEAGUE_CHAMPIONSHIP,
            "F": GameType.CHAMPIONSHIP,
            "W": GameType.WORLD_SERIES,
        }

        return type_mapping.get(game_type, GameType.REGULAR)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception(_is_retryable),
    )
    async def get_live_game_feed(self, game_pk: int) -> dict[str, Any] | None:
        """Fetch the live game feed from the MLB Stats API v1.1 endpoint.

        This is a separate endpoint from the base v1 API and returns real-time
        play-by-play data including allPlays, linescore, and game state.
        """
        if not self.session:
            raise RuntimeError("Client not initialized. Use async context manager.")

        url = f"https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live"

        logger.debug("Fetching live game feed", game_pk=game_pk)

        try:
            async with self.session.get(url) as response:
                response.raise_for_status()
                data = await response.json()
                logger.debug("Live game feed fetched", game_pk=game_pk)
                return data  # type: ignore[no-any-return]

        except aiohttp.ClientError as e:
            logger.error("Failed to fetch live game feed", game_pk=game_pk, error=str(e))
            raise
        except TimeoutError:
            logger.error("Live game feed request timed out", game_pk=game_pk)
            raise

    async def get_league_scoreboard(self, target_date: date) -> list[LiveScoreboardGame]:
        """Get every MLB game's current state for a given day, in one request.

        Used by the 'Silly Baseball Is Happening' poller — covers all games
        league-wide via linescore data, with no per-game live-feed polling.
        Regular season and postseason games only (spring training excluded).
        """
        params = {
            "sportId": 1,
            "date": target_date.isoformat(),
            "hydrate": "linescore",
        }

        try:
            data = await self._make_request("schedule", params=params)
        except Exception as e:
            logger.error("Failed to fetch league scoreboard", date=target_date.isoformat(), error=str(e))
            raise

        games = []
        for date_entry in data.get("dates", []):
            for game_data in date_entry.get("games", []):
                game_type = game_data.get("gameType", "R")
                if game_type not in ("R", "P", "D", "L", "F", "W"):
                    continue  # skip spring training / exhibition / all-star
                try:
                    game = self._parse_scoreboard_game(game_data)
                    if game:
                        games.append(game)
                except Exception as e:
                    logger.warning(
                        "Failed to parse scoreboard game",
                        game_pk=game_data.get("gamePk"),
                        error=str(e)
                    )
                    continue

        return games

    def _parse_scoreboard_game(self, game_data: dict[str, Any]) -> LiveScoreboardGame | None:
        """Parse one game entry from the league scoreboard response."""
        try:
            teams = game_data["teams"]
            home = teams["home"]
            away = teams["away"]

            linescore = game_data.get("linescore", {})
            ls_teams = linescore.get("teams", {})

            innings = [
                ScoreboardInning(
                    num=inning["num"],
                    home=InningLine(**inning.get("home", {})),
                    away=InningLine(**inning.get("away", {})),
                )
                for inning in linescore.get("innings", [])
            ]

            defense = linescore.get("defense", {})
            pitcher = defense.get("pitcher", {})

            raw_game_date = game_data.get("gameDate")
            game_date = (
                datetime.fromisoformat(raw_game_date.replace("Z", "+00:00")) if raw_game_date else None
            )

            return LiveScoreboardGame(
                game_pk=game_data["gamePk"],
                game_type=game_data.get("gameType", "R"),
                is_live=game_data.get("status", {}).get("abstractGameState") == "Live",
                game_date=game_date,
                home_team=home["team"]["name"],
                away_team=away["team"]["name"],
                home_score=ls_teams.get("home", {}).get("runs", home.get("score", 0)) or 0,
                away_score=ls_teams.get("away", {}).get("runs", away.get("score", 0)) or 0,
                current_inning=linescore.get("currentInning"),
                is_top_inning=linescore.get("isTopInning"),
                scheduled_innings=linescore.get("scheduledInnings", 9) or 9,
                innings=innings,
                current_pitcher_id=pitcher.get("id"),
                current_pitcher_name=pitcher.get("fullName"),
            )
        except (KeyError, ValueError, TypeError) as e:
            logger.error("Failed to parse scoreboard game data", error=str(e), data=game_data)
            return None

    async def get_active_player_positions(self, season: int) -> dict[int, str]:
        """Bulk-fetch primary position abbreviations for all active players in a season.

        Used to build a same-day cache so the silly-baseball poller doesn't need a
        network call per unfamiliar pitcher id — this one call covers everyone.
        """
        try:
            data = await self._make_request("sports/1/players", params={"season": season})
        except Exception as e:
            logger.error("Failed to fetch active player positions", season=season, error=str(e))
            raise

        positions: dict[int, str] = {}
        for person in data.get("people", []):
            person_id = person.get("id")
            abbreviation = person.get("primaryPosition", {}).get("abbreviation")
            if person_id is not None and abbreviation:
                positions[person_id] = abbreviation

        return positions

    async def get_player_primary_position(self, person_id: int) -> str | None:
        """Fetch a single player's primary position abbreviation (fallback lookup).

        Used only when a pitcher id isn't in the daily-synced position cache
        (e.g. a same-day call-up).
        """
        try:
            data = await self._make_request(f"people/{person_id}")
            people = data.get("people", [])
            if not people:
                return None
            abbreviation: str | None = people[0].get("primaryPosition", {}).get("abbreviation")
            return abbreviation
        except Exception as e:
            logger.warning("Failed to fetch player primary position", person_id=person_id, error=str(e))
            return None

    async def get_team_transactions(
        self,
        team_id: int | None = None,
        start_date: date | None = None,
        end_date: date | None = None
    ) -> list[Transaction]:
        """Get transactions for a team within a date range."""
        params: dict[str, Any] = {}

        if team_id:
            params["teamId"] = team_id

        if start_date:
            params["startDate"] = start_date.isoformat()

        if end_date:
            params["endDate"] = end_date.isoformat()

        try:
            data = await self._make_request("transactions", params=params)
            return self._parse_transactions_response(data)

        except Exception as e:
            logger.error("Failed to fetch team transactions", error=str(e))
            raise

    async def get_mariners_transactions(
        self,
        start_date: date | None = None,
        end_date: date | None = None
    ) -> list[Transaction]:
        """Get Mariners transactions within a date range."""
        return await self.get_team_transactions(
            team_id=self.team_id,
            start_date=start_date,
            end_date=end_date
        )

    def _parse_transactions_response(self, data: dict[str, Any]) -> list[Transaction]:
        """Parse the MLB API transactions response into Transaction objects."""
        transactions = []

        for transaction_data in data.get("transactions", []):
            try:
                transaction = self._parse_transaction_data(transaction_data)
                if transaction:
                    transactions.append(transaction)
            except Exception as e:
                logger.warning(
                    "Failed to parse transaction data",
                    transaction_id=transaction_data.get("id"),
                    error=str(e)
                )
                continue

        logger.info("Parsed transactions", total_transactions=len(transactions))
        return transactions

    def _parse_transaction_data(self, transaction_data: dict[str, Any]) -> Transaction | None:
        """Parse individual transaction data from MLB API response."""
        try:
            # Extract basic transaction information
            transaction_id = transaction_data["id"]

            # Person information
            person = transaction_data["person"]
            person_id = person["id"]
            person_name = person["fullName"]

            # Team information
            from_team_id = None
            from_team_name = None
            to_team_id = None
            to_team_name = None

            if "fromTeam" in transaction_data:
                from_team = transaction_data["fromTeam"]
                from_team_id = from_team["id"]
                from_team_name = from_team["name"]

            if "toTeam" in transaction_data:
                to_team = transaction_data["toTeam"]
                to_team_id = to_team["id"]
                to_team_name = to_team["name"]

            # Date information
            transaction_date = datetime.fromisoformat(
                transaction_data["date"]
            ).date()

            effective_date = None
            if "effectiveDate" in transaction_data:
                effective_date = datetime.fromisoformat(
                    transaction_data["effectiveDate"]
                ).date()

            resolution_date = None
            if "resolutionDate" in transaction_data:
                resolution_date = datetime.fromisoformat(
                    transaction_data["resolutionDate"]
                ).date()

            # Transaction type and description
            type_code = transaction_data["typeCode"]
            type_description = transaction_data["typeDesc"]
            description = transaction_data["description"]

            return Transaction(
                transaction_id=transaction_id,
                person_id=person_id,
                person_name=person_name,
                from_team_id=from_team_id,
                from_team_name=from_team_name,
                to_team_id=to_team_id,
                to_team_name=to_team_name,
                transaction_date=transaction_date,
                effective_date=effective_date,
                resolution_date=resolution_date,
                type_code=type_code,
                type_description=type_description,
                description=description
            )

        except (KeyError, ValueError, TypeError) as e:
            logger.error("Failed to parse transaction data", error=str(e), data=transaction_data)
            return None
