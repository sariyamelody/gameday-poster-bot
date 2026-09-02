"""Tests for the silly-baseball poller's request-volume gating (window + cooldown).

These guard against a repeat of the incident where the poller hit the MLB API
every tick, 24/7, with no regard for whether a game was actually happening —
which (combined with retrying non-retryable 4xx responses) contributed to MLB
blocking the server's IP and silently breaking pre-game notifications too.
"""

from datetime import UTC, datetime, timedelta

from mariners_bot.main import _should_skip_silly_baseball_tick


class TestShouldSkipSillyBaseballTick:
    def test_unsynced_window_polls_regardless_of_time(self) -> None:
        """Fail open before the window has ever been synced (e.g. right after startup)."""
        now = datetime(2026, 9, 2, 3, 0, tzinfo=UTC)  # middle of the night
        assert (
            _should_skip_silly_baseball_tick(
                now=now, cooldown_until=None, window_synced=False, window=None
            )
            is False
        )

    def test_synced_no_games_today_skips(self) -> None:
        """A known-empty window (no games today) suppresses polling entirely."""
        now = datetime(2026, 9, 2, 18, 0, tzinfo=UTC)
        assert (
            _should_skip_silly_baseball_tick(
                now=now, cooldown_until=None, window_synced=True, window=None
            )
            is True
        )

    def test_inside_window_polls(self) -> None:
        now = datetime(2026, 9, 2, 20, 0, tzinfo=UTC)
        window = (datetime(2026, 9, 2, 16, 10, tzinfo=UTC), datetime(2026, 9, 3, 8, 10, tzinfo=UTC))
        assert (
            _should_skip_silly_baseball_tick(
                now=now, cooldown_until=None, window_synced=True, window=window
            )
            is False
        )

    def test_outside_window_skips(self) -> None:
        now = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)  # well before first pitch
        window = (datetime(2026, 9, 2, 16, 10, tzinfo=UTC), datetime(2026, 9, 3, 8, 10, tzinfo=UTC))
        assert (
            _should_skip_silly_baseball_tick(
                now=now, cooldown_until=None, window_synced=True, window=window
            )
            is True
        )

    def test_active_cooldown_skips_even_inside_window(self) -> None:
        now = datetime(2026, 9, 2, 20, 0, tzinfo=UTC)
        window = (datetime(2026, 9, 2, 16, 10, tzinfo=UTC), datetime(2026, 9, 3, 8, 10, tzinfo=UTC))
        cooldown_until = now + timedelta(minutes=5)
        assert (
            _should_skip_silly_baseball_tick(
                now=now, cooldown_until=cooldown_until, window_synced=True, window=window
            )
            is True
        )

    def test_expired_cooldown_does_not_skip(self) -> None:
        now = datetime(2026, 9, 2, 20, 0, tzinfo=UTC)
        window = (datetime(2026, 9, 2, 16, 10, tzinfo=UTC), datetime(2026, 9, 3, 8, 10, tzinfo=UTC))
        cooldown_until = now - timedelta(seconds=1)
        assert (
            _should_skip_silly_baseball_tick(
                now=now, cooldown_until=cooldown_until, window_synced=True, window=window
            )
            is False
        )
