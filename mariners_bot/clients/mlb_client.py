"""MLB Stats API client."""

from datetime import UTC, datetime

import aiohttp
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from ..config import Settings
from ..models import Game, GameStatus

logger = structlog.get_logger(__name__)


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

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit."""
        if self.session:
            await self.session.close()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10)
    )
    async def _make_request(self, endpoint: str, params: dict | None = None) -> dict:
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
                return data

        except aiohttp.ClientError as e:
            logger.error("MLB API request failed", error=str(e), url=url)
            raise
        except TimeoutError:
            logger.error("MLB API request timed out", url=url)
            raise

    async def get_team_schedule(
        self,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        season: int | None = None
    ) -> list[Game]:
        """Get the Mariners schedule for a date range."""
        params = {
            "teamId": self.team_id,
            "sportId": 1,  # MLB
        }

        if season:
            params["season"] = season
        else:
            # Default to current year
            params["season"] = datetime.now().year

        if start_date:
            params["startDate"] = start_date.strftime("%Y-%m-%d")

        if end_date:
            params["endDate"] = end_date.strftime("%Y-%m-%d")

        try:
            data = await self._make_request("schedule", params=params)
            return self._parse_schedule_response(data)

        except Exception as e:
            logger.error("Failed to fetch team schedule", error=str(e))
            raise

    async def get_game_details(self, game_id: str) -> Game | None:
        """Get detailed information for a specific game."""
        params = {
            "gamePk": game_id,
            "hydrate": "team,linescore"
        }

        try:
            data = await self._make_request("schedule", params=params)
            games = self._parse_schedule_response(data)
            return games[0] if games else None

        except Exception as e:
            logger.error("Failed to fetch game details", game_id=game_id, error=str(e))
            return None

    def _parse_schedule_response(self, data: dict) -> list[Game]:
        """Parse the MLB API schedule response into Game objects."""
        games = []

        for date_entry in data.get("dates", []):
            for game_data in date_entry.get("games", []):
                try:
                    game = self._parse_game_data(game_data)
                    if game and game.is_mariners_game:
                        games.append(game)
                except Exception as e:
                    logger.warning(
                        "Failed to parse game data",
                        game_id=game_data.get("gamePk"),
                        error=str(e)
                    )
                    continue

        logger.info("Parsed schedule", total_games=len(games))
        return games

    def _parse_game_data(self, game_data: dict) -> Game | None:
        """Parse individual game data from MLB API response."""
        try:
            # Extract basic game information
            game_id = str(game_data["gamePk"])
            game_date_str = game_data["gameDate"]

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
                status=status
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
