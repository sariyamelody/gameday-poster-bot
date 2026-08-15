"""Tests for the 'Silly Baseball Is Happening' detection logic."""

import pytest

from mariners_bot.config import Settings
from mariners_bot.models import LiveScoreboardGame, ScoreboardInning
from mariners_bot.models.scoreboard import InningLine
from mariners_bot.scheduler.silly_baseball_detector import (
    SillyBaseballAlertType,
    SillyBaseballDetector,
)


@pytest.fixture
def settings() -> Settings:
    return Settings()


@pytest.fixture
def detector(settings: Settings) -> SillyBaseballDetector:
    return SillyBaseballDetector(settings)


def make_game(**overrides: object) -> LiveScoreboardGame:
    """Build a live game with sane defaults, overridable per test."""
    defaults: dict[str, object] = {
        "game_pk": 1001,
        "game_type": "R",
        "is_live": True,
        "home_team": "Tampa Bay Rays",
        "away_team": "Baltimore Orioles",
        "home_score": 2,
        "away_score": 2,
        "current_inning": 5,
        "is_top_inning": False,
        "scheduled_innings": 9,
        "innings": [],
        "current_pitcher_id": None,
        "current_pitcher_name": None,
    }
    defaults.update(overrides)
    return LiveScoreboardGame(**defaults)  # type: ignore[arg-type]


class TestPositionPlayerPitching:
    def test_fires_when_pitcher_is_not_a_pitcher(self, detector: SillyBaseballDetector) -> None:
        game = make_game(current_pitcher_id=42, current_pitcher_name="Yohel Pozo")
        alerts = detector.evaluate([game], position_cache={42: "C"})

        assert len(alerts) == 1
        assert alerts[0].alert_type == SillyBaseballAlertType.POSITION_PLAYER_PITCHING
        assert "Yohel Pozo" in alerts[0].message

    @pytest.mark.parametrize("position", ["P", "TWP"])
    def test_does_not_fire_for_pitchers_or_two_way_players(
        self, detector: SillyBaseballDetector, position: str
    ) -> None:
        game = make_game(current_pitcher_id=42, current_pitcher_name="Real Pitcher")
        alerts = detector.evaluate([game], position_cache={42: position})

        assert alerts == []

    def test_does_not_fire_when_pitcher_missing_from_cache(self, detector: SillyBaseballDetector) -> None:
        game = make_game(current_pitcher_id=42, current_pitcher_name="Unknown")
        alerts = detector.evaluate([game], position_cache={})

        assert alerts == []

    def test_unresolved_pitcher_ids_reports_cache_misses(self, detector: SillyBaseballDetector) -> None:
        game = make_game(current_pitcher_id=42)
        assert detector.unresolved_pitcher_ids([game], position_cache={}) == {42}
        assert detector.unresolved_pitcher_ids([game], position_cache={42: "C"}) == set()

    def test_unresolved_ignores_non_live_games(self, detector: SillyBaseballDetector) -> None:
        game = make_game(current_pitcher_id=42, is_live=False)
        assert detector.unresolved_pitcher_ids([game], position_cache={}) == set()


class TestExtraInnings:
    def test_fires_at_configured_start_inning(self, detector: SillyBaseballDetector) -> None:
        game = make_game(current_inning=12)
        alerts = detector.evaluate([game], position_cache={})

        assert len(alerts) == 1
        assert alerts[0].alert_type == SillyBaseballAlertType.EXTRA_INNINGS

    def test_does_not_fire_before_extra_innings(self, detector: SillyBaseballDetector) -> None:
        game = make_game(current_inning=11)
        alerts = detector.evaluate([game], position_cache={})

        assert alerts == []

    def test_custom_threshold_respected(self, settings: Settings) -> None:
        settings.silly_baseball_extra_innings_start = 10
        detector = SillyBaseballDetector(settings)
        game = make_game(current_inning=10)

        alerts = detector.evaluate([game], position_cache={})
        assert len(alerts) == 1


class TestBlowout:
    def test_fires_at_or_above_margin(self, detector: SillyBaseballDetector) -> None:
        game = make_game(home_score=12, away_score=2)
        alerts = detector.evaluate([game], position_cache={})

        assert len(alerts) == 1
        assert alerts[0].alert_type == SillyBaseballAlertType.BLOWOUT
        assert "Tampa Bay Rays" in alerts[0].message  # leading team named

    def test_does_not_fire_below_margin(self, detector: SillyBaseballDetector) -> None:
        game = make_game(home_score=8, away_score=2)  # margin 6 < default 10
        alerts = detector.evaluate([game], position_cache={})

        assert alerts == []

    def test_away_team_leading_is_named_as_leader(self, detector: SillyBaseballDetector) -> None:
        game = make_game(home_score=1, away_score=11)
        alerts = detector.evaluate([game], position_cache={})

        assert "Baltimore Orioles is blowing out" in alerts[0].message


class TestSlugfestInning:
    def test_fires_when_home_scores_enough_in_one_inning(self, detector: SillyBaseballDetector) -> None:
        game = make_game(innings=[
            ScoreboardInning(num=3, home=InningLine(runs=8, errors=0), away=InningLine(runs=0, errors=0)),
        ])
        alerts = detector.evaluate([game], position_cache={})

        assert len(alerts) == 1
        assert alerts[0].alert_type == SillyBaseballAlertType.SLUGFEST_INNING
        assert "Tampa Bay Rays scored 8 runs in the bottom 3rd" in alerts[0].message

    def test_fires_when_away_scores_enough_in_one_inning(self, detector: SillyBaseballDetector) -> None:
        game = make_game(innings=[
            ScoreboardInning(num=7, home=InningLine(runs=0, errors=0), away=InningLine(runs=9, errors=0)),
        ])
        alerts = detector.evaluate([game], position_cache={})

        assert "Baltimore Orioles scored 9 runs in the top 7th" in alerts[0].message

    def test_does_not_fire_below_threshold(self, detector: SillyBaseballDetector) -> None:
        game = make_game(innings=[
            ScoreboardInning(num=3, home=InningLine(runs=4, errors=0), away=InningLine(runs=0, errors=0)),
        ])
        alerts = detector.evaluate([game], position_cache={})

        assert alerts == []


class TestComedyOfErrors:
    def test_home_errors_attributed_to_top_half(self, detector: SillyBaseballDetector) -> None:
        # Home team's errors happen while fielding, i.e. during the top half.
        game = make_game(innings=[
            ScoreboardInning(num=5, home=InningLine(runs=0, errors=3), away=InningLine(runs=0, errors=0)),
        ])
        alerts = detector.evaluate([game], position_cache={})

        assert len(alerts) == 1
        assert alerts[0].alert_type == SillyBaseballAlertType.COMEDY_OF_ERRORS
        assert "Tampa Bay Rays committed 3 errors in the top 5th" in alerts[0].message

    def test_away_errors_attributed_to_bottom_half(self, detector: SillyBaseballDetector) -> None:
        game = make_game(innings=[
            ScoreboardInning(num=6, home=InningLine(runs=0, errors=0), away=InningLine(runs=0, errors=4)),
        ])
        alerts = detector.evaluate([game], position_cache={})

        assert "Baltimore Orioles committed 4 errors in the bottom 6th" in alerts[0].message

    def test_does_not_fire_below_threshold(self, detector: SillyBaseballDetector) -> None:
        game = make_game(innings=[
            ScoreboardInning(num=6, home=InningLine(runs=0, errors=1), away=InningLine(runs=0, errors=0)),
        ])
        alerts = detector.evaluate([game], position_cache={})

        assert alerts == []


class TestGeneralBehavior:
    def test_skips_non_live_games(self, detector: SillyBaseballDetector) -> None:
        game = make_game(current_inning=15, is_live=False)
        alerts = detector.evaluate([game], position_cache={})

        assert alerts == []

    def test_multiple_conditions_can_fire_for_same_game(self, detector: SillyBaseballDetector) -> None:
        game = make_game(current_inning=12, home_score=15, away_score=2)
        alerts = detector.evaluate([game], position_cache={})

        alert_types = {a.alert_type for a in alerts}
        assert SillyBaseballAlertType.EXTRA_INNINGS in alert_types
        assert SillyBaseballAlertType.BLOWOUT in alert_types

    def test_evaluates_across_multiple_games(self, detector: SillyBaseballDetector) -> None:
        game1 = make_game(game_pk=1, current_inning=12)
        game2 = make_game(game_pk=2, home_score=20, away_score=1)
        alerts = detector.evaluate([game1, game2], position_cache={})

        game_pks = {a.game_pk for a in alerts}
        assert game_pks == {1, 2}
