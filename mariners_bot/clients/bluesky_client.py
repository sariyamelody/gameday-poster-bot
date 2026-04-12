"""Bluesky public API client for fetching Salmon Run race results."""

from typing import Any

import aiohttp
import structlog

logger = structlog.get_logger(__name__)

_FEED_ENDPOINT = "https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed"
_SALMON_RUN_KEYWORDS = ("#salmonrun", "salmon run")


class BlueskyClient:
    """Lightweight client for the Bluesky public AT Protocol API (no auth required)."""

    def __init__(self) -> None:
        self.session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> "BlueskyClient":
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10),
            headers={"User-Agent": "mariners-bot/0.1.0"},
        )
        return self

    async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        if self.session:
            await self.session.close()

    async def get_new_salmon_run_posts(
        self,
        handle: str,
        seen_uris: set[str],
    ) -> list[tuple[str, str]]:
        """Return (uri, text) for unseen Salmon Run posts from *handle*.

        Fetches the 10 most recent posts and returns any containing salmon-run
        keywords whose URIs haven't been seen yet, in chronological order.
        """
        if not self.session:
            raise RuntimeError("Client not initialized. Use async context manager.")

        try:
            async with self.session.get(
                _FEED_ENDPOINT,
                params={"actor": handle, "limit": 25},
            ) as resp:
                if resp.status != 200:
                    logger.warning(
                        "Bluesky API non-200 response", status=resp.status, handle=handle
                    )
                    return []
                data: dict[str, Any] = await resp.json()
        except Exception as e:
            logger.error("Bluesky API request failed", handle=handle, error=str(e))
            return []

        # Feed is newest-first; reverse so callers process in chronological order.
        results: list[tuple[str, str]] = []
        for item in reversed(data.get("feed", [])):
            post = item.get("post", {})
            uri: str = post.get("uri", "")
            if not uri or uri in seen_uris:
                continue
            text: str = post.get("record", {}).get("text", "")
            if any(kw in text.lower() for kw in _SALMON_RUN_KEYWORDS):
                results.append((uri, text))

        return results
