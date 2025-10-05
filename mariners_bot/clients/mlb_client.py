"""MLB Stats API client."""

from datetime import UTC, date, datetime
from typing import Any

import aiohttp
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from ..config import Settings
from ..models import Game, GameStatus, GameType, Transaction

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

    async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        """Async context manager exit."""
        if self.session:
            await self.session.close()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10)
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

    async def get_team_schedule(
        self,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        season: int | None = None,
        game_types: list[str] | None = None
    ) -> list[Game]:
        """Get the Mariners schedule for a date range.
        
        Args:
            start_date: Start date for schedule
            end_date: End date for schedule  
            season: Season year
            game_types: List of game types to include. Options:
                       'R' = Regular season, 'S' = Spring training, 
                       'P' = Postseason, 'D' = Division Series,
                       'L' = League Championship, 'F' = Championship Series,
                       'W' = World Series
                       Defaults to all types
        """
        if game_types is None:
            game_types = ['R', 'S', 'P', 'D', 'L', 'F', 'W']  # Include all game types by default
            
        all_games = []
        
        # Fetch games for each game type separately since API doesn't support multiple gameTypes
        for game_type in game_types:
            try:
                if game_type in ['P', 'D', 'L', 'F', 'W']:  # All postseason game types
                    # For postseason games, we need to fetch all games and filter for Mariners
                    # because the API may not return postseason games when filtering by teamId
                    params: dict[str, Any] = {
                        "sportId": 1,  # MLB
                        "gameType": game_type,
                    }
                else:
                    # For regular season and spring training, use teamId filter
                    params: dict[str, Any] = {
                        "teamId": self.team_id,
                        "sportId": 1,  # MLB
                        "gameType": game_type,
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

                logger.debug("Fetching schedule", game_type=game_type, params=params)
                data = await self._make_request("schedule", params=params)
                games = self._parse_schedule_response(data, game_type)
                
                # For postseason games, we need to filter for Mariners games since we fetched all teams
                if game_type in ['P', 'D', 'L', 'F', 'W']:
                    mariners_games = [game for game in games if game.is_mariners_game]
                    all_games.extend(mariners_games)
                    logger.debug("Fetched and filtered postseason games", 
                               game_type=game_type, 
                               total_games=len(games),
                               mariners_games=len(mariners_games))
                else:
                    all_games.extend(games)
                    logger.debug("Fetched games", game_type=game_type, count=len(games))
                
            except Exception as e:
                logger.warning("Failed to fetch schedule for game type", 
                             game_type=game_type, error=str(e))
                # Continue with other game types even if one fails
                continue
        
        # Remove duplicates based on game_id and sort by date
        unique_games = {}
        for game in all_games:
            unique_games[game.game_id] = game
            
        sorted_games = sorted(unique_games.values(), key=lambda g: g.date)
        
        logger.info("Fetched complete schedule", 
                   total_games=len(sorted_games), 
                   game_types=game_types)
        
        return sorted_games

    async def get_game_details(self, game_id: str) -> Game | None:
        """Get detailed information for a specific game."""
        params = {
            "gamePk": game_id,
            "hydrate": "team,linescore"
        }

        try:
            data = await self._make_request("schedule", params=params)
            # For game details, we don't know the game type, so we'll try to infer it
            # from the response or default to regular season
            games = self._parse_schedule_response(data, "R")  # Default to regular season
            return games[0] if games else None

        except Exception as e:
            logger.error("Failed to fetch game details", game_id=game_id, error=str(e))
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
                status=status,
                game_type=game_type  # Pass the game type from the API request
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
