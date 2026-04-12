"""Salmon Run result monitor — polls Bluesky between innings at home games."""

import asyncio
from collections.abc import Awaitable, Callable

import structlog

from ..clients import BlueskyClient
from ..config import Settings

logger = structlog.get_logger(__name__)


class SalmonRunMonitor:
    """Polls Bluesky for Salmon Run race results during the between-inning window.

    Usage:
        monitor = SalmonRunMonitor(settings, on_result=post_fn)
        # When an inning ends:
        monitor.on_inning_end(game_id, is_home_game)
        # When the next inning starts:
        monitor.on_inning_start()
        # On shutdown:
        monitor.stop()
    """

    def __init__(
        self,
        settings: Settings,
        on_result: Callable[[str], Awaitable[None]],
    ) -> None:
        self.settings = settings
        self._on_result = on_result

        self._task: asyncio.Task[None] | None = None
        self._stop_handle: asyncio.TimerHandle | None = None
        self._game_id: str | None = None
        self._posted: bool = False
        self._seen_uris: set[str] = set()

    def on_inning_end(self, game_id: str, is_home_game: bool) -> None:
        """Open the polling window (call when an inning ends).

        No-ops for road games, if we already found a result this game, or if
        polling is already running.  Resets per-game state when *game_id*
        changes.
        """
        if not is_home_game:
            return

        if game_id != self._game_id:
            self._game_id = game_id
            self._posted = False
            self._seen_uris = set()

        if self._posted:
            return

        if self._task and not self._task.done():
            return  # already polling

        # Cancel any pending auto-stop left over from a previous inning.
        if self._stop_handle:
            self._stop_handle.cancel()
            self._stop_handle = None

        self._task = asyncio.create_task(self._poll_loop())
        logger.info("Salmon Run polling started", game_id=game_id)

    def on_inning_start(self) -> None:
        """Schedule the polling window to close 2 minutes from now."""
        if self._stop_handle:
            self._stop_handle.cancel()
        loop = asyncio.get_event_loop()
        self._stop_handle = loop.call_later(120, self._cancel)

    def stop(self) -> None:
        """Cancel all polling immediately (call on bot shutdown)."""
        if self._stop_handle:
            self._stop_handle.cancel()
            self._stop_handle = None
        if self._task and not self._task.done():
            self._task.cancel()

    def _cancel(self) -> None:
        """Cancel the poll task (invoked by the 2-minute timer)."""
        self._stop_handle = None
        if self._task and not self._task.done():
            self._task.cancel()
            logger.info("Salmon Run polling stopped (timer)")

    async def _poll_loop(self) -> None:
        """Poll Bluesky every poll_interval seconds until a result is found or cancelled."""
        try:
            async with BlueskyClient() as bsky:
                while True:
                    try:
                        posts = await bsky.get_new_salmon_run_posts(
                            handle=self.settings.salmon_run_bsky_handle,
                            seen_uris=self._seen_uris,
                        )
                        for uri, text in posts:
                            self._seen_uris.add(uri)
                            await self._on_result(text)
                            self._posted = True
                            logger.info("Salmon Run result posted", uri=uri)
                        if self._posted:
                            return
                    except Exception as e:
                        logger.error("Salmon Run poll error", error=str(e))
                    await asyncio.sleep(self.settings.salmon_run_poll_interval)
        except asyncio.CancelledError:
            pass
