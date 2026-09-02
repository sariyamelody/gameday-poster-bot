"""League-wide live scoreboard model, used by the 'Silly Baseball Is Happening' detector.

Parsed from a single `GET /api/v1/schedule?hydrate=linescore` call that covers every
MLB game on a given day — no per-game live-feed polling involved.
"""

from datetime import datetime

from pydantic import BaseModel, Field

from ..utils import format_ordinal


class InningLine(BaseModel):
    """Runs/errors for one team in one half-inning."""

    runs: int | None = Field(default=None)
    errors: int | None = Field(default=None)


class ScoreboardInning(BaseModel):
    """One inning's linescore entry.

    `home` reflects the home team's fielding stats during the TOP half (when the
    away team is batting); `away` reflects the away team's fielding stats during
    the BOTTOM half. Errors are always attributed to the team that committed
    them, i.e. the team that was fielding, not batting.
    """

    num: int
    home: InningLine
    away: InningLine


class LiveScoreboardGame(BaseModel):
    """One game's current state from the league-wide scoreboard poll."""

    game_pk: int
    game_type: str
    is_live: bool
    game_date: datetime | None = None  # scheduled first pitch (UTC); used to gate polling
    home_team: str
    away_team: str
    home_score: int
    away_score: int
    current_inning: int | None = None
    is_top_inning: bool | None = None
    scheduled_innings: int = 9
    innings: list[ScoreboardInning] = Field(default_factory=list)
    current_pitcher_id: int | None = None
    current_pitcher_name: str | None = None

    @property
    def is_extra_innings(self) -> bool:
        """Whether the game is currently in (or past) the first extra inning."""
        return self.current_inning is not None and self.current_inning > self.scheduled_innings

    @property
    def run_margin(self) -> int:
        """Absolute run differential between the two teams."""
        return abs(self.home_score - self.away_score)

    @property
    def half_inning_label(self) -> str:
        """E.g. 'top 9th' or 'bottom 12th', for message formatting."""
        if self.current_inning is None:
            return ""
        half = "top" if self.is_top_inning else "bottom"
        return f"{half} {format_ordinal(self.current_inning)}"
