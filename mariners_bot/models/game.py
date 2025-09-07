"""Game data model."""

from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field


class GameStatus(str, Enum):
    """Game status enumeration."""

    SCHEDULED = "scheduled"
    LIVE = "live"
    FINAL = "final"
    POSTPONED = "postponed"
    CANCELLED = "cancelled"


class Game(BaseModel):
    """Represents a Seattle Mariners game."""

    game_id: str = Field(..., description="MLB gamePk identifier")
    date: datetime = Field(..., description="Game start time in UTC")
    home_team: str = Field(..., description="Home team name")
    away_team: str = Field(..., description="Away team name")
    venue: str = Field(..., description="Stadium name")
    status: GameStatus = Field(default=GameStatus.SCHEDULED, description="Game status")
    notification_sent: bool = Field(default=False, description="Whether notification was sent")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Record creation time")
    updated_at: datetime | None = Field(default=None, description="Last update time")

    @property
    def gameday_url(self) -> str:
        """Generate MLB Gameday URL for this game."""
        return f"https://www.mlb.com/gameday/{self.game_id}"

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

        return (
            f"{self.away_team} {away_indicator} @ {self.home_team} {home_indicator} "
            f"({self.date.strftime('%Y-%m-%d %H:%M UTC')})"
        )

    model_config = {
        "json_encoders": {
            datetime: lambda v: v.isoformat(),
        }
    }
