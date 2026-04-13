"""Tests for play-by-play message formatting."""

from typing import Any

from mariners_bot.main import MarinersBot


def _make_play(event: str, description: str, outs: int = 1, is_scoring: bool = False,
               away_score: int | None = None, home_score: int | None = None) -> dict[str, Any]:
    return {
        "result": {
            "event": event,
            "description": description,
            "awayScore": away_score,
            "homeScore": home_score,
        },
        "about": {"isScoringPlay": is_scoring},
        "count": {"outs": outs},
        "playEvents": [],
    }


class TestFormatPlay:
    def setup_method(self) -> None:
        self.bot = MarinersBot.__new__(MarinersBot)

    def test_normal_play_no_review(self) -> None:
        play = _make_play("Strikeout", "Josh Naylor strikes out swinging.")
        text = self.bot._format_play(play)
        assert "Josh Naylor strikes out swinging." in text
        assert "challenge" not in text.lower()
        assert "overturned" not in text.lower()
        assert "upheld" not in text.lower()

    def test_out_dots(self) -> None:
        play = _make_play("Flyout", "Flyout.", outs=2)
        text = self.bot._format_play(play)
        assert "●●○" in text

    def test_scoring_play_shows_score(self) -> None:
        play = _make_play("Home Run", "Home run!", outs=0, is_scoring=True,
                          away_score=2, home_score=1)
        text = self.bot._format_play(play)
        assert "2–1" in text

    def test_non_scoring_play_hides_score(self) -> None:
        play = _make_play("Groundout", "Groundout.", outs=1, is_scoring=False,
                          away_score=2, home_score=1)
        text = self.bot._format_play(play)
        assert "2–1" not in text

    # -------------------------------------------------------------------------
    # Play-level review (manager challenge / umpire review)
    # -------------------------------------------------------------------------

    def test_play_level_review_upheld(self) -> None:
        play = _make_play("Manager Challenge", "Manager challenges the call.")
        play["reviewDetails"] = {"isOverturned": False, "inProgress": False, "reviewType": "manager"}
        text = self.bot._format_play(play)
        assert "Call upheld" in text
        assert "overturned" not in text.lower()

    def test_play_level_review_overturned(self) -> None:
        play = _make_play("Manager Challenge", "Manager challenges the call.")
        play["reviewDetails"] = {"isOverturned": True, "inProgress": False, "reviewType": "manager"}
        text = self.bot._format_play(play)
        assert "Call overturned" in text
        assert "upheld" not in text.lower()

    # -------------------------------------------------------------------------
    # Pitch-level ABS challenge
    # -------------------------------------------------------------------------

    def _make_abs_pitch(self, call_desc: str, overturned: bool, challenger: str) -> dict[str, Any]:
        return {
            "type": "pitch",
            "isPitch": True,
            "details": {"call": {"description": call_desc}},
            "reviewDetails": {
                "isOverturned": overturned,
                "inProgress": False,
                "reviewType": "MJ",
                "challengeTeamId": 136,
                "player": {"id": 1, "fullName": challenger},
            },
        }

    def test_abs_challenge_upheld(self) -> None:
        play = _make_play("Strikeout", "Josh Naylor strikes out swinging.")
        play["playEvents"] = [self._make_abs_pitch("Called Strike", False, "Josh Naylor")]
        text = self.bot._format_play(play)
        assert "Josh Naylor challenges" in text
        assert "called strike" in text
        assert "Call upheld" in text
        assert "overturned" not in text.lower()

    def test_abs_challenge_overturned(self) -> None:
        play = _make_play("Walk", "Josh Naylor walks.")
        play["playEvents"] = [self._make_abs_pitch("Called Strike", True, "Josh Naylor")]
        text = self.bot._format_play(play)
        assert "Josh Naylor challenges" in text
        assert "called strike" in text
        assert "Call overturned" in text
        assert "upheld" not in text.lower()

    def test_abs_challenge_no_player_name(self) -> None:
        play = _make_play("Strikeout", "Batter strikes out.")
        pitch = self._make_abs_pitch("Called Strike", False, "")
        pitch["reviewDetails"]["player"] = {}
        play["playEvents"] = [pitch]
        text = self.bot._format_play(play)
        assert "Challenge" in text
        assert "called strike" in text

    def test_non_pitch_event_with_review_ignored(self) -> None:
        """reviewDetails on a non-pitch playEvent should not trigger ABS line."""
        play = _make_play("Strikeout", "Batter strikes out.")
        play["playEvents"] = [{
            "type": "action",
            "isPitch": False,
            "details": {},
            "reviewDetails": {"isOverturned": True, "inProgress": False, "reviewType": "MJ"},
        }]
        text = self.bot._format_play(play)
        assert "challenges" not in text
        assert "overturned" not in text.lower()

    def test_only_first_abs_challenge_shown(self) -> None:
        """If multiple pitches have reviewDetails, only the first is appended."""
        play = _make_play("Strikeout", "Batter strikes out.")
        play["playEvents"] = [
            self._make_abs_pitch("Called Strike", False, "Player A"),
            self._make_abs_pitch("Ball", True, "Player B"),
        ]
        text = self.bot._format_play(play)
        assert "Player A" in text
        assert "Player B" not in text
