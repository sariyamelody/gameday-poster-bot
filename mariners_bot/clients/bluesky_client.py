"""Bluesky public API client for fetching Salmon Run race results."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import aiohttp
import structlog

logger = structlog.get_logger(__name__)

_FEED_ENDPOINT = "https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed"
_SALMON_RUN_KEYWORDS = ("#salmonrun", "salmon run")


@dataclass
class SalmonRunPost:
    """A Bluesky post containing a Salmon Run race result."""

    uri: str
    text: str
    author_handle: str
    author_display_name: str
    thumbnail_url: str | None  # video thumbnail or first image, if present
    created_at: datetime

    @property
    def web_url(self) -> str:
        """Public bsky.app URL for this post."""
        rkey = self.uri.split("/")[-1]
        return f"https://bsky.app/profile/{self.author_handle}/post/{rkey}"


def _extract_thumbnail(embed: dict[str, Any]) -> str | None:
    """Return a thumbnail/image URL from a post embed, or None."""
    embed_type = embed.get("$type", "")
    if embed_type == "app.bsky.embed.video#view":
        return embed.get("thumbnail") or None
    if embed_type == "app.bsky.embed.images#view":
        images = embed.get("images", [])
        if images:
            return images[0].get("thumb") or images[0].get("fullsize") or None
    return None


def _parse_created_at(value: str) -> datetime | None:
    """Parse a Bluesky ISO-8601 createdAt string to an aware UTC datetime."""
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except (ValueError, AttributeError):
        return None


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
        since: datetime | None = None,
    ) -> list[SalmonRunPost]:
        """Return unseen Salmon Run posts from *handle* in chronological order.

        Fetches the 25 most recent posts and returns any containing salmon-run
        keywords whose URIs haven't been seen and were published after *since*.
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
        results: list[SalmonRunPost] = []
        for item in reversed(data.get("feed", [])):
            post = item.get("post", {})
            uri: str = post.get("uri", "")
            if not uri or uri in seen_uris:
                continue
            text: str = post.get("record", {}).get("text", "")
            if not any(kw in text.lower() for kw in _SALMON_RUN_KEYWORDS):
                continue
            created_at = _parse_created_at(post.get("record", {}).get("createdAt", ""))
            if created_at is None:
                continue
            if since is not None and created_at <= since:
                continue
            author = post.get("author", {})
            results.append(SalmonRunPost(
                uri=uri,
                text=text,
                author_handle=author.get("handle", handle),
                author_display_name=author.get("displayName") or author.get("handle", handle),
                thumbnail_url=_extract_thumbnail(post.get("embed", {})),
                created_at=created_at,
            ))

        return results
