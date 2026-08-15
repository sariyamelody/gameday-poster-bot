"""Pure detection logic for 'Silly Baseball Is Happening' alerts.

Takes a snapshot of the league-wide scoreboard (one poll, all live games) and a
position cache, and returns the alerts that should fire. No I/O here — the
scoreboard fetch, position-cache population, dedup checks, and message
delivery all live in the caller (main.py's `_check_silly_baseball`).
"""

from dataclasses import dataclass
from enum import Enum

from ..config import Settings
from ..models import LiveScoreboardGame
from ..utils import format_ordinal

NON_PITCHER_POSITIONS_THAT_CAN_PITCH = {"P", "TWP"}  # pitcher or two-way player


class SillyBaseballAlertType(str, Enum):
    """The five 'silly baseball' conditions we detect from linescore data alone."""

    POSITION_PLAYER_PITCHING = "position_player_pitching"
    EXTRA_INNINGS = "extra_innings"
    BLOWOUT = "blowout"
    SLUGFEST_INNING = "slugfest_inning"
    COMEDY_OF_ERRORS = "comedy_of_errors"


@dataclass
class SillyBaseballAlert:
    """One detected condition, ready to be deduped and sent."""

    alert_type: SillyBaseballAlertType
    game_pk: int
    message: str


class SillyBaseballDetector:
    """Evaluates scoreboard snapshots against the five alert conditions."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def unresolved_pitcher_ids(
        self, games: list[LiveScoreboardGame], position_cache: dict[int, str]
    ) -> set[int]:
        """Pitcher ids currently on the mound in a live game but missing from the cache.

        Callers should resolve these (daily bulk sync should normally cover
        everyone; this only catches same-day call-ups) before calling evaluate(),
        so position-player-pitching detection isn't silently skipped.
        """
        return {
            game.current_pitcher_id
            for game in games
            if game.is_live and game.current_pitcher_id is not None
            and game.current_pitcher_id not in position_cache
        }

    def evaluate(
        self, games: list[LiveScoreboardGame], position_cache: dict[int, str]
    ) -> list[SillyBaseballAlert]:
        """Return every alert condition currently true across all live games.

        Dedup (at-most-once-per-game-per-type) is the caller's responsibility.
        """
        alerts: list[SillyBaseballAlert] = []

        for game in games:
            if not game.is_live:
                continue

            alerts.extend(self._check_position_player_pitching(game, position_cache))
            alerts.extend(self._check_extra_innings(game))
            alerts.extend(self._check_blowout(game))
            alerts.extend(self._check_slugfest_inning(game))
            alerts.extend(self._check_comedy_of_errors(game))

        return alerts

    def _check_position_player_pitching(
        self, game: LiveScoreboardGame, position_cache: dict[int, str]
    ) -> list[SillyBaseballAlert]:
        pitcher_id = game.current_pitcher_id
        if pitcher_id is None:
            return []

        position = position_cache.get(pitcher_id)
        if position is None or position in NON_PITCHER_POSITIONS_THAT_CAN_PITCH:
            return []

        message = (
            "🃏 <b>Silly Baseball Is Happening</b>\n"
            f"{game.current_pitcher_name} ({position}) is pitching for the "
            f"{self._pitching_team_name(game)} in the {game.half_inning_label}.\n"
            f"{self._score_line(game)}"
        )
        return [SillyBaseballAlert(SillyBaseballAlertType.POSITION_PLAYER_PITCHING, game.game_pk, message)]

    def _check_extra_innings(self, game: LiveScoreboardGame) -> list[SillyBaseballAlert]:
        start = self.settings.silly_baseball_extra_innings_start
        if game.current_inning is None or game.current_inning < start:
            return []

        message = (
            "🃏 <b>Silly Baseball Is Happening</b>\n"
            f"{game.away_team} @ {game.home_team} is in the {game.half_inning_label} — extra innings!\n"
            f"{self._score_line(game)}"
        )
        return [SillyBaseballAlert(SillyBaseballAlertType.EXTRA_INNINGS, game.game_pk, message)]

    def _check_blowout(self, game: LiveScoreboardGame) -> list[SillyBaseballAlert]:
        if game.run_margin < self.settings.silly_baseball_blowout_margin:
            return []

        leader, trailer, leader_score, trailer_score = self._leader_trailer(game)
        message = (
            "🃏 <b>Silly Baseball Is Happening</b>\n"
            f"{leader} is blowing out {trailer} {leader_score}-{trailer_score} in the {game.half_inning_label}.\n"
        )
        return [SillyBaseballAlert(SillyBaseballAlertType.BLOWOUT, game.game_pk, message)]

    def _check_slugfest_inning(self, game: LiveScoreboardGame) -> list[SillyBaseballAlert]:
        threshold = self.settings.silly_baseball_slugfest_runs
        for inning in game.innings:
            for side, team_name in (("home", game.home_team), ("away", game.away_team)):
                line = inning.home if side == "home" else inning.away
                if line.runs is not None and line.runs >= threshold:
                    half = "bottom" if side == "home" else "top"
                    message = (
                        "🃏 <b>Silly Baseball Is Happening</b>\n"
                        f"{team_name} scored {line.runs} runs in the {half} {format_ordinal(inning.num)}.\n"
                        f"{self._score_line(game)}"
                    )
                    return [SillyBaseballAlert(SillyBaseballAlertType.SLUGFEST_INNING, game.game_pk, message)]
        return []

    def _check_comedy_of_errors(self, game: LiveScoreboardGame) -> list[SillyBaseballAlert]:
        threshold = self.settings.silly_baseball_error_count
        for inning in game.innings:
            # A team's errors happen while it's fielding: home team fields the top
            # half (away batting), away team fields the bottom half (home batting).
            for side, team_name, half in (("home", game.home_team, "top"), ("away", game.away_team, "bottom")):
                line = inning.home if side == "home" else inning.away
                if line.errors is not None and line.errors >= threshold:
                    message = (
                        "🃏 <b>Silly Baseball Is Happening</b>\n"
                        f"{team_name} committed {line.errors} errors in the {half} {format_ordinal(inning.num)}.\n"
                        f"{self._score_line(game)}"
                    )
                    return [SillyBaseballAlert(SillyBaseballAlertType.COMEDY_OF_ERRORS, game.game_pk, message)]
        return []

    @staticmethod
    def _pitching_team_name(game: LiveScoreboardGame) -> str:
        # The pitching team is whichever team is on defense — home team pitches
        # during the top half, away team pitches during the bottom half.
        return game.home_team if game.is_top_inning else game.away_team

    @staticmethod
    def _score_line(game: LiveScoreboardGame) -> str:
        return f"{game.away_team} {game.away_score} - {game.home_team} {game.home_score}"

    @staticmethod
    def _leader_trailer(game: LiveScoreboardGame) -> tuple[str, str, int, int]:
        if game.home_score >= game.away_score:
            return game.home_team, game.away_team, game.home_score, game.away_score
        return game.away_team, game.home_team, game.away_score, game.home_score
