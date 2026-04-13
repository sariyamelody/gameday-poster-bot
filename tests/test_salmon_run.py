"""Tests for Salmon Run Bluesky client and monitor."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mariners_bot.clients.bluesky_client import (
    BlueskyClient,
    SalmonRunPost,
    _extract_thumbnail,
    _parse_created_at,
)
from mariners_bot.config import Settings
from mariners_bot.scheduler.salmon_run_monitor import SalmonRunMonitor

_NOW = datetime(2026, 4, 12, 3, 0, 0, tzinfo=UTC)
_YESTERDAY = _NOW - timedelta(days=1)
_EARLIER_TODAY = _NOW - timedelta(hours=2)


def make_feed(*posts: dict[str, Any]) -> dict[str, Any]:
    """Build a fake Bluesky getAuthorFeed response (newest-first, like the real API)."""
    return {
        "feed": [
            {
                "post": {
                    "uri": p["uri"],
                    "record": {
                        "text": p["text"],
                        "createdAt": p.get("created_at", _NOW.isoformat()),
                    },
                    "author": {
                        "handle": p.get("handle", "test.bsky.social"),
                        "displayName": p.get("display_name", "Test Account"),
                    },
                    "embed": p.get("embed", {}),
                }
            }
            for p in posts
        ]
    }


def make_post(**kwargs: Any) -> SalmonRunPost:
    return SalmonRunPost(
        uri=kwargs.get("uri", "at://did:plc:abc/app.bsky.feed.post/rkey123"),
        text=kwargs.get("text", "Humpy wins! #SalmonRun"),
        author_handle=kwargs.get("author_handle", "circlingseasports.bsky.social"),
        author_display_name=kwargs.get("author_display_name", "Circling Seattle Sports"),
        thumbnail_url=kwargs.get("thumbnail_url", None),
        created_at=kwargs.get("created_at", _NOW),
    )


def settings() -> Settings:
    return Settings(telegram_bot_token="test")


# ---------------------------------------------------------------------------
# SalmonRunPost
# ---------------------------------------------------------------------------

class TestSalmonRunPost:
    def test_web_url_builds_from_uri(self) -> None:
        post = make_post(
            uri="at://did:plc:abc/app.bsky.feed.post/rkey123",
            author_handle="circlingseasports.bsky.social",
        )
        assert post.web_url == "https://bsky.app/profile/circlingseasports.bsky.social/post/rkey123"


# ---------------------------------------------------------------------------
# _extract_thumbnail
# ---------------------------------------------------------------------------

class TestExtractThumbnail:
    def test_video_embed(self) -> None:
        embed = {
            "$type": "app.bsky.embed.video#view",
            "thumbnail": "https://video.bsky.app/thumbnail.jpg",
        }
        assert _extract_thumbnail(embed) == "https://video.bsky.app/thumbnail.jpg"

    def test_images_embed(self) -> None:
        embed = {
            "$type": "app.bsky.embed.images#view",
            "images": [{"thumb": "https://cdn.bsky.app/thumb.jpg"}],
        }
        assert _extract_thumbnail(embed) == "https://cdn.bsky.app/thumb.jpg"

    def test_images_embed_falls_back_to_fullsize(self) -> None:
        embed = {
            "$type": "app.bsky.embed.images#view",
            "images": [{"fullsize": "https://cdn.bsky.app/full.jpg"}],
        }
        assert _extract_thumbnail(embed) == "https://cdn.bsky.app/full.jpg"

    def test_unknown_embed_type(self) -> None:
        assert _extract_thumbnail({"$type": "app.bsky.embed.record#view"}) is None

    def test_empty_embed(self) -> None:
        assert _extract_thumbnail({}) is None


# ---------------------------------------------------------------------------
# _parse_created_at
# ---------------------------------------------------------------------------

class TestParseCreatedAt:
    def test_parses_z_suffix(self) -> None:
        result = _parse_created_at("2026-04-12T02:58:45.331Z")
        assert result is not None
        assert result.tzinfo is UTC
        assert result.year == 2026

    def test_parses_offset(self) -> None:
        result = _parse_created_at("2026-04-12T02:58:45+00:00")
        assert result is not None
        assert result.tzinfo is UTC

    def test_returns_none_on_invalid(self) -> None:
        assert _parse_created_at("not-a-date") is None

    def test_returns_none_on_empty(self) -> None:
        assert _parse_created_at("") is None


# ---------------------------------------------------------------------------
# BlueskyClient
# ---------------------------------------------------------------------------

class TestBlueskyClient:
    async def _fetch(
        self,
        feed: dict[str, Any],
        seen: set[str] | None = None,
        since: datetime | None = None,
    ) -> list[SalmonRunPost]:
        """Helper: run get_new_salmon_run_posts with a mocked HTTP response."""
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value=feed)
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_resp)
        mock_session.close = AsyncMock()

        with patch("aiohttp.ClientSession", return_value=mock_session):
            async with BlueskyClient() as bsky:
                return await bsky.get_new_salmon_run_posts(
                    "test.bsky.social", seen or set(), since=since
                )

    @pytest.mark.asyncio
    async def test_returns_salmon_run_posts(self) -> None:
        feed = make_feed(
            {"uri": "at://1", "text": "Sockeye wins tonight. #SalmonRun"},
            {"uri": "at://2", "text": "Game recap here."},
        )
        results = await self._fetch(feed)
        assert len(results) == 1
        assert results[0].uri == "at://1"
        assert results[0].text == "Sockeye wins tonight. #SalmonRun"

    @pytest.mark.asyncio
    async def test_filters_non_salmon_posts(self) -> None:
        feed = make_feed(
            {"uri": "at://1", "text": "Silver boat wins the Hydro Challenge. #HydroChallenge"},
            {"uri": "at://2", "text": "Mariners walk off in the 9th!"},
        )
        results = await self._fetch(feed)
        assert results == []

    @pytest.mark.asyncio
    async def test_matches_case_insensitively(self) -> None:
        feed = make_feed({"uri": "at://1", "text": "King wins the salmon run tonight!"})
        results = await self._fetch(feed)
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_skips_seen_uris(self) -> None:
        feed = make_feed(
            {"uri": "at://1", "text": "Humpy wins! #SalmonRun"},
            {"uri": "at://2", "text": "Sockeye wins! #SalmonRun"},
        )
        results = await self._fetch(feed, seen={"at://1"})
        assert [p.uri for p in results] == ["at://2"]

    @pytest.mark.asyncio
    async def test_returns_chronological_order(self) -> None:
        # Feed is newest-first; results should be oldest-first (chronological).
        feed = make_feed(
            {"uri": "at://newer", "text": "King wins! #SalmonRun"},
            {"uri": "at://older", "text": "Silver wins! #SalmonRun"},
        )
        results = await self._fetch(feed)
        assert [p.uri for p in results] == ["at://older", "at://newer"]

    @pytest.mark.asyncio
    async def test_populates_author_fields(self) -> None:
        feed = make_feed({
            "uri": "at://1",
            "text": "Humpy wins! #SalmonRun",
            "handle": "circlingseasports.bsky.social",
            "display_name": "Circling Seattle Sports",
        })
        results = await self._fetch(feed)
        assert results[0].author_handle == "circlingseasports.bsky.social"
        assert results[0].author_display_name == "Circling Seattle Sports"

    @pytest.mark.asyncio
    async def test_extracts_video_thumbnail(self) -> None:
        feed = make_feed({
            "uri": "at://1",
            "text": "Sockeye wins! #SalmonRun",
            "embed": {
                "$type": "app.bsky.embed.video#view",
                "thumbnail": "https://video.bsky.app/thumb.jpg",
            },
        })
        results = await self._fetch(feed)
        assert results[0].thumbnail_url == "https://video.bsky.app/thumb.jpg"

    @pytest.mark.asyncio
    async def test_thumbnail_none_when_no_embed(self) -> None:
        feed = make_feed({"uri": "at://1", "text": "Silver wins! #SalmonRun"})
        results = await self._fetch(feed)
        assert results[0].thumbnail_url is None

    @pytest.mark.asyncio
    async def test_filters_posts_before_since(self) -> None:
        cutoff = _NOW - timedelta(hours=1)
        feed = make_feed(
            {"uri": "at://old", "text": "Humpy wins! #SalmonRun",
             "created_at": _YESTERDAY.isoformat()},
            {"uri": "at://new", "text": "Sockeye wins! #SalmonRun",
             "created_at": _NOW.isoformat()},
        )
        results = await self._fetch(feed, since=cutoff)
        assert [p.uri for p in results] == ["at://new"]

    @pytest.mark.asyncio
    async def test_skips_post_exactly_at_since(self) -> None:
        feed = make_feed(
            {"uri": "at://exact", "text": "King wins! #SalmonRun",
             "created_at": _NOW.isoformat()},
        )
        results = await self._fetch(feed, since=_NOW)
        assert results == []

    @pytest.mark.asyncio
    async def test_no_since_returns_all_matching(self) -> None:
        feed = make_feed(
            {"uri": "at://old", "text": "Humpy wins! #SalmonRun",
             "created_at": _YESTERDAY.isoformat()},
            {"uri": "at://new", "text": "Sockeye wins! #SalmonRun",
             "created_at": _NOW.isoformat()},
        )
        results = await self._fetch(feed)
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_skips_post_with_missing_created_at(self) -> None:
        # If createdAt is absent the post is silently skipped rather than erroring.
        feed = make_feed({"uri": "at://1", "text": "Silver wins! #SalmonRun"})
        feed["feed"][0]["post"]["record"].pop("createdAt", None)
        results = await self._fetch(feed)
        assert results == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_non_200(self) -> None:
        mock_resp = AsyncMock()
        mock_resp.status = 429
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_resp)
        mock_session.close = AsyncMock()

        with patch("aiohttp.ClientSession", return_value=mock_session):
            async with BlueskyClient() as bsky:
                results = await bsky.get_new_salmon_run_posts("test.bsky.social", set())
        assert results == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_network_error(self) -> None:
        mock_session = MagicMock()
        mock_session.get = MagicMock(side_effect=OSError("connection refused"))
        mock_session.close = AsyncMock()

        with patch("aiohttp.ClientSession", return_value=mock_session):
            async with BlueskyClient() as bsky:
                results = await bsky.get_new_salmon_run_posts("test.bsky.social", set())
        assert results == []


# ---------------------------------------------------------------------------
# SalmonRunMonitor
# ---------------------------------------------------------------------------

def make_monitor(
    on_result: Callable[[SalmonRunPost], Awaitable[None]] | None = None,
) -> SalmonRunMonitor:
    if on_result is None:
        on_result = AsyncMock()
    return SalmonRunMonitor(settings(), on_result=on_result)


class TestSalmonRunMonitor:
    def test_noop_for_road_game(self) -> None:
        monitor = make_monitor()
        with patch("asyncio.create_task") as mock_create:
            monitor.on_inning_end("game1", is_home_game=False)
            mock_create.assert_not_called()

    def test_starts_task_for_home_game(self) -> None:
        monitor = make_monitor()
        mock_task = MagicMock(spec=asyncio.Task)
        mock_task.done.return_value = False
        # Patch _poll_loop so no unawaited coroutine is created when create_task discards it.
        with patch.object(monitor, "_poll_loop", return_value=MagicMock()), \
                patch("asyncio.create_task", return_value=mock_task) as mock_create:
            monitor.on_inning_end("game1", is_home_game=True)
            mock_create.assert_called_once()

    def test_does_not_double_start(self) -> None:
        monitor = make_monitor()
        mock_task = MagicMock(spec=asyncio.Task)
        mock_task.done.return_value = False
        monitor._task = mock_task  # already running

        with patch("asyncio.create_task") as mock_create:
            monitor.on_inning_end("game1", is_home_game=True)
            mock_create.assert_not_called()

    def test_resets_state_on_new_game(self) -> None:
        monitor = make_monitor()
        monitor._game_id = "old_game"
        monitor._posted = True
        monitor._seen_uris = {"at://old"}

        with patch.object(monitor, "_poll_loop", return_value=MagicMock()), \
                patch("asyncio.create_task"):
            monitor.on_inning_end("new_game", is_home_game=True)

        assert monitor._game_id == "new_game"
        assert monitor._posted is False
        assert monitor._seen_uris == set()

    def test_sets_game_start_cutoff_on_new_game(self) -> None:
        monitor = make_monitor()
        assert monitor._game_start_cutoff is None

        before = datetime.now(UTC)
        with patch.object(monitor, "_poll_loop", return_value=MagicMock()), \
                patch("asyncio.create_task"):
            monitor.on_inning_end("game1", is_home_game=True)
        after = datetime.now(UTC)

        assert monitor._game_start_cutoff is not None
        assert before <= monitor._game_start_cutoff <= after

    def test_does_not_reset_cutoff_on_same_game(self) -> None:
        monitor = make_monitor()
        fixed_cutoff = datetime(2026, 4, 12, 1, 0, 0, tzinfo=UTC)
        monitor._game_id = "game1"
        monitor._game_start_cutoff = fixed_cutoff

        with patch.object(monitor, "_poll_loop", return_value=MagicMock()), \
                patch("asyncio.create_task"):
            monitor.on_inning_end("game1", is_home_game=True)

        assert monitor._game_start_cutoff == fixed_cutoff

    def test_cancels_pending_stop_when_inning_ends_early(self) -> None:
        # If an inning finishes before the 2-minute auto-stop fires, the pending
        # cancel must be cleared so it doesn't kill the extended polling window.
        monitor = make_monitor()
        pending_stop = MagicMock()
        monitor._stop_handle = pending_stop

        mock_task = MagicMock(spec=asyncio.Task)
        mock_task.done.return_value = False
        monitor._task = mock_task  # polling already running

        monitor.on_inning_end("game1", is_home_game=True)

        pending_stop.cancel.assert_called_once()
        assert monitor._stop_handle is None

    def test_noop_if_already_posted_this_game(self) -> None:
        monitor = make_monitor()
        monitor._game_id = "game1"
        monitor._posted = True

        with patch("asyncio.create_task") as mock_create:
            monitor.on_inning_end("game1", is_home_game=True)
            mock_create.assert_not_called()

    def test_on_inning_start_schedules_stop(self) -> None:
        monitor = make_monitor()
        mock_loop = MagicMock()
        with patch("asyncio.get_event_loop", return_value=mock_loop):
            monitor.on_inning_start()
        mock_loop.call_later.assert_called_once_with(120, monitor._cancel)

    def test_on_inning_start_replaces_existing_stop(self) -> None:
        monitor = make_monitor()
        old_handle = MagicMock()
        monitor._stop_handle = old_handle

        mock_loop = MagicMock()
        with patch("asyncio.get_event_loop", return_value=mock_loop):
            monitor.on_inning_start()

        old_handle.cancel.assert_called_once()

    def test_stop_cancels_task_and_handle(self) -> None:
        monitor = make_monitor()
        mock_task = MagicMock(spec=asyncio.Task)
        mock_task.done.return_value = False
        mock_handle = MagicMock()
        monitor._task = mock_task
        monitor._stop_handle = mock_handle

        monitor.stop()

        mock_handle.cancel.assert_called_once()
        mock_task.cancel.assert_called_once()

    def test_cancel_noop_when_task_already_done(self) -> None:
        monitor = make_monitor()
        mock_task = MagicMock(spec=asyncio.Task)
        mock_task.done.return_value = True
        monitor._task = mock_task

        monitor._cancel()  # should not raise
        mock_task.cancel.assert_not_called()

    @pytest.mark.asyncio
    async def test_poll_loop_calls_on_result_and_stops(self) -> None:
        on_result = AsyncMock()
        monitor = make_monitor(on_result=on_result)
        monitor._game_id = "game1"

        post = make_post()
        mock_bsky = AsyncMock()
        mock_bsky.get_new_salmon_run_posts = AsyncMock(return_value=[post])
        mock_bsky.__aenter__ = AsyncMock(return_value=mock_bsky)
        mock_bsky.__aexit__ = AsyncMock(return_value=False)

        with patch("mariners_bot.scheduler.salmon_run_monitor.BlueskyClient", return_value=mock_bsky):
            await monitor._poll_loop()

        on_result.assert_awaited_once_with(post)
        assert monitor._posted is True
        assert post.uri in monitor._seen_uris

    @pytest.mark.asyncio
    async def test_poll_loop_handles_cancellation(self) -> None:
        monitor = make_monitor()

        mock_bsky = AsyncMock()
        mock_bsky.get_new_salmon_run_posts = AsyncMock(return_value=[])
        mock_bsky.__aenter__ = AsyncMock(return_value=mock_bsky)
        mock_bsky.__aexit__ = AsyncMock(return_value=False)

        with patch("mariners_bot.scheduler.salmon_run_monitor.BlueskyClient", return_value=mock_bsky):
            with patch("asyncio.sleep", side_effect=asyncio.CancelledError):
                await monitor._poll_loop()  # should not raise

    @pytest.mark.asyncio
    async def test_poll_loop_continues_on_client_error(self) -> None:
        on_result = AsyncMock()
        monitor = make_monitor(on_result=on_result)

        call_count = 0

        async def flaky_fetch(**_: Any) -> list[SalmonRunPost]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise OSError("timeout")
            return [make_post()]

        mock_bsky = AsyncMock()
        mock_bsky.get_new_salmon_run_posts = flaky_fetch
        mock_bsky.__aenter__ = AsyncMock(return_value=mock_bsky)
        mock_bsky.__aexit__ = AsyncMock(return_value=False)

        sleep_calls = 0

        async def fake_sleep(_: float) -> None:
            nonlocal sleep_calls
            sleep_calls += 1

        with patch("mariners_bot.scheduler.salmon_run_monitor.BlueskyClient", return_value=mock_bsky):
            with patch("asyncio.sleep", side_effect=fake_sleep):
                await monitor._poll_loop()

        assert call_count == 2
        on_result.assert_awaited_once()
