"""Game data model."""

from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field, field_serializer


class GameStatus(str, Enum):
    """Game status enumeration."""

    SCHEDULED = "scheduled"
    LIVE = "live"
    FINAL = "final"
    POSTPONED = "postponed"
    CANCELLED = "cancelled"


class GameType(str, Enum):
    """Game type enumeration."""

    REGULAR = "R"          # Regular season
    SPRING = "S"           # Spring training
    POSTSEASON = "P"       # Postseason (generic)
    DIVISION_SERIES = "D"  # Division Series
    LEAGUE_CHAMPIONSHIP = "L"  # League Championship Series
    CHAMPIONSHIP = "F"     # Championship Series
    WORLD_SERIES = "W"     # World Series


class Game(BaseModel):
    """Represents a Seattle Mariners game."""

    game_id: str = Field(..., description="MLB gamePk identifier")
    date: datetime = Field(..., description="Game start time in UTC")
    home_team: str = Field(..., description="Home team name")
    away_team: str = Field(..., description="Away team name")
    venue: str = Field(..., description="Stadium name")
    status: GameStatus = Field(default=GameStatus.SCHEDULED, description="Game status")
    game_type: GameType = Field(default=GameType.REGULAR, description="Game type (regular, postseason, etc.)")
    notification_sent: bool = Field(default=False, description="Whether notification was sent")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Record creation time")
    updated_at: datetime | None = Field(default=None, description="Last update time")

    @property
    def gameday_url(self) -> str:
        """Generate MLB Gameday URL for this game."""
        return f"https://www.mlb.com/gameday/{self.game_id}"

    @property
    def baseball_savant_url(self) -> str:
        """Generate Baseball Savant Gamefeed URL for this game."""
        return f"https://baseballsavant.mlb.com/gamefeed?gamePk={self.game_id}"

    @property
    def is_mariners_home(self) -> bool:
        """Check if Mariners are the home team."""
        return "mariners" in self.home_team.lower() or "seattle" in self.home_team.lower()

    @property
    def is_mariners_away(self) -> bool:
        """Check if Mariners are the away team."""
        return "mariners" in self.away_team.lower() or "seattle" in self.away_team.lower()

    @property
    def is_mariners_game(self) -> bool:
        """Check if this is a Mariners game."""
        return self.is_mariners_home or self.is_mariners_away

    @property
    def opponent(self) -> str:
        """Get the opposing team name."""
        if self.is_mariners_home:
            return self.away_team
        elif self.is_mariners_away:
            return self.home_team
        else:
            return "Unknown"

    def __str__(self) -> str:
        """String representation of the game."""
        home_indicator = "🏠" if self.is_mariners_home else ""
        away_indicator = "✈️" if self.is_mariners_away else ""

        # Add game type emoji/indicator
        type_indicator = ""
        if self.game_type in [GameType.POSTSEASON, GameType.DIVISION_SERIES, GameType.LEAGUE_CHAMPIONSHIP, GameType.CHAMPIONSHIP]:
            type_indicator = "🏆 "
        elif self.game_type == GameType.WORLD_SERIES:
            type_indicator = "🌟 "
        elif self.game_type == GameType.SPRING:
            type_indicator = "🌸 "

        return (
            f"{type_indicator}{self.away_team} {away_indicator} @ {self.home_team} {home_indicator} "
            f"({self.date.strftime('%Y-%m-%d %H:%M UTC')})"
        )

    @field_serializer('date', 'created_at', 'updated_at')
    def serialize_datetime(self, value: datetime | None) -> str | None:
        """Serialize datetime fields to ISO format."""
        return value.isoformat() if value else None

    @field_serializer('status')
    def serialize_status(self, value: GameStatus) -> str:
        """Serialize GameStatus enum to string value."""
        return value.value
